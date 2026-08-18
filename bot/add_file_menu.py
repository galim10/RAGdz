from urllib.parse import urlsplit, unquote

from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup
from clients import WikiClient
from config import settings
from data_parser import FileManager
import logging

logger = logging.getLogger(__name__)
router = Router()
bot: Bot | None = None
file_manager = FileManager()


def set_bot_instance(bot_instance: Bot) -> None:
    global bot
    bot = bot_instance


class FileStates(StatesGroup):
    waiting_file = State()
    waiting_url_or_title = State()


async def show_add_menu(callback: types.CallbackQuery):
    try:
        builder = InlineKeyboardBuilder()
        builder.button(text="Загрузить файл", callback_data="upload_file")
        builder.button(text="Поиск произведения в Интернете", callback_data="upload_file_from_wiki")
        builder.button(text="⏪Назад", callback_data="main_menu")
        builder.adjust(1)
        await callback.message.edit_text(
            text="Добавление произведения",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка при показе меню добавления файла: {e}", exc_info=True)


@router.callback_query(F.data == "add_file_menu")
async def add_menu(callback: types.CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        await show_add_menu(callback)
    except Exception as e:
        logger.error(f"Ошибка в add_menu: {e}", exc_info=True)


@router.callback_query(F.data == "upload_file")
async def upload_file(callback: types.CallbackQuery, state: FSMContext):
    try:
        await state.set_state(FileStates.waiting_file)
        await callback.message.edit_text(
            text="Отправьте файл.\nПоддерживаемые форматы: txt, pdf, docx",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="upload_file_quit")]])
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка при установке режима загрузки файла: {e}", exc_info=True)


@router.callback_query(F.data == "upload_file_quit")
async def upload_file_quit(callback: types.CallbackQuery, state: FSMContext):
    try:
        curr_state = await state.get_state()
        if curr_state == FileStates.waiting_file:
            await state.clear()
        await show_add_menu(callback)
    except Exception as e:
        logger.error(f"Ошибка при выходе из режима загрузки: {e}", exc_info=True)


@router.callback_query(F.data == "upload_file_from_wiki")
async def upload_file_from_wiki(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(FileStates.waiting_url_or_title)
    await callback.message.edit_text(
        text="Введите название текста или ссылку на Википедию с этим текстом(адрес начинается с ru.wikisource.org/wiki/)",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="add_file_menu")]])
    )
    await callback.answer()


@router.message(StateFilter(FileStates.waiting_file), F.document)
async def process_file(message: types.Message, state: FSMContext):
    try:
        document = message.document
        file_name = document.file_name

        if not file_name:
            await message.answer("❌ Не удалось определить имя файла")
            return

        if document.file_size and document.file_size > settings.max_file_size_mb * 1024 * 1024:
            await message.answer(f"❌ Файл слишком большой. Максимальный размер: {settings.max_file_size_mb} МБ")
            return

        if file_name.lower().endswith((".txt", ".pdf", ".docx")):
            try:

                await file_manager.add_file(bot, message.from_user.id, file_name, document.file_id)

                await state.clear()
                logger.info(f"Файл {file_name} успешно загружен пользователем {message.from_user.id}")
                await message.answer(
                    text=f"✅ Файл '{file_name}' успешно загружен!\n\nВернитесь назад или отправьте еще один документ",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="upload_file_quit")]]),
                )
            except Exception as e:
                logger.error(f"Ошибка при загрузке файла {file_name}: {e}", exc_info=True)
                await message.answer(
                    text=f"❌ Ошибка при загрузке файла: {str(e)}",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="upload_file_quit")]]),
                )
        else:
            logger.warning(f"Попытка загрузить файл неподдерживаемого формата: {file_name}")
            await message.answer("❌ Неподдерживаемый формат. Используйте txt, pdf или docx")
    except Exception as e:
        logger.error(f"Критическая ошибка в process_file: {e}", exc_info=True)
        await message.answer("❌ Ошибка сервера")


async def upload_text_wiki(user_id: int, page_title: str):
    logger.info(f"uploading text from {page_title}")
    async with WikiClient() as client:
        page_text = await client.get_page_text(page_title)
        await file_manager.save_text(user_id, page_text, page_title)


@router.message(StateFilter(FileStates.waiting_url_or_title), F.text)
async def wiki_search(message: types.Message, state: FSMContext):
    try:
        text = message.text.strip()
        is_url = "ru.wikisource.org" in text
        if is_url:
            await state.clear()
            await message.answer(text="Отлично! Вы отправили ссылку.\n\nНачинаю обработку...")

            url = message.text
            page_title = url[url.find("ru.wikisource.org") + 23:]

            await upload_text_wiki(message.from_user.id, page_title)
            await message.answer(
                text="Текст загружен!",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="add_file_menu")]])
            )
        else:
            async with WikiClient() as client:
                titles = await client.get_samples(text)
                if not titles:
                    await message.answer("❌ По этому запросу ничего не найдено. Попробуйте другое название.")
                    return
                builder = InlineKeyboardBuilder()

                for i, title in enumerate(titles):
                    builder.button(text=title, callback_data=f"text_wiki_{i}")

                await state.update_data(titles=titles)

                builder.button(text="⏪Назад", callback_data="add_file_menu")

                builder.adjust(1)

                await message.answer(text="Вы отправили название страницы.\n\nВыберете подходящий вариант из списка",
                                     reply_markup=builder.as_markup())
    except Exception as e:
        logger.error(f"Критическая ошибка в wiki_search: {e}", exc_info=True)
        await message.answer("❌ Ошибка сервера")


@router.callback_query(F.data.startswith("text_wiki"))
async def process_title(callback: CallbackQuery, state: FSMContext):
    logger.info(f"processing title: {callback.data}")
    try:
        index = int(callback.data.split('_')[-1])
        data = await state.get_data()
        logger.info(f"state data: {data}")
        title = data["titles"][index]
        await state.clear()
        await callback.message.edit_text(f"Отлично! Вы выбрали {title}\n\nНачинаю обработку...")

        await upload_text_wiki(callback.from_user.id, title)
        await callback.message.edit_text(
            text="Текст загружен!",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="add_file_menu")]])
        )
    except Exception as e:
        logger.error(f"Критическая ошибка в process_title: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка сервера")

@router.message(StateFilter(FileStates.waiting_file))
async def wrong_message(message: types.Message):
    try:
        await message.answer(
            text="Отправьте файл.\nПоддерживаемые форматы: txt, pdf, docx",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="upload_file_quit")]])
        )
    except Exception as e:
        logger.error(f"Ошибка при ответе на неправильное сообщение: {e}", exc_info=True)

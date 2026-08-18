from aiogram import Router, types, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pathlib import Path

from clients.openrouter_client import OpenRouterClient
from clients.sber_client import SberClient
from data_parser import FileManager
from config import settings
import logging

logger = logging.getLogger(__name__)
router = Router()
titles_limit = settings.titles_limit
cache = dict()

class QueryStates(StatesGroup):
    waiting_one_book_query = State()


async def show_query_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()

    builder.button(text="Личная библиотека", callback_data=f"pers_lib")
    builder.button(text="Общая библиотека", callback_data="gl_lib")
    builder.button(text="⏪Назад", callback_data="main_menu")

    builder.adjust(1)

    await callback.message.edit_text(
        text="Выберите из какой библиотеки Вы хотите выбрать произведение.",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "query_menu")
async def query_menu(callback: types.CallbackQuery):
    await show_query_menu(callback)


async def show_personally_library(callback: types.CallbackQuery, state: FSMContext, cur_page: int):
    user_id = callback.from_user.id
    titles = FileManager.get_titles_from_user(user_id)
    builder = InlineKeyboardBuilder()

    if len(titles) == 0:
        await callback.message.edit_text(
            text="Вы еще не добавили ни одного произведения.\nМожете поискать нужное в общей библиотеке или добавить нужное самостоятельно",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="query_menu")]])
        )
        await callback.answer()
        return

    s = cur_page * titles_limit
    e = min(s + titles_limit, len(titles))

    pages_count = (len(titles) + titles_limit - 1) // titles_limit
    titles = titles[s:e]

    for i, title in enumerate(titles):
        builder.button(text=title, callback_data=f"pers_select_{i}")

    await state.update_data(titles=titles)

    view = [1] * len(titles)
    view.append(0)
    if cur_page > 0:
        builder.button(text="◀️Предыдущая страница", callback_data=f"pers_lib:{max(0, cur_page - 1)}")
        view[-1] += 1
    if cur_page + 1 < pages_count:
        builder.button(text="Следующая страница ⏭️", callback_data=f"pers_lib:{min(pages_count - 1, cur_page + 1)}")
        view[-1] += 1
    if view[-1] == 0:
        view.pop()

    builder.button(text="⏪Назад", callback_data="query_menu")
    view.append(1)
    builder.adjust(*view)

    await callback.message.edit_text(
        text=f"Это ваша личная библиотека\nВыберете интересующее произведение\n\n------Страница {cur_page + 1}/{pages_count}------",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pers_lib"))
async def personal_library(callback: types.CallbackQuery, state: FSMContext):
    text = callback.data
    page = None
    if len(text.split(":")) > 1:
        page = int(text.split(":")[1])
    else:
        page = 0
    await show_personally_library(callback, state, page)


@router.callback_query(F.data.startswith("pers_select_"))
async def pre_give_answer(callback: types.CallbackQuery, state: FSMContext):
    try:
        index = int(callback.data.split("_")[-1])
        data = await state.get_data()
        titles = data.get('titles', [])
        if index >= len(titles):
            await callback.answer("Книга не найдена. Выберите её заново", show_alert=True)
            return
        title = titles[index]
        user_dir = Path(settings.get_user_books_dir(callback.from_user.id))
        file_path = user_dir / title
        await state.update_data(file_path=file_path)
        await state.update_data(current_title=title)
        cache[callback.from_user.id] = []
        await state.set_state(QueryStates.waiting_one_book_query)

        await callback.message.edit_text(
            "Отлично! Напишите, что хотите найти.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="OBQ_reject")]])
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка при выборе книги: {e}", exc_info=True)
        await callback.answer("Произошла ошибка. Попробуйте ещё раз", show_alert=True)


@router.message(StateFilter(QueryStates.waiting_one_book_query), F.text)
async def give_answer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        data = await state.get_data()
        title = data.get('current_title')
        if not title:
            await message.answer("❌ Не удалось найти выбранную книгу. Выберите её заново.")
            await state.clear()
            return

        owner_id = data.get('owner_user_id', user_id)

        if user_id not in cache:
            cache[user_id] = []
        cache[user_id].append(message.text)

        async with SberClient(owner_id) as client:
            response = await client.query(cache[user_id], title)
        cache[user_id].append(response)

        await message.answer(
            text=response,
        )
        await message.answer(
            text="Вот что получилось",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="OBQ_reject")]])
        )
    except Exception as e:
        logger.error(f"Ошибка при формировании ответа: {e}", exc_info=True)
        cache[user_id] = []
        await state.clear()
        await message.answer("❌ Произошла ошибка при формировании ответа. Попробуйте ещё раз.")


@router.callback_query(F.data == "OBQ_reject")
async def obq_reject(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    cache[user_id] = []
    await show_personally_library(callback, state, 0)


async def show_global_library(callback: types.CallbackQuery, state: FSMContext, cur_page: int):
    titles = FileManager.get_all_titles()
    builder = InlineKeyboardBuilder()

    if len(titles) == 0:
        await callback.message.edit_text(
            text="В библиотеке нет произведений.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="query_menu")]])
        )
        await callback.answer()
        return

    s = cur_page * titles_limit
    e = min(s + titles_limit, len(titles))

    pages_count = (len(titles) + titles_limit - 1) // titles_limit
    titles = titles[s:e]

    for i, (user_id, title) in enumerate(titles):
        builder.button(text=title, callback_data=f"gl_select_{i}")

    await state.update_data(titles=titles)

    view = [1] * len(titles)
    view.append(0)
    if cur_page > 0:
        builder.button(text="◀️Предыдущая страница", callback_data=f"gl_lib:{max(0, cur_page - 1)}")
        view[-1] += 1
    if cur_page + 1 < pages_count:
        builder.button(text="Следующая страница ⏭️", callback_data=f"gl_lib:{min(pages_count - 1, cur_page + 1)}")
        view[-1] += 1
    if view[-1] == 0:
        view.pop()

    builder.button(text="⏪Назад", callback_data="query_menu")
    view.append(1)
    builder.adjust(*view)

    await callback.message.edit_text(
        text=f"Общая библиотека\nВыберете произведение\n\n------Страница {cur_page + 1}/{pages_count}------",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gl_lib"))
async def global_library(callback: types.CallbackQuery, state: FSMContext):
    text = callback.data
    if len(text.split(':')) > 1:
        page = int(text.split(':')[1])
    else:
        page = 0
    await show_global_library(callback, state, page)


@router.callback_query(F.data.startswith("gl_select_"))
async def pre_give_global_answer(callback: types.CallbackQuery, state: FSMContext):
    try:
        index = int(callback.data.split("_")[-1])
        data = await state.get_data()
        titles = data.get('titles', [])
        if index >= len(titles):
            await callback.answer("Книга не найдена. Выберите её заново", show_alert=True)
            return
        owner_user_id, title = titles[index]

        user_dir = Path(settings.get_user_books_dir(owner_user_id))
        file_path = user_dir / title
        await state.update_data(file_path=file_path)
        await state.update_data(current_title=title)
        await state.update_data(owner_user_id=owner_user_id)
        cache[callback.from_user.id] = []
        await state.set_state(QueryStates.waiting_one_book_query)

        await callback.message.edit_text(
            "Отлично! Напишите, что хотите найти.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⏪Назад", callback_data="OBQ_reject")]])
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка при выборе книги: {e}", exc_info=True)
        await callback.answer("Произошла ошибка. Попробуйте ещё раз", show_alert=True)

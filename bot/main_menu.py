from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import logging

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("start"))
async def start(message: types.Message):
    user = message.from_user
    await message.answer(
        text=f"Привет, {user.first_name}! Тут Вы можете быстро узнать все, что Вы хотите из ваших любимых произведений.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Главное меню", callback_data="main_menu")]])
    )


async def show_main_menu(callback: types.CallbackQuery | types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="Задать вопрос", callback_data="query_menu")
    builder.button(text="Добавить произведения", callback_data="add_file_menu")
    builder.adjust(1)

    if isinstance(callback, types.CallbackQuery):
        await callback.message.edit_text(
            text="Вы в главном меню\n\nВыберете действие",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
    else:
        await callback.answer(
            text="Вы в главном меню\n\nВыберете действие",
            reply_markup=builder.as_markup()
        )


@router.callback_query((F.data == "main_menu"))
async def main_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_main_menu(callback)


@router.message(Command("menu"))
async def menu(message: types.Message):
    await show_main_menu(message)


@router.message(Command("info"))
async def info(message: types.Message):
    await message.answer(
        text="Этот бот помогает отвечать на вопросы по вашим книгам.\n"
        "Загрузите произведение (txt, pdf, docx) или найдите его в Викитеке, "
        "а затем задайте вопрос — ответ будет основан на содержимом книги."
    )

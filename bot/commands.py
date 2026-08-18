from aiogram import Bot
from aiogram.types import BotCommand

async def set_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="info", description="Информация о боте"),
        BotCommand(command="menu", description="Главное меню")
    ]

    await bot.set_my_commands(commands)

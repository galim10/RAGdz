from config import settings
from .commands import set_commands
from .main_menu import router as menu_router
from .queries_menu import router as queries_router
from . import add_file_menu
from aiogram import Bot, Dispatcher
import logging

logger = logging.getLogger(__name__)
bot: Bot | None = None


async def start_bot() -> None:
    global bot
    
    try:
        logger.info("Инициализация бота...")
        bot = Bot(settings.bot_token)

        dp = Dispatcher()

        add_file_menu.set_bot_instance(bot)

        await set_commands(bot)
        
        dp.include_router(menu_router)
        dp.include_router(queries_router)
        dp.include_router(add_file_menu.router)

        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        raise
    finally:
        if bot:
            await bot.session.close()

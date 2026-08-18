import asyncio
import logging
from logging_config import setup_logging
from config import settings
from bot.__init__ import start_bot

logger = logging.getLogger(__name__)

async def main():
    try:
        setup_logging()
        logger.info("Запуск приложения...")
        await start_bot()
    except Exception as e:
        logger.error(f"Ошибка при запуске приложения: {e}", exc_info=True)
        raise

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение завершено пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)

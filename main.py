import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

import config
import database
from bot_handlers import router
from scheduler import run_scheduler_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("GrandMobileAIBot")

async def main():
    logger.info("Bot ma'lumotlar bazasi ishga tushirilmoqda...")
    await database.init_db()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher()
    dp.include_router(router)

    # Bot ma'lumotlarini tekshirish
    me = await bot.get_me()
    logger.info(f"Bot muvaffaqiyatli ishga tushdi: @{me.username} ({me.first_name})")
    logger.info(f"Kanal: {config.CHANNEL_ID}")

    # Fon rejimida yangiliklar monitoringini ishga tushirish
    def on_scheduler_done(t: asyncio.Task):
        try:
            t.result()
        except asyncio.CancelledError:
            pass
        except Exception as err:
            logger.critical(f"Scheduler vazifasi kutilmaganda to'xtadi: {err}", exc_info=True)

    scheduler_task = asyncio.create_task(run_scheduler_worker(bot))
    scheduler_task.add_done_callback(on_scheduler_done)

    try:
        # Pollingni boshlash (VPS tarmog'i uzilib qolmasligi uchun polling_timeout=30)
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"], polling_timeout=30)
    finally:
        scheduler_task.cancel()
        await bot.session.close()
        logger.info("Bot to'xtatildi.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Dastur to'xtatildi.")

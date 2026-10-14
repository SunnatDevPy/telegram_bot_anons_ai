import sys

# Windows konsolida UTF-8 va emojilar to'g'ri ishlashi uchun
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import asyncio
import logging
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

    # Fon rejimida yangiliklar monitoringini boshqarish va auto-restart
    current_scheduler_task: list[asyncio.Task] = []

    def start_scheduler():
        task = asyncio.create_task(run_scheduler_worker(bot))
        task.add_done_callback(on_scheduler_done)
        current_scheduler_task.clear()
        current_scheduler_task.append(task)
        return task

    def on_scheduler_done(t: asyncio.Task):
        try:
            t.result()
        except asyncio.CancelledError:
            pass
        except Exception as err:
            try:
                import html
                from diagnostics import send_admin_alert
                asyncio.create_task(send_admin_alert(
                    bot=bot,
                    message=(
                        f"🚨 <b>Kritik Xatolik:</b> Scheduler kuzatuvchi vazifasi kutilmaganda to'xtadi:\n"
                        f"<code>{html.escape(str(err))}</code>\n\n"
                        f"🔄 <i>Tizim 5 soniyadan so'ng monitoringni avtomatik qayta ishga tushiradi...</i>"
                    ),
                    alert_type="scheduler_died",
                    cooldown_minutes=5
                ))
            except Exception:
                pass

            try:
                logger.critical(f"Scheduler vazifasi kutilmaganda to'xtadi: {err}", exc_info=True)
            except Exception:
                pass

            # Avtomatik ravishda 5 soniyada qayta ishga tushirish (Watchdog)
            async def auto_recover():
                await asyncio.sleep(5)
                logger.info("Scheduler monitoring vazifasi avtomatik qayta ishga tushirilmoqda...")
                start_scheduler()

            asyncio.create_task(auto_recover())

    start_scheduler()

    try:
        # Pollingni boshlash (VPS tarmog'i uzilib qolmasligi uchun polling_timeout=30)
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"], polling_timeout=30)
    finally:
        for t in current_scheduler_task:
            if not t.done():
                t.cancel()
        await bot.session.close()
        logger.info("Bot to'xtatildi.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Dastur to'xtatildi.")

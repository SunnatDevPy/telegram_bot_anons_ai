import asyncio
from datetime import datetime, timezone, timedelta, time
from aiogram import Bot
from aiogram.types import FSInputFile
import config
import database
from rss_parser import scan_all_feeds
from ai_formatter import format_news, safe_telegram_caption, generate_daily_digest
from bot_handlers import get_moderation_keyboard
from media_helper import send_media_robust

def is_night_mode_active() -> bool:
    """Toshkent vaqti bo'yicha tungi tinchlik rejimi faolmi (23:30 dan 07:30 gacha)"""
    now_t = datetime.now(config.TASHKENT_TZ)
    h, m = now_t.hour, now_t.minute
    if (h == 23 and m >= 30) or (0 <= h < 7) or (h == 7 and m < 30):
        return True
    return False

def get_next_morning_time(delay_index: int = 0) -> str:
    """Ertalabki soat 08:00 ga rejalashtirish vaqtini hisoblash"""
    now_t = datetime.now(config.TASHKENT_TZ)
    h, m = now_t.hour, now_t.minute
    # Agar yarim kechadan keyin bo'lsa (00:00 - 07:30), o'sha kungi 08:00, aks holda ertangi 08:00
    target_date = now_t.date() if (h < 7 or (h == 7 and m < 30)) else (now_t + timedelta(days=1)).date()
    morning_base = datetime.combine(target_date, time(8, 0), tzinfo=config.TASHKENT_TZ)
    morning_time = morning_base + timedelta(minutes=delay_index * 20)
    return morning_time.astimezone(timezone.utc).isoformat()

async def send_to_channel(bot: Bot, image_url: str, caption: str):
    """Kanalga rasm yoki matnli xabarni chop etish"""
    full_photo_caption = safe_telegram_caption(caption, config.CHANNEL_FOOTER, max_len=1024)
    if image_url:
        sent = await send_media_robust(
            bot=bot,
            chat_id=config.CHANNEL_ID,
            media_url=image_url,
            caption=full_photo_caption
        )
        if sent:
            return True

    text_msg = safe_telegram_caption(caption, config.CHANNEL_FOOTER, max_len=4096)
    await bot.send_message(
        chat_id=config.CHANNEL_ID,
        text=text_msg,
        parse_mode="HTML"
    )
    return True

async def send_draft_to_admin(bot: Bot, admin_id: int, post_id: int, image_url: str, caption: str, original_url: str, source_name: str):
    """Adminga tasdiqlash uchun postni yuborish (to'g'ridan-to'g'ri kanal ko'rinishida)"""
    kb = get_moderation_keyboard(post_id)
    safe_cap = safe_telegram_caption(caption, max_len=1024)

    if image_url:
        sent = await send_media_robust(
            bot=bot,
            chat_id=admin_id,
            media_url=image_url,
            caption=safe_cap,
            reply_markup=kb
        )
        if sent:
            return

    await bot.send_message(
        chat_id=admin_id,
        text=safe_telegram_caption(caption, max_len=4096),
        parse_mode="HTML",
        reply_markup=kb
    )

async def check_due_scheduled_posts(bot: Bot):
    """Rejalashtirilgan vaqti yetib kelgan postlarni kanalga chiqarish"""
    # Tungi rejimda rejalashtirilgan postlar ham chiqarilmaydi
    if is_night_mode_active():
        return

    due_posts = await database.get_due_scheduled_posts()
    for post in due_posts:
        now_str = datetime.now(timezone.utc).isoformat()
        # Avval statusni published qilamiz, shunda reja qayta ishlamaydi
        await database.set_post_status(post["id"], "published", published_at=now_str)
        try:
            await send_to_channel(bot, post["image_url"], post["uz_caption"])
            print(f"[Avto-Reja] Post #{post['id']} kanalga muvaffaqiyatli chiqarildi.")
        except Exception as e:
            print(f"[Avto-Reja Xatolik] Post #{post['id']}: {e}")

async def check_daily_digest(bot: Bot):
    """Har kuni soat 21:00 da o'sha kungi eng muhim 5 ta xabar dayjestini chiqarish"""
    now_t = datetime.now(config.TASHKENT_TZ)
    if now_t.hour != config.DAILY_DIGEST_HOUR:
        return

    today_str = now_t.strftime("%Y-%m-%d")
    last_sent = await database.get_setting("last_daily_digest_date", "")
    if last_sent == today_str:
        return

    today_posts = await database.get_today_published_posts(today_str, limit=5)
    if len(today_posts) < 2:
        return

    digest_text = await generate_daily_digest(today_posts)
    if not digest_text:
        return

    await database.set_setting("last_daily_digest_date", today_str)

    # Logo bilan chiqarish
    if config.LOGO_PATH.exists():
        try:
            await bot.send_photo(
                chat_id=config.CHANNEL_ID,
                photo=FSInputFile(str(config.LOGO_PATH)),
                caption=safe_telegram_caption(digest_text, max_len=1024),
                parse_mode="HTML"
            )
            print("[Kechki Dayjest] Kanalga logo bilan chiqarildi! 📊")
            return
        except Exception as e:
            print(f"[Dayjest Rasm Xatolik]: {e}")

    await bot.send_message(
        chat_id=config.CHANNEL_ID,
        text=safe_telegram_caption(digest_text, max_len=4096),
        parse_mode="HTML"
    )
    print("[Kechki Dayjest] Kanalga matn sifatida chiqarildi! 📊")

async def process_news_cycle(bot: Bot) -> int:
    """Yangiliklarni izlash, qayta ishlash va rejimga qarab yo'naltirish"""
    mode = await database.get_setting("mode", "moderation")
    raw_admin = await database.get_setting("admin_id")
    admin_id = int(raw_admin) if raw_admin and raw_admin.isdigit() else None

    # 1. Rejalashtirilgan postlarni tekshirish
    await check_due_scheduled_posts(bot)

    # 2. Yangi RSS xabarlarini skanerlash
    new_items = await scan_all_feeds(limit_per_feed=2)
    processed_count = 0
    in_night = is_night_mode_active()

    for item in new_items:
        uz_caption = await format_news(
            title=item["title"],
            summary=item["summary"],
            source_name=item["source_name"],
            category=item.get("category", "IT & AI"),
            original_url=item["url"]
        )

        now_str = datetime.now(timezone.utc).isoformat()

        if mode == "auto":
            # Tungi rejimda barcha yangi xabarlar ertalabki 08:00 ga navbatga qo'yiladi
            if in_night:
                morning_sched = get_next_morning_time(processed_count)
                await database.create_post(
                    url=item["url"],
                    title=item["title"],
                    original_summary=item["summary"],
                    uz_caption=uz_caption,
                    image_url=item["image_url"],
                    source_name=item["source_name"],
                    status="scheduled",
                    scheduled_at=morning_sched
                )
                processed_count += 1
                print(f"[Tungi Rejim] '{item['title']}' ertalab 08:00 ga navbatga qo'yildi.")
            elif processed_count == 0:
                # Kunduzgi rejim: Birinchi xabar darhol kanalga
                post_id = await database.create_post(
                    url=item["url"],
                    title=item["title"],
                    original_summary=item["summary"],
                    uz_caption=uz_caption,
                    image_url=item["image_url"],
                    source_name=item["source_name"],
                    status="published",
                    published_at=now_str
                )
                try:
                    await send_to_channel(bot, item["image_url"], uz_caption)
                    processed_count += 1
                    print(f"[Avto-Chiqarish] '{item['title']}' kanalga joylandi.")
                except Exception as e:
                    print(f"[Avto-Chiqarish Xatolik]: {e}")
            else:
                # Keyingi xabarlar +25 daqiqaga navbatga qo'yiladi
                delay_mins = processed_count * 25
                sched_time = (datetime.now(timezone.utc) + timedelta(minutes=delay_mins)).isoformat()
                await database.create_post(
                    url=item["url"],
                    title=item["title"],
                    original_summary=item["summary"],
                    uz_caption=uz_caption,
                    image_url=item["image_url"],
                    source_name=item["source_name"],
                    status="scheduled",
                    scheduled_at=sched_time
                )
                processed_count += 1
                print(f"[Avto-Navbat] '{item['title']}' +{delay_mins} daqiqaga rejalashtirildi.")
        else:
            # MODERATSIYA REJIMI
            post_id = await database.create_post(
                url=item["url"],
                title=item["title"],
                original_summary=item["summary"],
                uz_caption=uz_caption,
                image_url=item["image_url"],
                source_name=item["source_name"],
                status="draft"
            )
            processed_count += 1

            if admin_id:
                try:
                    await send_draft_to_admin(
                        bot=bot,
                        admin_id=admin_id,
                        post_id=post_id,
                        image_url=item["image_url"],
                        caption=uz_caption,
                        original_url=item["url"],
                        source_name=item["source_name"]
                    )
                except Exception as e:
                    print(f"[Admin Yuborish Xatolik]: {e}")

        await asyncio.sleep(3)

    return processed_count

last_cleanup_date = None

async def run_scheduler_worker(bot: Bot):
    """Fon rejimida doimiy ishlovchi sikl"""
    global last_cleanup_date
    print("⏳ Fon yangiliklar kuzatuvchisi ishga tushdi.")
    while True:
        try:
            await process_news_cycle(bot)
        except Exception as e:
            print(f"[Scheduler Sikl Xatolik]: {e}")

        # Kuniga bir marta eski arxivni tozalash (disk va baza to'lib ketmasligi uchun)
        now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if last_cleanup_date != now_date:
            last_cleanup_date = now_date
            await database.cleanup_old_posts(days=45)

        raw_interval = await database.get_setting("interval_minutes", "25")
        interval_minutes = int(raw_interval) if raw_interval and raw_interval.isdigit() else 25

        # 30 soniyalik qadamlar bilan uxlash va rejalarni tekshirish
        wait_seconds = interval_minutes * 60
        steps = max(1, wait_seconds // 30)
        for _ in range(steps):
            await asyncio.sleep(30)
            await check_due_scheduled_posts(bot)
            await check_daily_digest(bot)

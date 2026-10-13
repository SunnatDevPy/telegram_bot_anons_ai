import asyncio
from datetime import datetime, timezone
from aiogram import Bot
import config
import database
from rss_parser import scan_all_feeds
from ai_formatter import format_news
from bot_handlers import get_moderation_keyboard

async def send_to_channel(bot: Bot, image_url: str, caption: str):
    """Kanalga rasm yoki matnli xabarni chop etish"""
    full_caption = caption + config.CHANNEL_FOOTER
    if image_url:
        try:
            await bot.send_photo(
                chat_id=config.CHANNEL_ID,
                photo=image_url,
                caption=full_caption[:1024],
                parse_mode="HTML"
            )
            return True
        except Exception as e:
            print(f"[Kanal Rasm Xatolik]: {e}. Matnning o'zi yuborilmoqda...")

    await bot.send_message(
        chat_id=config.CHANNEL_ID,
        text=full_caption[:4096],
        parse_mode="HTML"
    )
    return True

async def send_draft_to_admin(bot: Bot, admin_id: int, post_id: int, image_url: str, caption: str, original_url: str, source_name: str):
    """Adminga tasdiqlash uchun postni yuborish (to'g'ridan-to'g'ri kanal ko'rinishida)"""
    kb = get_moderation_keyboard(post_id)

    if image_url:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=image_url,
                caption=caption[:1024],
                parse_mode="HTML",
                reply_markup=kb
            )
            return
        except Exception as e:
            print(f"[Admin Rasm Xatolik]: {e}")

    await bot.send_message(
        chat_id=admin_id,
        text=caption[:4096],
        parse_mode="HTML",
        reply_markup=kb
    )

async def check_due_scheduled_posts(bot: Bot):
    """Rejalashtirilgan vaqti yetib kelgan postlarni kanalga chiqarish"""
    due_posts = await database.get_due_scheduled_posts()
    for post in due_posts:
        now_str = datetime.now(timezone.utc).isoformat()
        try:
            await send_to_channel(bot, post["image_url"], post["uz_caption"])
            await database.set_post_status(post["id"], "published", published_at=now_str)
            print(f"[Avto-Reja] Post #{post['id']} kanalga muvaffaqiyatli chiqarildi.")
        except Exception as e:
            print(f"[Avto-Reja Xatolik] Post #{post['id']}: {e}")

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

    for item in new_items:
        # Gemini orqali o'zbekcha professional post yaratish
        uz_caption = await format_news(
            title=item["title"],
            summary=item["summary"],
            source_name=item["source_name"],
            category=item.get("category", "IT & AI"),
            original_url=item["url"]
        )

        now_str = datetime.now(timezone.utc).isoformat()

        if mode == "auto":
            # AVTOMATIK REJIM: Birinchi xabar hoziroq, keyingilari esa obunachilarni bezovta qilmaslik uchun 25 daqiqadan navbatga qo'yiladi
            if processed_count == 0:
                try:
                    await send_to_channel(bot, item["image_url"], uz_caption)
                    await database.create_post(
                        url=item["url"],
                        title=item["title"],
                        original_summary=item["summary"],
                        uz_caption=uz_caption,
                        image_url=item["image_url"],
                        source_name=item["source_name"],
                        status="published",
                        published_at=now_str
                    )
                    processed_count += 1
                    print(f"[Avto-Chiqarish] '{item['title']}' kanalga joylandi.")
                except Exception as e:
                    print(f"[Avto-Chiqarish Xatolik]: {e}")
            else:
                from datetime import timedelta
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
            # MODERATSIYA REJIMI: Adminga qoralama ko'rinishida yuborish
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

        # Telegram chekloviga tushmaslik uchun qisqa kutish
        await asyncio.sleep(3)

    return processed_count

async def run_scheduler_worker(bot: Bot):
    """Fon rejimida doimiy ishlovchi sikl"""
    print("⏳ Fon yangiliklar kuzatuvchisi ishga tushdi.")
    while True:
        try:
            await process_news_cycle(bot)
        except Exception as e:
            print(f"[Scheduler Sikl Xatolik]: {e}")

        # Dinamik intervalni bazadan o'qish
        raw_interval = await database.get_setting("interval_minutes", "25")
        interval_minutes = int(raw_interval) if raw_interval and raw_interval.isdigit() else 25

        # 30 soniyalik qadamlar bilan uxlash (rejalashtirilgan postlarni o'z vaqtida chiqarish uchun)
        wait_seconds = interval_minutes * 60
        steps = max(1, wait_seconds // 30)
        for _ in range(steps):
            await asyncio.sleep(30)
            await check_due_scheduled_posts(bot)

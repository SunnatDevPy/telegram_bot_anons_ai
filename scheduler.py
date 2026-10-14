import html
import asyncio
from datetime import datetime, timezone, timedelta, time
from typing import Optional
from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardMarkup
import config
import database
import ai_formatter
from rss_parser import scan_all_feeds
from ai_formatter import (
    format_news, safe_telegram_caption, generate_daily_digest,
    attach_source_link
)
from bot_handlers import get_moderation_keyboard, get_channel_post_keyboard
from media_helper import send_media_robust
from diagnostics import send_admin_alert
from backup_service import check_daily_backup

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
    spacing = getattr(config, "POST_SPACING_MINUTES", 10)
    morning_time = morning_base + timedelta(minutes=delay_index * spacing)
    return morning_time.astimezone(timezone.utc).isoformat()

async def send_to_channel(
    bot: Bot,
    image_url: str,
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None
):
    """Kanalga rasm yoki matnli xabarni inline tugmalar bilan birga chop etish"""
    try:
        full_photo_caption = safe_telegram_caption(caption, config.CHANNEL_FOOTER, max_len=1024)
        if image_url:
            sent = await send_media_robust(
                bot=bot,
                chat_id=config.CHANNEL_ID,
                media_url=image_url,
                caption=full_photo_caption,
                reply_markup=reply_markup
            )
            if sent:
                return True

        text_msg = safe_telegram_caption(caption, config.CHANNEL_FOOTER, max_len=4096)
        await bot.send_message(
            chat_id=config.CHANNEL_ID,
            text=text_msg,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return True
    except Exception as e:
        await send_admin_alert(
            bot=bot,
            message=(
                f"🚨 <b>Kanalga post chiqarishda xatolik yuz berdi:</b>\n"
                f"<code>{html.escape(str(e))}</code>\n\n"
                f"Iltimos, botning <code>{config.CHANNEL_ID}</code> kanalida administrator ekanligini va post chiqarish huquqini tekshiring."
            ),
            alert_type="channel_post_failed",
            cooldown_minutes=15
        )
        raise e

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
    if not due_posts:
        return

    spacing_mins = getattr(config, "POST_SPACING_MINUTES", 10)

    # Oxirgi chiqqan postdan beri kamida 10 daqiqa o'tganligini kafolatlash
    last_pub = await database.get_last_published_post()
    if last_pub and last_pub.get("published_at"):
        try:
            lp_dt = datetime.fromisoformat(last_pub["published_at"])
            elapsed_mins = (datetime.now(timezone.utc) - lp_dt).total_seconds() / 60
            if elapsed_mins < spacing_mins:
                return  # Hali oraliq vaqt to'lmadi, keyingi tekshiruvda chiqariladi
        except Exception:
            pass

    # Ketma-ket spam bo'lmasligi uchun faqat 1 ta eng birinchi rejadagi post chiqariladi
    post = due_posts[0]
    now_str = datetime.now(timezone.utc).isoformat()
    await database.set_post_status(post["id"], "published", published_at=now_str)
    try:
        channel_kb = await get_channel_post_keyboard(post_id=post["id"], original_url=post.get("url"))
        cap_with_link = attach_source_link(post["uz_caption"], post.get("source_name", "IT"), post.get("url", ""))
        await send_to_channel(bot, post["image_url"], cap_with_link, reply_markup=channel_kb)
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

    # Navbatni tekshirish: Agar navbatda allaqachon yetarlicha post bo'lsa, AI kvotasini behuda sarflamaslik
    current_queue = await database.get_scheduled_count()
    max_queue = getattr(config, "MAX_SCHEDULED_QUEUE", 6)
    if mode == "auto" and current_queue >= max_queue:
        print(f"[Monitoring] Navbat to'la ({current_queue}/{max_queue} ta post). Yangiliklar eskirib ketmasligi va AI kvotasi tejalishi uchun navbat bo'shashi kutilmoqda.")
        return 0

    # 2. Yangi RSS xabarlarini skanerlash
    new_items = await scan_all_feeds(limit_per_feed=2)

    # AQLIY FILTRLASH: AI kvotasini tejash uchun faqat eng yuqori dolzarblikdagi (priority) sara yangiliklarni saralash
    # Bitta siklda 1 yoki uzog'i bilan 2 ta eng sara xabar olinadi
    high_priority = [it for it in new_items if it.get("priority", 0) >= 25]
    if high_priority:
        selected_items = high_priority[:2]
    else:
        selected_items = new_items[:1] if new_items else []

    processed_count = 0
    in_night = is_night_mode_active()

    for item in selected_items:
        uz_caption = await format_news(
            title=item["title"],
            summary=item["summary"],
            source_name=item["source_name"],
            category=item.get("category", "IT & AI"),
            original_url=item["url"]
        )

        if getattr(ai_formatter, "LAST_FORMATTER_USED", "") == "fallback":
            await send_admin_alert(
                bot=bot,
                message=(
                    "⚠️ <b>Google Gemini AI ogohlantirishi:</b>\n"
                    "Barcha Gemini API kalitlarida kvota tugadi (429) yoki server javob bermadi. "
                    "Yangiliklar to'xtab qolmasligi uchun avtomatik ravishda <i>Google Translate (Fallback)</i> orqali tayyorlanmoqda."
                ),
                alert_type="gemini_fallback",
                cooldown_minutes=60
            )

        now_str = datetime.now(timezone.utc).isoformat()

        if mode == "auto":
            spacing_mins = getattr(config, "POST_SPACING_MINUTES", 10)
            now_utc = datetime.now(timezone.utc)

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
            else:
                last_pub = await database.get_last_published_post()
                last_sched_str = await database.get_last_scheduled_time()

                # Oxirgi e'lon qilingan postdan beri necha daqiqa o'tgan?
                elapsed_mins = 999.0
                if last_pub and last_pub.get("published_at"):
                    try:
                        lp_dt = datetime.fromisoformat(last_pub["published_at"])
                        elapsed_mins = (now_utc - lp_dt).total_seconds() / 60
                    except Exception:
                        pass

                # Agar oxirgi postga kamida 10 daqiqa bo'lgan bo'lsa va navbatda reja bo'lmasa: darhol 1-post chiqadi
                can_publish_now = (processed_count == 0 and elapsed_mins >= spacing_mins and not last_sched_str)

                if can_publish_now:
                    now_str = now_utc.isoformat()
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
                        channel_kb = await get_channel_post_keyboard(post_id=post_id, original_url=item["url"])
                        await send_to_channel(bot, item["image_url"], uz_caption, reply_markup=channel_kb)
                        processed_count += 1
                        print(f"[Avto-Chiqarish] '{item['title']}' kanalga joylandi.")
                    except Exception as e:
                        print(f"[Avto-Chiqarish Xatolik]: {e}")
                else:
                    # Navbat to'lib ketishi va yangiliklar eskirib ketmasligi uchun tekshiruv
                    current_queue = await database.get_scheduled_count()
                    max_queue = getattr(config, "MAX_SCHEDULED_QUEUE", 6)
                    if current_queue >= max_queue:
                        print(f"[Avto-Navbat To'lgan] Hozirda {current_queue} ta post navbatda. Yangilik eskirib ketmasligi uchun navbatga qo'shilmadi: '{item['title']}'")
                        continue

                    # Aks holda kamida 10 daqiqa oraliq bilan navbatga (scheduled) qo'yiladi
                    anchor_time = now_utc
                    if last_sched_str:
                        try:
                            ls_dt = datetime.fromisoformat(last_sched_str)
                            if ls_dt > anchor_time:
                                anchor_time = ls_dt
                        except Exception:
                            pass
                    elif last_pub and last_pub.get("published_at") and elapsed_mins < spacing_mins:
                        try:
                            anchor_time = datetime.fromisoformat(last_pub["published_at"])
                        except Exception:
                            pass

                    # Rejalashtirilgan vaqtni hisoblash (har bir post orasida kamida 10 daqiqa)
                    delay_step = processed_count + (0 if can_publish_now else 1)
                    sched_dt = max(now_utc + timedelta(minutes=1), anchor_time + timedelta(minutes=delay_step * spacing_mins))
                    sched_time = sched_dt.isoformat()

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
                    diff_mins = max(1, int((sched_dt - now_utc).total_seconds() / 60))
                    print(f"[Avto-Navbat] '{item['title']}' +{diff_mins} daqiqaga rejalashtirildi (10 min oraliq).")
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
            try:
                await send_admin_alert(
                    bot=bot,
                    message=f"⚠️ <b>Monitoring siklida kutilmagan xatolik:</b>\n\n<code>{html.escape(str(e)[:250])}</code>",
                    alert_type="scheduler_cycle_error",
                    cooldown_minutes=30
                )
            except Exception:
                pass

        try:
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
                try:
                    await check_due_scheduled_posts(bot)
                    await check_daily_digest(bot)
                    await check_daily_backup(bot)
                    # Eskirgan (4 soatdan ortiqqa cho'zilib ketgan) navbatlarni avtomatik tozalash
                    await database.cancel_stale_scheduled_posts(max_future_hours=4)
                except Exception as sub_e:
                    print(f"[Scheduler Oraliq Tekshiruv Xatolik]: {sub_e}")
        except Exception as outer_e:
            print(f"[Scheduler Tashqi Sikl Xatolik]: {outer_e}")
            await asyncio.sleep(30)

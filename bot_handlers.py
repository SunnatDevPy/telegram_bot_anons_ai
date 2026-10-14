import asyncio
import html
import re
from typing import Optional, Union, Dict, Any, List
from datetime import datetime, timedelta, timezone
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

import config
import database
from ai_formatter import (
    regenerate_news, safe_telegram_caption, answer_ai_assistant_question,
    attach_source_link
)
from media_helper import send_media_robust, send_user_preview_media
from diagnostics import get_system_health, format_health_html

router = Router()

# Reklama yuborish holatlarini saqlash
admin_broadcast_state = {}    # {admin_id: "waiting_message"}
admin_broadcast_pending = {}  # {admin_id: {"chat_id": int, "message_id": int}}

# Admin qoralama postni tahrirlash holati: {admin_id: {"post_id": int, "origin": str, "page": int}}
admin_edit_draft_state = {}

# Foydalanuvchilar qidiruv va AI maslahatchi holatlari
user_search_state = {}        # {user_id: True}
user_ai_state = {}            # {user_id: True}

# Admin Premium Emoji sozlash holati: {admin_id: "lightning"}
admin_emoji_edit_state = {}

EMOJI_METADATA = {
    "lightning": {"name": "Sarlavha (Chaqmoq)", "default": "⚡️"},
    "pin": {"name": "Mohiyati", "default": "📌"},
    "chart": {"name": "Tafsilotlar", "default": "📊"},
    "bulb": {"name": "Xulosa", "default": "💡"},
    "antenna": {"name": "Manba", "default": "📡"},
    "tag": {"name": "Hashtag", "default": "🏷"},
}

def render_emojis_panel_text() -> str:
    """Premium Emojilar boshqaruv paneli matni"""
    lines = [
        "✨ <b>Telegram Premium Custom Emoji Boshqaruvi</b>\n",
        "Kanal postlaridagi bo'limlar uchun o'zingizning animatsiyali Premium emojilaringizni o'rnatishingiz mumkin.\n",
        "📋 <b>Hozirgi holat:</b>"
    ]
    for key, meta in EMOJI_METADATA.items():
        eid = config.PREMIUM_EMOJIS.get(key, "").strip()
        if eid and eid.isdigit():
            preview = f'<tg-emoji emoji-id="{eid}">{meta["default"]}</tg-emoji>'
            lines.append(f"• {preview} <b>{meta['name']}:</b> <code>{eid}</code>")
        else:
            lines.append(f"• {meta['default']} <b>{meta['name']}:</b> Standart {meta['default']}")

    lines.append(
        "\n💡 <b>Qanday qilib Premium Emoji ID olish mumkin?</b>\n"
        "1. Shunchaki botga o'zingiz xohlagan <b>Premium Emojini</b> xabar qilib yuboring — bot darhol uning <b>ID raqami</b> va <b>linkini</b> aniqlaydi hamda 1 ta tugma bilan kerakli bo'limga biriktiradi!\n"
        "2. Yoki pastdagi tugmalar orqali ID raqamini qo'lda yozing."
    )
    return "\n".join(lines)

def get_emojis_keyboard() -> InlineKeyboardMarkup:
    """Premium Emojilar menyusi tugmalari (har bir bo'lim uchun faol holat ko'rsatiladi)"""
    def btn_label(key: str) -> str:
        meta = EMOJI_METADATA.get(key, {"name": key, "default": "✨"})
        eid = config.PREMIUM_EMOJIS.get(key, "").strip()
        if eid and eid.isdigit():
            return f"{meta['default']} {meta['name']} ✅"
        return f"{meta['default']} {meta['name']} (Standart)"

    kb = [
        [
            InlineKeyboardButton(text=btn_label("lightning"), callback_data="edit_emoji:lightning"),
            InlineKeyboardButton(text=btn_label("pin"), callback_data="edit_emoji:pin"),
        ],
        [
            InlineKeyboardButton(text=btn_label("chart"), callback_data="edit_emoji:chart"),
            InlineKeyboardButton(text=btn_label("bulb"), callback_data="edit_emoji:bulb"),
        ],
        [
            InlineKeyboardButton(text=btn_label("antenna"), callback_data="edit_emoji:antenna"),
            InlineKeyboardButton(text=btn_label("tag"), callback_data="edit_emoji:tag"),
        ],
        [
            InlineKeyboardButton(text="🔄 Barchasini standartga qaytarish", callback_data="reset_all_emojis")
        ],
        [
            InlineKeyboardButton(text="⚙️ Admin panelga qaytish", callback_data="view_admin_panel")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

async def render_feeds_panel_text() -> str:
    """RSS manbalarini boshqarish paneli matni"""
    post_counts = await database.get_feed_post_counts()
    lines = [
        "📡 <b>NeuraTech — RSS Manbalarini Boshqarish</b>\n",
        "Quyidagi ro'yxat orqali bot qaysi IT/AI manbalaridan yangilik olayotganini kuzatishingiz va istalgan manbani bir zumda yoqishingiz yoki to'xtatishingiz mumkin.\n",
        "💡 <i>O'chirilgan manbalarga so'rov yuborilmaydi (server trafigi va 403 bloklari tejaladi).</i>\n"
    ]

    enabled_count = 0
    total_count = len(config.RSS_SOURCES)

    for i, src in enumerate(config.RSS_SOURCES, start=1):
        slug = src.get("slug", src["name"].lower().replace(" ", "_"))
        is_on = (await database.get_setting(f"feed_{slug}_enabled", "1")) == "1"
        if is_on:
            enabled_count += 1
        icon = "🟢" if is_on else "🔴"
        status_text = "Faol" if is_on else "O'chirilgan"
        cnt = post_counts.get(src["name"], 0)
        lines.append(f"{icon} <b>{i}. {src['name']}</b> ({status_text})")
        lines.append(f"   ├ 📂 <i>Kategoriya:</i> {src.get('category', 'IT & AI')}")
        lines.append(f"   └ 📊 <i>Bazadagi postlar:</i> <b>{cnt} ta</b>\n")

    lines.append(f"📊 <b>Umumiy holat:</b> {enabled_count} / {total_count} ta manba faol.")
    return "\n".join(lines)

async def get_feeds_keyboard() -> InlineKeyboardMarkup:
    """RSS manbalari uchun 2 ustunli yoqish/o'chirish tugmalari"""
    buttons = []
    for src in config.RSS_SOURCES:
        slug = src.get("slug", src["name"].lower().replace(" ", "_"))
        is_on = (await database.get_setting(f"feed_{slug}_enabled", "1")) == "1"
        icon = "🟢" if is_on else "🔴"
        btn_text = f"{icon} {src['name']}"
        buttons.append(InlineKeyboardButton(text=btn_text, callback_data=f"toggle_feed:{slug}"))

    # 2 tadan qilib qatorlarga ajratish
    rows = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i+2])

    rows.append([
        InlineKeyboardButton(text="🟢 Barchasini yoqish", callback_data="feed_all:enable"),
        InlineKeyboardButton(text="🔴 Barchasini o'chirish", callback_data="feed_all:disable")
    ])
    rows.append([
        InlineKeyboardButton(text="⚙️ Admin panelga qaytish", callback_data="view_admin_panel")
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_admin_keyboard(
    mode: str,
    interval: int,
    source_btn_enabled: bool = False,
    ai_btn_enabled: bool = False,
    queue_count: int = 0
) -> InlineKeyboardMarkup:
    """Admin boshqaruv paneli tugmalari (Asosiy harakatlar yashil rangda)"""
    mode_btn_text = "🟢 Rejim: Avtomatik (Faol)" if mode == "auto" else "🟡 Rejim: Moderatsiya (Tasdiqlash)"
    toggle_mode = "moderation" if mode == "auto" else "auto"

    source_btn_text = "🔗 Manba: 🟢 Faol" if source_btn_enabled else "🔗 Manba: 🔴 O'chirilgan"
    ai_btn_text = "🤖 AI Maslahat: 🟢 Faol" if ai_btn_enabled else "🤖 AI Maslahat: 🔴 O'chirilgan"

    queue_btn_text = f"📋 Postlar Navbati ({queue_count} ta)" if queue_count > 0 else "📋 Postlar Navbati (Bo'sh)"

    kb = [
        [InlineKeyboardButton(text=mode_btn_text, callback_data=f"set_mode:{toggle_mode}")],
        [
            InlineKeyboardButton(text="⏱ 15 min", callback_data="set_interval:15"),
            InlineKeyboardButton(text="⏱ 30 min", callback_data="set_interval:30"),
            InlineKeyboardButton(text="⏱ 60 min", callback_data="set_interval:60"),
        ],
        [
            InlineKeyboardButton(text=queue_btn_text, callback_data="admin_queue:0")
        ],
        [
            InlineKeyboardButton(text=source_btn_text, callback_data="toggle_source_btn"),
            InlineKeyboardButton(text=ai_btn_text, callback_data="toggle_ai_btn")
        ],
        [
            InlineKeyboardButton(text=f"📡 RSS Manbalari ({len(config.RSS_SOURCES)} ta)", callback_data="admin_feeds_menu")
        ],
        [
            InlineKeyboardButton(text="📊 Holat (/status)", callback_data="view_bot_status"),
            InlineKeyboardButton(text="💾 Baza Nusxasi (/backup)", callback_data="admin_download_backup")
        ],
        [
            InlineKeyboardButton(text="✨ Premium Emojilarni sozlash", callback_data="admin_emojis_menu")
        ],
        [
            InlineKeyboardButton(text="🟢 📢 Reklama (Broadcast) yuborish", callback_data="start_broadcast")
        ],
        [
            InlineKeyboardButton(text="🟢 🔍 Hozir yangilik qidirish", callback_data="manual_check")
        ],
        [
            InlineKeyboardButton(text="📱 Mijozlar menyusi (Top 10)", callback_data="view_user_menu"),
            InlineKeyboardButton(text="🔄 Yangilash", callback_data="refresh_panel")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def format_sched_time(iso_str: str) -> str:
    """Rejalashtirilgan vaqtni Toshkent vaqti va oraliq daqiqa bilan formatlash"""
    if not iso_str:
        return "Noma'lum"
    try:
        dt = datetime.fromisoformat(iso_str)
        dt_t = dt.astimezone(config.TASHKENT_TZ)
        now_t = datetime.now(config.TASHKENT_TZ)
        diff_mins = int((dt_t - now_t).total_seconds() / 60)
        time_str = dt_t.strftime("%H:%M")

        if dt_t.date() == now_t.date():
            prefix = "Bugun"
        elif dt_t.date() == (now_t + timedelta(days=1)).date():
            prefix = "Ertaga"
        else:
            prefix = dt_t.strftime("%d-%b")

        if diff_mins <= 0:
            rel = "navbatda kutmoqda"
        elif diff_mins < 60:
            rel = f"+{diff_mins} daqiqa"
        else:
            h = diff_mins // 60
            m = diff_mins % 60
            rel = f"+{h} soat {m} min"

        return f"{prefix} soat {time_str} ({rel})"
    except Exception:
        return str(iso_str)[:16]

async def render_queue_page(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Rejalashtirilgan postlar navbati matni va paginatsiya tugmalari"""
    limit = 5
    total_count = await database.get_scheduled_count()
    if total_count == 0:
        text = (
            "📋 <b>NeuraTech — Rejalashtirilgan Postlar Navbati</b>\n\n"
            "🟢 <b>Hozircha navbatda turgan postlar yo'q (bo'sh).</b>\n\n"
            "Yangi xabarlar RSS manbalari skanerlanganda avtomatik ravishda navbatga qo'shiladi."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 🔍 Hozir yangilik qidirish", callback_data="manual_check")],
            [InlineKeyboardButton(text="⚙️ Admin panelga qaytish", callback_data="view_admin_panel")]
        ])
        return text, kb

    total_pages = max(1, (total_count + limit - 1) // limit)
    page = max(0, min(page, total_pages - 1))
    offset = page * limit

    posts = await database.get_scheduled_posts(limit=limit, offset=offset)

    lines = [
        "📋 <b>NeuraTech — Rejalashtirilgan Postlar Navbati</b>\n",
        f"⏳ Jami kutilmoqda: <b>{total_count} ta post</b>",
        f"📄 Sahifa: <b>{page + 1} / {total_pages}</b>\n"
    ]

    kb_buttons = []
    num_icons = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

    for i, post in enumerate(posts):
        num_icon = num_icons[i] if i < len(num_icons) else f"{i+1}."
        title = post.get("title", "Sarlavhasiz")
        source = post.get("source_name", "IT")
        sched_label = format_sched_time(post.get("scheduled_at", ""))

        lines.append(
            f"{num_icon} <b>{title[:75]}...</b>\n"
            f"   🕒 Vaqti: <code>{sched_label}</code> | <i>{source}</i>\n"
        )
        kb_buttons.append([
            InlineKeyboardButton(
                text=f"{num_icon} {title[:36]}...",
                callback_data=f"view_q_post:{post['id']}:{page}"
            )
        ])

    # Paginatsiya qatori
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"admin_queue:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="ignore_cb"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"admin_queue:{page + 1}"))

    if nav_row:
        kb_buttons.append(nav_row)

    # Tezkor chiqarish
    kb_buttons.append([
        InlineKeyboardButton(text="🚀 Eng birinchi postni hoziroq chiqarish", callback_data="queue_pub_first")
    ])

    # Qaytish
    kb_buttons.append([
        InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"admin_queue:{page}"),
        InlineKeyboardButton(text="⚙️ Admin panel", callback_data="view_admin_panel")
    ])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_buttons)

async def get_channel_post_keyboard(post_id: Optional[int] = None, original_url: Optional[str] = None) -> Optional[InlineKeyboardMarkup]:
    """Kanalga chiquvchi post ostiga ixcham inline tugmalar bloki (Manba va AI maslahat alohida boshqariladi)"""
    default_src = "1" if getattr(config, "ENABLE_CHANNEL_SOURCE_BUTTON", False) else "0"
    default_ai = "1" if getattr(config, "ENABLE_CHANNEL_AI_BUTTON", False) else "0"
    source_btn_enabled = (await database.get_setting("channel_source_btn", default_src) == "1")
    ai_btn_enabled = (await database.get_setting("channel_ai_btn", default_ai) == "1")

    # Agar ikkala tugma ham o'chirilgan bo'lsa, xabar ostiga umuman tugma qo'yilmaydi (chiroyli toza xabar)
    if not source_btn_enabled and not ai_btn_enabled:
        return None

    buttons = []
    row1 = []
    if source_btn_enabled and original_url and str(original_url).startswith("http"):
        row1.append(InlineKeyboardButton(text="🔗 Manba ↗", url=str(original_url)))

    if ai_btn_enabled and post_id:
        bot_user = getattr(config, "BOT_USERNAME", "neuratech_news_bot").lstrip("@")
        row1.append(InlineKeyboardButton(
            text="🤖 AI bilan muhokama",
            url=f"https://t.me/{bot_user}?start=discuss_{post_id}"
        ))

    if row1:
        buttons.append(row1)

    disc_link = getattr(config, "DISCUSSION_CHAT_LINK", "").strip()
    if disc_link and disc_link.startswith("http"):
        buttons.append([InlineKeyboardButton(text="💬 Fikr bildirish / Guruh", url=disc_link)])

    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

async def get_rendered_admin_panel() -> tuple[str, InlineKeyboardMarkup]:
    """Admin panelining yangilangan matni va tugmalarini tayyorlash"""
    text = await format_panel_text()
    mode = await database.get_setting("mode", "moderation")
    interval = int(await database.get_setting("interval_minutes", "25"))
    default_src = "1" if getattr(config, "ENABLE_CHANNEL_SOURCE_BUTTON", False) else "0"
    default_ai = "1" if getattr(config, "ENABLE_CHANNEL_AI_BUTTON", False) else "0"
    source_btn_enabled = (await database.get_setting("channel_source_btn", default_src) == "1")
    ai_btn_enabled = (await database.get_setting("channel_ai_btn", default_ai) == "1")
    queue_count = await database.get_scheduled_count()
    return text, get_admin_keyboard(mode, interval, source_btn_enabled, ai_btn_enabled, queue_count)

def get_moderation_keyboard(post_id: int) -> InlineKeyboardMarkup:
    """Moderatsiya uchun har bir post ostidagi tugmalar (Asosiy chiqarish tugmasi yashil belgi bilan)"""
    kb = [
        [InlineKeyboardButton(text="🟢 🚀 Hoziroq kanalga chiqarish", callback_data=f"pub:{post_id}")],
        [
            InlineKeyboardButton(text="⏳ +30 daqiqa", callback_data=f"delay:{post_id}:30"),
            InlineKeyboardButton(text="⏳ +1 soat", callback_data=f"delay:{post_id}:60")
        ],
        [
            InlineKeyboardButton(text="✏️ Matnni tahrirlash", callback_data=f"edit_draft:{post_id}"),
            InlineKeyboardButton(text="🔄 Qayta yozdirish (AI)", callback_data=f"regen:{post_id}")
        ],
        [InlineKeyboardButton(text="🗑 Bekor qilish (O'chirish)", callback_data=f"reject:{post_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_user_reply_keyboard() -> ReplyKeyboardMarkup:
    """Oddiy obunachilar (mijozlar) uchun pastki menyu tugmalari"""
    kb = [
        [KeyboardButton(text="🟢 🔥 Top 10 ta AI Yangilik")],
        [KeyboardButton(text="🔍 Qidiruv"), KeyboardButton(text="💬 AI Maslahatchi")],
        [KeyboardButton(text="⚡️ Eng so'nggi yangilik"), KeyboardButton(text="📢 Rasmiy Kanalimiz")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

async def check_is_admin(user_id: int) -> bool:
    admin_id = await database.get_setting("admin_id")
    if not admin_id:
        await database.set_setting("admin_id", str(user_id))
        return True
    return str(user_id) == str(admin_id)

async def format_panel_text() -> str:
    mode = await database.get_setting("mode", "moderation")
    interval = await database.get_setting("interval_minutes", "25")
    default_src = "1" if getattr(config, "ENABLE_CHANNEL_SOURCE_BUTTON", False) else "0"
    default_ai = "1" if getattr(config, "ENABLE_CHANNEL_AI_BUTTON", False) else "0"
    source_btn_setting = await database.get_setting("channel_source_btn", default_src)
    ai_btn_setting = await database.get_setting("channel_ai_btn", default_ai)
    source_btn_display = "🟢 Faol (Tugmada)" if source_btn_setting == "1" else "ℹ️ Matn ichida (Tugmasiz)"
    ai_btn_display = "🟢 Faol" if ai_btn_setting == "1" else "🔴 O'chirilgan"
    stats = await database.get_stats()
    gemini_status = "🟢 Ulangan (Multi-Key)" if config.GEMINI_API_KEY else "🔴 Kalit kiritilmagan (.env)"

    mode_display = "🟢 Avtomatik (Kanalga to'g'ridan-to'g'ri)" if mode == "auto" else "🟡 Moderatsiya (Tasdiqlash bilan)"
    last_backup = await database.get_setting("last_backup_timestamp", "Hali olinmagan")

    text = (
        "🤖 <b>NeuraTech AI — IT & AI Yangiliklar Boshqaruvi</b>\n\n"
        f"⚙️ <b>Sozlamalar:</b>\n"
        f"• <b>Rejim:</b> {mode_display}\n"
        f"• <b>Vaqt oralig'i:</b> Har {interval} daqiqada\n"
        f"• <b>Manba tugmasi:</b> {source_btn_display}\n"
        f"• <b>AI Maslahatchi:</b> {ai_btn_display}\n"
        f"• <b>Oxirgi zaxira (Backup):</b> <code>{last_backup}</code>\n"
        f"• <b>Kanal:</b> <code>{config.CHANNEL_ID}</code> (<a href='{config.CHANNEL_LINK}'>{config.CHANNEL_USERNAME}</a>)\n"
        f"• <b>Google Gemini AI:</b> {gemini_status}\n\n"
        f"📊 <b>Statistika:</b>\n"
        f"• 👥 Bot mijozlari: <b>{stats['users_active']}</b> ta faol (Jami: {stats['users_total']} ta)\n"
        f"• 📡 Barcha tahlil qilingan: <b>{stats['total']}</b>\n"
        f"• 📢 Kanalga chiqarilgan: <b>{stats['published']}</b>\n"
        f"• ⏳ Navbatda (Kutilmoqda): <b>{stats['scheduled']}</b>\n"
        f"• ❌ Rad etilgan: <b>{stats['rejected']}</b>\n\n"
        "Quyidagi tugmalar orqali bot sozlamalarini boshqaring:"
    )
    return text

async def render_top_10_digest(is_admin: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    """Top 10 ta yangilik matni va interaktiv tugmalarini shakllantirish"""
    posts = await database.get_top_news(10)
    if not posts:
        return (
            "Hozircha yangiliklar bazasi shakllanmoqda. Tez orada eng so'nggi xabarlar paydo bo'ladi!",
            InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=f"📢 {config.CHANNEL_USERNAME} ga a'zo bo'lish", url=config.CHANNEL_LINK)
            ]])
        )

    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text_lines = ["🔥 <b>Eng So'nggi Top IT va AI Yangiliklari:</b>\n"]

    kb_row1 = []
    kb_row2 = []

    for i, post in enumerate(posts[:10]):
        num_icon = number_emojis[i] if i < len(number_emojis) else f"{i+1}."
        title = post["title"]
        source = post.get("source_name", "IT")
        text_lines.append(f"{num_icon} <b>{title}</b> <i>({source})</i>\n")

        btn = InlineKeyboardButton(text=f"{i+1}", callback_data=f"user_post:{post['id']}")
        if i < 5:
            kb_row1.append(btn)
        else:
            kb_row2.append(btn)

    text_lines.append("\n<i>Batafsil o'qish uchun quyidagi raqamlardan birini tanlang:</i>")

    inline_rows = []
    if kb_row1:
        inline_rows.append(kb_row1)
    if kb_row2:
        inline_rows.append(kb_row2)

    bottom_row = [
        InlineKeyboardButton(text=f"📢 {config.CHANNEL_USERNAME} ga a'zo bo'lish", url=config.CHANNEL_LINK),
        InlineKeyboardButton(text="🔄 Yangilash", callback_data="user_refresh_top")
    ]
    inline_rows.append(bottom_row)

    if is_admin:
        inline_rows.append([
            InlineKeyboardButton(text="⚙️ Admin panelga qaytish", callback_data="view_admin_panel")
        ])

    return ("\n".join(text_lines), InlineKeyboardMarkup(inline_keyboard=inline_rows))

# ==================== /START VA ASOSIY BUYRUQLAR ====================

@router.message(Command("start"))
async def cmd_start(message: Message):
    # Foydalanuvchini bazaga kiritish
    await database.register_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )

    # 1. Kanal inline tugmasidan kelgan deep-link so'rovini tekshirish (/start discuss_123)
    deep_arg = ""
    if message.text and len(message.text.split(maxsplit=1)) > 1:
        deep_arg = message.text.split(maxsplit=1)[1].strip()

    if deep_arg.startswith("discuss_"):
        target_post = None
        try:
            target_post_id = int(deep_arg.split("_", 1)[1])
            target_post = await database.get_post(target_post_id)
        except Exception:
            target_post = None

        if target_post:
            user_ai_state[message.from_user.id] = {
                "post_id": target_post["id"],
                "title": target_post.get("title", ""),
                "context": target_post.get("uz_caption", "") or target_post.get("original_summary", "")
            }
            user_search_state.pop(message.from_user.id, None)

            discuss_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📖 To'liq maqolani botda o'qish", callback_data=f"user_post:{target_post['id']}")],
                [InlineKeyboardButton(text="❌ Muhokamani yakunlash", callback_data="user_cancel_ai")]
            ])

            greeting = (
                f"🤖 <b>NeuraTech AI — Maqola Muhokamasi</b>\n\n"
                f"Siz kanalimizdagi quyidagi yangilik yuzasidan savol bermoqchisiz:\n"
                f"📌 <b>«{target_post['title']}»</b>\n\n"
                f"<i>Ushbu yangilik bo'yicha o'zingizni qiziqtirgan savolingizni yozing. Masalan:</i>\n"
                f"• «Buning oddiy foydalanuvchiga qanday foydasi bor?»\n"
                f"• «Ushbu texnologiya qachon ommalashadi yoki sotuvga chiqadi?»\n"
                f"• «Bu yangilik nimasi bilan inqilobiy?»\n\n"
                f"✍️ <i>Savolingizni quyida yozib qoldiring:</i>"
            )
            await message.answer(greeting, parse_mode="HTML", reply_markup=discuss_kb)
            return

    # Standart /start harakati
    is_admin = await check_is_admin(message.from_user.id)
    if is_admin:
        text, kb = await get_rendered_admin_panel()
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        user_name = message.from_user.first_name or "Foydalanuvchi"
        welcome_text = (
            f"👋 <b>Assalomu alaykum, {user_name}!</b>\n\n"
            f"🤖 <b>{config.CHANNEL_USERNAME}</b> — Sun'iy intellekt, benchmarklar va IT texnologiyalar olamining eng sara yangiliklari botiga xush kelibsiz!\n\n"
            "Quyidagi tugmalar orqali xabarlarni o'qishingiz mumkin:"
        )
        await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_user_reply_keyboard())
        
        digest_text, kb = await render_top_10_digest(is_admin=False)
        await message.answer(digest_text, parse_mode="HTML", reply_markup=kb)

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not await check_is_admin(message.from_user.id):
        await message.reply("Kechirasiz, ushbu buyruq faqat bot ma'muri uchun.")
        return
    text, kb = await get_rendered_admin_panel()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.message(Command("status", "health"))
async def cmd_status(message: Message, bot: Bot):
    if not await check_is_admin(message.from_user.id):
        await message.reply("Kechirasiz, ushbu buyruq faqat bot ma'muri uchun.")
        return
    wait_msg = await message.answer("⏳ <i>Tizim parametrlari va Gemini kalitlari tekshirilmoqda...</i>", parse_mode="HTML")
    health_data = await get_system_health(bot=bot, force_refresh=True)
    report = format_health_html(health_data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Qayta tekshirish", callback_data="refresh_bot_status")],
        [InlineKeyboardButton(text="⚙️ Admin panelga qaytish", callback_data="view_admin_panel")]
    ])
    try:
        await wait_msg.delete()
    except Exception:
        pass
    await message.answer(report, parse_mode="HTML", reply_markup=kb)

@router.message(Command("backup"))
async def cmd_backup(message: Message, bot: Bot):
    if not await check_is_admin(message.from_user.id):
        await message.reply("Kechirasiz, ushbu buyruq faqat bot ma'muri uchun.")
        return

    wait_msg = await message.answer("⏳ <i>Baza zaxira nusxasi tayyorlanmoqda va siqilmoqda...</i>", parse_mode="HTML")
    from backup_service import send_database_backup
    success = await send_database_backup(bot=bot, admin_id=message.from_user.id, is_automatic=False)
    try:
        await wait_msg.delete()
    except Exception:
        pass
    if not success:
        await message.answer("❌ Zaxira nusxasini tayyorlashda xatolik yuz berdi.")

# ==================== FOYDALANUVCHILAR (MIJOZLAR) UCHUN BO'LIM ====================

@router.message(F.text.in_({"🟢 🔥 Top 10 ta AI Yangilik", "🔥 Top 10 ta AI Yangilik", "/top"}))
async def cmd_user_top(message: Message):
    await database.register_user(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    is_admin = await check_is_admin(message.from_user.id)
    digest_text, kb = await render_top_10_digest(is_admin=is_admin)
    await message.answer(digest_text, parse_mode="HTML", reply_markup=kb)

@router.message(F.text.in_({"⚡️ Eng so'nggi yangilik", "/latest"}))
async def cmd_user_latest(message: Message):
    await database.register_user(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    posts = await database.get_top_news(1)
    if not posts:
        await message.answer("Hozircha yangiliklar topilmadi.")
        return

    post = posts[0]
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 🔥 Top 10 ro'yxatini ko'rish", callback_data="user_refresh_top")],
        [InlineKeyboardButton(text=f"📢 {config.CHANNEL_USERNAME} ga a'zo bo'lish", url=config.CHANNEL_LINK)]
    ])

    caption = post["uz_caption"]
    if post.get("image_url"):
        sent = await send_user_preview_media(message, post["image_url"], caption, back_kb)
        if sent:
            return

    await message.answer(
        text=caption[:4096],
        parse_mode="HTML",
        reply_markup=back_kb
    )

@router.message(F.text.in_({"📢 Rasmiy Kanalimiz", f"📢 {config.CHANNEL_USERNAME} Kanali"}))
async def cmd_user_channel(message: Message):
    await database.register_user(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    text = (
        f"📢 <b>{config.CHANNEL_USERNAME}</b> — IT va AI dunyosining eng qaynoq anonslari!\n\n"
        "Eng so'nggi modellarni solishtirish, benchmark natijalari va texnologik inqiloblar bizning kanalda e'lon qilib boriladi."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Kanalga o'tish ↗", url=config.CHANNEL_LINK)
    ]])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.message(F.text.in_({"🔍 Qidiruv", "/search"}))
async def cmd_user_search(message: Message):
    await database.register_user(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    user_search_state[message.from_user.id] = True
    user_ai_state.pop(message.from_user.id, None)
    text = (
        "🔍 <b>Yangiliklar ichidan qidirish</b>\n\n"
        "Qidirayotgan mavzu, brend yoki kalit so'zni yozing:\n"
        "<i>Masalan: RTX 5090, DeepSeek, Claude, Apple, S25 Ultra, GPT-5</i>"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(F.text.in_({"💬 AI Maslahatchi", "/ask", "/ai"}))
async def cmd_user_ai_assistant(message: Message):
    await database.register_user(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    user_ai_state[message.from_user.id] = True
    user_search_state.pop(message.from_user.id, None)
    text = (
        "💬 <b>NeuraTech AI Maslahatchi</b>\n\n"
        "IT, dasturlash, texnika, chiplar yoki sun'iy intellekt bo'yicha savolingizni yozing:\n"
        "<i>Masalan: 'Dasturlash uchun qaysi noutbuk yaxshi?', 'Claude 3.7 nima?', 'RTX 5090 ning kuchi qanday?'</i>\n\n"
        "NeuraTech AI sizga professional va lo'nda tushuntirib beradi ⚡️"
    )
    await message.answer(text, parse_mode="HTML")

@router.callback_query(F.data.startswith("ai_ask_inline:"))
async def cb_ai_ask_inline(call: CallbackQuery):
    await database.register_user(call.from_user.id, call.from_user.username, call.from_user.first_name, call.from_user.last_name)
    query = call.data.split(":", 1)[1]
    await call.answer("NeuraTech AI javob tayyorlamoqda... ⏳")
    wait_msg = await call.message.answer(f"🧠 <i>'{query}' bo'yicha NeuraTech AI tahlil qilmoqda...</i>", parse_mode="HTML")
    answer = await answer_ai_assistant_question(query)
    try:
        await wait_msg.delete()
    except Exception:
        pass
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Yana savol berish", callback_data="ai_ask_again")],
        [InlineKeyboardButton(text=f"📢 {config.CHANNEL_USERNAME}", url=config.CHANNEL_LINK)]
    ])
    await call.message.answer(f"🤖 <b>NeuraTech AI:</b>\n\n{answer}", parse_mode="HTML", reply_markup=reply_kb)

@router.callback_query(F.data == "ai_ask_again")
async def cb_ai_ask_again(call: CallbackQuery):
    await database.register_user(call.from_user.id, call.from_user.username, call.from_user.first_name, call.from_user.last_name)
    user_ai_state[call.from_user.id] = True
    await call.answer()
    await call.message.answer(
        "💬 <b>NeuraTech AI Maslahatchi</b>\n\n"
        "Savolingizni yozib qoldiring:",
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("ai_discuss_again:"))
async def cb_ai_discuss_again(call: CallbackQuery):
    await database.register_user(call.from_user.id, call.from_user.username, call.from_user.first_name, call.from_user.last_name)
    post_id = int(call.data.split(":")[1])
    post = await database.get_post(post_id)
    if post:
        user_ai_state[call.from_user.id] = {
            "post_id": post_id,
            "title": post.get("title", ""),
            "context": post.get("uz_caption", "") or post.get("original_summary", "")
        }
        await call.answer()
        await call.message.answer(
            f"💬 <b>«{post['title']}»</b> mavzusi bo'yicha savolingizni yozib qoldiring:",
            parse_mode="HTML"
        )
    else:
        user_ai_state[call.from_user.id] = True
        await call.answer()
        await call.message.answer("💬 Savolingizni yozib qoldiring:", parse_mode="HTML")

@router.callback_query(F.data == "user_cancel_ai")
async def cb_user_cancel_ai(call: CallbackQuery):
    user_ai_state.pop(call.from_user.id, None)
    await call.answer("Muhokama yakunlandi.")
    await call.message.answer(
        "Bosh menyuga qaytdingiz. Marhamat, bo'limlardan birini tanlang:",
        reply_markup=get_user_reply_keyboard()
    )

@router.callback_query(F.data.startswith("user_post:"))
async def cb_user_post(call: CallbackQuery):
    await database.register_user(call.from_user.id, call.from_user.username, call.from_user.first_name, call.from_user.last_name)
    post_id = int(call.data.split(":")[1])
    post = await database.get_post(post_id)
    if not post:
        await call.answer("Xabar topilmadi!", show_alert=True)
        return

    nav_row = [InlineKeyboardButton(text="⬅️ Top 10 ro'yxatiga qaytish", callback_data="user_refresh_top")]
    if post.get("url"):
        nav_row.insert(0, InlineKeyboardButton(text="🔗 Manba ↗", url=post["url"]))

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Ushbu maqola bo'yicha AI bilan muhokama", callback_data=f"ai_discuss_again:{post_id}")],
        nav_row,
        [InlineKeyboardButton(text=f"📢 {config.CHANNEL_USERNAME} ga a'zo bo'lish", url=config.CHANNEL_LINK)]
    ])

    await call.answer()
    caption = post["uz_caption"]

    if post.get("image_url"):
        sent = await send_user_preview_media(call, post["image_url"], caption, back_kb)
        if sent:
            return

    await call.message.answer(
        text=caption[:4096],
        parse_mode="HTML",
        reply_markup=back_kb
    )

@router.callback_query(F.data == "user_refresh_top")
async def cb_user_refresh_top(call: CallbackQuery):
    await database.register_user(call.from_user.id, call.from_user.username, call.from_user.first_name, call.from_user.last_name)
    is_admin = await check_is_admin(call.from_user.id)
    digest_text, kb = await render_top_10_digest(is_admin=is_admin)
    try:
        if call.message.text:
            await call.message.edit_text(digest_text, parse_mode="HTML", reply_markup=kb)
        else:
            await call.message.answer(digest_text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await call.message.answer(digest_text, parse_mode="HTML", reply_markup=kb)
    await call.answer("Ro'yxat yangilandi ✅")

@router.callback_query(F.data == "view_user_menu")
async def cb_view_user_menu(call: CallbackQuery):
    digest_text, kb = await render_top_10_digest(is_admin=True)
    await call.message.edit_text(digest_text, parse_mode="HTML", reply_markup=kb)
    await call.answer("Mijozlar ko'rinishi ochildi")

@router.callback_query(F.data == "view_admin_panel")
async def cb_view_admin_panel(call: CallbackQuery):
    text = await format_panel_text()
    mode = await database.get_setting("mode", "moderation")
    interval = int(await database.get_setting("interval_minutes", "25"))
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_keyboard(mode, interval))
    await call.answer("Admin panelga qaytildi")

# ==================== ADMIN REKLAMA (BROADCAST) TIZIMI ====================

@router.callback_query(F.data == "start_broadcast")
async def cb_start_broadcast(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    admin_broadcast_state[call.from_user.id] = "waiting_message"
    u_stats = await database.get_users_count()
    await call.answer()

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔴 ❌ Bekor qilish", callback_data="cancel_broadcast")
    ]])

    await call.message.answer(
        "📢 <b>MIJOZLARGA REKLAMA (XABAR) JO'NATISH</b>\n\n"
        f"👥 Hozirgi faol mijozlar soni: <b>{u_stats['active']} ta</b> (Jami bazada: {u_stats['total']} ta)\n\n"
        "Istalgan xabaringizni yuboring (oddiy matn, rasm, video, audio, ovozli xabar, inline tugmali post yoki <b>Telegram Premium emojilari</b> bo'lgan xabar).\n\n"
        "<i>💡 Bot xabarni 100% birga-bir (barcha premium emojilar va formatlari bilan) nusxalab barcha mijozlarga yetkazadi.</i>\n\n"
        "Bekor qilish uchun pastdagi tugmani bosing yoki /cancel deb yozing.",
        parse_mode="HTML",
        reply_markup=cancel_kb
    )

@router.message(Command("broadcast", "reklama"))
async def cmd_broadcast(message: Message):
    if not await check_is_admin(message.from_user.id):
        return
    admin_broadcast_state[message.from_user.id] = "waiting_message"
    u_stats = await database.get_users_count()
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔴 ❌ Bekor qilish", callback_data="cancel_broadcast")
    ]])
    await message.answer(
        "📢 <b>MIJOZLARGA REKLAMA (XABAR) JO'NATISH</b>\n\n"
        f"👥 Hozirgi faol mijozlar soni: <b>{u_stats['active']} ta</b>\n\n"
        "Istalgan xabaringizni yuboring (matn, rasm, video, audio yoki <b>Telegram Premium emojilari</b> bo'lgan xabar).\n\n"
        "<i>💡 Bot xabarni 100% birga-bir ko'rinishda barcha mijozlarga yetkazadi.</i>\n\n"
        "Bekor qilish uchun /cancel deb yozing.",
        parse_mode="HTML",
        reply_markup=cancel_kb
    )

@router.message(Command("cancel"))
async def cmd_cancel(message: Message):
    admin_id = message.from_user.id
    if admin_id in admin_broadcast_state:
        admin_broadcast_state.pop(admin_id, None)
        admin_broadcast_pending.pop(admin_id, None)
        await message.answer("❌ Reklama yuborish bekor qilindi.")

@router.callback_query(F.data == "confirm_broadcast")
async def cb_confirm_broadcast(call: CallbackQuery, bot: Bot):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    admin_id = call.from_user.id
    pending = admin_broadcast_pending.get(admin_id)
    if not pending:
        await call.answer("Yuborish uchun reklama topilmadi!", show_alert=True)
        return

    admin_broadcast_pending.pop(admin_id, None)
    await call.message.edit_text("⏳ <b>Reklama barcha mijozlarga jo'natilmoqda...</b>\n<i>Iltimos, kuting.</i>", parse_mode="HTML")

    active_user_ids = await database.get_active_users()
    total = len(active_user_ids)
    success = 0
    blocked = 0
    failed = 0

    for u_id in active_user_ids:
        try:
            await bot.copy_message(
                chat_id=u_id,
                from_chat_id=pending["chat_id"],
                message_id=pending["message_id"]
            )
            success += 1
        except Exception as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ["blocked", "forbidden", "deactivated", "chat not found", "user is deactivated"]):
                blocked += 1
                await database.set_user_active(u_id, False)
            else:
                failed += 1
        await asyncio.sleep(0.04)  # Xavfsiz oraliq (Telegram spam limitidan qochish)

    report = (
        "✅ <b>REKLAMA MUVAFFAQIYATLI YUBORILDI!</b>\n\n"
        f"📊 <b>Natijalar:</b>\n"
        f"• 🟢 Yetkazildi: <b>{success} ta</b>\n"
        f"• 🚫 Botni bloklaganlar: <b>{blocked} ta</b>\n"
        f"• ⚠️ Boshqa xatoliklar: <b>{failed} ta</b>\n"
        f"• 👥 Jami mijozlar: <b>{total} ta</b>"
    )
    await call.message.answer(report, parse_mode="HTML")
    await call.answer("Yakunlandi! ✅")

@router.callback_query(F.data == "cancel_broadcast")
async def cb_cancel_broadcast(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return
    admin_broadcast_state.pop(call.from_user.id, None)
    admin_broadcast_pending.pop(call.from_user.id, None)
    try:
        await call.message.edit_text("❌ <b>Reklama jo'natish bekor qilindi.</b>", parse_mode="HTML")
    except Exception:
        pass
    await call.answer("Bekor qilindi")

# ==================== ADMIN SOZLAMALARI VA BOSHQARUV ====================

@router.callback_query(F.data.startswith("set_mode:"))
async def cb_set_mode(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    new_mode = call.data.split(":")[1]
    await database.set_setting("mode", new_mode)
    await call.answer(f"Rejim o'zgartirildi: {new_mode.upper()}")
    
    text, kb = await get_rendered_admin_panel()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

@router.callback_query(F.data.startswith("set_interval:"))
async def cb_set_interval(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    new_interval = int(call.data.split(":")[1])
    await database.set_setting("interval_minutes", str(new_interval))
    await call.answer(f"Interval o'rnatildi: Har {new_interval} daqiqada")

    text, kb = await get_rendered_admin_panel()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

@router.callback_query(F.data == "toggle_source_btn")
async def cb_toggle_source_btn(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    curr = await database.get_setting("channel_source_btn", "0")
    new_val = "0" if curr == "1" else "1"
    await database.set_setting("channel_source_btn", new_val)
    status_label = "yoqildi (🟢 Faol)" if new_val == "1" else "o'chirildi (ℹ️ Matn ichida qoldi)"
    await call.answer(f"Manba tugmasi {status_label}!", show_alert=False)

    text, kb = await get_rendered_admin_panel()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

@router.callback_query(F.data == "toggle_ai_btn")
async def cb_toggle_ai_btn(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    curr = await database.get_setting("channel_ai_btn", "0")
    new_val = "0" if curr == "1" else "1"
    await database.set_setting("channel_ai_btn", new_val)
    status_label = "yoqildi (🟢 Faol)" if new_val == "1" else "o'chirildi (🔴 O'chirilgan)"
    await call.answer(f"AI Maslahatchi tugmasi {status_label}!", show_alert=False)

    text, kb = await get_rendered_admin_panel()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

@router.callback_query(F.data == "manual_check")
async def cb_manual_check(call: CallbackQuery, bot: Bot):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    await call.answer("🔍 Yangiliklar tekshirilmoqda...", show_alert=False)
    from scheduler import process_news_cycle
    count = await process_news_cycle(bot)
    
    text, kb = await get_rendered_admin_panel()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    await call.message.answer(f"✅ Tekshiruv yakunlandi! Yangi topilgan xabarlar: <b>{count}</b> ta.", parse_mode="HTML")

@router.callback_query(F.data == "refresh_panel")
async def cb_refresh_panel(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer()
        return

    text, kb = await get_rendered_admin_panel()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    await call.answer("Yangilandi ✅")

@router.callback_query(F.data == "view_admin_panel")
async def cb_view_admin_panel(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return
    text, kb = await get_rendered_admin_panel()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "view_bot_status")
async def cb_view_bot_status(call: CallbackQuery, bot: Bot):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return
    await call.answer("Tizim tekshirilmoqda... ⏳")
    health_data = await get_system_health(bot=bot, force_refresh=False)
    report = format_health_html(health_data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Qayta tekshirish", callback_data="refresh_bot_status")],
        [InlineKeyboardButton(text="⚙️ Admin panelga qaytish", callback_data="view_admin_panel")]
    ])
    try:
        await call.message.edit_text(report, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await call.message.answer(report, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "refresh_bot_status")
async def cb_refresh_bot_status(call: CallbackQuery, bot: Bot):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return
    await call.answer("Ma'lumotlar yangilanmoqda... ⏳")
    health_data = await get_system_health(bot=bot, force_refresh=True)
    report = format_health_html(health_data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Qayta tekshirish", callback_data="refresh_bot_status")],
        [InlineKeyboardButton(text="⚙️ Admin panelga qaytish", callback_data="view_admin_panel")]
    ])
    try:
        await call.message.edit_text(report, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

@router.callback_query(F.data == "admin_download_backup")
async def cb_admin_download_backup(call: CallbackQuery, bot: Bot):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return
    await call.answer("Baza zaxira nusxasi tayyorlanmoqda... ⏳", show_alert=False)
    from backup_service import send_database_backup
    success = await send_database_backup(bot=bot, admin_id=call.from_user.id, is_automatic=False)
    if not success:
        await call.message.answer("❌ Zaxira nusxasini tayyorlashda xatolik yuz berdi.")

# ==================== POSTLAR NAVBATI (QUEUE MANAGER) ====================

@router.message(Command("queue", "navbat"))
async def cmd_queue(message: Message):
    if not await check_is_admin(message.from_user.id):
        await message.reply("Kechirasiz, ushbu buyruq faqat bot ma'muri uchun.")
        return
    text, kb = await render_queue_page(page=0)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("admin_queue:"))
async def cb_admin_queue(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return
    page = int(call.data.split(":")[1])
    text, kb = await render_queue_page(page=page)
    try:
        if call.message.text:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("view_q_post:"))
async def cb_view_q_post(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    parts = call.data.split(":")
    post_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    post = await database.get_post(post_id)
    if not post:
        await call.answer("Post topilmadi!", show_alert=True)
        return

    sched_time = format_sched_time(post.get("scheduled_at", ""))
    preview_header = (
        f"📋 <b>Navbatdagi Post #{post['id']} Tafsilotlari</b>\n"
        f"🕒 Rejalashtirilgan vaqt: <code>{sched_time}</code>\n"
        f"📡 Manba: <b>{post.get('source_name', 'IT')}</b>\n"
        "────────────────────\n\n"
    )
    full_text = preview_header + post["uz_caption"]

    q_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 🚀 Hoziroq kanalga chiqarish", callback_data=f"q_pub:{post_id}:{page}")],
        [
            InlineKeyboardButton(text="✏️ Matnni tahrirlash", callback_data=f"q_edit:{post_id}:{page}"),
            InlineKeyboardButton(text="🗑 Navbatdan o'chirish (Bekor qilish)", callback_data=f"q_del:{post_id}:{page}")
        ],
        [InlineKeyboardButton(text="⬅️ Navbat ro'yxatiga qaytish", callback_data=f"admin_queue:{page}")]
    ])

    await call.answer()
    if post.get("image_url"):
        sent = await send_user_preview_media(call, post["image_url"], full_text, q_kb)
        if sent:
            return

    await call.message.answer(
        text=full_text[:4096],
        parse_mode="HTML",
        reply_markup=q_kb
    )

@router.callback_query(F.data.startswith("q_pub:"))
async def cb_q_publish(call: CallbackQuery, bot: Bot):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    parts = call.data.split(":")
    post_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    post = await database.get_post(post_id)
    if not post or post["status"] == "published":
        await call.answer("Ushbu post allaqachon chiqarilgan yoki topilmadi!", show_alert=True)
        return

    now_str = datetime.now(timezone.utc).isoformat()
    await database.set_post_status(post_id, "published", published_at=now_str)

    try:
        from scheduler import send_to_channel
        channel_kb = await get_channel_post_keyboard(post_id=post["id"], original_url=post.get("url"))
        cap_with_link = attach_source_link(post["uz_caption"], post.get("source_name", "IT"), post.get("url", ""))
        await send_to_channel(bot, post["image_url"], cap_with_link, reply_markup=channel_kb)
        await call.answer("Post kanalga muvaffaqiyatli chiqarildi! 🚀", show_alert=False)
    except Exception as e:
        await call.answer(f"Xatolik: {e}", show_alert=True)
        return

    text, kb = await render_queue_page(page=page)
    try:
        if call.message.text:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("q_del:"))
async def cb_q_delete(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    parts = call.data.split(":")
    post_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    await database.cancel_scheduled_post(post_id)
    await call.answer("Post navbatdan o'chirildi 🗑", show_alert=False)

    text, kb = await render_queue_page(page=page)
    try:
        if call.message.text:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "queue_pub_first")
async def cb_queue_pub_first(call: CallbackQuery, bot: Bot):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    posts = await database.get_scheduled_posts(limit=1, offset=0)
    if not posts:
        await call.answer("Navbatda postlar yo'q!", show_alert=True)
        return

    post = posts[0]
    now_str = datetime.now(timezone.utc).isoformat()
    await database.set_post_status(post["id"], "published", published_at=now_str)

    try:
        from scheduler import send_to_channel
        channel_kb = await get_channel_post_keyboard(post_id=post["id"], original_url=post.get("url"))
        cap_with_link = attach_source_link(post["uz_caption"], post.get("source_name", "IT"), post.get("url", ""))
        await send_to_channel(bot, post["image_url"], cap_with_link, reply_markup=channel_kb)
        await call.answer(f"«{post['title'][:30]}...» kanalga chiqarildi! 🚀", show_alert=False)
    except Exception as e:
        await call.answer(f"Xatolik: {e}", show_alert=True)
        return

    text, kb = await render_queue_page(page=0)
    try:
        if call.message.text:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "ignore_cb")
async def cb_ignore_cb(call: CallbackQuery):
    await call.answer()

# ==================== PREMIUM EMOJI BOSHQARUVI ====================

@router.message(Command("emojis", "emoji"))
async def cmd_emojis(message: Message):
    if not await check_is_admin(message.from_user.id):
        return
    text = render_emojis_panel_text()
    await message.answer(text, parse_mode="HTML", reply_markup=get_emojis_keyboard())

@router.callback_query(F.data == "admin_emojis_menu")
async def cb_admin_emojis_menu(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return
    text = render_emojis_panel_text()
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_emojis_keyboard())
    await call.answer()

@router.callback_query(F.data.startswith("edit_emoji:"))
async def cb_edit_emoji(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    key = call.data.split(":")[1]
    meta = EMOJI_METADATA.get(key, {"name": key, "default": "✨"})
    admin_emoji_edit_state[call.from_user.id] = key

    current_id = config.PREMIUM_EMOJIS.get(key, "").strip()
    status_str = f"ID: <code>{current_id}</code>" if current_id else f"Standart {meta['default']}"

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔄 Standart {meta['default']} ga qaytarish", callback_data=f"reset_emoji:{key}")],
        [InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data="admin_emojis_menu")]
    ])

    text = (
        f"✨ <b>{meta['name']} uchun Premium Emoji sozlash</b>\n\n"
        f"Hozirgi holat: {status_str}\n\n"
        "<b>Qanday o'rnatish mumkin:</b>\n"
        "1. Shunchaki menga o'zingiz xohlagan <b>Premium Emojini</b> (stiker/emoji panelingizdan) yuboring.\n"
        "2. Yoki uning raqamli <b>Custom Emoji ID</b> sini yozib yuboring (masalan: <code>5368324170671202288</code>).\n"
        "3. Standart emojiga qaytarish uchun <code>0</code> yoki <code>reset</code> deb yozing.\n\n"
        "<i>Bekor qilish uchun pastdagi tugmani bosing yoki /cancel deb yozing.</i>"
    )
    await call.message.answer(text, parse_mode="HTML", reply_markup=cancel_kb)
    await call.answer()

@router.callback_query(F.data.startswith("set_emoji_id:"))
async def cb_set_emoji_id(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    parts = call.data.split(":")
    key = parts[1]
    eid = parts[2]
    meta = EMOJI_METADATA.get(key, {"name": key, "default": "✨"})

    await database.set_setting(f"emoji_{key}", eid)
    config.PREMIUM_EMOJIS[key] = eid

    await call.answer("Muvaffaqiyatli saqlandi! ✅", show_alert=False)

    confirm_text = (
        f"✅ <b>{meta['name']}</b> uchun Premium Emoji saqlandi!\n\n"
        f"🆔 <b>Emoji ID:</b> <code>{eid}</code>\n"
        f"Ko'rinishi: <tg-emoji emoji-id=\"{eid}\">{meta['default']}</tg-emoji>\n\n"
        "Endi ushbu bo'limdagi barcha yangiliklar ushbu premium emoji bilan e'lon qilinadi."
    )
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✨ Emojilar menyusiga qaytish", callback_data="admin_emojis_menu")
    ]])
    try:
        await call.message.edit_text(confirm_text, parse_mode="HTML", reply_markup=back_kb)
    except Exception:
        await call.message.answer(confirm_text, parse_mode="HTML", reply_markup=back_kb)

@router.callback_query(F.data.startswith("reset_emoji:"))
async def cb_reset_emoji(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    key = call.data.split(":")[1]
    meta = EMOJI_METADATA.get(key, {"name": key, "default": "✨"})
    await database.set_setting(f"emoji_{key}", "")
    config.PREMIUM_EMOJIS[key] = ""
    admin_emoji_edit_state.pop(call.from_user.id, None)

    await call.answer(f"{meta['name']} standart holatga qaytarildi ✅")
    text = render_emojis_panel_text()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_emojis_keyboard())
    except Exception:
        await call.message.answer(text, parse_mode="HTML", reply_markup=get_emojis_keyboard())

@router.callback_query(F.data == "reset_all_emojis")
async def cb_reset_all_emojis(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    for key in EMOJI_METADATA.keys():
        await database.set_setting(f"emoji_{key}", "")
        config.PREMIUM_EMOJIS[key] = ""

    await call.answer("Barcha emojilar standartga qaytarildi ✅", show_alert=True)
    text = render_emojis_panel_text()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_emojis_keyboard())
    except Exception:
        pass

@router.callback_query(F.data == "delete_message")
async def cb_delete_message(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()

# ==================== RSS MANBALARINI BOSHQARISH (FEED MANAGEMENT) ====================

@router.message(Command("feeds", "manbalar", "rss"))
async def cmd_feeds_list(message: Message):
    if not await check_is_admin(message.from_user.id):
        await message.reply("Kechirasiz, ushbu buyruq faqat bot ma'muri uchun.")
        return
    text = await render_feeds_panel_text()
    kb = await get_feeds_keyboard()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "admin_feeds_menu")
async def cb_admin_feeds_menu(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return
    text = await render_feeds_panel_text()
    kb = await get_feeds_keyboard()
    try:
        if call.message.text:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("toggle_feed:"))
async def cb_toggle_feed(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    slug = call.data.split(":")[1]
    feed_name = slug
    for src in config.RSS_SOURCES:
        if src.get("slug") == slug:
            feed_name = src["name"]
            break

    curr = await database.get_setting(f"feed_{slug}_enabled", "1")
    new_val = "0" if curr == "1" else "1"
    await database.set_setting(f"feed_{slug}_enabled", new_val)

    status_word = "yoqildi 🟢" if new_val == "1" else "o'chirildi 🔴"
    await call.answer(f"{feed_name} {status_word}", show_alert=False)

    text = await render_feeds_panel_text()
    kb = await get_feeds_keyboard()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

@router.callback_query(F.data.in_({"feed_all:enable", "feed_all:disable"}))
async def cb_toggle_all_feeds(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    new_val = "1" if call.data == "feed_all:enable" else "0"
    for src in config.RSS_SOURCES:
        slug = src.get("slug", src["name"].lower().replace(" ", "_"))
        await database.set_setting(f"feed_{slug}_enabled", new_val)

    alert_msg = "Barcha manbalar yoqildi! 🟢" if new_val == "1" else "Barcha manbalar o'chirildi! 🔴"
    await call.answer(alert_msg, show_alert=False)

    text = await render_feeds_panel_text()
    kb = await get_feeds_keyboard()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

# ==================== MODERATSIYA TUGMALARI ====================

@router.callback_query(F.data.startswith("pub:"))
async def cb_publish_post(call: CallbackQuery, bot: Bot):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    post_id = int(call.data.split(":")[1])
    post = await database.get_post(post_id)
    if not post or post["status"] == "published":
        await call.answer("Ushbu post allaqachon chiqarilgan yoki topilmadi!", show_alert=True)
        return

    now_str = datetime.now(timezone.utc).isoformat()
    final_caption = attach_source_link(post["uz_caption"], post.get("source_name", "IT"), post.get("url", ""))
    photo_caption = safe_telegram_caption(final_caption, config.CHANNEL_FOOTER, max_len=1024)
    text_message = safe_telegram_caption(final_caption, config.CHANNEL_FOOTER, max_len=4096)

    try:
        channel_kb = await get_channel_post_keyboard(post_id=post_id, original_url=post.get("url"))
        sent_successfully = False
        if post.get("image_url"):
            sent_successfully = await send_media_robust(
                bot=bot,
                chat_id=config.CHANNEL_ID,
                media_url=post["image_url"],
                caption=photo_caption,
                reply_markup=channel_kb
            )

        if not sent_successfully:
            await bot.send_message(
                chat_id=config.CHANNEL_ID,
                text=text_message,
                parse_mode="HTML",
                reply_markup=channel_kb
            )

        await database.set_post_status(post_id, "published", published_at=now_str)
        await call.answer("Kanalga muvaffaqiyatli chiqarildi! 🚀", show_alert=False)

        full_done = f"✅ <b>KANALGA CHIQARILDI!</b>\n\n{post['uz_caption']}"
        done_photo_caption = safe_telegram_caption(full_done, max_len=1024)
        if call.message.caption:
            await call.message.edit_caption(
                caption=done_photo_caption,
                parse_mode="HTML",
                reply_markup=None
            )
        else:
            await call.message.edit_text(
                text=full_done[:4096],
                parse_mode="HTML",
                reply_markup=None
            )

        await call.message.answer(
            f"🚀 <b>Post muvaffaqiyatli kanalga chiqarildi!</b>\n\n"
            f"📢 Kanal: <b>{config.CHANNEL_USERNAME}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        err_msg = str(e)
        detailed_cause = ""
        solution = ""

        if "bot is not a member" in err_msg.lower() or "forbidden" in err_msg.lower():
            detailed_cause = (
                f"⚠️ <b>Aniq sababi:</b> Botingiz <code>{config.CHANNEL_USERNAME}</code> kanalida a'zo yoki Administrator emas."
            )
            solution = (
                f"🛠 <b>Qanday hal qilish kerak:</b>\n"
                f"1. Telegramda <b>{config.CHANNEL_USERNAME}</b> kanaliga kiring.\n"
                "2. <b>Kanal sozlamalari ➡️ Administrators (Adminlar)</b> bo'limiga kiring.\n"
                "3. <b>Admin qo'shish</b> tugmasini bosib, botingizni qidiring.\n"
                "4. Botga <b>'Xabarlar yuborish' (Post Messages)</b> huquqini berib, saqlang."
            )
        elif "chat not found" in err_msg.lower():
            detailed_cause = (
                f"⚠️ <b>Aniq sababi:</b> Kanal ID (<code>{config.CHANNEL_ID}</code>) topilmadi."
            )
            solution = (
                "🛠 <b>Qanday hal qilish kerak:</b> Bot kanalga qo'shilmagan bo'lsa Telegram ushbu kanalni ko'rmaydi. Botni kanalga admin qilib qo'shing."
            )
        elif "rights" in err_msg.lower() or "not enough" in err_msg.lower():
            detailed_cause = (
                "⚠️ <b>Aniq sababi:</b> Botingiz kanalda admin, lekin unda <b>'Post Messages' (Xabar yozish)</b> ruxsati berilmagan."
            )
            solution = (
                "🛠 <b>Qanday hal qilish kerak:</b> Kanal adminlari ro'yxatidan botni ochib, 'Xabarlar yuborish' huquqini yoqing."
            )
        elif "caption_too_long" in err_msg.lower():
            detailed_cause = "⚠️ <b>Aniq sababi:</b> Xabar matni Telegram belgilagan 1024 ta belgidan oshib ketgan."
            solution = "🛠 <b>Qanday hal qilish kerak:</b> 🔄 'Qayta yozdirish' tugmasini bosing, AI uni qisqartirib beradi."
        else:
            detailed_cause = f"⚠️ <b>Aniq sababi:</b> <code>{err_msg}</code>"
            solution = "🛠 <b>Qanday hal qilish kerak:</b> Bot va kanal sozlamalarini tekshiring."

        error_report = (
            "❌ <b>KANALGA CHIQARISHDA XATOLIK YUZ BERDI!</b>\n\n"
            f"{detailed_cause}\n\n"
            f"{solution}\n\n"
            f"<i>Texnik xatolik: {err_msg}</i>"
        )
        await call.answer("Xatolik yuz berdi!", show_alert=True)
        await call.message.answer(error_report, parse_mode="HTML")

@router.callback_query(F.data.startswith("delay:"))
async def cb_delay_post(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    parts = call.data.split(":")
    post_id = int(parts[1])
    minutes = int(parts[2])

    post = await database.get_post(post_id)
    if not post:
        await call.answer("Post topilmadi!", show_alert=True)
        return

    sched_time = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    await database.set_post_status(post_id, "scheduled", scheduled_at=sched_time)

    await call.answer(f"Post {minutes} daqiqaga rejalashtirildi! ⏳")
    status_text = f"⏳ <b>REJALASHTIRILDI (+{minutes} daqiqa):</b>\n\n{post['uz_caption']}"

    if call.message.caption:
        await call.message.edit_caption(caption=status_text[:1024], parse_mode="HTML", reply_markup=None)
    else:
        await call.message.edit_text(text=status_text[:4096], parse_mode="HTML", reply_markup=None)

@router.callback_query(F.data.startswith("reject:"))
async def cb_reject_post(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    post_id = int(call.data.split(":")[1])
    await database.set_post_status(post_id, "rejected")
    await call.answer("Post bekor qilindi ❌")

    status_text = "❌ <b>BEKOR QILINDI</b> (Kanalga chiqarilmaydi)."
    if call.message.caption:
        await call.message.edit_caption(caption=status_text, parse_mode="HTML", reply_markup=None)
    else:
        await call.message.edit_text(text=status_text, parse_mode="HTML", reply_markup=None)

@router.callback_query(F.data.startswith("regen:"))
async def cb_regen_post(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    post_id = int(call.data.split(":")[1])
    post = await database.get_post(post_id)
    if not post:
        await call.answer("Post topilmadi!", show_alert=True)
        return

    await call.answer("Gemini matnni qayta yozmoqda... ⏳")

    new_caption = await regenerate_news(
        post["title"],
        post.get("original_summary", ""),
        post["uz_caption"]
    )

    await database.update_post_caption(post_id, new_caption)

    preview_text = f"🔄 <b>QAYTA YOZILGAN VARIANT:</b>\n\n{new_caption}"
    if call.message.caption:
        await call.message.edit_caption(
            caption=preview_text[:1024],
            parse_mode="HTML",
            reply_markup=get_moderation_keyboard(post_id)
        )
    else:
        await call.message.edit_text(
            text=preview_text[:4096],
            parse_mode="HTML",
            reply_markup=get_moderation_keyboard(post_id)
        )

@router.callback_query(F.data.startswith("edit_draft:") | F.data.startswith("q_edit:"))
async def cb_start_edit_draft(call: CallbackQuery):
    """Admin uchun post matnini qo'lda tahrirlash rejimini yoqish"""
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    parts = call.data.split(":")
    action = parts[0]
    post_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    origin = "queue" if action == "q_edit" else "moderation"

    post = await database.get_post(post_id)
    if not post or post["status"] == "published":
        await call.answer("Post topilmadi yoki allaqachon chiqarilgan!", show_alert=True)
        return

    admin_edit_draft_state[call.from_user.id] = {
        "post_id": post_id,
        "origin": origin,
        "page": page,
        "chat_id": call.message.chat.id,
        "message_id": call.message.message_id
    }

    await call.answer("Tahrirlash rejimi faollashdi ✏️")

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔴 ❌ Tahrirlashni bekor qilish", callback_data=f"cancel_edit_draft:{post_id}")
    ]])

    intro_text = (
        f"✏️ <b>Post #{post_id} matnini tahrirlash:</b>\n\n"
        "1. Pastdagi maxsus blokdagi matn ustiga bosing (avtomatik nusxalanadi).\n"
        "2. Kerakli o'zgartirishlarni kiritib, <b>botga yangi xabar qilib yuboring</b>.\n\n"
        "💡 <i>Formatlash:</i> <code>&lt;b&gt;qalin&lt;/b&gt;</code>, <code>&lt;i&gt;kursiv&lt;/i&gt;</code>, havolalar yoki Telegram formatlash menyusi.\n"
        "<i>(Postda rasm bo'lsa, Telegram rasm izohi 1024 belgidan oshmasligi kerak.)</i>\n\n"
        "Bekor qilish uchun: /cancel deb yozing yoki quyidagi tugmani bosing:"
    )

    await call.message.answer(intro_text, parse_mode="HTML", reply_markup=cancel_kb)

    raw_caption = post.get("uz_caption", "")
    escaped_for_pre = html.escape(raw_caption)
    if len(escaped_for_pre) > 3900:
        escaped_for_pre = escaped_for_pre[:3900] + "..."

    await call.message.answer(
        f"📋 <b>Post matni (nusxalash uchun ustiga bosing):</b>\n\n<pre><code>{escaped_for_pre}</code></pre>",
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("cancel_edit_draft:"))
async def cb_cancel_edit_draft(call: CallbackQuery):
    """Post tahrirlashni bekor qilish"""
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    admin_edit_draft_state.pop(call.from_user.id, None)
    await call.answer("Tahrirlash bekor qilindi ❌")
    try:
        if call.message.caption:
            await call.message.edit_caption(caption="❌ <b>Postni tahrirlash bekor qilindi.</b>", parse_mode="HTML", reply_markup=None)
        else:
            await call.message.edit_text("❌ <b>Postni tahrirlash bekor qilindi.</b>", parse_mode="HTML", reply_markup=None)
    except Exception:
        pass

# ==================== HAR QANDAY KELGAN XABARNI TUTISH ====================

@router.message()
async def handle_incoming_messages(message: Message, bot: Bot):
    """Har qanday foydalanuvchini bazaga yozish va adminga kelgan reklama xabarini tutish"""
    await database.register_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )

    admin_id = message.from_user.id
    is_admin = await check_is_admin(admin_id)

    # Maxsus Telegram Premium Custom Emoji entitiylarini qidirish
    custom_emojis = []
    if message.entities:
        for ent in message.entities:
            if ent.type == "custom_emoji" and ent.custom_emoji_id:
                if ent.custom_emoji_id not in custom_emojis:
                    custom_emojis.append(ent.custom_emoji_id)

    # 0. Agar admin post tahrirlash holatida bo'lsa:
    if is_admin and admin_id in admin_edit_draft_state:
        edit_info = admin_edit_draft_state.get(admin_id, {})
        post_id = edit_info.get("post_id")
        origin = edit_info.get("origin", "moderation")
        page = edit_info.get("page", 0)

        # Bekor qilish tekshiruvi
        if message.text and message.text.strip().lower() in ["/cancel", "bekor qilish"]:
            admin_edit_draft_state.pop(admin_id, None)
            await message.answer("❌ Postni tahrirlash bekor qilindi.")
            return

        post = await database.get_post(post_id)
        if not post or post["status"] == "published":
            admin_edit_draft_state.pop(admin_id, None)
            await message.answer("⚠️ Post bazada topilmadi yoki allaqachon kanalga chiqarilgan.")
            return

        # Yangi rasm yuborilgan bo'lsa
        new_image_url = None
        if message.photo:
            new_image_url = message.photo[-1].file_id

        # Matn va entitiylarni aniqlash
        formatting_entity_types = {
            "bold", "italic", "underline", "strikethrough", "code", "pre",
            "text_link", "custom_emoji", "blockquote"
        }
        has_entities = False
        if message.entities and any(e.type in formatting_entity_types for e in message.entities):
            has_entities = True
        elif message.caption_entities and any(e.type in formatting_entity_types for e in message.caption_entities):
            has_entities = True

        if has_entities:
            new_caption = message.html_text or (message.caption or "")
        else:
            new_caption = (message.text or message.caption or "").strip()

        if not new_caption:
            await message.answer("⚠️ Iltimos, post uchun yangi matnni yozing yoki /cancel buyrug'ini bosing.")
            return

        effective_image = new_image_url or post.get("image_url")
        has_image = bool(effective_image)

        # Ko'rinadigan matn uzunligini tekshirish
        visible_text = re.sub(r'<[^>]+>', '', new_caption)
        if has_image and len(visible_text) > 1024:
            await message.answer(
                f"⚠️ <b>Matn juda uzun ({len(visible_text)} ta belgi)!</b>\n\n"
                "Ushbu postda rasm mavjud. Telegram rasm ostidagi izohga ko'pi bilan <b>1024</b> ta belgiga ruxsat beradi.\n"
                "Iltimos, matnni biroz qisqartirib qaytadan yuboring (yoki bekor qilish uchun /cancel deb yozing).",
                parse_mode="HTML"
            )
            return
        elif not has_image and len(visible_text) > 4096:
            await message.answer(
                f"⚠️ <b>Matn uzunligi {len(visible_text)} ta belgi!</b>\n"
                "Telegram bitta xabarda ko'pi bilan 4096 ta belgiga ruxsat beradi. Iltimos qisqartirib yuboring.",
                parse_mode="HTML"
            )
            return

        # Klaviatura tayyorlash
        if origin == "queue":
            preview_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🟢 🚀 Hoziroq kanalga chiqarish", callback_data=f"q_pub:{post_id}:{page}")],
                [
                    InlineKeyboardButton(text="✏️ Yana tahrirlash", callback_data=f"q_edit:{post_id}:{page}"),
                    InlineKeyboardButton(text="🗑 Navbatdan o'chirish", callback_data=f"q_del:{post_id}:{page}")
                ],
                [InlineKeyboardButton(text="⬅️ Navbat ro'yxatiga qaytish", callback_data=f"admin_queue:{page}")]
            ])
        else:
            preview_kb = get_moderation_keyboard(post_id)

        header_badge = f"✏️ <b>TAHRIRLANGAN POST #{post_id} (PREVIEW):</b>\n\n"
        full_preview_text = header_badge + new_caption

        # Telegram HTML parse qila olishini tekshirish
        try:
            if has_image:
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=effective_image,
                    caption=safe_telegram_caption(full_preview_text, max_len=1024),
                    parse_mode="HTML",
                    reply_markup=preview_kb
                )
            else:
                await bot.send_message(
                    chat_id=admin_id,
                    text=safe_telegram_caption(full_preview_text, max_len=4096),
                    parse_mode="HTML",
                    reply_markup=preview_kb
                )
        except TelegramBadRequest as tbe:
            err_text = str(tbe)
            if "can't parse entities" in err_text.lower():
                await message.answer(
                    "⚠️ <b>HTML teglarda sintaktik xatolik aniqlandi!</b>\n\n"
                    f"Telegram xatoligi: <code>{html.escape(err_text)}</code>\n\n"
                    "Iltimos, ochiq qolgan teglarni (masalan <code>&lt;b&gt;...&lt;/b&gt;</code>) to'g'rilab qaytadan yuboring.\n"
                    "Bekor qilish uchun: /cancel",
                    parse_mode="HTML"
                )
                return
            else:
                await message.answer(
                    f"⚠️ Xatolik: <code>{html.escape(err_text)}</code>\nIltimos, qaytadan urinib ko'ring yoki /cancel bosing.",
                    parse_mode="HTML"
                )
                return
        except Exception as e:
            await message.answer(
                f"⚠️ Kutilmagan xatolik: <code>{html.escape(str(e))}</code>\nIltimos, qaytadan yuboring yoki /cancel bosing.",
                parse_mode="HTML"
            )
            return

        # Bazasida saqlash
        await database.update_post_content(post_id, new_caption, image_url=new_image_url)
        admin_edit_draft_state.pop(admin_id, None)

        await message.answer(
            f"✅ <b>Post #{post_id} matni muvaffaqiyatli yangilandi va saqlandi!</b>\n\n"
            "Yuqoridagi yangilangan preview ostidagi tugmalar orqali uni hoziroq kanalga chiqarishingiz yoki navbatda qoldirishingiz mumkin.",
            parse_mode="HTML"
        )
        return

    # 1. Agar admin reklama yuborish holatida bo'lsa:
    if admin_broadcast_state.get(admin_id) == "waiting_message":
        if message.text and message.text.strip().lower() in ["/cancel", "bekor qilish"]:
            admin_broadcast_state.pop(admin_id, None)
            admin_broadcast_pending.pop(admin_id, None)
            await message.answer("❌ Reklama yuborish bekor qilindi.")
            return

        # Holatni tozalaymiz va xabarni saqlaymiz
        admin_broadcast_state.pop(admin_id, None)
        admin_broadcast_pending[admin_id] = {
            "chat_id": message.chat.id,
            "message_id": message.message_id
        }

        # Adminga oldindan ko'rish (PREVIEW) yuborish
        await message.answer("🔍 <b>XABARINGIZNING PREVIEW (KO'RINISHI):</b>", parse_mode="HTML")
        await bot.copy_message(
            chat_id=admin_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )

        u_stats = await database.get_users_count()
        confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🟢 ✅ Barcha mijozlarga jo'natish ({u_stats['active']} ta)", callback_data="confirm_broadcast")],
            [InlineKeyboardButton(text="🔴 ❌ Bekor qilish", callback_data="cancel_broadcast")]
        ])

        await message.answer(
            f"👆 <b>Ushbu xabar barcha mijozlarga 1:1 ko'rinishda (barcha premium emojilar va formatlar bilan) yetkaziladi.</b>\n\n"
            f"👥 Qabul qiluvchilar: <b>{u_stats['active']} ta faol mijoz</b> (Jami bazada: {u_stats['total']} ta)\n\n"
            "Tasdiqlaysizmi?",
            parse_mode="HTML",
            reply_markup=confirm_kb
        )
        return

    # 2. Agar admin Premium Emoji sozlash holatida bo'lsa:
    if is_admin and admin_id in admin_emoji_edit_state:
        if message.text and message.text.strip().lower() in ["/cancel", "bekor qilish"]:
            admin_emoji_edit_state.pop(admin_id, None)
            await message.answer("❌ Emoji sozlash bekor qilindi.")
            return

        target_key = admin_emoji_edit_state.pop(admin_id)
        meta = EMOJI_METADATA.get(target_key, {"name": target_key, "default": "✨"})
        new_eid = ""

        if custom_emojis:
            new_eid = str(custom_emojis[0]).strip()
        elif message.text and message.text.strip().isdigit():
            new_eid = message.text.strip()
        elif message.text and message.text.strip().lower() in ["0", "reset", "standart"]:
            new_eid = ""
        else:
            await message.answer(
                "⚠️ Iltimos, Telegram Premium Emoji yuboring yoki uning raqamli ID sini yozing (masalan: <code>5368324170671202288</code>).\n"
                "Bekor qilish uchun /cancel deb yozing.",
                parse_mode="HTML"
            )
            admin_emoji_edit_state[admin_id] = target_key
            return

        await database.set_setting(f"emoji_{target_key}", new_eid)
        config.PREMIUM_EMOJIS[target_key] = new_eid

        back_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✨ Emojilar menyusi", callback_data="admin_emojis_menu")
        ]])

        if new_eid:
            await message.answer(
                f"✅ <b>{meta['name']}</b> uchun Premium Emoji saqlandi!\n\n"
                f"🆔 <b>Emoji ID:</b> <code>{new_eid}</code>\n"
                f"Ko'rinishi: <tg-emoji emoji-id=\"{new_eid}\">{meta['default']}</tg-emoji>",
                parse_mode="HTML",
                reply_markup=back_kb
            )
        else:
            await message.answer(
                f"✅ <b>{meta['name']}</b> uchun standart {meta['default']} emojisi tiklandi.",
                parse_mode="HTML",
                reply_markup=back_kb
            )
        return

    # 3. Agar admin oddiy holatda botga Premium Emoji yuborsa (ID aniqlash va 1-bosishda biriktirish):
    if is_admin and custom_emojis:
        for eid in custom_emojis[:2]:
            detect_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⚡️ Chaqmoq (Sarlavha)", callback_data=f"set_emoji_id:lightning:{eid}"),
                    InlineKeyboardButton(text="📌 Mohiyati", callback_data=f"set_emoji_id:pin:{eid}")
                ],
                [
                    InlineKeyboardButton(text="📊 Tafsilotlar", callback_data=f"set_emoji_id:chart:{eid}"),
                    InlineKeyboardButton(text="💡 Xulosa", callback_data=f"set_emoji_id:bulb:{eid}")
                ],
                [
                    InlineKeyboardButton(text="📡 Manba", callback_data=f"set_emoji_id:antenna:{eid}"),
                    InlineKeyboardButton(text="🏷 Hashtag", callback_data=f"set_emoji_id:tag:{eid}")
                ],
                [
                    InlineKeyboardButton(text="❌ Kerak emas (Yopish)", callback_data="delete_message")
                ]
            ])
            text = (
                "✨ <b>Telegram Premium Custom Emoji aniqlandi!</b>\n\n"
                f"🆔 <b>Emoji ID:</b> <code>{eid}</code> <i>(nusxalash uchun bosing)</i>\n"
                f"🔗 <b>Telegram Link:</b> <code>tg://emoji?id={eid}</code>\n"
                f"Ko'rinishi: <tg-emoji emoji-id=\"{eid}\">✨</tg-emoji>\n\n"
                "<b>Ushbu emojini kanal xabarlarining qaysi bo'limiga o'rnatmoqchisiz?</b>"
            )
            await message.answer(text, parse_mode="HTML", reply_markup=detect_kb)
        return

    # 4. Agar foydalanuvchi Qidiruv holatida bo'lsa:
    if user_search_state.get(message.from_user.id):
        user_search_state.pop(message.from_user.id, None)
        query = (message.text or "").strip()
        if not query:
            await message.answer("Iltimos, matnli kalit so'z yozing.")
            return

        posts = await database.search_posts(query, limit=5)
        if posts:
            lines = [f"🔍 <b>'{query}' bo'yicha topilgan yangiliklar:</b>\n"]
            kb_rows = []
            for i, p in enumerate(posts, 1):
                src = p.get('source_name', 'IT')
                lines.append(f"{i}️⃣ <b>{p['title']}</b> <i>({src})</i>\n")
                kb_rows.append([InlineKeyboardButton(text=f"📖 {i}. {p['title'][:40]}...", callback_data=f"user_post:{p['id']}")])
            
            kb_rows.append([
                InlineKeyboardButton(text="💬 AI dan so'rash", callback_data=f"ai_ask_inline:{query[:40]}"),
                InlineKeyboardButton(text=f"📢 {config.CHANNEL_USERNAME}", url=config.CHANNEL_LINK)
            ])
            await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
            return
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 AI Maslahatchidan so'rash", callback_data=f"ai_ask_inline:{query[:40]}")],
                [InlineKeyboardButton(text=f"📢 {config.CHANNEL_USERNAME} ga a'zo bo'lish", url=config.CHANNEL_LINK)]
            ])
            await message.answer(
                f"🔍 <i>'{query}'</i> mavzusi bo'yicha bazada hozircha yangilik topilmadi.\n\n"
                "Ushbu savolni <b>NeuraTech AI Maslahatchi</b>ga berishingiz mumkin:",
                parse_mode="HTML",
                reply_markup=kb
            )
            return

    # 3. Agar foydalanuvchi AI Maslahatchi holatida bo'lsa (umumiy yoki maqola bo'yicha):
    if message.from_user.id in user_ai_state:
        state_data = user_ai_state.pop(message.from_user.id, None)
        query = (message.text or "").strip()
        if not query:
            await message.answer("Iltimos, savolingizni matn ko'rinishida yozing.")
            return

        article_context = None
        article_title = ""
        post_id = None
        if isinstance(state_data, dict):
            article_context = state_data.get("context")
            article_title = state_data.get("title", "")
            post_id = state_data.get("post_id")

        header_prefix = f"🧠 <i>«{article_title[:45]}...» tahlil qilinmoqda...</i>" if article_title else "🧠 <i>NeuraTech AI javob tayyorlamoqda...</i>"
        wait_msg = await message.answer(header_prefix, parse_mode="HTML")
        answer = await answer_ai_assistant_question(query, article_context=article_context)
        try:
            await wait_msg.delete()
        except Exception:
            pass

        kb_rows = []
        if post_id:
            kb_rows.append([
                InlineKeyboardButton(text="💬 Shu mavzuda yana savol berish", callback_data=f"ai_discuss_again:{post_id}"),
                InlineKeyboardButton(text="📖 Maqolani o'qish", callback_data=f"user_post:{post_id}")
            ])
        else:
            kb_rows.append([InlineKeyboardButton(text="💬 Yana savol berish", callback_data="ai_ask_again")])

        kb_rows.append([
            InlineKeyboardButton(text=f"📢 {config.CHANNEL_USERNAME}", url=config.CHANNEL_LINK),
            InlineKeyboardButton(text="⬅️ Bosh menyu", callback_data="user_refresh_top")
        ])

        reply_kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        await message.answer(
            f"🤖 <b>NeuraTech AI:</b>\n\n{answer}",
            parse_mode="HTML",
            reply_markup=reply_kb
        )
        return

    # 4. Oddiy matn kelsa (foydalanuvchi to'g'ridan-to'g'ri so'z yoki savol yozsa):
    if message.text and len(message.text.strip()) >= 2:
        query = message.text.strip()
        # Avval bazadan qidirib ko'ramiz
        posts = await database.search_posts(query, limit=3)
        if posts:
            lines = [f"🔍 <b>'{query}' bo'yicha topilgan xabarlar:</b>\n"]
            kb_rows = []
            for i, p in enumerate(posts, 1):
                src = p.get('source_name', 'IT')
                lines.append(f"{i}️⃣ <b>{p['title']}</b> <i>({src})</i>\n")
                kb_rows.append([InlineKeyboardButton(text=f"📖 {i}. {p['title'][:40]}...", callback_data=f"user_post:{p['id']}")])
            kb_rows.append([
                InlineKeyboardButton(text="💬 AI Maslahatchidan so'rash", callback_data=f"ai_ask_inline:{query[:40]}")
            ])
            await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
            return

        # Agar qidiruvdan chiqmasa va savolga o'xshasa (yoki 8+ belgi bo'lsa)
        if "?" in query or len(query) >= 8:
            wait_msg = await message.answer("🧠 <i>NeuraTech AI tahlil qilmoqda...</i>", parse_mode="HTML")
            answer = await answer_ai_assistant_question(query)
            try:
                await wait_msg.delete()
            except Exception:
                pass
            reply_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Yana savol berish", callback_data="ai_ask_again")],
                [InlineKeyboardButton(text=f"📢 {config.CHANNEL_USERNAME}", url=config.CHANNEL_LINK)]
            ])
            await message.answer(
                f"🤖 <b>NeuraTech AI:</b>\n\n{answer}",
                parse_mode="HTML",
                reply_markup=reply_kb
            )
            return

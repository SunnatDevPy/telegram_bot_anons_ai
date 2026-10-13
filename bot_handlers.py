import asyncio
from datetime import datetime, timedelta, timezone
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command

import config
import database
from ai_formatter import regenerate_news

router = Router()

# Reklama yuborish holatlarini saqlash
admin_broadcast_state = {}    # {admin_id: "waiting_message"}
admin_broadcast_pending = {}  # {admin_id: {"chat_id": int, "message_id": int}}

def get_admin_keyboard(mode: str, interval: int) -> InlineKeyboardMarkup:
    """Admin boshqaruv paneli tugmalari (Asosiy harakatlar yashil rangda)"""
    mode_btn_text = "🟢 Rejim: Avtomatik (Faol)" if mode == "auto" else "🟡 Rejim: Moderatsiya (Tasdiqlash)"
    toggle_mode = "moderation" if mode == "auto" else "auto"

    kb = [
        [InlineKeyboardButton(text=mode_btn_text, callback_data=f"set_mode:{toggle_mode}")],
        [
            InlineKeyboardButton(text="⏱ 15 min", callback_data="set_interval:15"),
            InlineKeyboardButton(text="⏱ 30 min", callback_data="set_interval:30"),
            InlineKeyboardButton(text="⏱ 60 min", callback_data="set_interval:60"),
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

def get_moderation_keyboard(post_id: int) -> InlineKeyboardMarkup:
    """Moderatsiya uchun har bir post ostidagi tugmalar (Asosiy chiqarish tugmasi yashil belgi bilan)"""
    kb = [
        [InlineKeyboardButton(text="🟢 🚀 Hoziroq kanalga chiqarish", callback_data=f"pub:{post_id}")],
        [
            InlineKeyboardButton(text="⏳ +30 daqiqa", callback_data=f"delay:{post_id}:30"),
            InlineKeyboardButton(text="⏳ +1 soat", callback_data=f"delay:{post_id}:60")
        ],
        [InlineKeyboardButton(text="🔄 Qayta yozdirish (AI)", callback_data=f"regen:{post_id}")],
        [InlineKeyboardButton(text="🗑 Bekor qilish (O'chirish)", callback_data=f"reject:{post_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_user_reply_keyboard() -> ReplyKeyboardMarkup:
    """Oddiy obunachilar (mijozlar) uchun pastki menyu tugmalari"""
    kb = [
        [KeyboardButton(text="🟢 🔥 Top 10 ta AI Yangilik")],
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
    stats = await database.get_stats()
    gemini_status = "🟢 Ulangan (Gemini 3.8 Flash)" if config.GEMINI_API_KEY else "🔴 Kalit kiritilmagan (.env)"

    mode_display = "🟢 Avtomatik (Kanalga to'g'ridan-to'g'ri)" if mode == "auto" else "🟡 Moderatsiya (Tasdiqlash bilan)"

    text = (
        "🤖 <b>Grand Mobile Anons — IT & AI Yangiliklar Boshqaruvi</b>\n\n"
        f"⚙️ <b>Sozlamalar:</b>\n"
        f"• <b>Rejim:</b> {mode_display}\n"
        f"• <b>Vaqt oralig'i:</b> Har {interval} daqiqada\n"
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

    is_admin = await check_is_admin(message.from_user.id)
    if is_admin:
        text = await format_panel_text()
        mode = await database.get_setting("mode", "moderation")
        interval = int(await database.get_setting("interval_minutes", "25"))
        await message.answer(
            text, parse_mode="HTML",
            reply_markup=get_admin_keyboard(mode, interval)
        )
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
    text = await format_panel_text()
    mode = await database.get_setting("mode", "moderation")
    interval = int(await database.get_setting("interval_minutes", "25"))
    await message.answer(text, parse_mode="HTML", reply_markup=get_admin_keyboard(mode, interval))

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
        try:
            await message.answer_photo(
                photo=post["image_url"],
                caption=caption[:1024],
                parse_mode="HTML",
                reply_markup=back_kb
            )
            return
        except Exception:
            pass

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

@router.callback_query(F.data.startswith("user_post:"))
async def cb_user_post(call: CallbackQuery):
    await database.register_user(call.from_user.id, call.from_user.username, call.from_user.first_name, call.from_user.last_name)
    post_id = int(call.data.split(":")[1])
    post = await database.get_post(post_id)
    if not post:
        await call.answer("Xabar topilmadi!", show_alert=True)
        return

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Top 10 ro'yxatiga qaytish", callback_data="user_refresh_top")],
        [InlineKeyboardButton(text=f"📢 {config.CHANNEL_USERNAME} ga a'zo bo'lish", url=config.CHANNEL_LINK)]
    ])

    await call.answer()
    caption = post["uz_caption"]

    if post.get("image_url"):
        try:
            await call.message.answer_photo(
                photo=post["image_url"],
                caption=caption[:1024],
                parse_mode="HTML",
                reply_markup=back_kb
            )
            return
        except Exception:
            pass

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
    
    text = await format_panel_text()
    interval = int(await database.get_setting("interval_minutes", "25"))
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_keyboard(new_mode, interval))

@router.callback_query(F.data.startswith("set_interval:"))
async def cb_set_interval(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    new_interval = int(call.data.split(":")[1])
    await database.set_setting("interval_minutes", str(new_interval))
    await call.answer(f"Interval o'rnatildi: Har {new_interval} daqiqada")

    text = await format_panel_text()
    mode = await database.get_setting("mode", "moderation")
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_keyboard(mode, new_interval))

@router.callback_query(F.data == "manual_check")
async def cb_manual_check(call: CallbackQuery, bot: Bot):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    await call.answer("🔍 Yangiliklar tekshirilmoqda...", show_alert=False)
    from scheduler import process_news_cycle
    count = await process_news_cycle(bot)
    
    text = await format_panel_text()
    mode = await database.get_setting("mode", "moderation")
    interval = int(await database.get_setting("interval_minutes", "25"))
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_keyboard(mode, interval))
    except Exception:
        pass
    await call.message.answer(f"✅ Tekshiruv yakunlandi! Yangi topilgan xabarlar: <b>{count}</b> ta.", parse_mode="HTML")

@router.callback_query(F.data == "refresh_panel")
async def cb_refresh_panel(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        await call.answer()
        return

    text = await format_panel_text()
    mode = await database.get_setting("mode", "moderation")
    interval = int(await database.get_setting("interval_minutes", "25"))
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_keyboard(mode, interval))
    except Exception:
        pass
    await call.answer("Yangilandi ✅")

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

    caption = post["uz_caption"] + config.CHANNEL_FOOTER
    now_str = datetime.now(timezone.utc).isoformat()

    try:
        sent_successfully = False
        if post.get("image_url"):
            try:
                await bot.send_photo(
                    chat_id=config.CHANNEL_ID,
                    photo=post["image_url"],
                    caption=caption[:1024],
                    parse_mode="HTML"
                )
                sent_successfully = True
            except Exception as img_err:
                err_lower = str(img_err).lower()
                if any(x in err_lower for x in ["forbidden", "chat not found", "rights", "member", "blocked"]):
                    raise img_err
                print(f"[Rasm yuklanmadi, matn yuborilmoqda]: {img_err}")

        if not sent_successfully:
            await bot.send_message(
                chat_id=config.CHANNEL_ID,
                text=caption[:4096],
                parse_mode="HTML"
            )

        await database.set_post_status(post_id, "published", published_at=now_str)
        await call.answer("Kanalga muvaffaqiyatli chiqarildi! 🚀", show_alert=False)

        full_done = f"✅ <b>KANALGA CHIQARILDI!</b>\n\n{post['uz_caption']}"
        if call.message.caption:
            await call.message.edit_caption(
                caption=full_done[:1024],
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
    # Agar admin reklama yuborish holatida bo'lsa:
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

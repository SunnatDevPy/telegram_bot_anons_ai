import io
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from typing import Tuple, Dict, Any, Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile

import config
import database

def create_safe_sqlite_backup() -> Tuple[bytes, str, int, int]:
    """
    ACID xavfsiz SQLite zaxira nusxasini olish va ZIP formatida siqish.
    Qaytaradi: (zip_bytes, backup_filename, orig_size_bytes, zip_size_bytes)
    """
    now_t = datetime.now(config.TASHKENT_TZ)
    timestamp = now_t.strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"neuratech_backup_{timestamp}.zip"

    # 1. Tranzaksion to'liq nusxa olish uchun vaqtinchalik fayl ochamiz
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
        tmp_db_path = tmp_file.name

    try:
        # SQLite online backup API yordamida jonli va xavfsiz nusxa yaratamiz
        src_conn = sqlite3.connect(str(config.DB_PATH))
        dest_conn = sqlite3.connect(tmp_db_path)
        src_conn.backup(dest_conn)
        dest_conn.close()
        src_conn.close()

        orig_size = os.path.getsize(tmp_db_path)

        # 2. Nusxani ZIP formatida maksimal darajada siqamiz
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.write(tmp_db_path, arcname="bot_data.db")

        zip_bytes = zip_buffer.getvalue()
        zip_size = len(zip_bytes)

        return zip_bytes, backup_filename, orig_size, zip_size
    finally:
        # Vaqtinchalik faylni tozalash
        if os.path.exists(tmp_db_path):
            try:
                os.remove(tmp_db_path)
            except Exception:
                pass

async def send_database_backup(
    bot: Bot,
    admin_id: int,
    is_automatic: bool = False
) -> bool:
    """
    Baza zaxira nusxasini tayyorlash va uni adminga Telegram orqali yuborish
    """
    try:
        zip_bytes, filename, orig_size, zip_size = create_safe_sqlite_backup()
        stats = await database.get_stats()
        now_t = datetime.now(config.TASHKENT_TZ)

        orig_kb = orig_size / 1024
        zip_kb = zip_size / 1024
        ratio = ((orig_size - zip_size) / orig_size * 100) if orig_size > 0 else 0

        mode_label = "🌙 <b>Kunlik Avto-Zaxira (03:00)</b>" if is_automatic else "⚡️ <b>Qo'lda so'ralgan Zaxira Nusxasi</b>"

        caption = (
            f"💾 <b>NeuraTech AI Bot — Baza Zaxira Nusxasi (Backup)</b>\n\n"
            f"📌 <b>Turi:</b> {mode_label}\n"
            f"🕒 <b>Vaqt:</b> <code>{now_t.strftime('%Y-%m-%d %H:%M:%S')}</code> (Toshkent)\n"
            f"📦 <b>Fayl:</b> <code>{filename}</code>\n"
            f"📊 <b>Hajmi:</b> {orig_kb:.1f} KB ➡️ <b>{zip_kb:.1f} KB</b> (<i>-{ratio:.0f}% siqildi</i>)\n\n"
            f"👥 <b>Foydalanuvchilar:</b> {stats.get('users_total', 0)} ta (Faol: {stats.get('users_active', 0)})\n"
            f"📝 <b>Postlar:</b> {stats.get('total', 0)} ta (Chiqarilgan: {stats.get('published', 0)}, Navbatda: {stats.get('scheduled', 0)})\n\n"
            f"💡 <i>Tiklash uchun: arxiv ichidagi <code>bot_data.db</code> faylini loyiha papkasiga joylang.</i>"
        )

        doc = BufferedInputFile(zip_bytes, filename=filename)
        await bot.send_document(
            chat_id=admin_id,
            document=doc,
            caption=caption,
            parse_mode="HTML"
        )
        await database.set_setting("last_backup_timestamp", now_t.strftime("%Y-%m-%d %H:%M"))
        return True
    except Exception as e:
        print(f"[Backup Yuborish Xatolik]: {e}")
        return False

async def check_daily_backup(bot: Bot):
    """
    Har kecha Toshkent vaqti bilan soat 03:00 da (config.BACKUP_HOUR) adminga avtomatik zaxira nusxasini yuborish
    """
    now_t = datetime.now(config.TASHKENT_TZ)
    backup_hour = getattr(config, "BACKUP_HOUR", 3)
    if now_t.hour != backup_hour:
        return

    today_str = now_t.strftime("%Y-%m-%d")
    last_backup = await database.get_setting("last_daily_backup_date", "")
    if last_backup == today_str:
        return

    raw_admin = await database.get_setting("admin_id")
    admin_id = int(raw_admin) if raw_admin and raw_admin.isdigit() else config.INITIAL_ADMIN_ID
    if not admin_id:
        return

    success = await send_database_backup(bot, admin_id=admin_id, is_automatic=True)
    if success:
        await database.set_setting("last_daily_backup_date", today_str)
        print(f"[Kunlik Avto-Backup] Baza nusxasi adminga ({admin_id}) muvaffaqiyatli yetkazildi! 💾")

if __name__ == "__main__":
    zip_bytes, fname, orig, compressed = create_safe_sqlite_backup()
    print(f"OK: {fname} | Orig: {orig} bytes | Zip: {compressed} bytes")

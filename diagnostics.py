import os
import sys
import shutil
import platform
import asyncio
import html
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from aiogram import Bot
from google import genai
from google.genai import types

import config
import database

BOT_START_TIME = datetime.now(timezone.utc)

# Alert yuborish oraliqlarini nazorat qilish (spam bo'lmasligi uchun)
# {alert_type: datetime}
_LAST_ALERT_TIMES: Dict[str, datetime] = {}

# Gemini kalitlari holatini keshlab turish (har bir `/status` da API kvotani yeb qo'ymaslik uchun 3 daqiqa)
_GEMINI_KEY_CACHE: Dict[str, Any] = {
    "checked_at": None,
    "statuses": []
}

def get_uptime_str() -> str:
    """Botning ishga tushganidan beri o'tgan vaqtini o'zbekcha chiroyli formatda qaytarish"""
    delta = datetime.now(timezone.utc) - BOT_START_TIME
    total_seconds = int(delta.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days} kun")
    if hours > 0:
        parts.append(f"{hours} soat")
    parts.append(f"{minutes} daqiqa")
    return ", ".join(parts)

def get_ram_usage_mb() -> str:
    """Linux konteyneri yoki tizim operativ xotira (RAM) sarfini aniqlash"""
    # 1. Linux cgroup v2 (Docker da eng aniq)
    cgroup_v2 = "/sys/fs/cgroup/memory.current"
    if os.path.exists(cgroup_v2):
        try:
            with open(cgroup_v2, "r") as f:
                bytes_used = int(f.read().strip())
                return f"{bytes_used / (1024 * 1024):.1f} MB"
        except Exception:
            pass

    # 2. Linux cgroup v1
    cgroup_v1 = "/sys/fs/cgroup/memory/memory.usage_in_bytes"
    if os.path.exists(cgroup_v1):
        try:
            with open(cgroup_v1, "r") as f:
                bytes_used = int(f.read().strip())
                return f"{bytes_used / (1024 * 1024):.1f} MB"
        except Exception:
            pass

    # 3. Linux /proc/self/status (VmRSS)
    proc_status = "/proc/self/status"
    if os.path.exists(proc_status):
        try:
            with open(proc_status, "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        kb = int(parts[1])
                        return f"{kb / 1024:.1f} MB"
        except Exception:
            pass

    return "N/A"

def get_disk_free_gb() -> str:
    """Diskdagi bo'sh joyni aniqlash"""
    try:
        total, used, free = shutil.disk_usage(".")
        return f"{free / (1024**3):.1f} GB"
    except Exception:
        return "N/A"

def get_db_size_str() -> str:
    """SQLite baza fayli hajmi"""
    if config.DB_PATH.exists():
        bytes_size = os.path.getsize(config.DB_PATH)
        if bytes_size > 1024 * 1024:
            return f"{bytes_size / (1024 * 1024):.2f} MB"
        return f"{bytes_size / 1024:.1f} KB"
    return "0 KB"

async def check_gemini_keys_health(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Gemini API kalitlarini tezkor tekshirish (hovuz statistikasi va jonli holat)"""
    from ai_formatter import GEMINI_POOL
    pool_reports = {r["index"]: r for r in GEMINI_POOL.get_status_report()}

    global _GEMINI_KEY_CACHE
    now = datetime.now(timezone.utc)
    cached_at = _GEMINI_KEY_CACHE.get("checked_at")

    if not force_refresh and cached_at and (now - cached_at).total_seconds() < 180:
        cached_statuses = _GEMINI_KEY_CACHE.get("statuses", [])
        for s in cached_statuses:
            p_data = pool_reports.get(s["index"])
            if p_data:
                s["requests"] = p_data.get("requests", 0)
                s["success"] = p_data.get("success", 0)
                if p_data.get("cooldown"):
                    s["status"] = p_data["status"]
        return cached_statuses

    keys = config.get_gemini_api_keys()
    statuses = []
    for idx, key in enumerate(keys, 1):
        masked = key[:6] + "..." + key[-4:] if len(key) > 10 else f"Key-{idx}"
        p_data = pool_reports.get(idx, {})
        req_cnt = p_data.get("requests", 0)
        succ_cnt = p_data.get("success", 0)

        if p_data.get("cooldown"):
            status_str = p_data.get("status", "🟡 Sovutishda (429)")
        elif succ_cnt > 0:
            status_str = f"🟢 Faol ({succ_cnt} ta post)"
        elif idx <= len(GEMINI_POOL.nodes) and GEMINI_POOL.nodes[idx - 1].client is not None:
            status_str = "🟢 Faol (Tayyor)"
        else:
            status_str = "🔴 Ulanmagan"

        statuses.append({
            "index": idx,
            "masked": masked,
            "status": status_str,
            "requests": req_cnt,
            "success": succ_cnt
        })

    _GEMINI_KEY_CACHE = {
        "checked_at": now,
        "statuses": statuses
    }
    return statuses

async def get_system_health(bot: Bot, force_refresh: bool = False) -> Dict[str, Any]:
    """Barcha tizim ko'rsatkichlarini to'plash"""
    mode = await database.get_setting("mode", "moderation")
    interval = await database.get_setting("interval_minutes", "25")
    stats = await database.get_stats()

    # Oxirgi chop etilgan post
    last_pub = await database.get_last_published_post()
    last_pub_info = "Hozircha post yo'q"
    if last_pub and last_pub.get("published_at"):
        try:
            dt = datetime.fromisoformat(last_pub["published_at"])
            mins_ago = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
            if mins_ago < 60:
                time_ago_str = f"{mins_ago} daqiqa oldin"
            else:
                time_ago_str = f"{mins_ago // 60} soat, {mins_ago % 60} daqiqa oldin"
            safe_title = html.escape(last_pub.get("title", "")[:35])
            last_pub_info = f"«{safe_title}...» (<i>{time_ago_str}</i>)"
        except Exception:
            pass

    # Navbatdagi (Scheduled) postlar
    due_posts = await database.get_due_scheduled_posts()
    next_sched_time_str = "Navbat bo'sh"
    last_sched_str = await database.get_last_scheduled_time()

    async with database.get_db() as db:
        db.row_factory = database.aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM posts WHERE status = 'scheduled'"
        ) as cursor:
            row = await cursor.fetchone()
            scheduled_count = row["cnt"] if row else 0

        # Eng yaqin navbatdagi post vaqti
        async with db.execute(
            "SELECT scheduled_at, title FROM posts WHERE status = 'scheduled' ORDER BY scheduled_at ASC LIMIT 1"
        ) as cursor:
            next_row = await cursor.fetchone()
            if next_row and next_row["scheduled_at"]:
                try:
                    s_dt = datetime.fromisoformat(next_row["scheduled_at"]).astimezone(config.TASHKENT_TZ)
                    next_sched_time_str = s_dt.strftime("%H:%M")
                except Exception:
                    pass

    gemini_statuses = await check_gemini_keys_health(force_refresh=force_refresh)

    return {
        "uptime": get_uptime_str(),
        "tashkent_time": datetime.now(config.TASHKENT_TZ).strftime("%H:%M:%S"),
        "mode": "🟢 Avtomatik" if mode == "auto" else "🟡 Moderatsiya",
        "interval": interval,
        "channel_username": config.CHANNEL_USERNAME,
        "channel_id": config.CHANNEL_ID,
        "ram_usage": get_ram_usage_mb(),
        "disk_free": get_disk_free_gb(),
        "db_size": get_db_size_str(),
        "total_posts": stats.get("total", 0),
        "published_posts": stats.get("published", 0),
        "scheduled_count": scheduled_count,
        "next_sched_time": next_sched_time_str,
        "last_pub_info": last_pub_info,
        "gemini_statuses": gemini_statuses,
        "users_active": stats.get("users_active", 0),
    }

def format_health_html(data: Dict[str, Any]) -> str:
    """Diagnostika hisobotini chiroyli Telegram HTML ko'rinishida formatlash"""
    keys_lines = []
    for k in data["gemini_statuses"]:
        req_info = f" [⚡️ {k.get('success', 0)} ta post]" if k.get('success', 0) > 0 else ""
        keys_lines.append(f"  • Kalit #{k['index']} (<code>{k['masked']}</code>): {k['status']}{req_info}")
    keys_text = "\n".join(keys_lines) if keys_lines else "  • Kalitlar topilmadi"

    text = (
        "📊 <b>NeuraTech AI Bot — Salomatlik va Diagnostika</b>\n\n"
        f"🕒 <b>Tizim va Uptime:</b>\n"
        f"• <b>Faol vaqti (Uptime):</b> {data['uptime']}\n"
        f"• <b>Server vaqti:</b> {data['tashkent_time']} (Toshkent vaqti)\n"
        f"• <b>Ish rejimi:</b> {data['mode']} (Oraliq: {data['interval']} min)\n"
        f"• <b>Rasmiy kanal:</b> <code>{data['channel_id']}</code> ({data['channel_username']})\n\n"
        f"🤖 <b>Google Gemini AI Kalitlari (Multi-Key):</b>\n"
        f"{keys_text}\n\n"
        f"📡 <b>Yangiliklar & Kanal Oqimi:</b>\n"
        f"• <b>Oxirgi post:</b> {data['last_pub_info']}\n"
        f"• <b>Navbatda (Queue):</b> <b>{data['scheduled_count']}</b> ta post kutilmoqda\n"
        f"• <b>Eng yaqin post vaqti:</b> Soat {data['next_sched_time']} da\n"
        f"• <b>Chiqarilgan jami postlar:</b> {data['published_posts']} ta\n\n"
        f"💾 <b>Server Resurslari & Baza:</b>\n"
        f"• <b>Operativ xotira (RAM):</b> {data['ram_usage']}\n"
        f"• <b>Ma'lumotlar bazasi (SQLite):</b> {data['db_size']} ({data['total_posts']} ta post)\n"
        f"• <b>Disk bo'sh joyi:</b> {data['disk_free']} mavjud\n"
        f"• <b>Bot obunachilari:</b> {data['users_active']} ta foydalanuvchi\n\n"
        "✅ <i>Barcha komponentlar barqaror nazorat ostida.</i>"
    )
    return text

async def send_admin_alert(
    bot: Bot,
    message: str,
    alert_type: str = "general",
    cooldown_minutes: int = 30
) -> bool:
    """
    Adminga tezkor tizimli ogohlantirish (Alert) yuborish.
    Spam bo'lmasligi uchun har bir alert_type bo'yicha belgilangan cooldown_minutes kutiladi.
    """
    now = datetime.now(timezone.utc)
    last_sent = _LAST_ALERT_TIMES.get(alert_type)

    if last_sent and (now - last_sent).total_seconds() < (cooldown_minutes * 60):
        return False

    raw_admin = await database.get_setting("admin_id")
    admin_id = int(raw_admin) if raw_admin and raw_admin.isdigit() else config.INITIAL_ADMIN_ID
    if not admin_id:
        return False

    formatted_alert = (
        "🚨 <b>NEURATECH TIZIM OGOHLANTIRISHI</b>\n"
        f"⏰ <b>Vaqt:</b> {datetime.now(config.TASHKENT_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{message}\n\n"
        "<i>Holatni tekshirish uchun: /status</i>"
    )

    try:
        await bot.send_message(
            chat_id=admin_id,
            text=formatted_alert,
            parse_mode="HTML"
        )
        _LAST_ALERT_TIMES[alert_type] = now
        return True
    except Exception as e:
        print(f"[Alert Yuborish Xatolik]: {e}")
        return False

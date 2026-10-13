import aiosqlite
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
import config

def get_db():
    return aiosqlite.connect(config.DB_PATH)

async def init_db():
    """Baza jadvallarini yaratish va boshlang'ich sozlamalarni kiritish"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                original_summary TEXT,
                uz_caption TEXT NOT NULL,
                image_url TEXT,
                source_name TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                scheduled_at TEXT,
                published_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)

        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('mode', ?)",
            (config.DEFAULT_MODE,)
        )
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('interval_minutes', ?)",
            (str(config.CHECK_INTERVAL_MINUTES),)
        )
        if config.INITIAL_ADMIN_ID:
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_id', ?)",
                (str(config.INITIAL_ADMIN_ID),)
            )

        await db.commit()

async def get_setting(key: str, default: Any = None) -> Any:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row["value"]
            return default

async def set_setting(key: str, value: Any):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value))
        )
        await db.commit()

def normalize_url(url: str) -> str:
    """URL dan tracking parametrlarini olib tashlash"""
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

def normalize_title(title: str) -> str:
    """Sarlavhadan tinish belgilari va ortiqcha so'zlarni olib tashlash"""
    import re
    cleaned = re.sub(r'[^\w\s]', '', title.lower())
    return " ".join(cleaned.split())

async def is_url_exists(url: str) -> bool:
    clean_u = normalize_url(url)
    async with get_db() as db:
        async with db.execute("SELECT 1 FROM posts WHERE url LIKE ?", (f"%{clean_u}%",)) as cursor:
            row = await cursor.fetchone()
            return row is not None

async def is_duplicate_news(url: str, title: str) -> bool:
    """Bir xil URL yoki bir xil mavzudagi yangilik qayta chiqmasligini tekshirish"""
    # 1. URL bo'yicha tekshirish
    if await is_url_exists(url):
        return True

    # 2. Sarlavhalar o'xshashligini tekshirish (oxirgi 60 ta post bo'yicha)
    norm_t = normalize_title(title)
    words = set(norm_t.split())
    if len(words) <= 3:
        return False

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT title FROM posts ORDER BY id DESC LIMIT 60") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                existing_norm = normalize_title(row["title"])
                existing_words = set(existing_norm.split())
                if len(existing_words) > 3:
                    overlap = len(words & existing_words)
                    similarity = overlap / min(len(words), len(existing_words))
                    if similarity >= 0.65:
                        # 65% dan ortiq o'xshash bo'lsa - bu ayni bitta yangilik
                        return True

    return False

async def create_post(
    url: str,
    title: str,
    original_summary: str,
    uz_caption: str,
    image_url: Optional[str],
    source_name: str,
    status: str = "draft",
    scheduled_at: Optional[str] = None
) -> int:
    now_str = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO posts (
                url, title, original_summary, uz_caption, image_url,
                source_name, status, created_at, scheduled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (url, title, original_summary, uz_caption, image_url, source_name, status, now_str, scheduled_at))
        await db.commit()
        return cursor.lastrowid

async def get_post(post_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

async def update_post_caption(post_id: int, new_caption: str):
    async with get_db() as db:
        await db.execute("UPDATE posts SET uz_caption = ? WHERE id = ?", (new_caption, post_id))
        await db.commit()

async def set_post_status(
    post_id: int,
    status: str,
    scheduled_at: Optional[str] = None,
    published_at: Optional[str] = None
):
    async with get_db() as db:
        updates = ["status = ?"]
        params = [status]
        if scheduled_at is not None:
            updates.append("scheduled_at = ?")
            params.append(scheduled_at)
        if published_at is not None:
            updates.append("published_at = ?")
            params.append(published_at)
        params.append(post_id)

        query = f"UPDATE posts SET {', '.join(updates)} WHERE id = ?"
        await db.execute(query, tuple(params))
        await db.commit()

async def get_due_scheduled_posts() -> List[Dict[str, Any]]:
    now_str = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM posts 
            WHERE status = 'scheduled' AND scheduled_at <= ?
            ORDER BY scheduled_at ASC
        """, (now_str,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def register_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None
):
    """Foydalanuvchini bazaga qo'shish yoki faollashtirish"""
    now_str = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                is_active = 1
        """, (user_id, username, first_name, last_name, now_str))
        await db.commit()

async def get_active_users() -> List[int]:
    """Barcha faol mijozlar (obunachilar) Telegram ID ro'yxatini olish"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM users WHERE is_active = 1") as cursor:
            rows = await cursor.fetchall()
            return [r["user_id"] for r in rows]

async def set_user_active(user_id: int, is_active: bool):
    """Foydalanuvchi botni bloklaganida uni faolsizlantirish"""
    val = 1 if is_active else 0
    async with get_db() as db:
        await db.execute("UPDATE users SET is_active = ? WHERE user_id = ?", (val, user_id))
        await db.commit()

async def get_users_count() -> Dict[str, int]:
    """Mijozlar soni statistikasi"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT 
                COUNT(*) as total,
                COALESCE(SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END), 0) as active
            FROM users
        """) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"total": int(row["total"] or 0), "active": int(row["active"] or 0)}
            return {"total": 0, "active": 0}

async def get_stats() -> Dict[str, int]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT status, COUNT(*) as cnt FROM posts GROUP BY status") as cursor:
            rows = await cursor.fetchall()
            stats = {
                "total": 0,
                "published": 0,
                "draft": 0,
                "scheduled": 0,
                "rejected": 0,
                "users_total": 0,
                "users_active": 0
            }
            for row in rows:
                st = row["status"]
                cnt = row["cnt"]
                stats["total"] += cnt
                if st in stats:
                    stats[st] = cnt

        u_cnt = await get_users_count()
        stats["users_total"] = u_cnt["total"]
        stats["users_active"] = u_cnt["active"]
        return stats

async def get_top_news(limit: int = 10) -> List[Dict[str, Any]]:
    """Foydalanuvchilar (mijozlar) uchun top yangiliklar ro'yxatini olish"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # Avval kanalga chiqarilgan eng so'nggi xabarlarni olamiz
        async with db.execute("""
            SELECT * FROM posts 
            WHERE status = 'published' 
            ORDER BY id DESC LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            if len(rows) >= 3:
                return [dict(r) for r in rows]

        # Agar hali kanalga ko'p chiqmagan bo'lsa, eng so'nggi tasdiqlangan/tayyor postlarni olamiz
        async with db.execute("""
            SELECT * FROM posts 
            WHERE status != 'rejected'
            ORDER BY id DESC LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

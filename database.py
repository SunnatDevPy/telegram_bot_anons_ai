import json
import sqlite3
import aiosqlite
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
import config

def get_db():
    return aiosqlite.connect(config.DB_PATH)

async def init_db():
    """Baza jadvallarini yaratish va boshlang'ich sozlamalarni kiritish"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        # WAL rejimi: bir vaqtda o'qish va yozish imkonini beradi (scheduler + admin panel
        # parallel ishlaganda "database is locked" xatolarining oldini oladi)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000")

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

        await db.execute("""
            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                options_json TEXT NOT NULL,
                poll_type TEXT NOT NULL DEFAULT 'regular',
                correct_option_id INTEGER,
                explanation TEXT,
                topic TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                published_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_token_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_index INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        # Multi-Channel SaaS jadvallari: boshqa foydalanuvchilarning ulangan kanallari
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                channel_id TEXT UNIQUE NOT NULL,
                channel_title TEXT NOT NULL,
                channel_username TEXT,
                topics TEXT NOT NULL DEFAULT '["ai", "gadgets"]',
                tone TEXT NOT NULL DEFAULT 'standard',
                posts_per_day INTEGER NOT NULL DEFAULT 3,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_posted_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_channel_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_channel_id INTEGER NOT NULL,
                post_id INTEGER NOT NULL,
                delivered_at TEXT NOT NULL,
                UNIQUE(user_channel_id, post_id)
            )
        """)

        # Tez-tez ishlatiladigan so'rovlar uchun indekslar (baza o'sganda sekinlashuvni oldini oladi)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_posts_scheduled ON posts(status, scheduled_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_posts_source ON posts(source_name)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_posts_published ON posts(status, published_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_tokens_created ON ai_token_logs(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ai_tokens_model ON ai_token_logs(model_name)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_channels_owner ON user_channels(owner_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_channels_active ON user_channels(is_active)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_ch ON user_channel_deliveries(user_channel_id)")

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
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('channel_buttons', ?)",
            ("1" if getattr(config, "ENABLE_CHANNEL_BUTTONS", True) else "0",)
        )
        if getattr(config, "ENABLE_CHANNEL_SOURCE_BUTTON", True):
            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('channel_source_btn', '1')")
        if getattr(config, "ENABLE_CHANNEL_AI_BUTTON", True):
            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('channel_ai_btn', '1')")
        await db.commit()

    await migrate_settings_keys()
    await load_emojis_to_config()

async def migrate_settings_keys():
    """
    Bir martalik migratsiya: eski kod versiyasidan qolgan noto'g'ri nomlangan
    (feed_enabled_<slug>) kalitlarni joriy formatga (feed_<slug>_enabled) ko'chirish.
    Bu "schema drift"ni oldini oladi va bazada yetim kalit qoldirmaydi.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT key, value FROM settings") as cursor:
            rows = await cursor.fetchall()

        legacy = {}
        for row in rows:
            key = row["key"]
            if key.startswith("feed_enabled_"):
                slug = key[len("feed_enabled_"):]
                legacy[slug] = row["value"]

        for slug, value in legacy.items():
            new_key = f"feed_{slug}_enabled"
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (new_key, value)
            )
            await db.execute("DELETE FROM settings WHERE key = ?", (f"feed_enabled_{slug}",))

        if legacy:
            await db.commit()
            print(f"[DB Migratsiya] {len(legacy)} ta eski RSS sozlama kaliti yangi formatga ko'chirildi.")

async def load_emojis_to_config():
    """Bazadagi saqlangan Premium Emojilarni config.PREMIUM_EMOJIS ga yuklash"""
    keys = ["lightning", "pin", "chart", "bulb", "antenna", "tag"]
    for k in keys:
        val = await get_setting(f"emoji_{k}")
        if val is not None and str(val).strip():
            config.PREMIUM_EMOJIS[k] = str(val).strip()

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

STOP_WORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "up", "about", "into", "over", "after", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must",
    "it", "its", "it's", "that", "this", "these", "those", "reportedly", "says",
    "new", "first", "so", "more", "no", "just", "allegedly", "how", "what", "why"
}

def extract_core_keywords(text: str) -> set:
    """Sarlavhadan asosiy kalit so'zlarni ajratib olish (stemming va stop-so'zlarni olib tashlash)"""
    import re
    cleaned = re.sub(r'[^\w\s]', ' ', text.lower())
    words = cleaned.split()
    core = set()
    for w in words:
        if len(w) <= 2 or w in STOP_WORDS:
            continue
        stem = w
        if stem.endswith("ing") and len(stem) > 5:
            stem = stem[:-3]
        elif stem.endswith("ed") and len(stem) > 4:
            stem = stem[:-2]
        elif stem.endswith("s") and len(stem) > 3 and not stem.endswith("ss"):
            stem = stem[:-1]
        core.add(stem)
    return core

def normalize_url(url: str) -> str:
    """URL dan tracking parametrlarini olib tashlash va domen/pathni tozalash"""
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc.lower().replace("www.", "")
    path = parsed.path.rstrip("/")
    return f"{netloc}{path}".lower()

def escape_like(value: str) -> str:
    """
    SQL LIKE operatori uchun maxsus belgilarni (% va _) ekranlaydi.
    Busiz foydalanuvchi kiritgan yoki URL'dagi '_' belgisi wildcard sifatida
    talqin qilinib, bog'liq bo'lmagan yozuvlar bilan noto'g'ri moslik berishi mumkin.
    """
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

async def is_url_exists(url: str) -> bool:
    clean_u = normalize_url(url)
    if not clean_u:
        return False
    slug = clean_u.split("/")[-1] if "/" in clean_u else clean_u
    async with get_db() as db:
        if len(slug) >= 12:
            query = "SELECT 1 FROM posts WHERE url LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\'"
            params = (f"%{escape_like(clean_u)}%", f"%{escape_like(slug)}%")
        else:
            query = "SELECT 1 FROM posts WHERE url LIKE ? ESCAPE '\\'"
            params = (f"%{escape_like(clean_u)}%",)
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return row is not None

async def is_duplicate_news(url: str, title: str) -> bool:
    """Bir xil URL yoki bir xil mavzudagi yangilik qayta chiqmasligini tekshirish"""
    # 1. URL bo'yicha tekshirish
    if await is_url_exists(url):
        return True

    # 2. Sarlavhalardagi asosiy kalit so'zlar bo'yicha tekshirish (oxirgi 100 ta post)
    core_new = extract_core_keywords(title)
    if len(core_new) <= 2:
        return False

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT title, url FROM posts ORDER BY id DESC LIMIT 100") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                existing_core = extract_core_keywords(row["title"])
                if len(existing_core) <= 2:
                    continue
                common = core_new & existing_core
                similarity = len(common) / min(len(core_new), len(existing_core))
                # Agar 3 ta kalit so'z mos kelsa yoki o'xshashlik 45% dan yuqori bo'lsa
                if len(common) >= 3 and similarity >= 0.45:
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
    scheduled_at: Optional[str] = None,
    published_at: Optional[str] = None
) -> Optional[int]:
    """Yangi xabarni bazaga kiritish (URL takrorlansa IntegrityError bermaydi)"""
    now_str = datetime.now(timezone.utc).isoformat()
    pub_at = published_at if published_at else (now_str if status == "published" else None)
    async with get_db() as db:
        try:
            cursor = await db.execute("""
                INSERT INTO posts (
                    url, title, original_summary, uz_caption, image_url,
                    source_name, status, created_at, scheduled_at, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (url, title, original_summary, uz_caption, image_url, source_name, status, now_str, scheduled_at, pub_at))
            await db.commit()
            return cursor.lastrowid
        except (sqlite3.IntegrityError, aiosqlite.IntegrityError):
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id FROM posts WHERE url = ?", (url,)) as cur:
                row = await cur.fetchone()
                return row["id"] if row else None

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

async def update_post_content(post_id: int, new_caption: str, image_url: Optional[str] = None):
    """Postning matni va ixtiyoriy ravishda rasmini yangilash"""
    async with get_db() as db:
        if image_url is not None:
            await db.execute("UPDATE posts SET uz_caption = ?, image_url = ? WHERE id = ?", (new_caption, image_url, post_id))
        else:
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

async def get_last_published_post() -> Optional[Dict[str, Any]]:
    """Kanalga eng oxirgi chiqarilgan postni olish"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM posts 
            WHERE status = 'published' AND published_at IS NOT NULL
            ORDER BY published_at DESC LIMIT 1
        """) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_last_scheduled_time() -> Optional[str]:
    """Hozir navbatda turgan eng oxirgi kelgusi postning vaqtini olish"""
    now_str = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT scheduled_at FROM posts 
            WHERE status = 'scheduled' AND scheduled_at > ?
            ORDER BY scheduled_at DESC LIMIT 1
        """, (now_str,)) as cursor:
            row = await cursor.fetchone()
            return row["scheduled_at"] if row else None

async def get_scheduled_posts(limit: int = 5, offset: int = 0) -> List[Dict[str, Any]]:
    """Navbatdagi barcha rejalashtirilgan postlarni vaqti bo'yicha olish"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM posts 
            WHERE status = 'scheduled'
            ORDER BY scheduled_at ASC
            LIMIT ? OFFSET ?
        """, (limit, offset)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_scheduled_count() -> int:
    """Navbatda turgan postlar soni"""
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM posts WHERE status = 'scheduled'") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def cancel_scheduled_post(post_id: int) -> bool:
    """Postni navbatdan bekor qilish (status='rejected')"""
    async with get_db() as db:
        await db.execute("UPDATE posts SET status = 'rejected' WHERE id = ?", (post_id,))
        await db.commit()
        return True

async def clear_all_scheduled_posts() -> int:
    """Barcha navbatda turgan postlarni bekor qilish va navbatni tozalash"""
    async with get_db() as db:
        cursor = await db.execute("UPDATE posts SET status = 'rejected' WHERE status = 'scheduled'")
        count = cursor.rowcount
        await db.commit()
        return count

async def cancel_stale_scheduled_posts(
    max_future_hours: int = 4,
    max_overdue_hours: int = 2,
    max_queue_limit: int = 6
) -> int:
    """
    Rejalashtirilgan vaqti eskirgan, muddati 2 soatdan ortiq o'tib ketgan,
    juda uzoqqa (4+ soat) cho'zilgan yoki navbat limitidan oshgan postlarni avtomatik bekor qilish.
    """
    now = datetime.now(timezone.utc)
    future_threshold = (now + timedelta(hours=max_future_hours)).isoformat()
    past_threshold = (now - timedelta(hours=max_overdue_hours)).isoformat()
    total_cancelled = 0

    async with get_db() as db:
        # 1. 2 soatdan ortiq o'tib ketgan (overdue) yoki 4 soatdan keyinga rejalanganlarni bekor qilish
        cursor = await db.execute(
            "UPDATE posts SET status = 'rejected' WHERE status = 'scheduled' AND (scheduled_at > ? OR scheduled_at < ?)",
            (future_threshold, past_threshold)
        )
        total_cancelled += cursor.rowcount

        # 2. Agar navbatda hali ham max_queue_limit dan ortiq post qolgan bo'lsa:
        # Eng yaqin max_queue_limit tasini saqlab, eskirib qolgan ortiqcha postlarni bekor qilish
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM posts WHERE status = 'scheduled' ORDER BY scheduled_at ASC") as cur:
            rows = await cur.fetchall()
            if len(rows) > max_queue_limit:
                excess_ids = [r["id"] for r in rows[max_queue_limit:]]
                placeholders = ",".join("?" for _ in excess_ids)
                await db.execute(f"UPDATE posts SET status = 'rejected' WHERE id IN ({placeholders})", excess_ids)
                total_cancelled += len(excess_ids)

        await db.commit()
    return total_cancelled

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

async def search_posts(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Kalit so'z bo'yicha bazadan yangiliklarni qidirish"""
    clean_q = query.strip()
    if not clean_q:
        return []
    pattern = f"%{escape_like(clean_q)}%"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM posts 
            WHERE status != 'rejected' AND (title LIKE ? ESCAPE '\\' OR uz_caption LIKE ? ESCAPE '\\' OR original_summary LIKE ? ESCAPE '\\')
            ORDER BY id DESC LIMIT ?
        """, (pattern, pattern, pattern, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_today_published_posts(date_prefix: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Bugun e'lon qilingan yangiliklarni olish (Dayjest uchun)"""
    pattern = f"{date_prefix}%"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM posts 
            WHERE status = 'published' AND published_at LIKE ?
            ORDER BY id DESC LIMIT ?
        """, (pattern, limit)) as cursor:
            rows = await cursor.fetchall()
            if len(rows) >= 2:
                return [dict(r) for r in rows]

        # Agar bugungi sana bilan kamida 2 ta post topilmasa, kanalga chiqarilgan eng so'nggi postlarni olamiz
        async with db.execute("""
            SELECT * FROM posts 
            WHERE status = 'published'
            ORDER BY id DESC LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def cleanup_old_posts(days: int = 45, draft_days: int = 7) -> int:
    """Eski postlarni tozalash va disk hajmini siqish (joy to'lib ketmasligi uchun). Avtomatik rad etilgan draft postlar sonini qaytaradi."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rejected_cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    draft_cutoff = (datetime.now(timezone.utc) - timedelta(days=draft_days)).isoformat()
    stale_draft_count = 0
    try:
        async with get_db() as db:
            # 1. 14 kundan oshgan bekor qilingan (rejected) xabarlarni o'chirish
            await db.execute("DELETE FROM posts WHERE status = 'rejected' AND created_at < ?", (rejected_cutoff,))
            # 2. Muddatdan oshgan tasdiqlanmagan (draft) xabarlarni "rejected" holatiga o'tkazish.
            #    Aks holda admin ko'rmay qolgan qoralamalar bazada abadiy osilib qoladi.
            cursor = await db.execute(
                "UPDATE posts SET status = 'rejected' WHERE status = 'draft' AND created_at < ?",
                (draft_cutoff,)
            )
            stale_draft_count = cursor.rowcount
            # 3. 45 kundan oshgan arxiv postlarni tozalash
            await db.execute("DELETE FROM posts WHERE status = 'published' AND created_at < ?", (cutoff,))
            await db.commit()
            # 4. Disk hajmini bo'shatish (VACUUM)
            await db.execute("VACUUM")
            await db.commit()
    except Exception as e:
        print(f"[DB Cleanup Xatolik]: {e}")
    return stale_draft_count

async def get_feed_post_counts() -> Dict[str, int]:
    """Har bir RSS manbasidan bazaga kiritilgan xabarlar sonini olish"""
    async with get_db() as db:
        async with db.execute("SELECT source_name, COUNT(*) as cnt FROM posts GROUP BY source_name") as cursor:
            rows = await cursor.fetchall()
            return {r[0]: r[1] for r in rows if r[0]}

# ==================== SO'ROVNOMALAR (POLLS & QUIZZES) ====================

async def create_poll(
    question: str,
    options: List[str],
    poll_type: str = "regular",
    correct_option_id: Optional[int] = None,
    explanation: Optional[str] = None,
    topic: Optional[str] = None,
    status: str = "draft"
) -> int:
    """Yangi AI so'rovnoma yoki viktorinani bazaga saqlash"""
    now_str = datetime.now(timezone.utc).isoformat()
    options_json = json.dumps(options, ensure_ascii=False)
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO polls (question, options_json, poll_type, correct_option_id, explanation, topic, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (question, options_json, poll_type, correct_option_id, explanation, topic, status, now_str))
        await db.commit()
        return cursor.lastrowid

async def get_poll(poll_id: int) -> Optional[Dict[str, Any]]:
    """So'rovnomani ID bo'yicha olish"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["options"] = json.loads(data.get("options_json", "[]"))
            except Exception:
                data["options"] = []
            return data

async def update_poll_data(
    poll_id: int,
    question: str,
    options: List[str],
    poll_type: str = "regular",
    correct_option_id: Optional[int] = None,
    explanation: Optional[str] = None
):
    """Mavjud so'rovnoma ma'lumotlarini yangilash (qayta generatsiya qilinganda)"""
    options_json = json.dumps(options, ensure_ascii=False)
    async with get_db() as db:
        await db.execute("""
            UPDATE polls
            SET question = ?, options_json = ?, poll_type = ?, correct_option_id = ?, explanation = ?
            WHERE id = ?
        """, (question, options_json, poll_type, correct_option_id, explanation, poll_id))
        await db.commit()

async def set_poll_status(poll_id: int, status: str, published_at: Optional[str] = None):
    """So'rovnoma holatini o'zgartirish (published / rejected)"""
    async with get_db() as db:
        if published_at:
            await db.execute("UPDATE polls SET status = ?, published_at = ? WHERE id = ?", (status, published_at, poll_id))
        else:
            await db.execute("UPDATE polls SET status = ? WHERE id = ?", (status, poll_id))
        await db.commit()

# ==================== KENGAYTIRILGAN STATISTIKA (ANALYTICS) ====================

async def get_detailed_statistics() -> Dict[str, Any]:
    """
    Admin uchun kengaytirilgan statistika:
    - Foydalanuvchilar: jami, faol, bloklagan, bugun / 7 kun / 30 kun ichida qo'shilganlar
    - Postlar: jami, chiqarilgan, navbatda, qoralama, bekor qilingan
    - Davrlar kesimida: bugun, oxirgi 7 kun, oxirgi 30 kunda chiqarilgan postlar
    - Manbalar reytingi: qaysi manbadan nechta post chiqqan va jami nechta olingan
    - So'rovnomalar (Polls): jami va kanalga chiqarilganlar soni
    """
    now_t = datetime.now(config.TASHKENT_TZ)
    today_start_t = now_t.replace(hour=0, minute=0, second=0, microsecond=0)
    today_iso = today_start_t.astimezone(timezone.utc).isoformat()
    week_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    month_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    result: Dict[str, Any] = {
        "users_total": 0,
        "users_active": 0,
        "users_blocked": 0,
        "users_today": 0,
        "users_week": 0,
        "users_month": 0,
        "posts_total": 0,
        "published_total": 0,
        "scheduled_total": 0,
        "draft_total": 0,
        "rejected_total": 0,
        "published_today": 0,
        "published_week": 0,
        "published_month": 0,
        "polls_total": 0,
        "polls_published": 0,
        "top_sources": [],
        "last_published": None
    }

    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        # 1. Foydalanuvchilar statistikasi
        async with db.execute("""
            SELECT
                COUNT(*) as total,
                COALESCE(SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END), 0) as active,
                COALESCE(SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END), 0) as blocked,
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) as today,
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) as week,
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) as month
            FROM users
        """, (today_iso, week_iso, month_iso)) as cur:
            u_row = await cur.fetchone()
            if u_row:
                result["users_total"] = int(u_row["total"] or 0)
                result["users_active"] = int(u_row["active"] or 0)
                result["users_blocked"] = int(u_row["blocked"] or 0)
                result["users_today"] = int(u_row["today"] or 0)
                result["users_week"] = int(u_row["week"] or 0)
                result["users_month"] = int(u_row["month"] or 0)

        # 2. Postlar status bo'yicha
        async with db.execute("SELECT status, COUNT(*) as cnt FROM posts GROUP BY status") as cur:
            for r in await cur.fetchall():
                st = r["status"]
                cnt = int(r["cnt"] or 0)
                result["posts_total"] += cnt
                if st == "published":
                    result["published_total"] = cnt
                elif st == "scheduled":
                    result["scheduled_total"] = cnt
                elif st == "draft":
                    result["draft_total"] = cnt
                elif st == "rejected":
                    result["rejected_total"] = cnt

        # 3. Chiqarilgan postlar vaqt kesimida (bugun, hafta, oy)
        async with db.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN published_at >= ? THEN 1 ELSE 0 END), 0) as pub_today,
                COALESCE(SUM(CASE WHEN published_at >= ? THEN 1 ELSE 0 END), 0) as pub_week,
                COALESCE(SUM(CASE WHEN published_at >= ? THEN 1 ELSE 0 END), 0) as pub_month
            FROM posts
            WHERE status = 'published' AND published_at IS NOT NULL
        """, (today_iso, week_iso, month_iso)) as cur:
            p_row = await cur.fetchone()
            if p_row:
                result["published_today"] = int(p_row["pub_today"] or 0)
                result["published_week"] = int(p_row["pub_week"] or 0)
                result["published_month"] = int(p_row["pub_month"] or 0)

        # 4. Manbalar bo'yicha TOP-10 reyting (chiqarilgan va jami)
        async with db.execute("""
            SELECT
                COALESCE(source_name, 'Noma''lum') as src,
                COUNT(*) as total_cnt,
                COALESCE(SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END), 0) as pub_cnt
            FROM posts
            GROUP BY src
            ORDER BY pub_cnt DESC, total_cnt DESC
            LIMIT 10
        """) as cur:
            result["top_sources"] = [
                {"source": r["src"], "total": int(r["total_cnt"]), "published": int(r["pub_cnt"])}
                for r in await cur.fetchall()
            ]

        # 5. So'rovnomalar (Polls) statistikasi
        try:
            async with db.execute("""
                SELECT
                    COUNT(*) as total,
                    COALESCE(SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END), 0) as pub
                FROM polls
            """) as cur:
                pl_row = await cur.fetchone()
                if pl_row:
                    result["polls_total"] = int(pl_row["total"] or 0)
                    result["polls_published"] = int(pl_row["pub"] or 0)
        except Exception:
            pass

        # 6. Oxirgi chiqarilgan post
        async with db.execute("""
            SELECT id, title, source_name, published_at
            FROM posts
            WHERE status = 'published' AND published_at IS NOT NULL
            ORDER BY published_at DESC LIMIT 1
        """) as cur:
            lp = await cur.fetchone()
            if lp:
                result["last_published"] = dict(lp)

    return result

# ==================== AI TOKENLAR VA MODELLAR ANALITIKASI ====================

async def log_ai_token_usage(
    key_index: int,
    model_name: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0
):
    """Har bir muvaffaqiyatli Gemini so'rovining token sarfini bazada qayd etish"""
    now_str = datetime.now(timezone.utc).isoformat()
    try:
        async with get_db() as db:
            await db.execute("""
                INSERT INTO ai_token_logs (key_index, model_name, prompt_tokens, completion_tokens, total_tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (key_index, model_name, prompt_tokens, completion_tokens, total_tokens, now_str))
            await db.commit()
    except Exception as e:
        print(f"[log_ai_token_usage xatolik]: {e}")

async def get_ai_token_stats() -> Dict[str, Any]:
    """
    AI tokenlari va so'rovlarining bugungi va jami statistikasi:
    - Bugungi prompt, completion, total tokenlar (Toshkent vaqti bilan 00:00 dan boshlab)
    - Jami prompt, completion, total tokenlar
    - Modellar bo'yicha sarf (requests_today, tokens_today, requests_all, tokens_all)
    - Kalitlar bo'yicha sarf (requests_today, tokens_today, requests_all, tokens_all)
    """
    now_t = datetime.now(config.TASHKENT_TZ)
    today_start_t = now_t.replace(hour=0, minute=0, second=0, microsecond=0)
    today_iso = today_start_t.astimezone(timezone.utc).isoformat()

    stats: Dict[str, Any] = {
        "today": {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        },
        "all_time": {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        },
        "by_model": {},
        "by_key": {}
    }

    try:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row

            # 1. Bugungi sarf
            async with db.execute("""
                SELECT
                    COUNT(*) as reqs,
                    COALESCE(SUM(prompt_tokens), 0) as p_tok,
                    COALESCE(SUM(completion_tokens), 0) as c_tok,
                    COALESCE(SUM(total_tokens), 0) as t_tok
                FROM ai_token_logs
                WHERE created_at >= ?
            """, (today_iso,)) as cur:
                row = await cur.fetchone()
                if row:
                    stats["today"] = {
                        "requests": int(row["reqs"] or 0),
                        "prompt_tokens": int(row["p_tok"] or 0),
                        "completion_tokens": int(row["c_tok"] or 0),
                        "total_tokens": int(row["t_tok"] or 0)
                    }

            # 2. Barcha vaqtlar sarfi
            async with db.execute("""
                SELECT
                    COUNT(*) as reqs,
                    COALESCE(SUM(prompt_tokens), 0) as p_tok,
                    COALESCE(SUM(completion_tokens), 0) as c_tok,
                    COALESCE(SUM(total_tokens), 0) as t_tok
                FROM ai_token_logs
            """) as cur:
                row = await cur.fetchone()
                if row:
                    stats["all_time"] = {
                        "requests": int(row["reqs"] or 0),
                        "prompt_tokens": int(row["p_tok"] or 0),
                        "completion_tokens": int(row["c_tok"] or 0),
                        "total_tokens": int(row["t_tok"] or 0)
                    }

            # 3. Modellar kesimida sarf
            async with db.execute("""
                SELECT
                    model_name,
                    COUNT(*) as all_reqs,
                    COALESCE(SUM(total_tokens), 0) as all_tok,
                    COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) as today_reqs,
                    COALESCE(SUM(CASE WHEN created_at >= ? THEN total_tokens ELSE 0 END), 0) as today_tok
                FROM ai_token_logs
                GROUP BY model_name
            """, (today_iso, today_iso)) as cur:
                for r in await cur.fetchall():
                    stats["by_model"][r["model_name"]] = {
                        "requests_today": int(r["today_reqs"] or 0),
                        "tokens_today": int(r["today_tok"] or 0),
                        "requests_all": int(r["all_reqs"] or 0),
                        "tokens_all": int(r["all_tok"] or 0)
                    }

            # 4. Kalitlar kesimida sarf
            async with db.execute("""
                SELECT
                    key_index,
                    COUNT(*) as all_reqs,
                    COALESCE(SUM(total_tokens), 0) as all_tok,
                    COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) as today_reqs,
                    COALESCE(SUM(CASE WHEN created_at >= ? THEN total_tokens ELSE 0 END), 0) as today_tok
                FROM ai_token_logs
                GROUP BY key_index
            """, (today_iso, today_iso)) as cur:
                for r in await cur.fetchall():
                    stats["by_key"][int(r["key_index"])] = {
                        "requests_today": int(r["today_reqs"] or 0),
                        "tokens_today": int(r["today_tok"] or 0),
                        "requests_all": int(r["all_reqs"] or 0),
                        "tokens_all": int(r["all_tok"] or 0)
                    }
    except Exception as e:
        print(f"[get_ai_token_stats xatolik]: {e}")

    return stats

# ==================== MULTI-CHANNEL SAAS TIZIMI FUNKSIYALARI ====================

async def register_user_channel(
    owner_id: int,
    channel_id: str,
    channel_title: str,
    channel_username: Optional[str] = None,
    topics: Optional[List[str]] = None,
    posts_per_day: int = 3,
    tone: str = "standard"
) -> int:
    """Mijozning shaxsiy Telegram kanalini SaaS tizimiga ulash"""
    if topics is None:
        topics = ["ai", "gadgets"]
    topics_json = json.dumps(topics, ensure_ascii=False)
    now_str = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        await db.execute("""
            INSERT INTO user_channels (owner_id, channel_id, channel_title, channel_username, topics, tone, posts_per_day, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                owner_id=excluded.owner_id,
                channel_title=excluded.channel_title,
                channel_username=excluded.channel_username,
                is_active=1
        """, (owner_id, channel_id, channel_title, channel_username, topics_json, tone, posts_per_day, now_str))
        await db.commit()

        async with db.execute("SELECT id FROM user_channels WHERE channel_id = ?", (channel_id,)) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0

async def get_user_channels(owner_id: int) -> List[Dict[str, Any]]:
    """Muayyan foydalanuvchining ulangan barcha kanallari ro'yxati"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM user_channels WHERE owner_id = ? ORDER BY id DESC
        """, (owner_id,)) as cur:
            rows = await cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["topics_list"] = json.loads(d.get("topics") or '["ai", "gadgets"]')
                except Exception:
                    d["topics_list"] = ["ai", "gadgets"]
                result.append(d)
            return result

async def get_user_channel(channel_pk: int) -> Optional[Dict[str, Any]]:
    """Kanal ID raqami (PK) bo'yicha ma'lumot olish"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM user_channels WHERE id = ?", (channel_pk,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["topics_list"] = json.loads(d.get("topics") or '["ai", "gadgets"]')
            except Exception:
                d["topics_list"] = ["ai", "gadgets"]
            return d

async def get_user_channel_by_channel_id(channel_id: str) -> Optional[Dict[str, Any]]:
    """Telegram Chat ID bo'yicha kanalni aniqlash"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM user_channels WHERE channel_id = ?", (channel_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["topics_list"] = json.loads(d.get("topics") or '["ai", "gadgets"]')
            except Exception:
                d["topics_list"] = ["ai", "gadgets"]
            return d

async def update_user_channel(
    channel_pk: int,
    topics: Optional[List[str]] = None,
    posts_per_day: Optional[int] = None,
    is_active: Optional[int] = None,
    tone: Optional[str] = None
):
    """Mijoz kanalining sozlamalarini yangilash"""
    fields = []
    values = []

    if topics is not None:
        fields.append("topics = ?")
        values.append(json.dumps(topics, ensure_ascii=False))
    if posts_per_day is not None:
        fields.append("posts_per_day = ?")
        values.append(posts_per_day)
    if is_active is not None:
        fields.append("is_active = ?")
        values.append(is_active)
    if tone is not None:
        fields.append("tone = ?")
        values.append(tone)

    if not fields:
        return

    values.append(channel_pk)
    query = f"UPDATE user_channels SET {', '.join(fields)} WHERE id = ?"

    async with get_db() as db:
        await db.execute(query, tuple(values))
        await db.commit()

async def delete_user_channel(channel_pk: int, owner_id: int) -> bool:
    """Mijoz kanalini o'chirish"""
    async with get_db() as db:
        res = await db.execute("DELETE FROM user_channels WHERE id = ? AND owner_id = ?", (channel_pk, owner_id))
        await db.execute("DELETE FROM user_channel_deliveries WHERE user_channel_id = ?", (channel_pk,))
        await db.commit()
        return res.rowcount > 0

async def get_active_user_channels() -> List[Dict[str, Any]]:
    """Fon xizmati uchun barcha faol mijoz kanallari ro'yxati"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM user_channels WHERE is_active = 1") as cur:
            rows = await cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["topics_list"] = json.loads(d.get("topics") or '["ai", "gadgets"]')
                except Exception:
                    d["topics_list"] = ["ai", "gadgets"]
                result.append(d)
            return result

async def update_user_channel_last_posted(channel_pk: int):
    """Kanalga oxirgi post yuborilgan vaqtni yangilash"""
    now_str = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute("UPDATE user_channels SET last_posted_at = ? WHERE id = ?", (now_str, channel_pk))
        await db.commit()

async def log_channel_delivery(user_channel_id: int, post_id: int):
    """Mijoz kanaliga post yetkazilganligini qayd etish"""
    now_str = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute("""
            INSERT OR IGNORE INTO user_channel_deliveries (user_channel_id, post_id, delivered_at)
            VALUES (?, ?, ?)
        """, (user_channel_id, post_id, now_str))
        await db.commit()

async def has_post_been_delivered(user_channel_id: int, post_id: int) -> bool:
    """Post ushbu mijoz kanaliga allaqachon chiqarilganmi?"""
    async with get_db() as db:
        async with db.execute("""
            SELECT 1 FROM user_channel_deliveries WHERE user_channel_id = ? AND post_id = ?
        """, (user_channel_id, post_id)) as cur:
            row = await cur.fetchone()
            return row is not None

async def get_candidate_post_for_user_channel(user_channel_id: int, topics: List[str]) -> Optional[Dict[str, Any]]:
    """
    Mijoz kanaliga hali yuborilmagan, tanlangan mavzularga mos eng so'nggi postni topish.
    Asosiy kanaldan chiqqan yoki tayyor postlar orasidan tanlaydi.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # Mavzular filtrini tuzish
        topic_conds = []
        for t in topics:
            if t == "ai":
                topic_conds.append("(title LIKE '%AI%' OR title LIKE '%Sun''iy%' OR title LIKE '%GPT%' OR title LIKE '%Claude%' OR title LIKE '%Model%' OR uz_caption LIKE '%sun''iy intellekt%')")
            elif t == "gadgets":
                topic_conds.append("(title LIKE '%Smartfon%' OR title LIKE '%iPhone%' OR title LIKE '%Samsung%' OR title LIKE '%Xiaomi%' OR title LIKE '%Noutbuk%' OR title LIKE '%Chip%' OR title LIKE '%RTX%')")
            elif t == "cars":
                topic_conds.append("(title LIKE '%Avto%' OR title LIKE '%Mashina%' OR title LIKE '%BYD%' OR title LIKE '%Tesla%' OR title LIKE '%Cobalt%' OR title LIKE '%Elektromobil%')")
            elif t == "business":
                topic_conds.append("(title LIKE '%Biznes%' OR title LIKE '%Startap%' OR title LIKE '%Milliard%' OR title LIKE '%Investitsiya%' OR title LIKE '%Kompaniya%')")
            elif t == "coding":
                topic_conds.append("(title LIKE '%Dastur%' OR title LIKE '%Kod%' OR title LIKE '%Python%' OR title LIKE '%GitHub%' OR title LIKE '%Open Source%')")

        filter_clause = f"AND ({' OR '.join(topic_conds)})" if topic_conds else ""

        query = f"""
            SELECT p.*
            FROM posts p
            LEFT JOIN user_channel_deliveries d ON (d.post_id = p.id AND d.user_channel_id = ?)
            WHERE d.id IS NULL
              AND p.status = 'published'
              {filter_clause}
            ORDER BY p.id DESC
            LIMIT 1
        """
        async with db.execute(query, (user_channel_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)

        # Agar maxsus mavzu bo'yicha topilmasa, istalgan oxirgi chiqarilmagan postni berish
        fallback_query = """
            SELECT p.*
            FROM posts p
            LEFT JOIN user_channel_deliveries d ON (d.post_id = p.id AND d.user_channel_id = ?)
            WHERE d.id IS NULL
              AND p.status = 'published'
            ORDER BY p.id DESC
            LIMIT 1
        """
        async with db.execute(fallback_query, (user_channel_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


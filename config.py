import os
import sys

# Windows konsolida emojilar bilan ishlaganda UnicodeEncodeError bo'lmasligi uchun
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pathlib import Path
from dotenv import load_dotenv

from datetime import timezone, timedelta

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Toshkent vaqt mintaqasi (UTC+5)
TASHKENT_TZ = timezone(timedelta(hours=5))

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1002229527376").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@grand_mobile_anons").strip()
CHANNEL_LINK = os.getenv("CHANNEL_LINK", f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}").strip()

# Bot username va interaktiv kanal tugmalari sozlamalari
BOT_USERNAME = os.getenv("BOT_USERNAME", "neuratech_news_bot").strip().lstrip("@")
BOT_LINK = f"https://t.me/{BOT_USERNAME}"
ENABLE_CHANNEL_SOURCE_BUTTON = os.getenv("ENABLE_CHANNEL_SOURCE_BUTTON", "False").lower() in ("true", "1", "yes")
ENABLE_CHANNEL_AI_BUTTON = os.getenv("ENABLE_CHANNEL_AI_BUTTON", "False").lower() in ("true", "1", "yes")
ENABLE_CHANNEL_BUTTONS = ENABLE_CHANNEL_SOURCE_BUTTON or ENABLE_CHANNEL_AI_BUTTON
DISCUSSION_CHAT_LINK = os.getenv("DISCUSSION_CHAT_LINK", "").strip()

# Gemini API kalitlari hovuzi (Multi-Key Pool)
def get_gemini_api_keys() -> list:
    keys = []
    main_key = os.getenv("GEMINI_API_KEY", "").strip()
    if main_key and main_key not in keys:
        keys.append(main_key)
    for i in range(1, 15):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if k and k not in keys:
            keys.append(k)
    raw_keys = os.getenv("GEMINI_API_KEYS", "")
    for k in raw_keys.split(","):
        k = k.strip()
        if k and k not in keys:
            keys.append(k)
    return keys

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

raw_admin_id = os.getenv("ADMIN_ID", "").strip()
INITIAL_ADMIN_ID = int(raw_admin_id) if raw_admin_id.isdigit() else None

CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "25"))
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "moderation").strip().lower()

# Postlar orasidagi oraliq vaqt (daqiqada) va maksimal navbat sig'imi (eskirib ketmasligi uchun)
POST_SPACING_MINUTES = int(os.getenv("POST_SPACING_MINUTES", "10"))
MAX_SCHEDULED_QUEUE = int(os.getenv("MAX_SCHEDULED_QUEUE", "6"))

# Tungi tinchlik rejimi (Toshkent vaqti bilan 23:30 dan 07:30 gacha)
NIGHT_MODE_START_HOUR = 23
NIGHT_MODE_START_MINUTE = 30
NIGHT_MODE_END_HOUR = 7
NIGHT_MODE_END_MINUTE = 30

# Kechki Dayjest vaqti (Toshkent vaqti bilan soat 21:00)
DAILY_DIGEST_HOUR = 21

# Baza Avto-Zaxiralash vaqti (Toshkent vaqti bilan tungi soat 03:00)
BACKUP_HOUR = int(os.getenv("BACKUP_HOUR", "3"))

LOGO_PATH = BASE_DIR / "neuratech_logo.jpg"
DB_PATH = BASE_DIR / "bot_data.db"

# Telegram Premium Custom Emoji ID lari
PREMIUM_EMOJIS = {
    "lightning": os.getenv("EMOJI_LIGHTNING", "").strip(),
    "pin": os.getenv("EMOJI_PIN", "").strip(),
    "chart": os.getenv("EMOJI_CHART", "").strip(),
    "bulb": os.getenv("EMOJI_BULB", "").strip(),
    "antenna": os.getenv("EMOJI_ANTENNA", "").strip(),
    "tag": os.getenv("EMOJI_TAG", "").strip(),
}

def render_emoji(key: str, default_char: str) -> str:
    """Agar .env da Premium Emoji ID berilgan bo'lsa <tg-emoji> formatida, aks holda oddiy emoji qaytarish"""
    eid = PREMIUM_EMOJIS.get(key)
    if eid and eid.isdigit():
        return f'<tg-emoji emoji-id="{eid}">{default_char}</tg-emoji>'
    return default_char

# Sara IT, AI, startaplar va texno-kompaniyalar RSS manbalari (AI va Startaplar 1-o'rinda)
RSS_SOURCES = [
    {
        "name": "Google AI Blog",
        "slug": "google_ai",
        "url": "https://blog.google/innovation-and-ai/technology/ai/rss/",
        "category": "AI Modellar & Google DeepMind"
    },
    {
        "name": "OpenAI News",
        "slug": "openai",
        "url": "https://openai.com/news/rss.xml",
        "category": "AI Modellar & OpenAI"
    },
    {
        "name": "TechCrunch AI",
        "slug": "techcrunch_ai",
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "category": "AI Kompaniyalar & Startaplar"
    },
    {
        "name": "The Decoder",
        "slug": "the_decoder",
        "url": "https://the-decoder.com/feed/",
        "category": "AI Modellar & Tadqiqotlar"
    },
    {
        "name": "MarkTechpost",
        "slug": "marktechpost",
        "url": "https://www.marktechpost.com/feed/",
        "category": "AI Tadqiqotlar & LLM"
    },
    {
        "name": "TechCrunch Startups",
        "slug": "techcrunch_startups",
        "url": "https://techcrunch.com/category/startups/feed/",
        "category": "IT & Texno Startaplar"
    },
    {
        "name": "The Verge",
        "slug": "the_verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "category": "IT Kompaniyalar & Texnologiya"
    },
    {
        "name": "Tom's Hardware",
        "slug": "toms_hardware",
        "url": "https://www.tomshardware.com/feeds.xml",
        "category": "Chiplar, GPU & Uskunalar"
    },
    {
        "name": "Ars Technica",
        "slug": "ars_technica",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "category": "IT Sanoat & Texnologiya"
    },
    {
        "name": "GSMArena",
        "slug": "gsmarena",
        "url": "https://www.gsmarena.com/rss-news-reviews.php3",
        "category": "Flagman Smartfonlar & Yangi Devayslar"
    },
    {
        "name": "Engadget",
        "slug": "engadget",
        "url": "https://www.engadget.com/rss.xml",
        "category": "Yangi Texnologiyalar"
    }
]

# Dinamik kanal imzosi
CHANNEL_FOOTER = f"\n\n🔔 <b>Kanalimizga obuna bo‘ling:</b> <a href='{CHANNEL_LINK}'>{CHANNEL_USERNAME}</a>"

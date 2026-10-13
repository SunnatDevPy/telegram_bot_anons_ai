import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1002229527376").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@grand_mobile_anons").strip()
CHANNEL_LINK = os.getenv("CHANNEL_LINK", f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

raw_admin_id = os.getenv("ADMIN_ID", "").strip()
INITIAL_ADMIN_ID = int(raw_admin_id) if raw_admin_id.isdigit() else None

CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "25"))
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "moderation").strip().lower()

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

# Sara IT, AI, yangi chiplar va devayslar RSS manbalari (Chegirmasiz, faqat apparat ta'minoti va texnologiya)
RSS_SOURCES = [
    {
        "name": "Tom's Hardware",
        "url": "https://www.tomshardware.com/feeds.xml",
        "category": "Chiplar, GPU & Uskunalar"
    },
    {
        "name": "GSMArena",
        "url": "https://www.gsmarena.com/rss-news-reviews.php3",
        "category": "Flagman Smartfonlar & Yangi Devayslar"
    },
    {
        "name": "Engadget",
        "url": "https://www.engadget.com/rss.xml",
        "category": "Yangi Gadjetlar & Texnologiyalar"
    },
    {
        "name": "The Decoder",
        "url": "https://the-decoder.com/feed/",
        "category": "AI Modellar & Benchmarklar"
    },
    {
        "name": "TechCrunch AI",
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "category": "AI Kompaniyalar & Texnologiyalar"
    },
    {
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "category": "Yangi Devayslar & IT"
    },
    {
        "name": "Ars Technica",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "category": "Texnologiya & Ilm-fan"
    }
]

# Dinamik kanal imzosi
CHANNEL_FOOTER = f"\n\n🔔 <b>Kanalimizga obuna bo‘ling:</b> <a href='{CHANNEL_LINK}'>{CHANNEL_USERNAME}</a>"

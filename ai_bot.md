Bunday avtomatlashgan tizim 4 ta asosiy bo‘g‘indan iborat konveyer (pipeline) sifatida quriladi: Monitoring (RSS) $\rightarrow$ Takrorlanish filtratsiyasi $\rightarrow$ AI qayta ishlash $\rightarrow$ Telegramga chop etish.1. Ishlash arxitekturasi[Texnologik RSS / Saytlar] 
        │ (Har 15-30 daqiqada tekshirish)
        ▼
[Python Parser (feedparser)] ──► [SQLite / Redis] (Eski xabarlarni filtrlash)
        │
        ▼ (Yangi xabar matni + Rasm linki)
[LLM (Gemini Flash / OpenAI)] ──► Chiroyli o'zbekcha post generatsiyasi
        │
        ▼
[Telegram Bot (aiogram)] ──► Kanalingizga rasm bilan post yuborish
2. Tizimning asosiy komponentlariManbalar (RSS Feeds): Saytlarni HTML parsing (scraping) qilish o‘rniga toza RSS oqimlaridan foydalanish eng barqaror yo‘l:TechCrunch AI: [https://techcrunch.com/category/artificial-intelligence/feed/](https://techcrunch.com/category/artificial-intelligence/feed/)The Verge: [https://www.theverge.com/rss/index.xml](https://www.theverge.com/rss/index.xml)Google DeepMind / AI: [https://blog.google/technology/ai/rss/](https://blog.google/technology/ai/rss/)Ars Technica: [https://feeds.arstechnica.com/arstechnica/index](https://feeds.arstechnica.com/arstechnica/index)Takrorlanishdan himoya (Deduplication): Bir xil yangilik qayta-qayta chiqmasligi uchun maqolaning URL manzili yoki ID raqami kichik bazaga (SQLite yoki Redis) saqlanadi (if url in seen_urls: continue).AI formatlovchi (Prompt): Gemini 2.5 Flash yoki GPT-4o-mini modeli inglizcha matnni shunchaki tarjima qilmay, o‘quvchi e’tiborini tortadigan ixcham post (sarlavha, 3 ta asosiy tezis, xulosa va teglar) shaklida qayta yozadi.Telegram yetkazuvchi: aiogram yoki Telegram Bot API orqali send_photo(chat_id, photo=image_url, caption=text) buyrug‘i bilan rasm va matn bitta xabar qilib chiqariladi.3. Tayyor Python skript namunasiKutubxonalarni o‘rnatish:Bashpip install feedparser google-genai httpx
Ishchi skript kodi (news_bot.py):Pythonimport asyncio
import sqlite3
import feedparser
import httpx
from google import genai

BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
CHANNEL_ID = "@kanalingiz_username"
GEMINI_KEY = "GEMINI_API_KEY"

RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/index.xml"
]

ai_client = genai.Client(api_key=GEMINI_KEY)

# 1. Baza: eski xabarlarni eslab qolish
conn = sqlite3.connect("posted_news.db")
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS news (url TEXT PRIMARY KEY)")
conn.commit()

def is_posted(url: str) -> bool:
    cursor.execute("SELECT 1 FROM news WHERE url = ?", (url,))
    return cursor.fetchone() is not None

def mark_as_posted(url: str):
    cursor.execute("INSERT INTO news (url) VALUES (?)", (url,))
    conn.commit()

# 2. AI orqali o'zbekcha post shakllantirish
def format_post_with_ai(title: str, summary: str) -> str:
    prompt = f"""
Quyidagi IT/AI yangiligini Telegram kanali uchun jozibali, o'qishga oson o'zbekcha post qilib qayta yoz:
Original Title: {title}
Original Text: {summary}

Talablar:
- Yuqorida 1 ta kuchli sarlavha (emojilar bilan).
- Asosiy mazmunni 2-3 ta qisqa punktda (bullet points) tushuntir.
- 1 ta qisqa xulosa.
- Oxirida 3-4 ta hashtag (#AI #Texnologiya va h.k.).
- HTML formatlashdan (<b>, <i>) foydalan, ortiqcha kirish so'zlari yozma.
"""
    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text

# 3. Telegramga yuborish
async def post_to_telegram(photo_url: str, caption: str):
    async with httpx.AsyncClient() as client:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": CHANNEL_ID,
            "photo": photo_url,
            "caption": caption[:1024],  # Telegram caption limiti
            "parse_mode": "HTML"
        }
        await client.post(url, json=payload)

# 4. Asosiy monitoring tsikli
async def run_news_monitor():
    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:5]:  # Har tekshirishda eng so'nggi 5 tasi
            url = entry.link
            if is_posted(url):
                continue

            # Rasm manzilini topish
            image_url = None
            if "media_content" in entry and len(entry.media_content) > 0:
                image_url = entry.media_content[0].get("url")
            elif "enclosures" in entry and len(entry.enclosures) > 0:
                image_url = entry.enclosures[0].get("href")

            post_text = format_post_with_ai(entry.title, entry.summary)

            if image_url:
                await post_to_telegram(image_url, post_text)
            else:
                # Agar rasm bo'lmasa, sendMessage orqali matnning o'zi ketadi
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={"chat_id": CHANNEL_ID, "text": post_text, "parse_mode": "HTML"}
                    )

            mark_as_posted(url)
            await asyncio.sleep(5)  # Telegram spam chekloviga tushmaslik uchun

if __name__ == "__main__":
    asyncio.run(run_news_monitor())
Buni serverda yoki shaxsiy noutbukda cron (Linux) yoki APScheduler orqali har 20–30 daqiqada avtomatik ishga tushadigan qilib qo‘ysangiz, kanal yangiliklar bilan doimiy to‘lib boradi.Xabarlar to‘g‘ridan-to‘g‘ri kanalga avtomatik chiqsinmi yoki avval moderator (o‘zingiz) tasdiqlaydigan oraliq admin-bot burchagi kerakmi?
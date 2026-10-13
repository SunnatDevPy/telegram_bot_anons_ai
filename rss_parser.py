import asyncio
import re
from typing import List, Dict, Any, Optional
import feedparser
import httpx
import config
from database import is_duplicate_news

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

def extract_image_from_entry(entry: Any) -> Optional[str]:
    """RSS entry ob'ektidan rasm URL manzilini qidirish"""
    # 1. media_content
    if entry.get("media_content"):
        for item in entry.media_content:
            url = item.get("url")
            if url and ("image" in item.get("type", "") or item.get("medium") == "image" or any(url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"])):
                return url
        if entry.media_content[0].get("url"):
            return entry.media_content[0].get("url")

    # 2. enclosures
    if entry.get("enclosures"):
        for enc in entry.enclosures:
            if enc.get("href"):
                return enc["href"]

    # 3. media_thumbnail
    if entry.get("media_thumbnail"):
        for thumb in entry.media_thumbnail:
            if thumb.get("url"):
                return thumb["url"]

    # 4. Content yoki Summary ichidagi HTML img tegidan
    html_sources = []
    if "content" in entry and entry.content:
        for c in entry.content:
            if hasattr(c, "value"):
                html_sources.append(c.value)
    if "summary" in entry and entry.summary:
        html_sources.append(entry.summary)

    for html in html_sources:
        m = re.search(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', html, re.IGNORECASE)
        if m:
            img_url = m.group(1)
            # Kichik piktogrammalar yoki tracking pixellardan qochish
            if not any(k in img_url.lower() for k in ["spacer", "1x1", "avatar", "icon"]):
                return img_url

    return None

async def extract_og_image(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """RSS rasm bermagan taqdirda, maqola sahifasidan og:image olish"""
    try:
        resp = await client.get(url, timeout=7.0, follow_redirects=True)
        if resp.status_code == 200:
            m = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', resp.text, re.IGNORECASE)
            if not m:
                m = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']', resp.text, re.IGNORECASE)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None

def clean_html(raw_html: str) -> str:
    """HTML teglarini tozalash va toza matn qaytarish"""
    if not raw_html:
        return ""
    clean = re.sub(r'<[^<]+?>', '', raw_html)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

async def fetch_feed_entries(source: Dict[str, str], client: httpx.AsyncClient, limit: int = 5) -> List[Dict[str, Any]]:
    """Bitta RSS manbasidan so'nggi xabarlarni yuklash"""
    results = []
    try:
        resp = await client.get(source["url"], timeout=12.0, follow_redirects=True)
        if resp.status_code != 200:
            return results

        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:limit]:
            link = getattr(entry, "link", None)
            title = getattr(entry, "title", None)
            if not link or not title:
                continue

            clean_title = clean_html(title)
            # Bazada bormi yoki o'xshash mavzu chiqdimi tekshirish
            if await is_duplicate_news(link, clean_title):
                continue

            # Xulosa matnini tozalash
            raw_summary = getattr(entry, "summary", "") or ""
            summary = clean_html(raw_summary)

            # Rasm topish
            img_url = extract_image_from_entry(entry)
            if not img_url:
                img_url = await extract_og_image(link, client)

            results.append({
                "source_name": source["name"],
                "category": source.get("category", "IT & AI"),
                "url": link,
                "title": clean_html(title),
                "summary": summary,
                "image_url": img_url
            })
    except Exception as e:
        print(f"[RSS Xatolik] {source['name']}: {e}")

    return results

async def scan_all_feeds(limit_per_feed: int = 3) -> List[Dict[str, Any]]:
    """Barcha tasdiqlangan RSS manbalarini asinxron tekshirish"""
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
        tasks = [
            fetch_feed_entries(src, client, limit=limit_per_feed)
            for src in config.RSS_SOURCES
        ]
        feeds_results = await asyncio.gather(*tasks)

    # Natijalarni birlashtirish
    all_new_news = []
    for news_list in feeds_results:
        all_new_news.extend(news_list)

    return all_new_news

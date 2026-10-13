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
    """RSS entry ob'ektidan rasm, animatsiyali GIF yoki video URL manzilini qidirish"""
    # 1. media_content
    if entry.get("media_content"):
        # Avval video va GIF animatsiyalarni qidirish
        for item in entry.media_content:
            url = item.get("url")
            m_type = item.get("type", "").lower()
            medium = item.get("medium", "").lower()
            if url and ("video" in m_type or medium == "video" or "gif" in m_type or any(url.lower().endswith(ext) for ext in [".gif", ".mp4", ".webm", ".mov"])):
                return url

        # Keyin sifatli statik rasmlarni qidirish
        for item in entry.media_content:
            url = item.get("url")
            if url and ("image" in item.get("type", "") or item.get("medium") == "image" or any(url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"])):
                return url
        if entry.media_content[0].get("url"):
            return entry.media_content[0].get("url")

    # 2. enclosures (ko'pincha video va gif fayllar shu yerda bo'ladi)
    if entry.get("enclosures"):
        for enc in entry.enclosures:
            href = enc.get("href")
            e_type = enc.get("type", "").lower()
            if href and ("video" in e_type or "gif" in e_type or any(href.lower().endswith(ext) for ext in [".gif", ".mp4", ".webm"])):
                return href
        for enc in entry.enclosures:
            if enc.get("href"):
                return enc["href"]

    # 3. media_thumbnail
    if entry.get("media_thumbnail"):
        for thumb in entry.media_thumbnail:
            if thumb.get("url"):
                return thumb["url"]

    # 4. Content yoki Summary ichidagi <video>, <source>, yoki <img src="...gif"> teglari
    html_sources = []
    if "content" in entry and entry.content:
        for c in entry.content:
            if hasattr(c, "value"):
                html_sources.append(c.value)
    if "summary" in entry and entry.summary:
        html_sources.append(entry.summary)

    for html_text in html_sources:
        # Video yoki GIF teglari
        vid_match = re.search(r'<(?:video|source)[^>]+src=["\'](https?://[^"\']+\.(?:mp4|webm|mov))["\']', html_text, re.IGNORECASE)
        if vid_match:
            return vid_match.group(1)

        gif_match = re.search(r'<img[^>]+src=["\'](https?://[^"\']+\.gif)["\']', html_text, re.IGNORECASE)
        if gif_match:
            return gif_match.group(1)

        img_match = re.search(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', html_text, re.IGNORECASE)
        if img_match:
            img_url = img_match.group(1)
            if not any(k in img_url.lower() for k in ["spacer", "1x1", "avatar", "icon"]):
                return img_url

    return None

async def extract_og_image(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """RSS media bermagan taqdirda, maqola sahifasidan og:video, og:image yoki twitter:image olish"""
    try:
        resp = await client.get(url, timeout=7.0, follow_redirects=True)
        if resp.status_code == 200:
            # 1. Avval og:video ni tekshirish
            v_match = re.search(r'<meta\s+property=["\']og:video(?::secure_url)?["\']\s+content=["\'](https?://[^"\']+)["\']', resp.text, re.IGNORECASE)
            if v_match:
                return v_match.group(1)

            # 2. Keyin og:image yoki twitter:image
            m = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', resp.text, re.IGNORECASE)
            if not m:
                m = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']', resp.text, re.IGNORECASE)
            if not m:
                m = re.search(r'<meta\s+name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']', resp.text, re.IGNORECASE)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None

def upgrade_image_url(url: Optional[str]) -> Optional[str]:
    """Kichik pikselli yoki past sifatli thumbnail rasmlarni original Full HD sifatiga ko'tarish"""
    if not url:
        return None
    clean = url.strip()
    
    # 1. GSMArena: masalan .../-184x111/gsmarena_001.jpg -> .../inline/gsmarena_001.jpg
    if "gsmarena.com" in clean:
        clean = re.sub(r'/-\d+x\d+/', '/inline/', clean)
        clean = re.sub(r'/thumb/', '/inline/', clean)

    # 2. WordPress / TechCrunch / ArsTechnica: masalan ...-150x150.jpg -> ....jpg
    clean = re.sub(r'-\d{2,4}x\d{2,4}\.(jpg|jpeg|png|webp)', r'.\1', clean, flags=re.IGNORECASE)
    
    # Agar resize yoki w= parametr bo'lsa, original o'lchamini olish
    if "resize=" in clean or "fit=" in clean:
        clean = re.sub(r'\?(?:resize|fit|w|h|crop)=[^&]+(&.*)?$', '', clean)

    return clean

def is_low_res_url(url: Optional[str]) -> bool:
    """Rasm o'lchami kichik thumbnail ekanligini aniqlash"""
    if not url:
        return True
    low_markers = [
        "-184x111", "-150x150", "-100x100", "-300x",
        "thumbnail", "/thumb/", "avatar", "small", "160x", "184x"
    ]
    u = url.lower()
    return any(m in u for m in low_markers)

DEAL_KEYWORDS = [
    "deal", "deals", "save $", "save £", "save €", "discount", "discounts",
    "on sale", "price drop", "price cut", "coupon", "promo code", "chegirma",
    "lowest price", "all-time low", "record low", "buying guide", "gift guide",
    "best buy", "amazon deal", "prime day", "black friday", "cyber monday",
    "for only $", "for just $", "now $", "off at amazon", "off on amazon",
    "% off", "percent off", "drops to $", "steep discount", "bargain",
    "historic low", "cashback", "special offer", "clearance", "promo",
    "buy now for", "sale alert", "price slashed", "bundle deal"
]

DEAL_URL_PATTERNS = [
    "/deals/", "/deal/", "/sales/", "/coupons/", "/buying-guide",
    "/shopping/", "/best-deals", "/affiliate", "/discounts/"
]

def is_commercial_deal(title: str, url: str = "", summary: str = "") -> bool:
    """Do'kon aksiyalari, chegirmalar va savdo reklamalarini aniqlash va chetlab o'tish"""
    title_lower = title.lower()
    url_lower = url.lower()
    summary_lower = summary.lower() if summary else ""

    # 1. URL bo'yicha
    if any(p in url_lower for p in DEAL_URL_PATTERNS):
        return True

    # 2. Sarlavhadagi iboralar bo'yicha
    if any(kw in title_lower for kw in DEAL_KEYWORDS):
        return True

    # 3. Regex bo'yicha ($... off yoki save $...)
    if re.search(r'\$\d+[\s\w]*(?:off|save|discount|chegirma)', title_lower):
        return True
    if re.search(r'\b\d+%\s+off\b', title_lower):
        return True
    if re.search(r'\b(?:save|saving|saved)\s+\$\d+', title_lower):
        return True
    if re.search(r'\b(?:historic|massive|record|big)\s+(?:\$\d+|discount|price cut)', title_lower):
        return True

    # 4. Qisqa xulosada agar faqat narx aksiyasi bo'lsa
    if "save $" in summary_lower or "use coupon" in summary_lower or "at its lowest price" in summary_lower:
        return True

    return False

UNWANTED_TOPIC_PATTERNS = [
    # Kino, serial, striming narxlari va ko'ngilochar shoular
    "discovery plus", "discovery+", "disney+", "netflix", "hbo max", "paramount+", "peacock",
    "movie", "movies", "tv show", "tv series", "season finale", "episode", "box office",
    "trailer", "hollywood", "actor", "actress", "oscar", "emmy", "cinema",
    # O'yinlar (sof ko'ngilochar o'yinlar)
    "gameplay", "walkthrough", "hogwarts legacy", "gta 6", "grand theft auto", "nintendo switch",
    "playstation plus", "xbox game pass", "dlc", "zelda", "pokemon", "fortnite",
    # Mayda maishiy uy buyumlari
    "robot vacuum", "air purifier", "smart plug", "toaster", "blender", "coffee maker"
]

AI_STARTUP_PRIORITY_KEYWORDS = [
    "ai", "artificial intelligence", "llm", "gpt", "openai", "gemini", "deepseek",
    "anthropic", "claude", "machine learning", "neural", "chatgpt", "copilot", "agent",
    "startup", "startups", "funding", "seed", "series a", "series b", "venture capital",
    "vc", "invest", "investor", "acquisition", "acquired", "raises", "valuation", "unicorn",
    "apple", "microsoft", "google", "nvidia", "meta", "amazon", "intel", "amd", "tsmc",
    "qualcomm", "layoff", "layoffs", "ceo", "strategy", "ipo", "enterprise", "cloud",
    "y combinator", "accelerator", "tech company", "semiconductor", "chipmaker"
]

def is_unwanted_topic(title: str, url: str = "", summary: str = "") -> bool:
    """Kino, serial, o'yinlar va maishiy buyumlar kabi ikkinchi darajali mavzularni filtrlash"""
    combined = f"{title} {url} {summary}".lower()
    # Agar xabarda AI, chip, startap yoki texno gigantlar bo'lsa, o'tkazib yubormaymiz
    has_core_tech = any(
        re.search(rf'\b{re.escape(k)}\b', combined)
        for k in ["ai", "chip", "nvidia", "startup", "funding", "openai", "anthropic", "deepseek", "apple", "google", "microsoft"]
    )
    if has_core_tech:
        return False

    return any(p in combined for p in UNWANTED_TOPIC_PATTERNS)

def get_topic_priority(title: str, summary: str = "", category: str = "") -> int:
    """Mavzular prioriteti: AI, Startaplar va IT kompaniyalar 1-darajali (eng yuqori)"""
    score = 0
    combined = f"{title} {summary} {category}".lower()

    if "ai" in category.lower() or "startup" in category.lower():
        score += 50

    for kw in AI_STARTUP_PRIORITY_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', combined):
            score += 15

    return score

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
            raw_summary = getattr(entry, "summary", "") or ""
            summary = clean_html(raw_summary)

            # Do'kon chegirmalari, aksiyalar va savdo reklamalarini chetlab o'tish
            if is_commercial_deal(clean_title, link, summary):
                continue

            # Kino, serial va o'yinlar kabi ikkinchi darajali mavzularni chetlab o'tish
            if is_unwanted_topic(clean_title, link, summary):
                continue

            # Bazada bormi yoki o'xshash mavzu chiqdimi tekshirish
            if await is_duplicate_news(link, clean_title):
                continue

            # Rasm topish (Eng sifatli HD rasmni tanlash)
            img_url = extract_image_from_entry(entry)
            if not img_url or is_low_res_url(img_url):
                og_img = await extract_og_image(link, client)
                if og_img:
                    img_url = og_img
            img_url = upgrade_image_url(img_url)

            priority = get_topic_priority(clean_title, summary, source.get("category", ""))
            results.append({
                "source_name": source["name"],
                "category": source.get("category", "IT & AI"),
                "url": link,
                "title": clean_html(title),
                "summary": summary,
                "image_url": img_url,
                "priority": priority
            })
    except Exception as e:
        print(f"[RSS Xatolik] {source['name']}: {e}")

    return results

async def scan_all_feeds(limit_per_feed: int = 3) -> List[Dict[str, Any]]:
    """Barcha tasdiqlangan RSS manbalarini asinxron tekshirish va AI/Startaplar bo'yicha saralash"""
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
        tasks = [
            fetch_feed_entries(src, client, limit=limit_per_feed)
            for src in config.RSS_SOURCES
        ]
        feeds_results = await asyncio.gather(*tasks)

    # Natijalarni birlashtirish va prioriteti bo'yicha saralash (AI va Startaplar 1-o'rinda)
    all_new_news = []
    for news_list in feeds_results:
        all_new_news.extend(news_list)

    all_new_news.sort(key=lambda x: x.get("priority", 0), reverse=True)
    return all_new_news

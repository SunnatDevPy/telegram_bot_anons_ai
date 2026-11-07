import asyncio
import re
from typing import List, Dict, Any, Optional
import feedparser
import httpx
import config
import database
from database import is_duplicate_news

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

JUNK_IMAGE_PATTERNS = [
    "s.w.org", "core/emoji", "/emoji", "emoji/", "wp-smiley", "smiley",
    "gravatar.com", "avatar", "spacer", "1x1", "tracking", "pixel",
    "feedburner", "doubleclick", "badge", "icon-", "/icons/", "favicon",
    "wp-includes", "wp-content/plugins", "/plugins/", "share-button",
    "site-logo", "wp.com/latex.php"
]

def is_junk_or_emoji_url(url: Optional[str]) -> bool:
    """Emoji, avatar, tracking pixel yoki servis ikonkalarini aniqlash"""
    if not url:
        return True
    u = url.lower()
    return any(p in u for p in JUNK_IMAGE_PATTERNS)

def is_video_or_gif_url(url: Optional[str]) -> bool:
    """Media fayli video (.mp4, .webm) yoki animatsiya (.gif) ekanligini aniqlash"""
    if not url:
        return False
    u = url.lower()
    return any(ext in u for ext in [".mp4", ".webm", ".mov", ".m4v", ".ogv", ".mkv", ".gif"])

def is_low_res_url(url: Optional[str]) -> bool:
    """Rasm o'lchami kichik thumbnail, emoji yoki past sifatli ekanligini aniqlash"""
    if not url:
        return True

    # Video yoki GIF larni statik rasm o'lchami bo'yicha cheklamaymiz
    if is_video_or_gif_url(url):
        return False

    if is_junk_or_emoji_url(url):
        return True

    low_markers = [
        "-184x111", "-150x150", "-100x100", "-300x",
        "thumbnail", "/thumb/", "avatar", "small", "160x", "184x",
        "72x72", "48x48", "32x32", "16x16", "64x64", "80x80", "96x96",
        "120x120", "128x128", "200x200", "250x250"
    ]
    u = url.lower()
    if any(m in u for m in low_markers):
        return True

    # Regex orqali kichik o'lchamdagi piksellarni aniqlash: masalan -72x72., /120x90/, _200x150.jpg
    dim_match = re.search(r'[-_/](\d{1,4})x(\d{1,4})[-_./]', u)
    if dim_match:
        w, h = int(dim_match.group(1)), int(dim_match.group(2))
        if w < 400 or h < 225:
            return True

    return False

def extract_image_from_entry(entry: Any) -> Optional[str]:
    """RSS entry ob'ektidan rasm, animatsiyali GIF yoki video URL manzilini qidirish"""
    # 1. media_content
    if entry.get("media_content"):
        # Avval video va GIF animatsiyalarni qidirish
        for item in entry.media_content:
            url = item.get("url")
            m_type = item.get("type", "").lower()
            medium = item.get("medium", "").lower()
            if url and not is_junk_or_emoji_url(url) and ("video" in m_type or medium == "video" or "gif" in m_type or is_video_or_gif_url(url)):
                return url

        # Keyin sifatli statik rasmlarni qidirish
        for item in entry.media_content:
            url = item.get("url")
            if url and not is_junk_or_emoji_url(url) and not is_low_res_url(url) and ("image" in item.get("type", "") or item.get("medium") == "image" or any(url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"])):
                return url
        first_url = entry.media_content[0].get("url")
        if first_url and not is_junk_or_emoji_url(first_url) and not is_low_res_url(first_url):
            return first_url

    # 2. enclosures (ko'pincha video va gif fayllar shu yerda bo'ladi)
    if entry.get("enclosures"):
        for enc in entry.enclosures:
            href = enc.get("href")
            e_type = enc.get("type", "").lower()
            if href and not is_junk_or_emoji_url(href) and ("video" in e_type or "gif" in e_type or is_video_or_gif_url(href)):
                return href
        for enc in entry.enclosures:
            href = enc.get("href")
            if href and not is_junk_or_emoji_url(href) and not is_low_res_url(href):
                return href

    # 3. media_thumbnail
    if entry.get("media_thumbnail"):
        for thumb in entry.media_thumbnail:
            t_url = thumb.get("url")
            if t_url and not is_junk_or_emoji_url(t_url) and not is_low_res_url(t_url):
                return t_url

    # 4. Content yoki Summary ichidagi <video>, <source>, yoki sifatli <img> teglari
    html_sources = []
    if "content" in entry and entry.content:
        for c in entry.content:
            if hasattr(c, "value"):
                html_sources.append(c.value)
    if "summary" in entry and entry.summary:
        html_sources.append(entry.summary)

    for html_text in html_sources:
        # Video teglari (<video src="...">, <source src="...">, data-src="...")
        vid_match = re.search(
            r'<(?:video|source)[^>]+(?:src|data-src)=["\'](https?://[^"\']+\.(?:mp4|webm|mov|m4v)(?:\?[^"\']*)?)["\']',
            html_text,
            re.IGNORECASE
        )
        if vid_match and not is_junk_or_emoji_url(vid_match.group(1)):
            return vid_match.group(1).replace("&amp;", "&")

        # Matn yoki atribut ichidagi to'g'ridan-to'g'ri .mp4 / .webm havolalari
        direct_vid = re.search(
            r'["\'](https?://[^"\'<>\\\s]+\.(?:mp4|webm|mov)(?:\?[^"\'<>\\\s]*)?)["\']',
            html_text,
            re.IGNORECASE
        )
        if direct_vid and not is_junk_or_emoji_url(direct_vid.group(1)):
            return direct_vid.group(1).replace("&amp;", "&")

        # GIF animatsiyalar
        for gif_m in re.finditer(r'<img[^>]+src=["\'](https?://[^"\']+\.gif(?:\?[^"\']*)?)["\']', html_text, re.IGNORECASE):
            g_url = gif_m.group(1).replace("&amp;", "&")
            if not is_junk_or_emoji_url(g_url):
                return g_url

        # Sifatli statik rasmlar (emojilar, smileylar va ikonkalar qat'iy chetlab o'tiladi)
        for img_match in re.finditer(r'<img\s+([^>]*?)src=["\'](https?://[^"\']+)["\']([^>]*)>', html_text, re.IGNORECASE):
            attrs = (img_match.group(1) + " " + img_match.group(3)).lower()
            img_url = img_match.group(2).replace("&amp;", "&")
            if any(k in attrs for k in ["wp-smiley", "emoji", "avatar", "icon"]):
                continue
            if not is_junk_or_emoji_url(img_url) and not is_low_res_url(img_url):
                return img_url

    return None

def extract_video_from_html(html_text: str) -> Optional[str]:
    """Veb-sahifa HTML kodidan to'g'ridan-to'g'ri video (.mp4, .webm, .mov) yoki og:video manzilini qidirish"""
    if not html_text:
        return None

    # 1. Meta teglar: og:video, og:video:secure_url, og:video:url, twitter:player:stream
    meta_video_patterns = [
        r'<meta\s+[^>]*?(?:property|name)=["\'](?:og:video(?::secure_url|:url)?|twitter:player:stream)["\'][^>]*?content=["\'](https?://[^"\']+)["\']',
        r'<meta\s+[^>]*?content=["\'](https?://[^"\']+)["\'][^>]*?(?:property|name)=["\'](?:og:video(?::secure_url|:url)?|twitter:player:stream)["\']',
    ]
    for pat in meta_video_patterns:
        for m in re.finditer(pat, html_text, re.IGNORECASE):
            v_url = m.group(1).replace("&amp;", "&").strip()
            # YouTube/Vimeo iframe player sahifalarini emas, haqiqiy video oqimlarini olamiz
            if is_video_or_gif_url(v_url) and not is_junk_or_emoji_url(v_url):
                return v_url

    # 2. <video> va <source> teglari
    for m in re.finditer(
        r'<(?:video|source)[^>]+(?:src|data-src)=["\'](https?://[^"\']+\.(?:mp4|webm|mov|m4v)(?:\?[^"\']*)?)["\']',
        html_text,
        re.IGNORECASE
    ):
        v_url = m.group(1).replace("&amp;", "&").strip()
        if not is_junk_or_emoji_url(v_url):
            return v_url

    # 3. JSON-LD contentUrl yoki sahifa ichidagi to'g'ridan-to'g'ri .mp4 video
    for m in re.finditer(
        r'["\'](?:contentUrl|videoUrl|video_url|streamUrl)["\']\s*:\s*["\'](https?://[^"\'<>\\\s]+\.(?:mp4|webm|mov)(?:\?[^"\'<>\\\s]*)?)["\']',
        html_text,
        re.IGNORECASE
    ):
        v_url = m.group(1).replace("\\/", "/").replace("&amp;", "&").strip()
        if not is_junk_or_emoji_url(v_url):
            return v_url

    return None

def extract_paragraphs_from_html(html_text: str, max_chars: int = 2500) -> str:
    """Maqola sahifasidan asosiy matn paragraflarini (<p>) toza holda ajratib olish (boyitilgan tarjima uchun)"""
    if not html_text:
        return ""

    # Script, style, nav, footer, header qismlarini olib tashlash
    cleaned_body = re.sub(r'<(?:script|style|nav|footer|header|aside|noscript)[^>]*>.*?</(?:script|style|nav|footer|header|aside|noscript)>', ' ', html_text, flags=re.IGNORECASE | re.DOTALL)

    desc = ""
    for d_pat in [
        r'<meta\s+[^>]*?(?:property|name)=["\'](?:og:description|description)["\'][^>]*?content=["\']([^"\']+)["\']',
        r'<meta\s+[^>]*?content=["\']([^"\']+)["\'][^>]*?(?:property|name)=["\'](?:og:description|description)["\']',
    ]:
        dm = re.search(d_pat, cleaned_body, re.IGNORECASE)
        if dm:
            desc = clean_html(dm.group(1))
            break

    junk_phrases = [
        "cookie", "subscribe", "newsletter", "privacy policy", "terms of service",
        "all rights reserved", "sign up for", "read more:", "related articles",
        "advertisement", "sponsored", "affiliate commission", "follow us on"
    ]

    paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', cleaned_body, re.IGNORECASE | re.DOTALL)
    collected = []
    if desc and len(desc) > 35:
        collected.append(desc)

    total_len = len(desc)
    for p in paragraphs:
        cp = clean_html(p)
        if len(cp) < 45:
            continue
        cp_low = cp.lower()
        if any(jp in cp_low for jp in junk_phrases):
            continue
        if cp not in collected:
            collected.append(cp)
            total_len += len(cp)
            if total_len >= max_chars or len(collected) >= 8:
                break

    return "\n\n".join(collected)[:max_chars].strip()

async def extract_og_image(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """Maqola sahifasidan 1-navbatda VIDEO (og:video / <video>), topilmasa Full HD og:image olish"""
    try:
        resp = await client.get(url, timeout=10.0, follow_redirects=True)
        if resp.status_code == 200:
            # 1. Avval sahifada video bormi tekshiramiz (videoli xabarlar ustuvor!)
            vid_url = extract_video_from_html(resp.text)
            if vid_url:
                return vid_url

            # 2. Keyin og:image yoki twitter:image
            for pattern in [
                r'<meta\s+[^>]*?property=["\']og:image["\'][^>]*?content=["\']([^"\']+)["\']',
                r'<meta\s+[^>]*?content=["\']([^"\']+)["\'][^>]*?property=["\']og:image["\']',
                r'<meta\s+[^>]*?name=["\']twitter:image["\'][^>]*?content=["\']([^"\']+)["\']',
                r'<meta\s+[^>]*?content=["\']([^"\']+)["\'][^>]*?name=["\']twitter:image["\']'
            ]:
                m = re.search(pattern, resp.text, re.IGNORECASE)
                if m:
                    cand = m.group(1).replace("&amp;", "&").strip()
                    if not is_junk_or_emoji_url(cand) and not is_low_res_url(cand):
                        return cand
    except Exception:
        pass
    return None

async def enrich_entry_from_webpage(
    url: str,
    client: httpx.AsyncClient,
    current_summary: str,
    current_media: Optional[str]
) -> tuple[str, Optional[str]]:
    """
    Maqola veb-sahifasiga kirib:
    1) Agar sahifada VIDEO (.mp4/.webm/.mov) bo'lsa, uni oladi (hatto RSS statik rasm bergan bo'lsa ham!).
    2) Agar RSS rasm bermagan bo'lsa, Full HD og:image oladi.
    3) Agar RSS matni (summary) qisqa bo'lsa (< 900 belgi), sahifadagi to'liq paragraflarni qo'shib boyitadi!
    """
    new_summary = current_summary or ""
    new_media = current_media

    try:
        resp = await client.get(url, timeout=10.0, follow_redirects=True)
        if resp.status_code != 200 or not resp.text:
            return new_summary, new_media

        html_text = resp.text

        # 1. Har doim sahifadan VIDEO qidirib ko'ramiz (agar joriy media allaqachon video bo'lmasa)
        if not is_video_or_gif_url(new_media):
            page_video = extract_video_from_html(html_text)
            if page_video:
                new_media = page_video

        # 2. Agar hali ham media yo'q yoki past sifatli bo'lsa, og:image olamiz
        if not new_media or (not is_video_or_gif_url(new_media) and (is_low_res_url(new_media) or is_junk_or_emoji_url(new_media))):
            for pattern in [
                r'<meta\s+[^>]*?property=["\']og:image["\'][^>]*?content=["\']([^"\']+)["\']',
                r'<meta\s+[^>]*?content=["\']([^"\']+)["\'][^>]*?property=["\']og:image["\']',
                r'<meta\s+[^>]*?name=["\']twitter:image["\'][^>]*?content=["\']([^"\']+)["\']',
                r'<meta\s+[^>]*?content=["\']([^"\']+)["\'][^>]*?name=["\']twitter:image["\']'
            ]:
                m = re.search(pattern, html_text, re.IGNORECASE)
                if m:
                    cand = m.group(1).replace("&amp;", "&").strip()
                    if not is_junk_or_emoji_url(cand) and not is_low_res_url(cand):
                        new_media = cand
                        break

        # 3. Maqola matnini boyitish (AI chala yoki mavhum tarjima qilmasligi uchun to'liq faktlarni yig'ish)
        if len(new_summary) < 900:
            rich_text = extract_paragraphs_from_html(html_text, max_chars=2500)
            if len(rich_text) > len(new_summary):
                if new_summary and new_summary[:80] not in rich_text:
                    new_summary = f"{new_summary}\n\n{rich_text}"[:2500]
                else:
                    new_summary = rich_text[:2500]
    except Exception:
        pass

    return new_summary, new_media

def upgrade_image_url(url: Optional[str]) -> Optional[str]:
    """Kichik pikselli yoki past sifatli thumbnail rasmlarni original Full HD sifatiga ko'tarish"""
    if not url:
        return None
    clean = url.strip().replace("&amp;", "&")
    
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

    cat_lower = category.lower()
    if any(k in cat_lower for k in ["ai", "startup", "intellekt", "modellar", "llm", "deepmind", "openai"]):
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
            raw_summary = ""
            if hasattr(entry, "content") and entry.content:
                for c in entry.content:
                    val = getattr(c, "value", "")
                    if val and len(val) > len(raw_summary):
                        raw_summary = val
            if not raw_summary:
                raw_summary = getattr(entry, "summary", "") or ""
            summary = clean_html(raw_summary)
            if len(summary) > 2500:
                summary = summary[:2500]

            # Do'kon chegirmalari, aksiyalar va savdo reklamalarini chetlab o'tish
            if is_commercial_deal(clean_title, link, summary):
                continue

            # Kino, serial va o'yinlar kabi ikkinchi darajali mavzularni chetlab o'tish
            if is_unwanted_topic(clean_title, link, summary):
                continue

            # Bazada bormi yoki o'xshash mavzu chiqdimi tekshirish
            if await is_duplicate_news(link, clean_title):
                continue

            # Rasm yoki video topish (Eng sifatli HD media va Video tanlash)
            img_url = extract_image_from_entry(entry)

            # Maqola sahifasiga kirib:
            # 1) Agar sahifada VIDEO (.mp4/.webm/.mov) bo'lsa uni ustuvor olish,
            # 2) Agar RSS matni qisqa bo'lsa (< 900 belgi), sahifadagi to'liq paragraflarni qo'shib boyitish!
            summary, img_url = await enrich_entry_from_webpage(
                url=link,
                client=client,
                current_summary=summary,
                current_media=img_url
            )

            img_url = upgrade_image_url(img_url)

            # Agar yakuniy rasm ham past sifatli yoki emoji bo'lib qolsa, buzilgan holatda yubormaslik uchun tozalaymiz
            if img_url and not is_video_or_gif_url(img_url):
                if is_junk_or_emoji_url(img_url) or is_low_res_url(img_url):
                    img_url = None

            priority = get_topic_priority(clean_title, summary, source.get("category", ""))
            # Videoli yangiliklarga qo'shimcha ustuvorlik (+25 ball) beramiz
            if is_video_or_gif_url(img_url):
                priority += 25
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
    # Yoqilgan (aktiv) manbalarni aniqlash
    enabled_sources = []
    for src in config.RSS_SOURCES:
        slug = src.get("slug", src["name"].lower().replace(" ", "_"))
        is_on = await database.get_setting(f"feed_{slug}_enabled", "1")
        if is_on == "1":
            enabled_sources.append(src)

    if not enabled_sources:
        return []

    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
        tasks = [
            fetch_feed_entries(src, client, limit=limit_per_feed)
            for src in enabled_sources
        ]
        feeds_results = await asyncio.gather(*tasks)

    # Natijalarni birlashtirish va prioriteti bo'yicha saralash (AI va Startaplar 1-o'rinda)
    all_new_news = []
    for news_list in feeds_results:
        all_new_news.extend(news_list)

    all_new_news.sort(key=lambda x: x.get("priority", 0), reverse=True)
    return all_new_news

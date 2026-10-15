import os
import re
import html
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta
import httpx
from google import genai
from google.genai import types
import config

LAST_FORMATTER_USED = "gemini"

class GeminiKeyNode:
    """Multi-Key hovuzidagi har bir API kalit uchun holat va statistika konteyneri"""
    def __init__(self, key: str, index: int):
        self.key = key
        self.index = index
        self.masked = key[:6] + "..." + key[-4:] if len(key) > 10 else f"Key-{index}"
        self.client = None
        self._init_client()
        self.cooldown_until: Optional[datetime] = None
        self.total_requests = 0
        self.total_success = 0
        self.total_errors = 0
        self.consecutive_errors = 0
        self.last_error = ""
        self.last_used: Optional[datetime] = None

    def _init_client(self):
        try:
            self.client = genai.Client(
                api_key=self.key,
                http_options=types.HttpOptions(timeout=15000)
            )
        except Exception as e:
            print(f"[Gemini Client Init Xatolik Key #{self.index}]: {e}")
            self.client = None

    def is_available(self) -> bool:
        if self.cooldown_until is None:
            return True
        now = datetime.now(timezone.utc)
        return now >= self.cooldown_until

    def trigger_cooldown(self, seconds: int = 300, reason: str = "429 Quota"):
        now = datetime.now(timezone.utc)
        self.cooldown_until = now + timedelta(seconds=seconds)
        self.last_error = f"{reason} ({seconds}s)"
        self.consecutive_errors += 1
        self.total_errors += 1
        print(f"[Gemini KeyPool] Kalit #{self.index} ({self.masked}) {reason} tufayli {seconds} soniyaga sovutish rejimiga o'tkazildi.")

    def record_success(self):
        self.total_requests += 1
        self.total_success += 1
        self.consecutive_errors = 0
        self.cooldown_until = None
        self.last_used = datetime.now(timezone.utc)

class GeminiKeyPool:
    """
    Gemini API kalitlari uchun yuklamani teng taqsimlash (Round-Robin)
    va avtomatik nosozlikdan qochish (Smart Failover & Cooldown) menejeri
    """
    def __init__(self):
        self.nodes: List[GeminiKeyNode] = []
        self._rr_index = 0
        self.init_pool()

    def init_pool(self):
        keys = config.get_gemini_api_keys()
        self.nodes = [GeminiKeyNode(k, i + 1) for i, k in enumerate(keys) if k.strip()]

    def get_ordered_nodes(self) -> List[GeminiKeyNode]:
        """Round-Robin bo'yicha navbatdagi aktiv kalitdan boshlab ketma-ket ro'yxat shakllantirish"""
        if not self.nodes:
            self.init_pool()
        if not self.nodes:
            return []

        now = datetime.now(timezone.utc)
        # 1. Hozirda sovutishda bo'lmagan, tayyor kalitlarni aniqlash
        active = [n for n in self.nodes if n.is_available() and n.client is not None]

        # 2. Agar barcha kalitlar sovutishda bo'lsa (favqulodda holat), eng tez ochiladiganini sinash
        if not active:
            valid_nodes = [n for n in self.nodes if n.client is not None]
            if not valid_nodes:
                return []
            earliest = min(valid_nodes, key=lambda n: n.cooldown_until or now)
            return [earliest] + [n for n in valid_nodes if n != earliest]

        # 3. Round-Robin bo'yicha aylantirish (so'rovlarni teng taqsimlash)
        idx = self._rr_index % len(active)
        self._rr_index = (self._rr_index + 1) % len(active)

        selected = active[idx]
        other_active = [n for n in active if n != selected]
        cooldown_nodes = sorted(
            [n for n in self.nodes if not n.is_available() and n.client is not None],
            key=lambda n: n.cooldown_until or now
        )
        return [selected] + other_active + cooldown_nodes

    def get_status_report(self) -> List[Dict[str, Any]]:
        """Diagnostika (/status) uchun kalitlar statistikasi va holatini berish"""
        now = datetime.now(timezone.utc)
        report = []
        for n in self.nodes:
            if n.client is None:
                st = "🔴 Init Xatolik"
            elif n.is_available():
                st = f"🟢 Faol ({n.total_success} muvaffaqiyatli)" if n.total_success > 0 else "🟢 Faol (Ready)"
            else:
                rem = int((n.cooldown_until - now).total_seconds()) if n.cooldown_until else 0
                st = f"🟡 Sovutishda (429 - {max(0, rem)}s qoldi)"
            report.append({
                "index": n.index,
                "masked": n.masked,
                "status": st,
                "requests": n.total_requests,
                "success": n.total_success,
                "errors": n.total_errors,
                "consecutive_errors": n.consecutive_errors,
                "cooldown": not n.is_available()
            })
        return report

# Global yagona hovuz nusxasi
GEMINI_POOL = GeminiKeyPool()

def get_all_clients() -> list:
    """Barcha mavjud API kalitlar uchun mijozlar ro'yxati (orqaga moslik uchun)"""
    return [n.client for n in GEMINI_POOL.nodes if n.client is not None]

def get_gemini_client() -> Optional[genai.Client]:
    """Navbatdagi eng maqbul Gemini klientini olish"""
    nodes = GEMINI_POOL.get_ordered_nodes()
    return nodes[0].client if nodes else None

async def execute_gemini_prompt(prompt: str) -> Optional[str]:
    """
    Multi-Key Pool (Round-Robin & Smart Failover) orqali so'rov yuborish.
    - Har bir so'rov kalitlar o'rtasida navbatma-navbat (Round-Robin) taqsimlanadi.
    - 429 (kvota tugashi) olgan kalit avtomatik 5 daqiqaga sovutish rejimiga qo'yiladi.
    - Zudlik bilan navbatdagi zaxira kalitga uzatiladi.
    """
    candidate_models = [
        "gemini-3.1-flash-lite-preview",
        "gemini-3-flash-preview",
        "gemini-3.8-flash",
        "gemini-flash-latest"
    ]
    nodes = GEMINI_POOL.get_ordered_nodes()
    if not nodes:
        return None

    # Kalitlar bo'ylab navbat bilan urinish (Round-Robin tartibida)
    for node in nodes:
        if node.client is None:
            continue
        for model_name in candidate_models:
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        node.client.models.generate_content,
                        model=model_name,
                        contents=prompt
                    ),
                    timeout=20.0
                )
                if response and response.text:
                    node.record_success()
                    return response.text
            except (asyncio.TimeoutError, Exception) as e:
                err_msg = str(e)
                # 429 kvota limiti tekshiruvi
                if "429" in err_msg or "ResourceExhausted" in err_msg or "quota" in err_msg.lower():
                    node.trigger_cooldown(seconds=300, reason="429 Quota")
                    # Ushbu kalitda boshqa modellarni sinab o'tirmaymiz (chunki kalit kvotasi tugagan),
                    # zudlik bilan keyingi kalitga o'tamiz!
                    break
                elif "503" in err_msg or "unavailable" in err_msg.lower():
                    node.trigger_cooldown(seconds=60, reason="503 Server Busy")
                    break
                else:
                    node.total_errors += 1
                    continue

    return None

def clean_ugly_symbols(text: str) -> str:
    """Xom HTML teglar, [...] belgilari va RSS qoldiqlarini tozalash (qatorlarni saqlagan holda)"""
    if not text:
        return ""
    # HTML teglarni tozalash
    text = re.sub(r'<[^>]+>', ' ', text)
    # WordPress va RSS qo'shimchalarini tozalash
    text = re.sub(r'The post .*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'First appeared on .*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[\s*Read\s+more\s*\]', '', text, flags=re.IGNORECASE)
    # [...] yoki [...]... yoki [&hellip;] kabilarni olib tashlash
    text = re.sub(r'\[\s*\.{2,}\s*\]\.{0,}', '', text)
    text = re.sub(r'\[\s*…\s*\]\.{0,}', '', text)
    text = re.sub(r'&hellip;', '', text)
    text = re.sub(r'\[\s*\.\.\.\s*\]', '', text)
    text = re.sub(r'\.{3,}', '', text)
    # Faqat bir qatordagi ortiqcha bo'shliqlarni bittaga keltirish (lekin \n qator tashlashlarni saqlash!)
    text = re.sub(r'[^\S\r\n]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    # Agar jumla oxiri chala uzilgan bo'lsa, oxirgi nuqtagacha qisqartirish
    if text and not text.endswith(('.', '!', '?', ':')):
        last_punct = max(text.rfind('.'), text.rfind('!'), text.rfind('?'))
        if last_punct > len(text) * 0.4:
            text = text[:last_punct + 1]
        else:
            text += "."
    return text.strip()

async def translate_to_uzbek(text: str) -> str:
    """Matnni toza o'zbek tiliga erkin tarjima qilish (Google Translate vositasida)"""
    cleaned = clean_ugly_symbols(text)
    if not cleaned:
        return ""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": "uz", "dt": "t", "q": cleaned}
            )
            if resp.status_code == 200:
                data = resp.json()
                translated_parts = [item[0] for item in data[0] if item[0]]
                res = "".join(translated_parts).strip()
                return clean_ugly_symbols(res)
    except Exception as e:
        print(f"[Tarjima xatosi]: {e}")
    return cleaned

def clean_all_asterisks_to_html(text: str) -> str:
    """Barcha Markdown ** yoki * yulduzchalarini Telegram HTML <b> teglariga o'tkazish yoki tozalash"""
    if not text:
        return ""
    # 1. <b>**...**</b> yoki **<b>...</b>** aralash holatlarni tozalash
    text = re.sub(r'<b>\s*\*{1,3}(.*?)\*{1,3}\s*</b>', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'\*{1,3}\s*<b>(.*?)</b>\s*\*{1,3}', r'<b>\1</b>', text, flags=re.DOTALL)

    # 2. **matn** juftlarini (hatto ko'p qatorli bo'lsa ham) <b>matn</b> ga aylantirish
    text = re.sub(r'\*\*(.+?)\*\*', lambda m: f"<b>{m.group(1).strip()}</b>", text, flags=re.DOTALL)

    # 3. Yolg'iz yoki ortiqcha qolgan barcha ** larni to'liq olib tashlash
    text = re.sub(r'\*{2,}', '', text)

    # 4. <b> ichidagi ortiqcha yangi qator (\n) larni teglardan tashqariga chiqarish
    text = re.sub(r'<b>(.*?)(\n+)</b>', r'<b>\1</b>\2', text)
    text = re.sub(r'<b>\s*</b>', '', text)
    return text

def sanitize_telegram_html(text: str) -> str:
    """Telegram qabul qiladigan HTML teglarini tozalash va xavfsiz qilish"""
    text = re.sub(r'```(?:html)?', '', text)
    text = re.sub(r'```', '', text)
    text = re.sub(r'\[\s*\.{2,}\s*\]\.{0,}', '', text)
    text = re.sub(r'\[\s*…\s*\]\.{0,}', '', text)
    text = re.sub(r'&hellip;', '', text)
    text = re.sub(r'\[\s*\.\.\.\s*\]', '', text)

    text = clean_all_asterisks_to_html(text)

    # Faqat gorizontal bo'shliqlar tozalanadi, qator tashlashlar (\n) saqlanadi!
    text = re.sub(r'[^\S\r\n]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def format_paragraph_spacing(text: str) -> str:
    """Sarlavha, mohiyat, sitata va xulosalar orasida chiroyli bo'sh qatorlar (joy tashlashlar) bo'lishini ta'minlash"""
    if not text:
        return ""

    # 0. Har qanday 'Mohiyati:' so'zini olib tashlaymiz (emojidan keyin darhol matn boshlansin)
    text = re.sub(
        r'((?:<tg-emoji[^>]*>)?📌(?:</tg-emoji>)?)\s*(?:<b>)?\s*Mohiyati:?\s*(?:</b>)?\s*',
        r'\1 ',
        text,
        flags=re.IGNORECASE
    )

    # 1. Sarlavha va 📌 orasida 2 ta bo'sh qator (\n\n)
    text = re.sub(
        r'([^\n])\s*((?:<tg-emoji[^>]*>)?📌(?:</tg-emoji>)?)',
        r'\1\n\n\2',
        text
    )

    # 2. 📌 matni va <blockquote orasida 2 ta bo'sh qator (\n\n)
    text = re.sub(
        r'([^\n])\s*(<blockquote[^>]*>)',
        r'\1\n\n\2',
        text,
        flags=re.IGNORECASE
    )

    # 3. <blockquote va Asosiy tafsilotlar orasida
    text = re.sub(
        r'(<blockquote[^>]*>)\s*([^\n])',
        r'\1\2',
        text,
        flags=re.IGNORECASE
    )

    # Asosiy tafsilotlar va birinchi punkt (•) orasida bo'sh qator
    text = re.sub(
        r'((?:📊|📝|📌)?\s*(?:<b>)?\s*(?:Asosiy\s+)?tafsilotlar:?\s*(?:</b>)?)\s*([^\n])',
        r'\1\n\n\2',
        text,
        flags=re.IGNORECASE
    )

    # 4. Punktlar (•) orasida bo'sh qator
    text = re.sub(
        r'([^\n])\s*(•)',
        r'\1\n\n\2',
        text
    )

    # 5. Punkt yoki matn va 💡 Xulosa orasida bo'sh qator
    text = re.sub(
        r'([^\n])\s*((?:<b>)?\s*💡\s*(?:<b>)?\s*Xulosa:?\s*(?:</b>)?)',
        r'\1\n\n\2',
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(
        r'((?:<b>)?\s*💡\s*(?:<b>)?\s*Xulosa:?\s*(?:</b>)?)\s*([^\n])',
        r'\1\n\2',
        text,
        flags=re.IGNORECASE
    )

    # 6. </blockquote> va oldingi matn/keyingi matn
    text = re.sub(
        r'([^\n])\s*(</blockquote>)',
        r'\1\2',
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(
        r'(</blockquote>)\s*([^\n])',
        r'\1\n\n\2',
        text,
        flags=re.IGNORECASE
    )

    # 7. Hashtaglar bloki oldidan bo'sh qator
    text = re.sub(r'([^\n])\s*(🏷)', r'\1\n\n\2', text)
    if "🏷" not in text:
        text = re.sub(r'([^\n#\s])\s+(#\w+)', r'\1\n\n\2', text)

    # 8. 📡 Manba oldidan bo'sh qator
    text = re.sub(
        r'([^\n])\s*((?:<tg-emoji[^>]*>)?📡(?:</tg-emoji>)?\s*(?:<b>)?\s*Manba:?\s*(?:</b>)?)',
        r'\1\n\n\2',
        text,
        flags=re.IGNORECASE
    )

    # 9. Ketma-ket 3 yoki undan ortiq bo'sh qatorlarni 2 taga keltirish
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def apply_premium_emojis(text: str) -> str:
    """Agar Telegram Premium Emoji ID lari ko'rsatilgan bo'lsa, mos emojilarni <tg-emoji> formatiga o'tkazish"""
    if not text:
        return ""
    # 1. Avval mavjud <tg-emoji> teglarini tozalab, ichidagi belgi holatiga qaytaramiz (idempotentlik va yangilanish uchun)
    text = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', text)

    mappings = [
        (r'⚡️?', "lightning", "⚡️"),
        (r'📌', "pin", "📌"),
        (r'(?:📊|📝)', "chart", "📊"),
        (r'(?:💡|💭)', "bulb", "💡"),
        (r'📡', "antenna", "📡"),
        (r'(?:🏷️?|🔖)', "tag", "🏷"),
    ]
    for char_regex, key, fallback in mappings:
        eid = str(config.PREMIUM_EMOJIS.get(key, "")).strip()
        if eid and eid.isdigit():
            text = re.sub(char_regex, f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>', text)
    return text

def fix_blockquote_tags(t: str) -> str:
    """Ochiluvchi sitata (blockquote expandable) teglarini xavfsiz muvozanatlash"""
    # 1. Agar </blockquote> bor, lekin <blockquote yo'q bo'lsa
    if "</blockquote>" in t and "<blockquote" not in t:
        m = re.search(r'(📊|📝|📌\s*<b>?\s*Asosiy tafsilotlar|Asosiy tafsilotlar)', t)
        if m:
            t = t[:m.start()] + "<blockquote expandable>" + t[m.start():]
        else:
            t = t.replace("</blockquote>", "")

    # 2. Agar <blockquote bor, lekin </blockquote> yo'q bo'lsa
    if "<blockquote" in t and "</blockquote>" not in t:
        m = re.search(r'(🏷|#)', t)
        if m and m.start() > t.find("<blockquote"):
            t = t[:m.start()].strip() + "</blockquote>\n\n" + t[m.start():].strip()
        else:
            t += "</blockquote>"

    # 3. Agar umuman blockquote bo'lmasa, tafsilotlar qismini o'rab qo'yish
    if "<blockquote" not in t and "</blockquote>" not in t:
        m = re.search(r'(📊|📝|Asosiy tafsilotlar)', t)
        if m:
            start_pos = m.start()
            hash_match = re.search(r'(🏷|#)', t[start_pos:])
            if hash_match:
                end_pos = start_pos + hash_match.start()
                t = t[:start_pos] + "<blockquote expandable>" + t[start_pos:end_pos].strip() + "</blockquote>\n\n" + t[end_pos:].strip()
            else:
                t = t[:start_pos] + "<blockquote expandable>" + t[start_pos:].strip() + "</blockquote>"

    return t.strip()

def safe_telegram_caption(caption: str, footer: str = "", max_len: int = 1024) -> str:
    """Caption va footerni max_len belgidan oshirmasdan va ochiq HTML teglarni buzmasdan xavfsiz tayyorlash"""
    caption = clean_all_asterisks_to_html(caption)
    caption = apply_premium_emojis(caption)
    footer_text = footer if footer else ""
    full = (caption.strip() + "\n\n" + footer_text.strip()) if footer_text else caption.strip()

    # Telegram 1024 belgilik caption limitini teglarsiz sof ko'rinadigan matn bo'yicha hisoblaydi
    visible_text = re.sub(r'<[^>]+>', '', full)
    if len(visible_text) <= max_len:
        return full

    # Agar ko'rinadigan matn 1024 dan oshsa, footerni to'liq saqlab, captionni qisqartiramiz
    reserved_footer_len = len(re.sub(r'<[^>]+>', '', footer_text)) + 6 if footer_text else 0
    target_caption_len = max_len - reserved_footer_len
    if target_caption_len <= 150:
        target_caption_len = max_len - 10
        footer_text = ""

    trimmed = caption[:target_caption_len]
    last_break = max(trimmed.rfind('\n\n'), trimmed.rfind('\n'), trimmed.rfind(' '))
    if last_break > 100:
        trimmed = trimmed[:last_break]

    # Ochiq qolgan <a> yoki <tg-emoji> teglarini tozalash
    if trimmed.rfind("<a") > trimmed.rfind("</a>"):
        trimmed = trimmed[:trimmed.rfind("<a")].rstrip()

    if trimmed.rfind("<tg-emoji") > trimmed.rfind("</tg-emoji>"):
        trimmed = trimmed[:trimmed.rfind("<tg-emoji")].rstrip()

    # Ochiq qolgan teglarni yopish
    if trimmed.count("<b>") > trimmed.count("</b>"):
        trimmed += "</b>"
    if trimmed.count("<i>") > trimmed.count("</i>"):
        trimmed += "</i>"
    if "<blockquote" in trimmed and "</blockquote>" not in trimmed:
        trimmed += "</blockquote>"

    result = trimmed.strip()
    if footer_text:
        result += "\n\n" + footer_text.strip()
    return result

def attach_source_link(text: str, source_name: str, original_url: str) -> str:
    """Post matnidagi '📡 Manba: ...' qismini to'g'irlash:
    - 'Mohiyati:' so'zini tozalaydi (📌 dan keyin to'g'ridan-to'g'ri matn bo'ladi)
    - Sitata (blockquote) ichida yoki matnda qolgan eski Manba qatorlarini olib tashlaydi
    - Barcha dublikatlarni tozalab, postning eng oxiriga (Kanalimizga obuna bo'ling tepasiga) 1 dona toza havola qo'yadi
    """
    if not text:
        return text

    # 1. 'Mohiyati:' so'zini olib tashlaymiz
    text = re.sub(
        r'((?:<tg-emoji[^>]*>)?📌(?:</tg-emoji>)?)\s*(?:<b>)?\s*Mohiyati:?\s*(?:</b>)?\s*',
        r'\1 ',
        text,
        flags=re.IGNORECASE
    )

    safe_url = original_url.strip() if original_url else ""
    safe_source = html.escape(source_name) if source_name else "Batafsil"

    # 2. Mavjud barcha '📡 Manba: ...' qatorlarini (blockquote ichida yoki tashqarisidagi dublikatlarni) tozalaymiz
    text = re.sub(
        r'\n*\s*(?:<tg-emoji[^>]*>)?📡(?:</tg-emoji>)?\s*(?:<b>)?\s*Manba:?\s*(?:</b>)?\s*(?:<a\s+[^>]*>.*?</a>|[^<\n]*)(?=\s*</blockquote>|\n|$)',
        '',
        text,
        flags=re.IGNORECASE
    )

    # 3. Agar </blockquote> oxirida ortiqcha bo'sh qatorlar qolgan bo'lsa tozalash
    text = re.sub(r'\n+\s*</blockquote>', '</blockquote>', text)

    # 4. Endi yagona, toza '📡 Manba: ...' qatorini shakllantiramiz
    if safe_url.startswith("http"):
        link_html = f'📡 <b>Manba:</b> <a href="{safe_url}">{safe_source}</a>'
    else:
        link_html = f'📡 <b>Manba:</b> {safe_source}'

    # 5. Manba postning eng oxiriga, ya'ni kanal imzosi (🔔 Kanalimizga obuna bo'ling) ning tepasiga qo'yiladi!
    if "🔔" in text and "obuna" in text.lower():
        parts = re.split(
            r'(\n*\s*(?:<tg-emoji[^>]*>)?🔔(?:</tg-emoji>)?\s*<b>?\s*Kanalimizga obuna)',
            text,
            maxsplit=1,
            flags=re.IGNORECASE
        )
        if len(parts) >= 2:
            body = parts[0].rstrip()
            footer_part = "".join(parts[1:]).lstrip()
            return f"{body}\n\n{link_html}\n\n{footer_part}"

    return text.rstrip() + f"\n\n{link_html}"

async def fallback_formatter(title: str, summary: str, source_name: str, original_url: str = "") -> str:
    """Gemini ishlamagan taqdirda o'zbekcha tarjima qilib, ochiluvchi sitata (blockquote expandable) bilan xavfsiz formatlash"""
    clean_title = clean_ugly_symbols(title)
    clean_sum = clean_ugly_symbols(summary)

    uz_title_raw = await translate_to_uzbek(clean_title)
    uz_summary_raw = await translate_to_uzbek(clean_sum)

    uz_title = html.escape(clean_ugly_symbols(uz_title_raw[:150]))

    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', uz_summary_raw) if s.strip()]
    if len(sentences) >= 2:
        essence_raw = sentences[0]
        details_list = [f"• {s}" for s in sentences[1:4]]
        more_details_raw = "\n\n".join(details_list)
    elif len(sentences) == 1:
        essence_raw = sentences[0]
        more_details_raw = f"• Yangilik tafsilotlari: {uz_title_raw} bo'yicha birlamchi ma'lumotlar taqdim etildi."
    else:
        essence_raw = uz_title_raw
        more_details_raw = f"• {source_name} orqali e'lon qilingan eng so'nggi xabar."

    essence = html.escape(clean_ugly_symbols(essence_raw[:260]))
    more_details = html.escape(clean_ugly_symbols(more_details_raw[:450]))
    safe_source = html.escape(source_name)

    source_link = f"<a href='{original_url}'>{safe_source}</a>" if original_url else safe_source

    post = (
        f"⚡️ <b>{uz_title}</b>\n\n"
        f"📌 {essence}\n\n"
        f"<blockquote expandable>📊 <b>Asosiy tafsilotlar:</b>\n\n"
        f"{more_details}</blockquote>\n\n"
        f"🏷 #AI #IT #Texnologiya #Yangiliklar\n\n"
        f"📡 <b>Manba:</b> {source_link}"
    )
    spaced = format_paragraph_spacing(post)
    return apply_premium_emojis(spaced)

async def format_news(title: str, summary: str, source_name: str, category: str = "AI & IT", original_url: str = "") -> str:
    """Yangilikni o'zbek tiliga professional sarlavha, qisqa mohiyat va ochiluvchi sitata bilan tayyorlash"""
    client = get_gemini_client()

    prompt = f"""Sen — "{config.CHANNEL_USERNAME}" Telegram kanali uchun yetakchi IT va AI tahlilchisisan.
Quyidagi xabarni o'zbek tilida juda aniq, jozibador, CHUQUR VA MAZMUNGA BOY qilib qayta yoz.

Sarlavha: {title}
Bo'lim: {category}
Manba: {source_name}
Matn: {summary}

QAT'IY FORMAT TALABLARI (Har bir bo'lim orasida ALBATTA 2 ta bo'sh qator joy tashla!):

⚡️ <b>(1 ta kuchli, e'tiborni tortuvchi sarlavha)</b>

📌 (Asosiy voqea mohiyati: 1-2 ta aniq, teran jumla bilan nima bo'lganini tushuntir. DIQQAT: "Mohiyati:" so'zini aslo yozma! To'g'ridan-to'g'ri 📌 emojisi ortidan matn boshlansin!).

<blockquote expandable>📊 <b>Asosiy tafsilotlar:</b>
• (1-punkt: Texnologik mohiyat, parametrlar, yangi funksiyalar yoki modellar imkoniyati haqida aniq faktlar)

• (2-punkt: Benchmark natijalari, tezlik, aniqlik, xotira yoki moliyalashtirish/narx ko'rsatkichlari)

• (3-punkt: Mazkur yangilik amaliyotda kimlar uchun va qanday muammolarni hal qilishda ishlatilishi)

💡 <b>Xulosa:</b>
(1 ta teran tahliliy jumla bilan mazkur yangilikning soha kelajagiga ta'siri).</blockquote>

🏷 #AI #Texnologiya #{source_name.replace(' ', '')}

📡 <b>Manba:</b> {source_name}

QOIDALAR:
1. "Mohiyati:" so'zi aslo ishlatilmasin, 📌 dan keyin to'g'ridan-to'g'ri matn yozilsin.
2. 📡 <b>Manba:</b> qismi sitata (<blockquote) ichida emas, sitatadan TASHQARIDA va hashtagdan keyin, eng pastda tursin!
3. Bo'limlar bir-biriga yopishib qolmasin, oralarida aniq bo'sh qatorlar bo'lsin.
4. "Asosiy tafsilotlar" bo'limida 2-3 ta sermazmun punkt bo'lsin, umumiy yoki quruq gaplar bo'lmasin. Haqiqiy faktlar, raqamlar va texnik xususiyatlarni kirit!
5. HECH QACHON do'kon chegirmalari (deals), aksiyalar, narx arzonlashuvi (Save $..., % off, chegirma) haqida yozma! Texnologiyaning o'ziga e'tibor qarat!
6. HECH QACHON [...] yoki ... yoki chala gap yozma!
7. HECH QACHON Markdown yulduzchalaridan (**matn**) foydalanma! Qalin matn uchun faqat <b>matn</b> HTML teglaridan foydalan!
8. Telegram caption limiti 1024 belgi! Jami ko'rinadigan matn uzunligi 750-880 belgi oralig'ida bo'lsin.
"""

    global LAST_FORMATTER_USED
    raw_text = await execute_gemini_prompt(prompt)
    if raw_text:
        cleaned = sanitize_telegram_html(raw_text)
        cleaned = fix_blockquote_tags(cleaned)
        cleaned = attach_source_link(cleaned, source_name, original_url)
        cleaned = format_paragraph_spacing(cleaned)
        LAST_FORMATTER_USED = "gemini"
        return apply_premium_emojis(cleaned)

    LAST_FORMATTER_USED = "fallback"
    return await fallback_formatter(title, summary, source_name, original_url)

async def regenerate_news(title: str, summary: str, current_caption: str) -> str:
    """Matnni qayta generatsiya qilish (Multi-Key Pool yordamida)"""
    prompt = f"""Ushbu xabarni yanada ixchamroq, qisqa tezislar bilan, bo'limlar orasida 2 ta bo'sh qator tashlab, ochiluvchi sitata (<blockquote expandable>) formatida o'zbek tilida qayta yoz:
Sarlavha: {title}
Matn: {summary}

Format:
⚡️ <b>Yangi sarlavha</b>

📌 (qisqa 1 ta jumla. DIQQAT: "Mohiyati:" deb yozma, to'g'ridan-to'g'ri 📌 dan keyin boshla)

<blockquote expandable>📝 <b>Tafsilotlar:</b>
• (muhim nuqta)

• (natija)</blockquote>

🏷 #Hashtaglar

📡 <b>Manba:</b> ...

[...] belgilarisiz, 600 belgi atrofida.
"""
    raw_text = await execute_gemini_prompt(prompt)
    if raw_text:
        cleaned = sanitize_telegram_html(raw_text)
        cleaned = fix_blockquote_tags(cleaned)
        cleaned = format_paragraph_spacing(cleaned)
        return apply_premium_emojis(cleaned)
    return current_caption

async def generate_daily_digest(posts: list) -> str:
    """Kunlik eng sara 5 ta yangilik dayjestini tayyorlash"""
    if not posts:
        return ""

    articles_text = ""
    for idx, p in enumerate(posts[:5], 1):
        articles_text += f"{idx}. {p['title']}\nManba: {p.get('source_name', '')}\nQisqacha: {p.get('original_summary', '')[:160]}\n\n"

    prompt = f"""Sen — "{config.CHANNEL_USERNAME}" Telegram kanali uchun yetakchi IT va AI tahlilchisisan.
Bugun kanalda e'lon qilingan eng muhim yangiliklar asosida kechki "NeuraTech Dayjesti" postini o'zbek tilida tayyorla.

Bugungi maqolalar:
{articles_text}

QAT'IY FORMAT TALABI:
⚡️ <b>NEURATECH DAYJEST | Kunning eng muhim 5 ta texno voqeasi</b>

📌 <b>Qisqacha:</b>
Bugun IT, sun'iy intellekt, chiplar va gadjetlar olamida sodir bo'lgan eng qaynoq voqealar jamlanmasi:

<blockquote expandable>📊 <b>Kunning asosiy anonslari:</b>
1. <b>(1-mavzu nomi)</b> — (1 jumla bilan mazmuni)

2. <b>(2-mavzu nomi)</b> — (1 jumla bilan mazmuni)

3. <b>(3-mavzu nomi)</b> — (1 jumla bilan mazmuni)

4. <b>(4-mavzu nomi)</b> — (1 jumla bilan mazmuni)

5. <b>(5-mavzu nomi)</b> — (1 jumla bilan mazmuni)

💡 <b>Xulosa:</b>
(1 ta umumlashtiruvchi jumla).</blockquote>

🏷 #NeuraTech #Dayjest #AI #Texnologiya #Yangiliklar

QOIDALAR:
1. Telegram caption limiti 1024 belgi!
2. Har bir punkt orasida 2 ta bo'sh qator bo'lsin.
3. [...] yoki chala jumlalar bo'lmasin.
"""
    raw = await execute_gemini_prompt(prompt)
    if raw:
        cleaned = sanitize_telegram_html(raw)
        cleaned = fix_blockquote_tags(cleaned)
        cleaned = format_paragraph_spacing(cleaned)
        return apply_premium_emojis(cleaned)

    # Zaxira formatlash
    lines = [
        "⚡️ <b>NEURATECH DAYJEST | Kunning eng muhim texno anonslari</b>\n\n",
        "📌 <b>Bugun sodir bo'lgan asosiy voqealar:</b>\n\n",
        "<blockquote expandable>📊 <b>Kunning sara xabarlari:</b>\n"
    ]
    for idx, p in enumerate(posts[:5], 1):
        lines.append(f"{idx}. <b>{html.escape(p['title'][:80])}</b> ({html.escape(p.get('source_name', ''))})\n\n")
    lines.append("</blockquote>\n\n🏷 #NeuraTech #Dayjest #AI #IT")
    return apply_premium_emojis("".join(lines))

async def answer_ai_assistant_question(question: str, article_context: Optional[str] = None) -> str:
    """Foydalanuvchilarning savollariga NeuraTech AI sifatida o'zbek tilida javob berish (maqola konteksti bilan yoki umumiy)"""
    if article_context:
        prompt = f"""Sen — "{config.CHANNEL_USERNAME}" Telegram kanali va botining intellektual Sun'iy Intellekt maslahatchisisan (NeuraTech AI).
Foydalanuvchi kanalda e'lon qilingan quyidagi yangilik/maqola yuzasidan savol bermoqda:

MAQOLA MAZMUNI:
{article_context}

FOYDALANUVCHI SAVOLI:
"{question}"

QOIDALAR:
1. O'zbek tilida juda samimiy, tushunarli, aniq va professional javob ber.
2. Maqola mazmuniga bevosita tayangan holda savolga javob ber. Agar foydalanuvchi maqoladagi atama yoki yangilik haqida so'rasa, uni o'zbek tilida sodda qilib tushuntirib ber.
3. Javob juda uzun bo'lmasin (maksimal 2-3 ta qisqa abzas, 500-800 belgi).
4. Telegram HTML teglaridan (<b>, <i>, <code>) to'g'ri foydalan. Markdown (**yoki *) ishlatma!
"""
    else:
        prompt = f"""Sen — "{config.CHANNEL_USERNAME}" botining intellektual Sun'iy Intellekt maslahatchisisan (NeuraTech AI).
Foydalanuvchi quyidagi savolni yo'lladi:
"{question}"

QOIDALAR:
1. O'zbek tilida juda samimiy, aniq, lo'nda va professional tarzda javob ber.
2. Savol IT, sun'iy intellekt, gadjetlar, noutbuklar, dasturlash yoki texnologiyaga oid bo'lsa, eng so'nggi ma'lumotlar bilan tavsiya ber.
3. Javob juda uzun bo'lmasin (maksimal 2-3 ta qisqa abzas, 500-750 belgi).
4. Telegram HTML teglaridan (<b>, <i>, <code>) to'g'ri foydalan. Markdown (**yoki *) ishlatma!
"""
    raw = await execute_gemini_prompt(prompt)
    if raw:
        cleaned = sanitize_telegram_html(raw)
        return apply_premium_emojis(cleaned)
    return "Kechirasiz, ayni paytda AI serverlarida yuklama yuqori. Iltimos, birozdan so'ng qayta urinib ko'ring yoki savolingizni boshqacharoq shaklda bering."

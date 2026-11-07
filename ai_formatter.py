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

def strip_source_link(text: str) -> str:
    """Post matnidagi '📡 Manba: ...' qismini tozalash (manba kanal posti ostidagi rangli tugmada chiqarilganda)"""
    if not text:
        return text

    # 1. 'Mohiyati:' so'zini tozalash (agar qolib ketgan bo'lsa)
    text = re.sub(
        r'((?:<tg-emoji[^>]*>)?📌(?:</tg-emoji>)?)\s*(?:<b>)?\s*Mohiyati:?\s*(?:</b>)?\s*',
        r'\1 ',
        text,
        flags=re.IGNORECASE
    )

    # 2. Mavjud barcha '📡 Manba: ...' qatorlarini tozalash
    text = re.sub(
        r'\n*\s*(?:<tg-emoji[^>]*>)?📡(?:</tg-emoji>)?\s*(?:<b>)?\s*Manba:?\s*(?:</b>)?\s*(?:<a\s+[^>]*>.*?</a>|[^\n<]*)(?=\s*</blockquote>|\n|$)',
        '',
        text,
        flags=re.IGNORECASE
    )

    # 3. Agar </blockquote> oxirida ortiqcha bo'sh qatorlar qolgan bo'lsa tozalash
    text = re.sub(r'\n+\s*</blockquote>', '</blockquote>', text)

    # 4. Ortiqcha bo'sh qatorlarni ixchamlashtirish
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def prepare_channel_caption(text: str, source_name: str = "", original_url: str = "", source_btn_active: bool = True) -> str:
    """Kanalga yuboriladigan post matnini tayyorlash:
    - Agar Manba tugmasi (button) yoqilgan bo'lsa: matndan '📡 Manba: ...' olib tashlanadi (chiroyli, toza post).
    - Agar Manba tugmasi o'chirilgan bo'lsa: matn ichiga '📡 Manba: <a href=...>...</a>' qo'shiladi.
    """
    if source_btn_active and original_url and str(original_url).startswith("http"):
        return strip_source_link(text)
    return attach_source_link(text, source_name, original_url)


async def fallback_formatter(title: str, summary: str, source_name: str, original_url: str = "") -> str:
    """Gemini ishlamagan taqdirda o'zbekcha tarjima qilib, ochiluvchi sitata (blockquote expandable) bilan boyitilgan formatlash"""
    clean_title = clean_ugly_symbols(title)
    clean_sum = clean_ugly_symbols(summary[:1800])

    uz_title_raw = await translate_to_uzbek(clean_title)
    uz_summary_raw = await translate_to_uzbek(clean_sum)

    uz_title = html.escape(clean_ugly_symbols(uz_title_raw[:165]))

    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', uz_summary_raw) if len(s.strip()) > 15]
    if len(sentences) >= 3:
        essence_raw = f"{sentences[0]} {sentences[1]}"
        details_list = [f"• {s}" for s in sentences[2:6]]
        more_details_raw = "\n\n".join(details_list)
    elif len(sentences) == 2:
        essence_raw = sentences[0]
        more_details_raw = f"• {sentences[1]}\n\n• Maqolaning to'liq tafsilotlari bilan rasmiy manba orqali tanishishingiz mumkin."
    elif len(sentences) == 1:
        essence_raw = sentences[0]
        more_details_raw = f"• Mazkur texnologik yangilik bo'yicha batafsil ma'lumotlar {source_name} nashrida e'lon qilindi."
    else:
        essence_raw = uz_title_raw
        more_details_raw = f"• {source_name} orqali e'lon qilingan eng so'nggi xabar."

    essence = html.escape(clean_ugly_symbols(essence_raw[:280]))
    more_details = html.escape(clean_ugly_symbols(more_details_raw[:540]))
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
    """Yangilikni tabiiy, tushunarli o'zbek tiliga o'girish va boyitilgan faktlar bilan tayyorlash"""
    prompt = f"""Sen — "{config.CHANNEL_USERNAME}" Telegram kanali uchun professional IT va Sun'iy Intellekt jurnalisti hamda tarjimonisan.
Vazifang — quyidagi inglizcha yangilikni O'ZBEK TILIDA hamma bir o'qishda tushunadigan, ravon, aniq va MA'LUMOTGA BOY qilib yozib berish.

Asl sarlavha: {title}
Bo'lim: {category}
Manba: {source_name}
Maqola matni va faktlar:
{summary}

TARJIMA VA USLUB QOIDALARI (JUDA MUHIM!):
1. TUSHUNARSIZ VA MAVHUM IBORALAR TAQIQLANADI:
   - Hech qachon "Google inqilobi", "Texnologiya olamida portlash", "Yangi davr boshlandi", "Kutilmagan zarba" kabi mavhum va tushunarsiz sarlavha yozma!
   - Sarlavhada AYNAN KIM, NIMA QILGANI va QANDAY ANIQ NATIJA bergani oddiy, tushunarli o'zbek tilida yozilsin (Masalan: "Google yangi Gemini 2.5 Pro modelini taqdim etdi: kod yozish va tahlilda 40% tezroq").
2. SO'ZMA-SO'Z (KALKALAB) TARJIMA QILMA:
   - Inglizcha gap qurilishini so'zma-so'z o'girma. Ma'nosini to'liq tushunib, sof va tabiiy o'zbek tilida ifodala.
   - Texnik atamalarni (API, GPU, Chip, Open-source, Token, Server, Benchmark, Startap, AI agent) buzmasdan yoz va murakkab tushunchalarni oddiy so'z bilan qisqacha tushuntirib ket (masalan: "context window (bir vaqtda qayta ishlanadigan matn hajmi)", "fine-tuning (modelni maxsus vazifaga moslashtirish)").
3. KO'PROQ ANIQ MA'LUMOT VA FAKTLAR QO'SH:
   - Xabar quruq yoki 2 og'iz gap bo'lib qolmasin! Maqoladagi barcha muhim raqamlar, narxlar, modellar nomi, tezlik ko'rsatkichlari, kimlar uchun foydali ekani va qanday ishlashini batafsil yoritib ber.

QAT'IY FORMAT TALABLARI (Har bir bo'lim orasida ALBATTA 2 ta bo'sh qator tashla!):

⚡️ <b>(Aniq, tushunarli va ma'noli sarlavha — mavhum "inqilob" so'zlarisiz!)</b>

📌 (Voqeaning asosiy mohiyati: 2 ta to'liq, ravon va tushunarli jumlada kompaniya nima taqdim etgani va bu nima uchun muhimligini ochib ber. DIQQAT: "Mohiyati:" so'zini aslo yozma! To'g'ridan-to'g'ri 📌 emojisi ortidan matn boshlansin!).

<blockquote expandable>📊 <b>Asosiy tafsilotlar:</b>
• <b>Texnologiya va yangiliklar:</b> (Yangi model, qurilma yoki xizmat qanday ishlashi va unga qanday yangi imkoniyatlar qo'shilgani haqida aniq ma'lumot)

• <b>Raqamlar va ko'rsatkichlar:</b> (Parametrlar, tezlik, xotira, benchmark natijalari, investitsiya summasi yoki narxlar haqida aniq faktlar)

• <b>Ustunligi va farqi:</b> (Oldingi versiyalardan yoki raqobatchilardan — masalan OpenAI, Google, Apple, Nvidia yechimlaridan qanday amaliy ustunlikka ega ekani)

• <b>Kimlar uchun va qachondan:</b> (Dasturchilar, biznes yoki oddiy foydalanuvchilar bundan qanday foydalanishi mumkinligi va ishga tushish muddati)

💡 <b>Xulosa:</b>
(1-2 ta ravon jumlada ushbu yangilik foydalanuvchilar va IT bozori uchun nimani o'zgartirishi).</blockquote>

🏷 #(Mavzuga mos 3-4 ta aniq hashtag: masalan #Google #Gemini #AI yoki #Nvidia #GPU #Texnologiya)

📡 <b>Manba:</b> {source_name}

TEXNIK CHEKLOVLAR:
1. "Mohiyati:" so'zi aslo ishlatilmasin, 📌 dan keyin to'g'ridan-to'g'ri gap boshlansin.
2. 📡 <b>Manba:</b> qismi sitatadan (</blockquote>) TASHQARIDA, hashtagdan keyin eng pastda tursin.
3. HECH QACHON Markdown yulduzchalaridan (**matn**) foydalanma! Faqat <b>matn</b> HTML teglaridan foydalan!
4. HECH QACHON [...] yoki chala uzilgan gap yozma.
5. Telegram caption limiti 1024 belgi! Post ma'lumotga boy bo'lsin, lekin sof matn uzunligi 800–920 belgi oralig'ida bo'lsin (940 belgidan oshmasin, shunda barcha 4 ta punkt, xulosa va hashtaglar to'liq sig'adi).
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
    """Matnni qayta generatsiya qilish (yanada tushunarli, ravon va boyitilgan faktlar bilan)"""
    prompt = f"""Quyidagi IT/AI yangiligini O'ZBEK TILIDA yanada tushunarli, tabiiy, ma'lumotga boy va ravon qilib qayta yoz.
DIQQAT: "Google inqilobi" kabi mavhum iboralar va so'zma-so'z g'aliz tarjimalar aslo ishlatilmasin! Kim nima qilgani va qanday natija bergani oddiy, tushunarli tilda yozilsin.

Sarlavha: {title}
Matn: {summary}

Format:
⚡️ <b>(Aniq va tushunarli yangi sarlavha)</b>

📌 (2 ta ravon jumlada voqeaning asl mohiyati. "Mohiyati:" deb yozma, to'g'ridan-to'g'ri 📌 dan keyin boshla)

<blockquote expandable>📊 <b>Asosiy tafsilotlar:</b>
• <b>Texnik imkoniyatlar:</b> (Asosiy yangilik va funksiyalar)

• <b>Raqamlar va faktlar:</b> (Ko'rsatkichlar, tezlik, narx yoki parametrlar)

• <b>Amaliy ahamiyati:</b> (Foydalanuvchilar va soha uchun foydasi)

💡 <b>Xulosa:</b>
(1 ta aniq xulosa).</blockquote>

🏷 #Hashtaglar

📡 <b>Manba:</b> ...

Markdown (** yoki *) ishlatma, faqat <b> HTML teglaridan foydalan. Uzunligi 780–900 belgi atrofida bo'lsin.
"""
    raw_text = await execute_gemini_prompt(prompt)
    if raw_text:
        cleaned = sanitize_telegram_html(raw_text)
        cleaned = fix_blockquote_tags(cleaned)
        cleaned = format_paragraph_spacing(cleaned)
        return apply_premium_emojis(cleaned)
    return current_caption

UZ_MONTHS = [
    "", "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"
]

def get_tashkent_today_str() -> str:
    now_t = datetime.now(config.TASHKENT_TZ)
    return f"{now_t.day}-{UZ_MONTHS[now_t.month]}, {now_t.year}"

async def generate_daily_digest(posts: list) -> str:
    """Kunlik eng sara 5 ta yangilik dayjestini tayyorlash (soat 21:00 va admin sinovi uchun)"""
    if not posts:
        return ""

    today_label = get_tashkent_today_str()
    articles_text = ""
    for idx, p in enumerate(posts[:5], 1):
        articles_text += f"{idx}. {p['title']}\nManba: {p.get('source_name', '')}\nQisqacha: {p.get('original_summary', '')[:140]}\n\n"

    prompt = f"""Sen — "{config.CHANNEL_USERNAME}" Telegram kanali uchun yetakchi IT va AI tahlilchisisan.
Bugun kanalda e'lon qilingan eng muhim 5 ta xabar asosida kechki "NeuraTech Dayjesti" postini o'zbek tilida tayyorla.

Bugungi maqolalar:
{articles_text}

QAT'IY FORMAT TALABI (Aynan shu tartibda bo'lsin):
⚡️ <b>NEURATECH KECHKI DAYJEST</b>
📅 <i>{today_label} | Kunning eng muhim IT va AI voqealari</i>

<blockquote expandable>📊 <b>Kunning asosiy anonslari:</b>
1️⃣ <b>(Mavzu nomi)</b> — (1 ta ixcham, faktga boy jumla, 8-12 so'z).

2️⃣ <b>(Mavzu nomi)</b> — (1 ta ixcham, faktga boy jumla, 8-12 so'z).

3️⃣ <b>(Mavzu nomi)</b> — (1 ta ixcham, faktga boy jumla, 8-12 so'z).

4️⃣ <b>(Mavzu nomi)</b> — (1 ta ixcham, faktga boy jumla, 8-12 so'z).

5️⃣ <b>(Mavzu nomi)</b> — (1 ta ixcham, faktga boy jumla, 8-12 so'z).

💡 <b>Xulosa:</b>
(1 ta lo'nda xulosa, 8-12 so'z).</blockquote>

🏷 #NeuraTech #Dayjest #AI #IT #Texnologiya

QOIDALAR:
1. QAT'IY LIMIT: Har bir punkt 1 ta qisqa jumladan oshmasin (maksimal 70-90 belgi). Butun postning jami matni 600-750 belgi oralig'ida bo'lishi shart! (Telegram rasm posti chegarasi 1024 belgi).
2. Har bir punkt orasida 1 ta bo'sh qator bo'lsin.
3. Chala jumlalar, [...] yoki Markdown (* yoki **) umuman ishlatma. Faqat Telegram HTML teglari (<b>, <i>).
"""
    raw = await execute_gemini_prompt(prompt)
    if raw:
        cleaned = sanitize_telegram_html(raw)
        cleaned = fix_blockquote_tags(cleaned)
        cleaned = format_paragraph_spacing(cleaned)
        return apply_premium_emojis(cleaned)

    # Zaxira formatlash
    lines = [
        "⚡️ <b>NEURATECH KECHKI DAYJEST</b>\n",
        f"📅 <i>{today_label} | Kunning eng muhim IT va AI voqealari</i>\n\n",
        "<blockquote expandable>📊 <b>Kunning asosiy anonslari:</b>\n"
    ]
    num_badges = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    for idx, p in enumerate(posts[:5]):
        badge = num_badges[idx] if idx < len(num_badges) else f"{idx+1}."
        title = html.escape(p['title'][:65])
        source = html.escape(p.get('source_name', 'IT'))
        lines.append(f"{badge} <b>{title}</b> ({source})\n\n")
    lines.append("💡 <b>Xulosa:</b> Bugungi texnologik yangiliklar AI va apparat vositalari jadal rivojlanayotganini ko'rsatmoqda.</blockquote>\n\n")
    lines.append("🏷 #NeuraTech #Dayjest #AI #IT #Texnologiya")
    return apply_premium_emojis("".join(lines))

async def answer_ai_assistant_question(question: str, article_context: Optional[str] = None) -> str:
    """Foydalanuvchilarning savollariga NeuraTech AI sifatida o'zbek tilida javob berish (maqola konteksti bilan yoki umumiy)"""
    if article_context:
        prompt = f"""Sen — "{config.CHANNEL_USERNAME}" Telegram kanali va botining intellektual Sun'iy Intellekt maslahatchisisan (NeuraTech AI).
Foydalanuvchi kanalda e'lon qilingan quyidagi yangilik/maqola yuzasidan savol bermoqda:

MAQOLA VA MANBA MAZMUNI:
{article_context}

FOYDALANUVCHI SAVOLI:
"{question}"

QOIDALAR:
1. O'zbek tilida juda samimiy, aniq, faktlarga tayangan va professional texnik uslubda javob ber.
2. Maqola va manba mazmuniga bevosita tayangan holda savolga to'liq javob ber. Texnik atamalarni o'zbek tilida sodda va tushunarli qilib yorit.
3. Javob lo'nda va o'qishli bo'lsin (2-3 ta qisqa abzas, 500-800 belgi). Keraksiz kirish yoki quruq gaplar yozma.
4. Muhim faktlar, raqamlar yoki xususiyatlarni <b>qalin</b> qilib ajrat.
5. Telegram HTML teglaridan (<b>, <i>, <code>) to'g'ri foydalan. Markdown (** yoki *) aslo ishlatma!
"""
    else:
        prompt = f"""Sen — "{config.CHANNEL_USERNAME}" botining intellektual Sun'iy Intellekt maslahatchisisan (NeuraTech AI).
Foydalanuvchi quyidagi savolni yo'lladi:
"{question}"

QOIDALAR:
1. O'zbek tilida juda samimiy, aniq, lo'nda va professional tarzda javob ber.
2. Savol IT, sun'iy intellekt, gadjetlar, noutbuklar, dasturlash yoki texnologiyaga oid bo'lsa, eng so'nggi ma'lumotlar bilan tavsiya ber.
3. Javob 2-3 ta qisqa abzasda (500-750 belgi) bo'lsin.
4. Telegram HTML teglaridan (<b>, <i>, <code>) to'g'ri foydalan. Markdown (** yoki *) aslo ishlatma!
"""
    raw = await execute_gemini_prompt(prompt)
    if raw:
        cleaned = sanitize_telegram_html(raw)
        return apply_premium_emojis(cleaned)
    return "Kechirasiz, ayni paytda AI serverlarida yuklama yuqori. Iltimos, birozdan so'ng qayta urinib ko'ring yoki savolingizni boshqacharoq shaklda bering."

FALLBACK_POLLS = [
    {
        "question": "🤖 Kundalik ishingiz yoki o'qishingizda qaysi Sun'iy Intellekt modelidan eng ko'p foydalanasiz?",
        "options": [
            "ChatGPT (OpenAI GPT-4o / o3)",
            "Google Gemini 2.5 / Flash",
            "Claude (Anthropic Sonnet)",
            "DeepSeek / Grok / Boshqa"
        ],
        "poll_type": "regular",
        "correct_option_id": None,
        "explanation": None
    },
    {
        "question": "🧠 Viktorina: Katta til modellarida (LLM) 'RAG' texnologiyasining asosiy vazifasi nima?",
        "options": [
            "Modelni tashqi ishonchli ma'lumotlar bazasi bilan boyitish",
            "Videolarni yuqori sifatda (4K) generatsiya qilish",
            "Protsessorning elektr sarfini 50% ga kamaytirish",
            "Dastur kodini avtomatik ravishda shifrlash"
        ],
        "poll_type": "quiz",
        "correct_option_id": 0,
        "explanation": "RAG (Retrieval-Augmented Generation) AI modeliga javob berishdan oldin tashqi hujjatlar yoki bazadan aniq faktlarni qidirib topish imkonini beradi."
    },
    {
        "question": "💻 Dasturlash va kod yozishda qaysi AI yordamchisi siz uchun eng samarali?",
        "options": [
            "Cursor / Windsurf (AI IDE)",
            "GitHub Copilot",
            "ChatGPT / Claude veb-interfeysi",
            "Kodning barchasini o'zim yozaman"
        ],
        "poll_type": "regular",
        "correct_option_id": None,
        "explanation": None
    },
    {
        "question": "⚡️ Viktorina: Hozirgi zamonaviy AI chiplarini (H100, Blackwell B200) ishlab chiqaruvchi yetakchi kompaniya qaysi?",
        "options": [
            "Intel",
            "NVIDIA",
            "Qualcomm",
            "IBM"
        ],
        "poll_type": "quiz",
        "correct_option_id": 1,
        "explanation": "NVIDIA kompaniyasi H100, H200 va Blackwell B200 GPU arxitekturalari bilan global AI infratuzilmasining 80% dan ortig'ini ta'minlaydi."
    }
]

async def generate_ai_poll(
    topic: Optional[str] = None,
    recent_posts: Optional[List[Dict[str, Any]]] = None,
    poll_type: str = "regular"
) -> Dict[str, Any]:
    """
    Telegram kanali uchun qiziqarli AI so'rovnoma (regular) yoki viktorina (quiz) generatsiya qilish.
    Qaytaradi: {"question": str, "options": List[str], "poll_type": str, "correct_option_id": Optional[int], "explanation": Optional[str]}
    """
    import json
    import random

    mode_desc = (
        "VIKTORINA (QUIZ) rejimida: aniq 1 ta to'g'ri javobi bor qiziqarli texnologik savol va qisqa izoh (explanation) bo'lsin."
        if poll_type == "quiz"
        else "SO'ROVNOMA (REGULAR POLL) rejimida: obunachilarning fikri, tajribasi yoki tanlovini bilishga qaratilgan qizg'in muhokamali savol bo'lsin."
    )

    context_block = ""
    if topic and topic.strip():
        context_block = f"MAXSUS MAVZU: {topic.strip()}\nShu mavzu doirasida savol tuz."
    elif recent_posts:
        news_lines = [f"- {p.get('title', '')}" for p in recent_posts[:5] if p.get("title")]
        if news_lines:
            context_block = "KANALDAGI OXIRGI YANGILIKLAR:\n" + "\n".join(news_lines) + "\nShu yangiliklardan kelib chiqib dolzarb savol tuz."

    prompt = f"""Sen "{config.CHANNEL_USERNAME}" (NeuraTech AI) Telegram kanali uchun interaktiv so'rovnoma tuzuvchi mutaxassissan.
{mode_desc}
{context_block}

QAT'IY TALABLAR:
1. Faqat toza JSON obyekt qaytar (hech qanday ```json yoki kirish so'zi yozma).
2. Savol ("question") o'zbek tilida, qiziqarli, boshida 1 ta mos emoji bilan, uzunligi 250 belgidan oshmasin (HTML teglar ishlatma, chunki Telegram Poll oddiy matn qabul qiladi).
3. Variantlar ("options") aniq 4 ta bo'lsin, har biri 90 belgidan qisqa, tushunarli va aniq bo'lsin.
4. Agar poll_type="quiz" bo'lsa, "correct_option_id" (0 dan 3 gacha butun son) va "explanation" (to'g'ri javob sababi, 180 belgidan qisqa) bo'lishi shart.

JSON NAMUNASI:
{{
  "question": "🤖 Savol matni?",
  "options": ["1-variant", "2-variant", "3-variant", "4-variant"],
  "poll_type": "{poll_type}",
  "correct_option_id": 0,
  "explanation": "Qisqa va aniq izoh."
}}
"""
    raw = await execute_gemini_prompt(prompt)
    if raw:
        try:
            cleaned_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
            match = re.search(r"\{.*\}", cleaned_json, flags=re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                q = re.sub(r"<[^>]+>", "", str(data.get("question", "")).strip())[:255]
                opts_raw = data.get("options", [])
                opts = [re.sub(r"<[^>]+>", "", str(o).strip())[:95] for o in opts_raw if str(o).strip()][:4]
                if q and len(opts) >= 2:
                    p_type = "quiz" if poll_type == "quiz" else "regular"
                    corr_id = data.get("correct_option_id")
                    if p_type == "quiz":
                        corr_id = int(corr_id) if isinstance(corr_id, int) and 0 <= corr_id < len(opts) else 0
                    else:
                        corr_id = None
                    expl = re.sub(r"<[^>]+>", "", str(data.get("explanation") or "").strip())[:195] if p_type == "quiz" else None
                    return {
                        "question": q,
                        "options": opts,
                        "poll_type": p_type,
                        "correct_option_id": corr_id,
                        "explanation": expl
                    }
        except Exception as e:
            print(f"[AI Poll Parse Xatolik]: {e}")

    # Fallback
    candidates = [p for p in FALLBACK_POLLS if p["poll_type"] == poll_type] or FALLBACK_POLLS
    return dict(random.choice(candidates))

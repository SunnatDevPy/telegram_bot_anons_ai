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
import database

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
        self.total_tokens = 0
        self.last_model = ""

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

    def record_success(self, tokens: int = 0, model: str = ""):
        self.total_requests += 1
        self.total_success += 1
        self.consecutive_errors = 0
        self.cooldown_until = None
        self.last_used = datetime.now(timezone.utc)
        self.total_tokens += tokens
        if model:
            self.last_model = model

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
                "cooldown": not n.is_available(),
                "tokens": n.total_tokens,
                "last_model": n.last_model
            })
        return report

# Global yagona hovuz nusxasi
GEMINI_POOL = GeminiKeyPool()

GEMINI_MODELS_INFO = {
    "gemini-3.1-flash-lite-preview": {
        "title": "Gemini 3.1 Flash Lite",
        "role": "Asosiy (Ultra-tezkor & Tejamkor)",
        "rpd": "500 so'rov/kun",
        "rpm": "15 RPM",
        "tpm": "250,000 TPM",
        "context": "1,048,576 token",
        "status_tag": "🟢 Faol (Asosiy)"
    },
    "gemini-3-flash-preview": {
        "title": "Gemini 3 Flash",
        "role": "1-Zaxira (Chuqur tahlil)",
        "rpd": "50 so'rov/kun",
        "rpm": "15 RPM",
        "tpm": "1,000,000 TPM",
        "context": "1,048,576 token",
        "status_tag": "🟢 Tayyor (1-Zaxira)"
    },
    "gemini-3.8-flash": {
        "title": "Gemini 3.8 Flash",
        "role": "2-Zaxira (Kengaytirilgan)",
        "rpd": "20 so'rov/kun",
        "rpm": "15 RPM",
        "tpm": "1,000,000 TPM",
        "context": "1,048,576 token",
        "status_tag": "🟢 Tayyor (2-Zaxira)"
    },
    "gemini-flash-latest": {
        "title": "Gemini Flash Latest",
        "role": "Favqulodda Zaxira",
        "rpd": "20 so'rov/kun",
        "rpm": "15 RPM",
        "tpm": "1,000,000 TPM",
        "context": "1,048,576 token",
        "status_tag": "🟢 Tayyor (Favqulodda)"
    }
}

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
    - Token sarfi (Prompt, Candidate, Total) aniqlanadi va SQLite bazaga qayd qilinadi.
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
                    p_tok = 0
                    c_tok = 0
                    t_tok = 0
                    if hasattr(response, 'usage_metadata') and response.usage_metadata:
                        p_tok = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
                        c_tok = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0
                        t_tok = getattr(response.usage_metadata, 'total_token_count', 0) or (p_tok + c_tok)

                    node.record_success(tokens=t_tok, model=model_name)

                    # Baza statistikasiga yozish
                    try:
                        asyncio.create_task(
                            database.log_ai_token_usage(
                                key_index=node.index,
                                model_name=model_name,
                                prompt_tokens=p_tok,
                                completion_tokens=c_tok,
                                total_tokens=t_tok
                            )
                        )
                    except Exception as log_err:
                        print(f"[log_ai_token_usage log error]: {log_err}")

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

async def render_ai_stats_html() -> str:
    """
    /stats_ai komandasi uchun to'liq va chiroyli HTML hisobot generatsiyasi:
    - Bugungi va barcha vaqtlar token sarfi (Prompt, Completion, Total)
    - Modellar holati, ularning rollari va limitlari (RPD, RPM, TPM, Context)
    - Kalitlar hovuzi holati (Aktiv, 429 sovutish, har bir kalit sarflagan tokenlar)
    """
    stats = await database.get_ai_token_stats()
    today = stats.get("today", {})
    all_time = stats.get("all_time", {})
    by_model = stats.get("by_model", {})
    by_key = stats.get("by_key", {})

    tz_tashkent = timezone(timedelta(hours=5))
    now_tashkent = datetime.now(tz_tashkent)
    date_str = now_tashkent.strftime("%d.%m.%Y, %H:%M")

    cand_order = [
        "gemini-3.1-flash-lite-preview",
        "gemini-3-flash-preview",
        "gemini-3.8-flash",
        "gemini-flash-latest"
    ]
    models_blocks = []
    for i, m_id in enumerate(cand_order, 1):
        info = GEMINI_MODELS_INFO.get(m_id, {
            "title": m_id,
            "role": "Zaxira",
            "rpd": "Noma'lum",
            "rpm": "15 RPM",
            "tpm": "1M TPM",
            "context": "1M token",
            "status_tag": "🟢 Tayyor"
        })
        m_stat = by_model.get(m_id, {"requests_today": 0, "tokens_today": 0, "requests_all": 0, "tokens_all": 0})
        m_title = html.escape(info["title"])
        m_role = html.escape(info["role"])
        m_tag = info["status_tag"]
        rpd = info["rpd"]
        rpm = info["rpm"]
        tpm = info["tpm"]
        ctx = info["context"]
        r_td = m_stat["requests_today"]
        t_td = m_stat["tokens_today"]

        block = (
            f"<b>{i}️⃣ {m_title}</b> <i>({m_role})</i>\n"
            f"   • Holat: {m_tag}\n"
            f"   • Limit: <b>{rpd}</b> | {rpm} | {tpm}\n"
            f"   • Kontekst oynasi: <b>{ctx}</b>\n"
            f"   • Bugun: <b>{r_td} ta so'rov</b> (<b>{t_td:,}</b> token)"
        )
        models_blocks.append(block)

    models_text = "\n\n".join(models_blocks)

    pool_reports = GEMINI_POOL.get_status_report()
    key_lines = []
    for rep in pool_reports:
        k_idx = rep["index"]
        k_stat = by_key.get(k_idx, {"requests_today": 0, "tokens_today": 0, "tokens_all": 0})
        st_text = rep["status"]
        tok_td = k_stat["tokens_today"]
        req_td = k_stat["requests_today"]
        tok_all = k_stat["tokens_all"]
        key_lines.append(
            f"🔑 <b>Kalit #{k_idx}:</b> {st_text}\n"
            f"   └ Bugun: <b>{req_td} so'rov</b> (<b>{tok_td:,}</b> tok) | Jami: <b>{tok_all:,}</b> tok"
        )
    keys_text = "\n".join(key_lines) if key_lines else "<i>Kalitlar topilmadi</i>"

    t_req = today.get("requests", 0)
    t_prompt = today.get("prompt_tokens", 0)
    t_comp = today.get("completion_tokens", 0)
    t_total = today.get("total_tokens", 0)

    a_req = all_time.get("requests", 0)
    a_total = all_time.get("total_tokens", 0)

    text = (
        f"🧠 <b>GEMINI AI TOKEN VA MODEL STATISTIKASI</b>\n"
        f"📅 <i>Sana: {date_str} (Toshkent vaqti)</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>BUGUNGI SARF:</b>\n"
        f"• 🔄 So'rovlar soni: <b>{t_req:,} ta</b>\n"
        f"• 📥 Kiruvchi (Prompt): <b>{t_prompt:,}</b> token\n"
        f"• 📤 Javob (Completion): <b>{t_comp:,}</b> token\n"
        f"• ⚡ Jami bugun: <b>{t_total:,} token</b>\n\n"
        f"📈 <b>BARCHA VAQTLAR BO'YICHA:</b>\n"
        f"• 📬 Jami so'rovlar: <b>{a_req:,} ta</b>\n"
        f"• 💎 Jami tokenlar: <b>{a_total:,} token</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>AKTIV MODELLAR VA KVOTA LIMITLARI:</b>\n\n"
        f"{models_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗝 <b>MULTI-KEY HOVUZI ({len(GEMINI_POOL.nodes)} ta kalit):</b>\n"
        f"{keys_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Avtomatika: Agar biror kalit 429 (quota) ga uchrasa, tizim to'xtamay zudlik bilan keyingi kalitga va zaxira modelga o'tadi hamda 5 daqiqadan so'ng qayta ochiladi.</i>"
    )
    return text

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

# ==================== BEPUL AI ASBOBLAR VA PROMPTLAR RUKNI ====================

FALLBACK_AI_TOOLS = [
    {
        "tool_name": "Flux.1",
        "tool_url": "https://blackforestlabs.ai",
        "category": "Rasm & Dizayn",
        "title": "Flux.1 — Dunyodagi eng kuchli bepul AI rasm generatori",
        "caption": (
            "🛠 <b>KUNNING BEPUL AI VOSITASI</b>\n\n"
            "🚀 <b>Flux.1</b> — <i>Midjourney darajasidagi fotorealistik suratlarni yaratuvchi eng so'nggi ochiq model.</i>\n\n"
            "📌 <b>Asosiy imkoniyatlari:</b>\n"
            "• Odam yuzlari, qo'llar va ko'zlarni xatosiz, tabiiy chizadi.\n"
            "• Rasm ichidagi harflar va so'zlarni buzmasdan, chiroyli tipografika bilan yozadi.\n"
            "• HuggingFace va Fal.ai orqali bepul foydalanish mumkin.\n\n"
            "🆓 <b>Bepul imkoniyati:</b>\n"
            "• Har kuni bepul generatsiyalar va ochiq yuklab olish imkoniyati.\n\n"
            "💡 <b>Sinab ko'rish uchun Prompt:</b>\n"
            "<blockquote>A cinematic close-up portrait of an Uzbek cyber-engineer working in a neon-lit futuristic lab in Tashkent 2050, 8k resolution, photorealistic, highly detailed, dramatic lighting</blockquote>\n\n"
            "🔗 <b>Rasmiy sayt:</b> <a href=\"https://blackforestlabs.ai\">blackforestlabs.ai</a>\n\n"
            "🏷 #AITools #BepulAI #Dizayn #NeuraTech"
        ),
        "prompt_example": "A cinematic close-up portrait of an Uzbek cyber-engineer..."
    },
    {
        "tool_name": "Bolt.new",
        "tool_url": "https://bolt.new",
        "category": "Dasturlash & Web",
        "title": "Bolt.new — Brauzerda sun'iy intellekt orqali 1 daqiqada to'liq sayt va ilova yaratish",
        "caption": (
            "🛠 <b>KUNNING BEPUL AI VOSITASI</b>\n\n"
            "🚀 <b>Bolt.new</b> — <i>Bir og'iz matnli buyruq bilan to'liq dasturni noldan yig'ib beruvchi AI platforma.</i>\n\n"
            "📌 <b>Asosiy imkoniyatlari:</b>\n"
            "• React, Vue, Next.js yoki Node.js loyihalarni to'liq noldan yozadi.\n"
            "• Kodni to'g'ridan-to'g'ri brauzerda ishga tushirib, natijani jonli ko'rsatadi.\n"
            "• Netlify ga 1 ta tugma bilan jonli internetga joylash (deploy) imkoniyati bor.\n\n"
            "🆓 <b>Bepul imkoniyati:</b>\n"
            "• Har kuni yangilanadigan bepul AI tokenlar.\n\n"
            "💡 <b>Sinab ko'rish uchun Prompt:</b>\n"
            "<blockquote>Create a modern, responsive Telegram Bot Analytics Dashboard in dark mode with interactive charts and export to CSV button</blockquote>\n\n"
            "🔗 <b>Rasmiy sayt:</b> <a href=\"https://bolt.new\">bolt.new</a>\n\n"
            "🏷 #AITools #Dasturlash #WebDev #NeuraTech"
        ),
        "prompt_example": "Create a modern, responsive Telegram Bot Analytics Dashboard..."
    },
    {
        "tool_name": "Krea AI",
        "tool_url": "https://krea.ai",
        "category": "Video & Dizayn",
        "title": "Krea AI — Real vaqt rejimida rasm va video generatsiya qiluvchi bepul vosita",
        "caption": (
            "🛠 <b>KUNNING BEPUL AI VOSITASI</b>\n\n"
            "🚀 <b>Krea AI</b> — <i>Siz chizayotgan oddiy qoralamani real vaqtda durdonaga aylantiruvchi neyron tarmoq.</i>\n\n"
            "📌 <b>Asosiy imkoniyatlari:</b>\n"
            "• Real-time Canvas: Chizayotgan har bir chizig'ingiz bir zumda fotoga aylanadi.\n"
            "• AI Enhancer: Xira suratlarni 4K/8K gacha tiniqlashtiradi.\n"
            "• AI Video: Rasmlardan bir necha soniyada kinematik video hosil qiladi.\n\n"
            "🆓 <b>Bepul imkoniyati:</b>\n"
            "• Kunlik bepul kreditlar barcha foydalanuvchilar uchun mavjud.\n\n"
            "💡 <b>Sinab ko'rish uchun Prompt:</b>\n"
            "<blockquote>Futuristic sports car speeding through ancient Samarkand Registan square at night, glowing neon highlights, cyberpunk atmosphere, hyperrealistic 4k</blockquote>\n\n"
            "🔗 <b>Rasmiy sayt:</b> <a href=\"https://krea.ai\">krea.ai</a>\n\n"
            "🏷 #AITools #Grafika #KreaAI #NeuraTech"
        ),
        "prompt_example": "Futuristic sports car speeding through ancient Samarkand..."
    },
    {
        "tool_name": "Perplexity AI",
        "tool_url": "https://perplexity.ai",
        "category": "Qidiruv & Tadqiqot",
        "title": "Perplexity AI — Google qidiruvining sun'iy intellektli kuchaytirilgan o'rni",
        "caption": (
            "🛠 <b>KUNNING BEPUL AI VOSITASI</b>\n\n"
            "🚀 <b>Perplexity AI</b> — <i>Internetdagi eng so'nggi manbalarni tekshirib, havolalari bilan aniq javob beruvchi qidiruvchi.</i>\n\n"
            "📌 <b>Asosiy imkoniyatlari:</b>\n"
            "• Reklamalarsiz, faktlar va ishonchli manbalar bilan to'g'ridan-to'g'ri javob beradi.\n"
            "• PDF, Word yoki matnli hujjatlarni yuklab, ularni chuqur tahlil qildirishingiz mumkin.\n"
            "• Har qanday murakkab savolga bir necha soniyada xulosa tayyorlaydi.\n\n"
            "🆓 <b>Bepul imkoniyati:</b>\n"
            "• Cheksiz standart qidiruv va har 4 soatda bepul Pro qidiruvlar.\n\n"
            "💡 <b>Foydalanish namunasi:</b>\n"
            "<blockquote>2026-yilda O'zbekiston IT bozorida eng talabgir bo'lgan dasturlash tillari va sohalar bo'yicha tahliliy hisobot tayyorlab ber</blockquote>\n\n"
            "🔗 <b>Rasmiy sayt:</b> <a href=\"https://perplexity.ai\">perplexity.ai</a>\n\n"
            "🏷 #AITools #Perplexity #Qidiruv #NeuraTech"
        ),
        "prompt_example": "2026-yilda O'zbekiston IT bozorida eng talabgir bo'lgan dasturlash tillari..."
    },
    {
        "tool_name": "Suno AI",
        "tool_url": "https://suno.com",
        "category": "Musiqa & Ovoz",
        "title": "Suno AI — Matndan to'liq musiqa, vokal va qo'shiq yaratuvchi AI",
        "caption": (
            "🛠 <b>KUNNING BEPUL AI VOSITASI</b>\n\n"
            "🚀 <b>Suno AI</b> — <i>Istalgan she'r yoki mavzudan professional studiya sifatidagi qo'shiq yaratuvchi vosita.</i>\n\n"
            "📌 <b>Asosiy imkoniyatlari:</b>\n"
            "• Har qanday janrda (Pop, Rock, Hip-Hop, Milliy, Lo-Fi) musiqa yaratadi.\n"
            "• O'zbek tilidagi matnlarni ham to'g'ri talaffuz va ritm bilan kuylay oladi.\n"
            "• 2 daqiqalik to'liq vokal va musiqa trekini chiqaradi.\n\n"
            "🆓 <b>Bepul imkoniyati:</b>\n"
            "• Har kuni bepul 50 kredit (kuniga 10 tagacha bepul qo'shiq yaratish mumkin).\n\n"
            "💡 <b>Sinab ko'rish uchun uslub (Style):</b>\n"
            "<blockquote>Genre: Melodic Uzbek pop with modern synthwave beats. Mood: Inspiring, energetic. Lyrics: IT va dasturlash haqida quvnoq qo'shiq</blockquote>\n\n"
            "🔗 <b>Rasmiy sayt:</b> <a href=\"https://suno.com\">suno.com</a>\n\n"
            "🏷 #AITools #Suno #Musiqa #NeuraTech"
        ),
        "prompt_example": "Genre: Melodic Uzbek pop with modern synthwave beats..."
    }
]

async def generate_ai_tool_post(topic_or_category: Optional[str] = None) -> Dict[str, Any]:
    """
    Kanal uchun bepul AI asbobi va unga oid amaliy promptni generatsiya qilish.
    """
    import json
    import random

    topic_hint = f"Maxsus yo'nalish yoki vosita: {topic_or_category}\n" if topic_or_category else ""
    prompt = f"""Sen "{config.CHANNEL_USERNAME}" (NeuraTech AI) Telegram kanali uchun "Kunning Bepul AI Vositasi" rukniga post tayyorlovchi texnologik mutaxassissan.
{topic_hint}
Hozirgi kunda mashhur, foydali, eng muhimi bepul tarifi (free tier) bor bo'lgan yangi AI vosita (masalan: rasm, video, kod, ovoz, matn, 3D yoki ta'lim bo'yicha) haqida post tayyorla.

QAT'IY TALABLAR:
1. Faqat toza JSON obyekt qaytar (hech qanday markdown ```json tegisiz).
2. JSON maydonlari:
   - "tool_name": Vosita nomi (masalan: "Krea AI", "Bolt.new", "Flux.1", "Haiper AI", "Cursor", "ElevenLabs", "Suno", "v0.dev", "DeepSeek", "Ideogram")
   - "tool_url": Rasmiy sayti havolasi (https://...)
   - "category": Yo'nalishi ("Rasm & Dizayn", "Video & Animatsiya", "Dasturlash & Web", "Musiqa & Ovoz", "Qidiruv & Matn")
   - "title": Qisqa va jozibali sarlavha (70 belgidan oshmasin)
   - "description": 1 ta ta'sirli jumla (nima vazifa bajaradi)
   - "features": Asosiy imkoniyatlari (3 ta qisqa nuqta ro'yxati)
   - "free_tier": Bepul tarifida nimalar berilishi
   - "prompt_example": Boshlovchilar sinab ko'rishi uchun inglizcha yoki o'zbekcha aniq prompt namunasi
3. Matnlar toza o'zbek tilida, zamonaviy va professional bo'lsin.

JSON NAMUNASI:
{{
  "tool_name": "Nomi",
  "tool_url": "https://...",
  "category": "Rasm & Dizayn",
  "title": "Nomi — Qisqa jozibador sarlavha",
  "description": "Nima vazifani bajarishi.",
  "features": [
    "1-afzallik",
    "2-afzallik",
    "3-afzallik"
  ],
  "free_tier": "Kunlik bepul imkoniyatlari",
  "prompt_example": "Aniq prompt matni..."
}}
"""
    raw = await execute_gemini_prompt(prompt)
    if raw:
        try:
            cleaned_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
            match = re.search(r"\{.*\}", cleaned_json, flags=re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                name = html.escape(str(data.get("tool_name", "")).strip())
                url = str(data.get("tool_url", "")).strip() or "https://google.com"
                cat = html.escape(str(data.get("category", "Sun'iy Intellekt")).strip())
                title = clean_all_asterisks_to_html(str(data.get("title", "")).strip())
                desc = clean_all_asterisks_to_html(str(data.get("description", "")).strip())
                feats = [clean_all_asterisks_to_html(str(f).strip()) for f in data.get("features", []) if str(f).strip()]
                free_tier = clean_all_asterisks_to_html(str(data.get("free_tier", "")).strip())
                pr_ex = html.escape(str(data.get("prompt_example", "")).strip())

                if name and desc:
                    feats_lines = "\n".join([f"• {f}" for f in feats[:4]])
                    caption = (
                        f"🛠 <b>KUNNING BEPUL AI VOSITASI</b>\n\n"
                        f"🚀 <b>{name}</b> — <i>{desc}</i>\n\n"
                        f"📌 <b>Asosiy imkoniyatlari:</b>\n"
                        f"{feats_lines}\n\n"
                        f"🆓 <b>Bepul tarifi:</b>\n"
                        f"• {free_tier}\n\n"
                        f"💡 <b>Sinab ko'rish uchun Prompt:</b>\n"
                        f"<blockquote>{pr_ex}</blockquote>\n\n"
                        f"🔗 <b>Rasmiy sayt:</b> <a href=\"{url}\">{name}</a>\n\n"
                        f"🏷 #{cat.replace(' ', '').replace('&', '')} #AITools #NeuraTech #Foydali"
                    )
                    return {
                        "tool_name": name,
                        "tool_url": url,
                        "category": cat,
                        "title": title or f"{name} — Bepul AI vosita",
                        "caption": caption,
                        "prompt_example": pr_ex
                    }
        except Exception as e:
            print(f"[generate_ai_tool_post JSON Parse Xatolik]: {e}")

    # Fallback
    return dict(random.choice(FALLBACK_AI_TOOLS))

# ==================== GADJET VA SMARTFON TANLASH MASLAHATCHISI ====================

async def generate_gadget_advice(user_query: str) -> str:
    """
    Foydalanuvchining talabi bo'yicha eng maqbul gadjetlarni (smartfon, noutbuk, aksessuar)
    taqqoslab, narxi va plyus/minuslari bilan professional maslahat berish.
    """
    cleaned_query = (user_query or "").strip()
    if not cleaned_query:
        return (
            "📱 <b>Gadjet tanlash bo'yicha savolingizni yozing.</b>\n"
            "<i>Masalan: «$300 atrofida kamerasi va batareyasi zo'r telefon tavsiya qil» yoki «Talaba uchun $500 gacha noutbuk»</i>"
        )

    prompt = f"""Sen NeuraTech kanalining eng tajribali texnologik ekspertisan (Gadget & Tech Advisor).
Foydalanuvchi quyidagi talab bilan gadjet so'ramoqda:
"{cleaned_query}"

QAT'IY KO'RSATMALAR:
1. Foydalanuvchining byudjeti va ehtiyojiga eng mos keladigan 2 yoki 3 ta aniq qurilmani taqqoslab ber.
2. Har bir qurilma uchun:
   - Nomi va taxminiy narxi (O'zbekiston yoki global bozordagi narxi $ yoki so'mda).
   - ⚡️ Asosiy ustunligi (Nega aynan shu?).
   - ⚠️ Kamchiligi yoki e'tibor berish kerak bo'lgan jihati.
   - 🎯 Kim uchun mosligi.
3. Oxirida 1 ta aniq «Xulosa va yakuniy tavsiya» (eng optimal variant) ber.
4. Javobingni o'ta qulay, tushunarli, Telegram HTML teglari (<b>, <i>, <code>, <blockquote>) bilan bezat.
5. Hech qanday Markdown ** belgilari ishlatma! Faqat toza HTML teglari.
"""
    raw = await execute_gemini_prompt(prompt)
    if raw:
        cleaned = clean_all_asterisks_to_html(raw.strip())
        return cleaned

    # Fallback
    return (
        "📱 <b>NeuraTech Gadjet Maslahatchisi</b>\n\n"
        "Kechirasiz, sun'iy intellekt xizmati ayni daqiqada band. "
        "Iltimos, bir necha daqiqadan so'ng qayta urinib ko'ring yoki savolingizni aniqroq qilib yozing (masalan: byudjet va talabingizni ko'rsating)."
    )

def get_static_ai_tools_category(cat_key: str) -> str:
    """Foydalanuvchi botda toifalar bo'yicha eng zo'r bepul AI asboblarni ko'rishi uchun tayyor katalog"""
    catalogs = {
        "design": (
            "🎨 <b>TOP BEPUL AI VOSITALAR: Rasm & Dizayn</b>\n\n"
            "1️⃣ <b>Flux.1</b> (<a href=\"https://blackforestlabs.ai\">Sayt</a>)\n"
            "• Eng realistik fotolar, matnlarni rasm ichiga xatosiz yozadi.\n"
            "• Bepul: HuggingFace va Fal.ai orqali bepul.\n\n"
            "2️⃣ <b>Krea AI</b> (<a href=\"https://krea.ai\">Sayt</a>)\n"
            "• Real vaqtda chizish va rasmlarni 4K gacha tiniqlashtirish.\n"
            "• Bepul: Har kuni bepul kreditlar beriladi.\n\n"
            "3️⃣ <b>Ideogram AI</b> (<a href=\"https://ideogram.ai\">Sayt</a>)\n"
            "• Logo, banner va tipografika uchun eng zo'r neyrotarmoq.\n"
            "• Bepul: Kuniga 20 tagacha bepul generatsiya."
        ),
        "video": (
            "🎬 <b>TOP BEPUL AI VOSITALAR: Video & Animatsiya</b>\n\n"
            "1️⃣ <b>Haiper AI</b> (<a href=\"https://haiper.ai\">Sayt</a>)\n"
            "• Matn va rasmlardan kinematik video generatsiya qilish.\n"
            "• Bepul: Kunlik bepul kreditlar bilan HD video olish mumkin.\n\n"
            "2️⃣ <b>Luma Dream Machine</b> (<a href=\"https://lumalabs.ai\">Sayt</a>)\n"
            "• Kamera harakatlari va dinamik video yaratish.\n"
            "• Bepul: Har oy 30 tagacha bepul video.\n\n"
            "3️⃣ <b>Viggle AI</b> (<a href=\"https://viggle.ai\">Sayt</a>)\n"
            "• Istalgan odam rasmini raqsga tushirish yoki harakatlantirish.\n"
            "• Bepul: Discord va veb-saytda bepul rejimi mavjud."
        ),
        "code": (
            "💻 <b>TOP BEPUL AI VOSITALAR: Dasturlash & Web</b>\n\n"
            "1️⃣ <b>Bolt.new</b> (<a href=\"https://bolt.new\">Sayt</a>)\n"
            "• Brauzerda matn orqali to'liq veb-sayt va ilovalarni yig'ish.\n"
            "• Bepul: Kunlik tokenlar bilan to'liq dastur tayyorlash.\n\n"
            "2️⃣ <b>Cursor AI</b> (<a href=\"https://cursor.com\">Sayt</a>)\n"
            "• Dunyodagi eng kuchli AI kod muharriri (VS Code asosida).\n"
            "• Bepul: Standart modellardan cheksiz foydalanish.\n\n"
            "3️⃣ <b>v0.dev by Vercel</b> (<a href=\"https://v0.dev\">Sayt</a>)\n"
            "• Faqat ta'rif berish orqali zamonaviy UI/UX komponentlar yaratish.\n"
            "• Bepul: Boshlang'ich bepul kreditlar taqdim etiladi."
        ),
        "audio": (
            "🎵 <b>TOP BEPUL AI VOSITALAR: Ovoz & Musiqa</b>\n\n"
            "1️⃣ <b>Suno AI</b> (<a href=\"https://suno.com\">Sayt</a>)\n"
            "• Matndan to'liq qo'shiq va musiqa yaratish (o'zbekcha ham biladi!).\n"
            "• Bepul: Kuniga 10 tagacha bepul qo'shiq (50 kredit).\n\n"
            "2️⃣ <b>ElevenLabs</b> (<a href=\"https://elevenlabs.io\">Sayt</a>)\n"
            "• Inson ovozidan farq qilmaydigan tabiiy nutq va ovoz klonlash.\n"
            "• Bepul: Har oy 10,000 ta bepul belgi.\n\n"
            "3️⃣ <b>Udio AI</b> (<a href=\"https://udio.com\">Sayt</a>)\n"
            "• Yuqori sifatli musiqa treklarini qismlarga bo'lib yaratish.\n"
            "• Bepul: Har oy bepul 100 ta trek."
        ),
        "research": (
            "📝 <b>TOP BEPUL AI VOSITALAR: Matn & Qidiruv</b>\n\n"
            "1️⃣ <b>DeepSeek-R1</b> (<a href=\"https://chat.deepseek.com\">Sayt</a>)\n"
            "• Ochiq manbali, eng murakkab matematika va mantiqiy masalalarni yechuvchi AI.\n"
            "• Bepul: Mutlaqo bepul va tezkor!\n\n"
            "2️⃣ <b>Perplexity AI</b> (<a href=\"https://perplexity.ai\">Sayt</a>)\n"
            "• Real vaqtdagi internet qidiruvi va manbalarga asoslangan javoblar.\n"
            "• Bepul: Cheksiz standart qidiruv.\n\n"
            "3️⃣ <b>Claude 3.5 Sonnet</b> (<a href=\"https://claude.ai\">Sayt</a>)\n"
            "• Insoniy tushunish va tahlil qilish bo'yicha dunyoda 1-o'rindagi model.\n"
            "• Bepul: Kunlik bepul so'rovlar limiti bilan."
        )
    }
    return catalogs.get(cat_key, catalogs["design"])

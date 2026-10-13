import os
import re
import html
import asyncio
from typing import Optional
import httpx
from google import genai
import config

def get_gemini_client() -> Optional[genai.Client]:
    api_key = config.GEMINI_API_KEY
    if not api_key:
        return None
    try:
        return genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[Gemini Client Xatolik]: {e}")
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

def sanitize_telegram_html(text: str) -> str:
    """Telegram qabul qiladigan HTML teglarini tozalash va xavfsiz qilish"""
    text = re.sub(r'```(?:html)?', '', text)
    text = re.sub(r'```', '', text)
    text = re.sub(r'\[\s*\.{2,}\s*\]\.{0,}', '', text)
    text = re.sub(r'\[\s*…\s*\]\.{0,}', '', text)
    text = re.sub(r'&hellip;', '', text)
    text = re.sub(r'\[\s*\.\.\.\s*\]', '', text)
    text = re.sub(r'\.{3,}', '', text)
    # Faqat gorizontal bo'shliqlar tozalanadi, qator tashlashlar (\n) saqlanadi!
    text = re.sub(r'[^\S\r\n]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def format_paragraph_spacing(text: str) -> str:
    """Sarlavha, mohiyat, sitata va xulosalar orasida chiroyli bo'sh qatorlar (joy tashlashlar) bo'lishini ta'minlash"""
    if not text:
        return ""

    # 1. Sarlavha va Mohiyati orasida 2 ta bo'sh qator (\n\n)
    text = re.sub(
        r'([^\n])\s*((?:<b>)?\s*📌\s*(?:<b>)?\s*Mohiyati:?\s*(?:</b>)?)',
        r'\1\n\n\2',
        text,
        flags=re.IGNORECASE
    )

    # Mohiyati sarlavhasidan keyin matn yangi qatordan boshlansin
    text = re.sub(
        r'((?:<b>)?\s*📌\s*(?:<b>)?\s*Mohiyati:?\s*(?:</b>)?)\s*([^\n])',
        r'\1\n\2',
        text,
        flags=re.IGNORECASE
    )

    # 2. Mohiyati matni va <blockquote orasida 2 ta bo'sh qator (\n\n)
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

    # 6. Xulosa va 📡 Manba orasida bo'sh qator
    text = re.sub(
        r'([^\n])\s*((?:<b>)?\s*📡\s*(?:<b>)?\s*Manba:?\s*(?:</b>)?)',
        r'\1\n\n\2',
        text,
        flags=re.IGNORECASE
    )

    # 7. </blockquote> va oldingi matn/keyingi matn
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

    # 8. Hashtaglar bloki oldidan bo'sh qator
    text = re.sub(r'([^\n])\s*(🏷)', r'\1\n\n\2', text)
    if "🏷" not in text:
        text = re.sub(r'([^\n#\s])\s+(#\w+)', r'\1\n\n\2', text)

    # 9. Ketma-ket 3 yoki undan ortiq bo'sh qatorlarni 2 taga keltirish
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def apply_premium_emojis(text: str) -> str:
    """Agar .env da Telegram Premium Emoji ID lari ko'rsatilgan bo'lsa, mos emojilarni <tg-emoji> formatiga o'tkazish"""
    if not text:
        return ""
    mappings = [
        (r'⚡️?', "lightning", "⚡️"),
        (r'📌', "pin", "📌"),
        (r'📊', "chart", "📊"),
        (r'💡', "bulb", "💡"),
        (r'📡', "antenna", "📡"),
        (r'🏷', "tag", "🏷"),
    ]
    for char_regex, key, fallback in mappings:
        eid = config.PREMIUM_EMOJIS.get(key)
        if eid and eid.isdigit():
            # Agar oldin <tg-emoji> ichiga olinmagan bo'lsa
            text = re.sub(
                rf'(?<!<tg-emoji[^>]*>){char_regex}(?!</tg-emoji>)',
                f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>',
                text
            )
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
    footer_text = footer if footer else ""
    full = (caption.strip() + "\n\n" + footer_text.strip()) if footer_text else caption.strip()
    if len(full) <= max_len:
        return full

    # Agar 1024 dan oshsa, footerni to'liq saqlab, captionni qisqartiramiz
    reserved_footer_len = len(footer_text) + 6 if footer_text else 0
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
    return result[:max_len]

async def fallback_formatter(title: str, summary: str, source_name: str, original_url: str = "") -> str:
    """Gemini ishlamagan taqdirda o'zbekcha tarjima qilib, ochiluvchi sitata (blockquote expandable) bilan xavfsiz formatlash"""
    clean_title = clean_ugly_symbols(title)
    clean_sum = clean_ugly_symbols(summary)

    uz_title_raw = await translate_to_uzbek(clean_title)
    uz_summary_raw = await translate_to_uzbek(clean_sum)

    uz_title = html.escape(clean_ugly_symbols(uz_title_raw[:150]))

    sentences = re.split(r'(?<=[.!?])\s+', uz_summary_raw)
    if len(sentences) >= 2:
        essence_raw = " ".join(sentences[:2])
        more_details_raw = " ".join(sentences[2:]).strip()
    else:
        essence_raw = uz_summary_raw
        more_details_raw = ""

    if not more_details_raw or len(more_details_raw) < 15:
        more_details_raw = (
            f"Ushbu yangilik IT va sun'iy intellekt sohasidagi yangi bosqichni ifodalaydi.\n\n"
            f"Qo'shimcha texnik ma'lumotlar bilan quyidagi havola orqali tanishishingiz mumkin."
        )

    essence = html.escape(clean_ugly_symbols(essence_raw[:260]))
    more_details = html.escape(clean_ugly_symbols(more_details_raw[:350]))
    safe_source = html.escape(source_name)

    link_part = f"\n\n🔗 <a href='{original_url}'>Batafsil o'qish ↗</a>" if original_url else ""

    post = (
        f"⚡️ <b>{uz_title}</b>\n\n"
        f"📌 <b>Mohiyati:</b>\n"
        f"{essence}\n\n"
        f"<blockquote expandable>📝 <b>Batafsil ma'lumot:</b>\n"
        f"{more_details}\n\n"
        f"📡 <b>Manba:</b> {safe_source}{link_part}</blockquote>\n\n"
        f"🏷 #AI #IT #Texnologiya #Yangiliklar"
    )
    spaced = format_paragraph_spacing(post)
    return apply_premium_emojis(spaced)

async def format_news(title: str, summary: str, source_name: str, category: str = "AI & IT", original_url: str = "") -> str:
    """Yangilikni o'zbek tiliga professional sarlavha, qisqa mohiyat va ochiluvchi sitata bilan tayyorlash"""
    client = get_gemini_client()

    prompt = f"""Sen — "{config.CHANNEL_USERNAME}" Telegram kanali uchun yetakchi IT va AI tahlilchisisan.
Quyidagi xabarni o'zbek tilida juda aniq, jozibador qilib qayta yoz.

Sarlavha: {title}
Bo'lim: {category}
Manba: {source_name}
Matn: {summary}

QAT'IY FORMAT TALABLARI (Har bir bo'lim orasida ALBATTA 2 ta bo'sh qator joy tashla!):

⚡️ <b>(1 ta kuchli qalin sarlavha)</b>

📌 <b>Mohiyati:</b>
(1-2 ta aniq, tushunarli jumla bilan asosiy voqea).

<blockquote expandable>📊 <b>Asosiy tafsilotlar:</b>
• (1-punkt: muhim raqamlar, benchmarklar yoki yangilik)

• (2-punkt: kimga qaratilganligi yoki narx/xususiyat)

💡 <b>Xulosa:</b>
(1 ta jumla bilan soha va foydalanuvchilarga ta'siri).

📡 <b>Manba:</b> {source_name}</blockquote>

🏷 #AI #Texnologiya #{source_name.replace(' ', '')}

QOIDALAR:
1. Bo'limlar bir-biriga yopishib qolmasin, oralarida aniq bo'sh qatorlar bo'lsin.
2. HECH QACHON do'kon chegirmalari (deals), aksiyalar, narx arzonlashuvi (Save $..., % off, chegirma) haqida yozma! Agar qurilma haqida bo'lsa, chegirmaga emas, balki QURILMANING O'ZI VA TEXNIK KUCHI, CHIP VA PARAMETRLARIGA e'tibor qarat!
3. Har doim yangi devayslar (flagman smartfonlar, noutbuklar, konsollar, aqlli qurilmalar), yangi chiplar (Nvidia GPU, AMD/Intel CPU, Apple Silicon, Qualcomm, NPU), sun'iy intellekt modellari, benchmarklar hamda istiqbolli IT va AI startaplarni (investitsiyalar, innovatsion loyihalar) yorit!
4. HECH QACHON [...] yoki ... yoki chala gap yozma!
5. Telegram caption limiti 1024 belgi! Jami matn uzunligi 700-850 belgi bo'lsin.
"""

    if client:
        candidate_models = [
            "gemini-3.8-flash",
            "gemini-3.6-flash",
            "gemini-3-flash",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-flash-latest"
        ]
        for model_name in candidate_models:
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model_name,
                    contents=prompt
                )
                if response and response.text:
                    cleaned = sanitize_telegram_html(response.text)
                    cleaned = fix_blockquote_tags(cleaned)
                    cleaned = format_paragraph_spacing(cleaned)
                    return apply_premium_emojis(cleaned)
            except Exception as e:
                continue

    return await fallback_formatter(title, summary, source_name, original_url)

async def regenerate_news(title: str, summary: str, current_caption: str) -> str:
    """Matnni qayta generatsiya qilish"""
    client = get_gemini_client()
    if not client:
        return current_caption

    prompt = f"""Ushbu xabarni yanada ixchamroq, qisqa tezislar bilan, bo'limlar orasida 2 ta bo'sh qator tashlab, ochiluvchi sitata (<blockquote expandable>) formatida o'zbek tilida qayta yoz:
Sarlavha: {title}
Matn: {summary}

Format:
⚡️ <b>Yangi sarlavha</b>

📌 <b>Mohiyati:</b>
(qisqa 1 ta jumla)

<blockquote expandable>📝 <b>Tafsilotlar:</b>
• (muhim nuqta)

• (natija)</blockquote>

🏷 #Hashtaglar

[...] belgilarisiz, 600 belgi atrofida.
"""
    candidate_models = [
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3-flash",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-flash-latest"
    ]
    for model_name in candidate_models:
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model_name,
                contents=prompt
            )
            if response and response.text:
                cleaned = sanitize_telegram_html(response.text)
                cleaned = fix_blockquote_tags(cleaned)
                cleaned = format_paragraph_spacing(cleaned)
                return apply_premium_emojis(cleaned)
        except Exception:
            continue
    return current_caption

import re
import html
import asyncio
from typing import Optional, Dict, Any, List
import httpx
from ai_formatter import execute_gemini_prompt, clean_all_asterisks_to_html

async def parse_olx_sample_prices(query: str) -> List[Dict[str, str]]:
    """
    OLX.uz qidiruvidan xavfsiz va bloklanmaydigan tarzda namunaviy e'lonlar va narxlarni olish.
    Agar OLX javob bermasa yoki bloklasa, xatosiz bo'sh ro'yxat qaytaradi.
    """
    clean_q = re.sub(r'[^\w\s]', ' ', query).strip()
    if not clean_q:
        return []

    url = f"https://www.olx.uz/d/oz/obyavleniya/q-{clean_q.replace(' ', '-')}/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "uz,ru;q=0.9,en;q=0.8"
    }

    items = []
    try:
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                text = resp.text
                # Oddiy regex orqali narx va sarlavhalarni qidirish
                # Masalan: <p data-testid="ad-price"...> 1 200 y.e. </p>
                prices = re.findall(r'data-testid="ad-price"[^>]*>([^<]+)<', text)
                titles = re.findall(r'<h6[^>]*>([^<]+)<', text)
                for t, p in zip(titles[:4], prices[:4]):
                    t_clean = html.unescape(t.strip())
                    p_clean = html.unescape(p.strip())
                    if t_clean and p_clean:
                        items.append({"title": t_clean[:50], "price": p_clean})
    except Exception as e:
        # OLX bloklagan yoki internet sekin bo'lsa, xatosiz davom etamiz
        pass

    return items

async def get_market_price_report(user_query: str) -> str:
    """
    O'zbekiston va Dunyo bo'ylab har qanday mashina, smartfon yoki gadjet
    narxlarini tahlil qiluvchi va professional hisobot beruvchi asosiy funksiya.
    """
    q = (user_query or "").strip()
    if not q:
        return (
            "💰 <b>Narxlar Radari</b>\n\n"
            "Iltimos, qidirayotgan mashinangiz yoki gadjetingiz nomini yozing.\n"
            "<i>Masalan: «Cobalt 2024 narxi», «iPhone 15 Pro 128GB», «BYD Song Plus», «Gentra 3-pozitsiya», «Redmi Note 13 Pro»</i>"
        )

    # 1. OLX namunalarini tekshirish (asinxron)
    olx_samples = []
    try:
        olx_samples = await parse_olx_sample_prices(q)
    except Exception:
        pass

    olx_text = ""
    if olx_samples:
        lines = ["📌 <b>Haqiqiy e'lonlardan namunalar (OLX / Bozor):</b>"]
        for s in olx_samples[:3]:
            lines.append(f"• {html.escape(s['title'])}: <b>{html.escape(s['price'])}</b>")
        olx_text = "\n" + "\n".join(lines) + "\n"

    # 2. Gemini orqali O'zbekiston va Xalqaro bozor tahlili
    prompt = f"""Sen O'zbekiston va jahon bozoridagi avtomobillar, smartfonlar va texnika narxlari bo'yicha yetakchi tahlilchisan.
Foydalanuvchi quyidagi modelning narxini so'ramoqda:
"{q}"

QAT'IY VAZIFA:
O'zbekiston bozoridagi (Malika, Abu Saxiy, Sergeli avtomobil bozori, salonlar va OLX) hamda jahondagi real holat bo'yicha aniq narx ma'lumotlarini taqdim et.

TALABLAR:
1. Agar bu AVTOMOBIL bo'lsa (Cobalt, Gentra, Tracker, Onix, BYD, Malibu, Kia, Hyundai va h.k.):
   - O'zbekistondagi yangi (salon/diler) narxi ($ va so'mda).
   - Ikkilamchi (ishlatilgan/bozor) narxi ($ da).
   - Asosiy pozitsiyalari bo'yicha farq.
   - Bozor holati (navbat bormi, narx tushyaptimi yoki oshyapdimi?).

2. Agar bu SMARTFON yoki GADJET bo'lsa (iPhone, Samsung, Xiaomi, Honor, noutbuk va h.k.):
   - O'zbekistondagi do'konlardagi yangi narxi (Malika / Abu Saxiy / Uzum, $ va so'mda).
   - Ikkilamchi (ishlatilgan / ideal holatdagi) narxi.
   - Xorijdagi (AQSh / Yevropa rasmiy) MSRP narxi.
   - Qaysi xotira va modelni olish eng ma'qul.

3. Formatlash:
   - Telegram HTML teglari (<b>, <i>, <code>, <blockquote>).
   - Hech qanday Markdown ** ishlatma!
   - So'ngida qisqa 1 ta «Maslahat / Tavsiya» ber.
"""
    raw_ai = await execute_gemini_prompt(prompt)
    if raw_ai:
        ai_clean = clean_all_asterisks_to_html(raw_ai.strip())
        res = (
            f"💰 <b>NEURATECH BOZOR NARXLARI RADARI</b>\n"
            f"🔍 <i>So'rov: {html.escape(q)}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{ai_clean}\n"
            f"{olx_text}"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>Narxlar O'zbekiston (Toshkent va viloyatlar) hamda global bozorning o'rtacha ko'rsatkichlari asosida keltirilgan.</i>"
        )
        return res

    # Fallback
    return (
        f"💰 <b>Narxlar Radari: «{html.escape(q)}»</b>\n\n"
        f"Hozirda bozor ma'lumotlari bazasi yangilanmoqda. "
        f"Iltimos, so'rovingizni to'liqroq yozing (masalan: model yili, pozitsiyasi yoki xotira hajmi)."
    )

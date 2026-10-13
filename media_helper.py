import html
from typing import Optional, Union
import httpx
from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

import io
from PIL import Image, ImageDraw, ImageFont

def apply_watermark(image_bytes: bytes) -> bytes:
    """Rasmning pastki o'ng burchagiga estetik NeuraTech suv belgisi (Watermark) qo'yish"""
    try:
        base_img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        width, height = base_img.size

        if width < 300 or height < 200:
            return image_bytes

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        text = "⚡️ NeuraTech | @neuratech_uz"
        font_size = max(13, int(min(width, height) * 0.034))
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        padding_x = int(font_size * 0.7)
        padding_y = int(font_size * 0.4)
        pill_w = text_w + padding_x * 2
        pill_h = text_h + padding_y * 2

        margin = int(min(width, height) * 0.03)
        x2 = width - margin
        y2 = height - margin
        x1 = x2 - pill_w
        y1 = y2 - pill_h

        # Shaffof qoramtir hoshiyali fon
        draw.rounded_rectangle(
            (x1, y1, x2, y2),
            radius=int(pill_h / 2),
            fill=(12, 18, 30, 185),
            outline=(0, 210, 255, 140),
            width=1
        )

        text_x = x1 + padding_x
        text_y = y1 + padding_y - (bbox[1] if bbox[1] < 0 else 0)
        draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255, 240))

        combined = Image.alpha_composite(base_img, overlay).convert("RGB")
        out_io = io.BytesIO()
        combined.save(out_io, format="JPEG", quality=90)
        return out_io.getvalue()
    except Exception as e:
        print(f"[Watermark xatolik]: {e}")
        return image_bytes

async def send_media_robust(
    bot: Bot,
    chat_id: Union[int, str],
    media_url: Optional[str],
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None
) -> bool:
    """
    Rasm yoki videoni yuklab olib, hotlink/CDN bloklarini chetlab o'tgan holda Telegramga xavfsiz yuborish.
    Agar video bo'lsa send_video, rasm bo'lsa watermark bilan send_photo ishlatadi.
    """
    if not media_url:
        return False

    clean_url = html.unescape(str(media_url)).strip()
    if not clean_url.startswith("http"):
        return False

    is_video_ext = any(clean_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".webm"])

    # 1. Avval fayl baytlarini httpx orqali yuklab olish (CDN va bot-bloklashlarni yengish)
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(clean_url)
            if resp.status_code == 200 and len(resp.content) > 500:
                content_type = resp.headers.get("content-type", "").lower()
                is_video = is_video_ext or "video" in content_type

                if is_video:
                    input_file = BufferedInputFile(resp.content, filename="video.mp4")
                    await bot.send_video(
                        chat_id=chat_id,
                        video=input_file,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                else:
                    watermarked_bytes = apply_watermark(resp.content)
                    input_file = BufferedInputFile(watermarked_bytes, filename="news.jpg")
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=input_file,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                return True
    except Exception as buf_err:
        print(f"[send_media_robust buffer xatolik]: {buf_err}. URL orqali urinilmoqda...")

    # 2. Agar baytlar orqali bo'lmasa, to'g'ridan-to'g'ri URL orqali Telegram'ga uzatish
    try:
        if is_video_ext:
            await bot.send_video(
                chat_id=chat_id,
                video=clean_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            await bot.send_photo(
                chat_id=chat_id,
                photo=clean_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        return True
    except Exception as url_err:
        print(f"[send_media_robust URL xatolik]: {url_err}")
        return False

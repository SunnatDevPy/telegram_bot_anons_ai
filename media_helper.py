import html
from typing import Optional, Union, Any
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

import os
import io
from PIL import Image, ImageDraw, ImageFont

FONT_PATHS = [
    os.path.join(os.path.dirname(__file__), "assets", "font.ttf"),
    "assets/font.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]

def get_watermark_font(size: int) -> ImageFont.ImageFont:
    """Tiniq TrueType shriftni yuklash"""
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()

def apply_watermark(image_bytes: bytes) -> bytes:
    """Rasmning pastki o'ng burchagiga estetik, tiniq va zamonaviy NeuraTech suv belgisi (Watermark) qo'yish"""
    try:
        base_img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        width, height = base_img.size

        # Kichik o'lchamli rasmlarga watermark qo'yilmaydi
        if width < 300 or height < 200:
            out_io = io.BytesIO()
            base_img.convert("RGB").save(out_io, format="JPEG", quality=95, subsampling=0)
            return out_io.getvalue()

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Matn va shrift o'lchami rasm hajmiga mutanosib (aniq va tiniq)
        font_size = max(16, int(min(width, height) * 0.038))
        font = get_watermark_font(font_size)

        text = "NeuraTech  |  @neuratech_uz"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        # Vektorli chaqmoq (⚡️) piktogrammasi o'lchamlari
        icon_w = int(font_size * 0.75)
        icon_h = int(font_size * 1.05)
        icon_spacing = int(font_size * 0.35)

        pad_x = int(font_size * 0.85)
        pad_y = int(font_size * 0.45)

        total_content_w = icon_w + icon_spacing + text_w
        pill_w = total_content_w + pad_x * 2
        pill_h = max(text_h, icon_h) + pad_y * 2

        margin = max(18, int(min(width, height) * 0.035))
        x2 = width - margin
        y2 = height - margin
        x1 = x2 - pill_w
        y1 = y2 - pill_h

        # 1. Zamonaviy qora shisha fon (Glassmorphism badge) va neon hoshiya
        draw.rounded_rectangle(
            (x1, y1, x2, y2),
            radius=int(pill_h / 2),
            fill=(10, 15, 26, 220),
            outline=(0, 210, 255, 190),
            width=2
        )

        # 2. Tiniq vektor chaqmoq belgisi (Neon Cyan)
        ix = x1 + pad_x
        iy = y1 + pad_y + (max(text_h, icon_h) - icon_h) // 2
        bolt_points = [
            (ix + icon_w * 0.65, iy),
            (ix + icon_w * 0.15, iy + icon_h * 0.52),
            (ix + icon_w * 0.52, iy + icon_h * 0.52),
            (ix + icon_w * 0.35, iy + icon_h),
            (ix + icon_w * 0.90, iy + icon_h * 0.42),
            (ix + icon_w * 0.55, iy + icon_h * 0.42),
        ]
        draw.polygon(bolt_points, fill=(0, 235, 255, 255))

        # 3. Tiniq oq yozuv
        tx = ix + icon_w + icon_spacing
        ty = y1 + pad_y - bbox[1] + (max(text_h, icon_h) - text_h) // 2
        draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))

        combined = Image.alpha_composite(base_img, overlay).convert("RGB")
        out_io = io.BytesIO()
        # Maksimal sifat (quality=95 va subsampling=0) bilan saqlash (matn qirralari xira bo'lmaydi)
        combined.save(out_io, format="JPEG", quality=95, subsampling=0)
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
    Rasm, video yoki animatsiyali GIFni yuklab olib, Telegramga mos ravishda xavfsiz yuborish.
    - GIF (.gif) bo'lsa -> bot.send_animation (avtomatik qayta-qayta aylanuvchi animatsiya)
    - Video (.mp4, .mov, .webm) bo'lsa -> bot.send_video
    - Rasm (.jpg, .png, .webp) bo'lsa -> watermark badge bilan bot.send_photo
    """
    if not media_url:
        return False

    clean_url = html.unescape(str(media_url)).strip()
    if not clean_url.startswith("http"):
        return False

    url_lower = clean_url.lower()
    is_gif_ext = url_lower.endswith(".gif") or ".gif?" in url_lower
    is_video_ext = any(url_lower.endswith(ext) or f"{ext}?" in url_lower for ext in [".mp4", ".mov", ".webm"])

    # 1. Avval fayl baytlarini httpx orqali yuklab olish (CDN va bot-bloklashlarni yengish)
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=16.0, follow_redirects=True) as client:
            resp = await client.get(clean_url)
            if resp.status_code == 200 and len(resp.content) > 500:
                content_type = resp.headers.get("content-type", "").lower()
                is_gif = is_gif_ext or "image/gif" in content_type or "gif" in content_type
                is_video = is_video_ext or ("video/" in content_type and not is_gif)

                if is_gif:
                    input_file = BufferedInputFile(resp.content, filename="animation.gif")
                    await bot.send_animation(
                        chat_id=chat_id,
                        animation=input_file,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                    return True
                elif is_video:
                    input_file = BufferedInputFile(resp.content, filename="video.mp4")
                    await bot.send_video(
                        chat_id=chat_id,
                        video=input_file,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                        supports_streaming=True
                    )
                    return True
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
        if is_gif_ext:
            await bot.send_animation(
                chat_id=chat_id,
                animation=clean_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        elif is_video_ext:
            await bot.send_video(
                chat_id=chat_id,
                video=clean_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
                supports_streaming=True
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

async def send_user_preview_media(
    message_or_call: Any,
    media_url: Optional[str],
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None
) -> bool:
    """Foydalanuvchi yoki adminga bot ichida rasm, video yoki animatsiyani to'g'ri formatda preview qilish"""
    if not media_url:
        return False

    clean_url = html.unescape(str(media_url)).strip()
    if not clean_url.startswith("http"):
        return False

    url_lower = clean_url.lower()
    is_gif = url_lower.endswith(".gif") or ".gif?" in url_lower
    is_video = any(url_lower.endswith(ext) or f"{ext}?" in url_lower for ext in [".mp4", ".mov", ".webm"])

    msg_obj = message_or_call.message if hasattr(message_or_call, "message") else message_or_call

    try:
        if is_gif:
            await msg_obj.answer_animation(
                animation=clean_url,
                caption=caption[:1024],
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return True
        elif is_video:
            await msg_obj.answer_video(
                video=clean_url,
                caption=caption[:1024],
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return True
        else:
            await msg_obj.answer_photo(
                photo=clean_url,
                caption=caption[:1024],
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return True
    except Exception as e:
        print(f"[send_user_preview_media xatolik]: {e}")
        return False

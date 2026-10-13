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

async def send_media_robust(
    bot: Bot,
    chat_id: Union[int, str],
    media_url: Optional[str],
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None
) -> bool:
    """
    Rasm yoki videoni yuklab olib, hotlink/CDN bloklarini chetlab o'tgan holda Telegramga xavfsiz yuborish.
    Agar video bo'lsa send_video, rasm bo'lsa send_photo ishlatadi.
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
                    ext = ".png" if "png" in content_type else ".jpg"
                    input_file = BufferedInputFile(resp.content, filename=f"image{ext}")
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

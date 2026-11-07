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
import config

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

        # Kichik o'lchamli rasmlar (masalan emojilar yoki mayda ikonkalar) post banneriga yaramaydi
        # Bunday holatda kanalning rasmiy HD logotipidan foydalanamiz
        if width < 320 or height < 200:
            if hasattr(config, "LOGO_PATH") and os.path.exists(config.LOGO_PATH):
                try:
                    with open(config.LOGO_PATH, "rb") as lf:
                        logo_data = lf.read()
                        if logo_data != image_bytes:
                            return apply_watermark(logo_data)
                except Exception:
                    pass
            out_io = io.BytesIO()
            base_img.convert("RGB").save(out_io, format="JPEG", quality=95, subsampling=0)
            return out_io.getvalue()

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Matn va shrift o'lchami rasm hajmiga mutanosib va ixcham (matnlarni to'sib qo'ymaslik uchun)
        font_size = max(14, int(min(width, height) * 0.032))
        font = get_watermark_font(font_size)

        text = "NeuraTech"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        # Vektorli chaqmoq (⚡️) piktogrammasi o'lchamlari
        icon_w = int(font_size * 0.70)
        icon_h = int(font_size * 1.00)
        icon_spacing = int(font_size * 0.30)

        pad_x = int(font_size * 0.70)
        pad_y = int(font_size * 0.35)

        total_content_w = icon_w + icon_spacing + text_w
        pill_w = total_content_w + pad_x * 2
        pill_h = max(text_h, icon_h) + pad_y * 2

        margin = max(14, int(min(width, height) * 0.025))
        x2 = width - margin
        y2 = height - margin
        x1 = x2 - pill_w
        y1 = y2 - pill_h

        # 1. Ixcham zamonaviy qora shisha fon (Glassmorphism badge) va neon hoshiya
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

        # 3. Tiniq oq NeuraTech yozuvi
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

def generate_tech_cover_banner(
    title: str,
    category: str = "IT & AI",
    source_name: str = "NeuraTech"
) -> bytes:
    """
    Rasmsiz yangiliklar uchun zamonaviy Dark-Tech / Cyber Neon 1200x675 HD
    muqova (Cover Banner) generatsiya qilish.
    Shriftda emoji kvadratchalari chiqmasligi uchun barcha piktogrammalar
    vektorli grafikada chiziladi.
    """
    import hashlib
    import re as _re

    w, h = 1200, 675
    img = Image.new("RGBA", (w, h), (7, 12, 22, 255))
    draw = ImageDraw.Draw(img)

    # Sarlavhadan xesh olib rang urg'ularini biroz farqlash
    seed_hash = int(hashlib.md5((title or "neuratech").encode("utf-8", errors="ignore")).hexdigest()[:6], 16)
    accent_palettes = [
        ((0, 225, 255), (0, 140, 255)),    # Cyber Cyan & Electric Blue
        ((0, 240, 180), (0, 180, 255)),    # Neon Emerald & Cyan
        ((140, 100, 255), (0, 215, 255)),  # AI Violet & Cyan
        ((56, 189, 248), (99, 102, 241)),  # Sky & Indigo
    ]
    primary_rgb, secondary_rgb = accent_palettes[seed_hash % len(accent_palettes)]

    # 1. To'q ko'k-qora chuqur gradient fon
    for y in range(h):
        ratio = y / float(h)
        r = int(6 + ratio * 10)
        g = int(10 + ratio * 16)
        b = int(20 + ratio * 28)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

    # 2. Yuqori o'ng va pastki chap burchakda neon nur (Radial Glow)
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(glow)
    cx1, cy1 = 1060, 110
    for rad in range(380, 20, -12):
        alpha = int(34 * (1 - (rad / 380.0) ** 1.6))
        g_draw.ellipse(
            [cx1 - rad, cy1 - rad, cx1 + rad, cy1 + rad],
            fill=(primary_rgb[0], primary_rgb[1], primary_rgb[2], alpha)
        )
    cx2, cy2 = 160, h - 80
    for rad in range(300, 20, -12):
        alpha = int(22 * (1 - (rad / 300.0) ** 1.6))
        g_draw.ellipse(
            [cx2 - rad, cy2 - rad, cx2 + rad, cy2 + rad],
            fill=(secondary_rgb[0], secondary_rgb[1], secondary_rgb[2], alpha)
        )
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    # 3. Texnologik to'r (Subtle Cyber Grid)
    grid_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gr_draw = ImageDraw.Draw(grid_overlay)
    for gx in range(0, w, 75):
        gr_draw.line([(gx, 0), (gx, h)], fill=(primary_rgb[0], primary_rgb[1], primary_rgb[2], 11))
    for gy in range(0, h, 75):
        gr_draw.line([(0, gy), (w, gy)], fill=(primary_rgb[0], primary_rgb[1], primary_rgb[2], 11))
    img = Image.alpha_composite(img, grid_overlay)
    draw = ImageDraw.Draw(img)

    # Shriftlar
    f_badge = get_watermark_font(20)
    f_title = get_watermark_font(44)
    f_brand = get_watermark_font(23)
    f_sub = get_watermark_font(19)

    # Matndan HTML teglar va emojilarni tozalash (toza tipografiya uchun)
    def _clean_printable(val: str) -> str:
        val = _re.sub(r"<[^>]+>", "", val or "")
        # Faqat lotin, kirill, raqam va tinish belgilarini qoldirish
        val = _re.sub(r"[^\w\s\d.,:;!?()\"'«»\-–—/+%$#@&*ʻʼ’`]", "", val, flags=_re.UNICODE)
        return _re.sub(r"\s+", " ", val).strip()

    clean_cat = (_clean_printable(category) or "AI & TEXNOLOGIYA").upper()
    if len(clean_cat) > 32:
        clean_cat = clean_cat[:32] + "..."

    clean_src = _clean_printable(source_name) or "NeuraTech AI"
    if len(clean_src) > 26:
        clean_src = clean_src[:26] + "..."

    # 4. Yuqori chap: Kategoriya kapsulasi + vektor chaqmoq
    cat_bbox = draw.textbbox((0, 0), clean_cat, font=f_badge)
    cat_tw = cat_bbox[2] - cat_bbox[0]
    cat_pill_w = 52 + cat_tw + 26
    draw.rounded_rectangle(
        [70, 52, 70 + cat_pill_w, 100],
        radius=24,
        fill=(12, 22, 42, 235),
        outline=(primary_rgb[0], primary_rgb[1], primary_rgb[2], 210),
        width=2
    )
    # Chaqmoq belgisi
    bx, by, bw, bh = 90, 64, 15, 22
    bolt = [
        (bx + bw * 0.65, by),
        (bx + bw * 0.15, by + bh * 0.52),
        (bx + bw * 0.52, by + bh * 0.52),
        (bx + bw * 0.35, by + bh),
        (bx + bw * 0.90, by + bh * 0.42),
        (bx + bw * 0.55, by + bh * 0.42),
    ]
    draw.polygon(bolt, fill=(primary_rgb[0], primary_rgb[1], primary_rgb[2], 255))
    draw.text((116, 64), clean_cat, font=f_badge, fill=(primary_rgb[0], primary_rgb[1], primary_rgb[2], 255))

    # 5. Yuqori o'ng: Manba kapsulasi
    src_label = f"MANBA: {clean_src.upper()}"
    src_bbox = draw.textbbox((0, 0), src_label, font=f_sub)
    src_tw = src_bbox[2] - src_bbox[0]
    src_pill_w = src_tw + 44
    draw.rounded_rectangle(
        [w - 70 - src_pill_w, 52, w - 70, 100],
        radius=24,
        fill=(14, 24, 44, 215),
        outline=(80, 110, 160, 180),
        width=1
    )
    draw.text((w - 70 - src_pill_w + 22, 65), src_label, font=f_sub, fill=(190, 215, 245, 255))

    # 6. Markaziy sarlavha bloki (avto qatorlarga bo'lish)
    clean_title = _clean_printable(title) or "Sun'iy intellekt va zamonaviy texnologiyalar olamidagi eng so'nggi yangiliklar"
    words = clean_title.split()
    lines = []
    curr = ""
    max_text_w = w - 210
    for wd in words:
        test = (curr + " " + wd).strip()
        tb = draw.textbbox((0, 0), test, font=f_title)
        if (tb[2] - tb[0]) <= max_text_w:
            curr = test
        else:
            if curr:
                lines.append(curr)
            curr = wd
    if curr:
        lines.append(curr)

    if len(lines) > 4:
        lines = lines[:4]
        lines[-1] = lines[-1][:max(10, len(lines[-1]) - 3)].rstrip() + "..."

    line_h = 62
    total_th = len(lines) * line_h
    start_y = max(165, (h - total_th) // 2 - 10)

    # Sarlavha chap tomonidagi neon chiziq (Accent Bar)
    draw.rounded_rectangle(
        [70, start_y - 8, 80, start_y + total_th + 6],
        radius=5,
        fill=(primary_rgb[0], primary_rgb[1], primary_rgb[2], 255)
    )

    for i, ln in enumerate(lines):
        y_pos = start_y + i * line_h
        # Yumshoq qora soya
        draw.text((108, y_pos + 3), ln, font=f_title, fill=(0, 0, 0, 210))
        # Asosiy oq yozuv
        draw.text((105, y_pos), ln, font=f_title, fill=(255, 255, 255, 255))

    # 7. Pastki brending: Chapda NeuraTech AI, O'ngda Kanal username
    draw.rounded_rectangle(
        [70, h - 96, 320, h - 46],
        radius=25,
        fill=(10, 16, 30, 235),
        outline=(primary_rgb[0], primary_rgb[1], primary_rgb[2], 200),
        width=2
    )
    bx2, by2, bw2, bh2 = 92, h - 83, 16, 24
    bolt2 = [
        (bx2 + bw2 * 0.65, by2),
        (bx2 + bw2 * 0.15, by2 + bh2 * 0.52),
        (bx2 + bw2 * 0.52, by2 + bh2 * 0.52),
        (bx2 + bw2 * 0.35, by2 + bh2),
        (bx2 + bw2 * 0.90, by2 + bh2 * 0.42),
        (bx2 + bw2 * 0.55, by2 + bh2 * 0.42),
    ]
    draw.polygon(bolt2, fill=(primary_rgb[0], primary_rgb[1], primary_rgb[2], 255))
    draw.text((118, h - 84), "NeuraTech AI", font=f_brand, fill=(255, 255, 255, 255))

    ch_user = getattr(config, "CHANNEL_USERNAME", "@neura_tech_ai") or "@neura_tech_ai"
    if not ch_user.startswith("@"):
        ch_user = "@" + ch_user
    ch_bbox = draw.textbbox((0, 0), ch_user, font=f_brand)
    ch_w = (ch_bbox[2] - ch_bbox[0]) + 48
    draw.rounded_rectangle(
        [w - 70 - ch_w, h - 96, w - 70, h - 46],
        radius=25,
        fill=(10, 16, 30, 215),
        outline=(70, 100, 150, 170),
        width=1
    )
    draw.text((w - 70 - ch_w + 24, h - 84), ch_user, font=f_brand, fill=(primary_rgb[0], primary_rgb[1], primary_rgb[2], 255))

    out_io = io.BytesIO()
    img.convert("RGB").save(out_io, format="JPEG", quality=95, subsampling=0)
    return out_io.getvalue()

def get_or_create_cover_for_post(
    title: str,
    category: str = "IT & AI",
    source_name: str = "NeuraTech"
) -> Optional[str]:
    """
    Rasmsiz yangilik uchun HD banner yaratib, `assets/covers/` ichiga saqlaydi
    va fayl yo'lini qaytaradi.
    """
    import hashlib
    try:
        covers_dir = os.path.join(os.path.dirname(__file__), "assets", "covers")
        os.makedirs(covers_dir, exist_ok=True)
        digest = hashlib.md5(f"{title}|{source_name}".encode("utf-8", errors="ignore")).hexdigest()[:12]
        file_path = os.path.join(covers_dir, f"cover_{digest}.jpg")
        if not os.path.exists(file_path):
            banner_bytes = generate_tech_cover_banner(title=title, category=category, source_name=source_name)
            with open(file_path, "wb") as f:
                f.write(banner_bytes)
        return file_path
    except Exception as e:
        print(f"[Cover Banner Yaratish Xatolik]: {e}")
        return None

async def send_media_robust(
    bot: Bot,
    chat_id: Union[int, str],
    media_url: Optional[str],
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None
) -> bool:
    """
    Rasm, video, animatsiyali GIF, lokal fayl yoki Telegram file_id ni xavfsiz yuborish.
    - Lokal fayl (masalan, avto-generatsiya qilingan Cover Banner) -> bot.send_photo
    - Telegram file_id -> bot.send_photo
    - GIF (.gif) -> bot.send_animation
    - Video (.mp4, .mov, .webm) -> bot.send_video
    - Rasm URL (.jpg, .png, .webp) -> watermark badge bilan bot.send_photo
    """
    if not media_url:
        return False

    clean_url = html.unescape(str(media_url)).strip()
    if not clean_url:
        return False

    # 0. Lokal fayl tekshiruvi (masalan: assets/covers/cover_xxx.jpg)
    if os.path.exists(clean_url):
        try:
            with open(clean_url, "rb") as f:
                raw_bytes = f.read()
            # Agar bu avto-kover bo'lmasa, suv belgisi qo'shamiz; koverda o'z brendingi bor
            if "cover_" not in os.path.basename(clean_url):
                raw_bytes = apply_watermark(raw_bytes)
            input_file = BufferedInputFile(raw_bytes, filename=os.path.basename(clean_url) or "cover.jpg")
            await bot.send_photo(
                chat_id=chat_id,
                photo=input_file,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return True
        except Exception as loc_err:
            print(f"[send_media_robust local file xatolik]: {loc_err}")
            return False

    # 0.5. Telegram file_id tekshiruvi (http bilan boshlanmaydigan ID: tg_video:, tg_anim: yoki photo file_id)
    if not clean_url.startswith("http"):
        try:
            if clean_url.startswith("tg_video:"):
                await bot.send_video(
                    chat_id=chat_id,
                    video=clean_url.split(":", 1)[1],
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    supports_streaming=True
                )
            elif clean_url.startswith("tg_anim:"):
                await bot.send_animation(
                    chat_id=chat_id,
                    animation=clean_url.split(":", 1)[1],
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
        except Exception as fid_err:
            print(f"[send_media_robust file_id xatolik]: {fid_err}")
            return False

    url_lower = clean_url.lower()
    is_gif_ext = url_lower.endswith(".gif") or ".gif?" in url_lower
    is_video_ext = any(url_lower.endswith(ext) or f"{ext}?" in url_lower for ext in [".mp4", ".mov", ".webm", ".m4v", ".ogv", ".mkv"])
    req_timeout = 38.0 if (is_video_ext or is_gif_ext) else 25.0

    # 1. Avval fayl baytlarini httpx orqali yuklab olish (CDN va bot-bloklashlarni yengish)
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=req_timeout, follow_redirects=True) as client:
            resp = await client.get(clean_url)
            if resp.status_code == 200 and 500 < len(resp.content) <= 48 * 1024 * 1024:
                content_type = resp.headers.get("content-type", "").lower()
                magic = resp.content[:32]
                is_magic_gif = magic.startswith(b"GIF8")
                is_magic_video = b"ftyp" in magic or magic.startswith(b"\x1a\x45\xdf\xa3")

                is_gif = is_gif_ext or "image/gif" in content_type or "gif" in content_type or is_magic_gif
                is_video = is_video_ext or ("video/" in content_type and not is_gif) or is_magic_video

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
    """Foydalanuvchi yoki adminga bot ichida rasm, video, animatsiya yoki lokal bannerni preview qilish"""
    if not media_url:
        return False

    clean_url = html.unescape(str(media_url)).strip()
    if not clean_url:
        return False

    msg_obj = message_or_call.message if hasattr(message_or_call, "message") else message_or_call

    # Lokal fayl bo'lsa
    if os.path.exists(clean_url):
        try:
            with open(clean_url, "rb") as f:
                input_file = BufferedInputFile(f.read(), filename=os.path.basename(clean_url) or "cover.jpg")
            await msg_obj.answer_photo(
                photo=input_file,
                caption=caption[:1024],
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return True
        except Exception as e:
            print(f"[send_user_preview_media local xatolik]: {e}")
            return False

    # Telegram file_id bo'lsa
    if not clean_url.startswith("http"):
        try:
            if clean_url.startswith("tg_video:"):
                await msg_obj.answer_video(
                    video=clean_url.split(":", 1)[1],
                    caption=caption[:1024],
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
            elif clean_url.startswith("tg_anim:"):
                await msg_obj.answer_animation(
                    animation=clean_url.split(":", 1)[1],
                    caption=caption[:1024],
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
            else:
                await msg_obj.answer_photo(
                    photo=clean_url,
                    caption=caption[:1024],
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
            return True
        except Exception as e:
            print(f"[send_user_preview_media file_id xatolik]: {e}")
            return False

    url_lower = clean_url.lower()
    is_gif = url_lower.endswith(".gif") or ".gif?" in url_lower
    is_video = any(url_lower.endswith(ext) or f"{ext}?" in url_lower for ext in [".mp4", ".mov", ".webm", ".m4v", ".ogv", ".mkv"])

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

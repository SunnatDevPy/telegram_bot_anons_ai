# 🤖 AI & IT Telegram Avto-Post va Reklama Boti

Ushbu bot dunyodagi eng yetakchi IT va AI manbalaridan (benchmarklar, yangi modellar, kompaniyalar, chiplar, GPUlar) yangiliklarni avtomatik kuzatib boradi, **Google Gemini AI** orqali o'zbek tiliga professional, jozibador va tushunarli formatda qayta yozadi hamda sifatli rasmi bilan birga rasmiy Telegram kanaliga joylaydi.

---

## 🌟 Yangi va Asosiy Imkoniyatlari

1. **⚙️ .env orqali Rasmiy Kanalni Boshqarish**:
   - `.env` faylida kanalingiz ma'lumotlarini istalgan vaqt o'zgartirishingiz mumkin:
     ```env
     CHANNEL_ID=-1002229527376
     CHANNEL_USERNAME=@grand_mobile_anons
     CHANNEL_LINK=https://t.me/grand_mobile_anons
     ```
   - Kanal o'zgarganda botning barcha xabarlari, havolalari va imzolari avtomatik tarzda yangi kanalga moslashadi.

2. **📢 Mijozlarga Reklama (Broadcast) Yuborish (1:1 va Premium Emojilar bilan)**:
   - Botga a'zo bo'lgan barcha foydalanuvchilar avtomatik tarzda SQLite bazasida ro'yxatga olinadi.
   - Admin xohlagan vaqtda **`🟢 📢 Reklama (Broadcast) yuborish`** tugmasi yoki `/broadcast` (`/reklama`) buyrug'ini bosadi.
   - Ixtiyoriy xabar (matn, rasm, video, audio, havola yoki **Telegram Premium emojilari**) yuboriladi.
   - Telegramning rasmiy `copy_message` tizimi orqali xabar barcha formatlari, shriftlari va maxsus premium emojilari bilan **100% birga-bir (1:1)** ko'chiriladi.
   - Yuborishdan oldin adminga **PREVIEW (Oldindan ko'rish)** va tasdiqlash tugmalari ko'rsatiladi.
   - Yuborilgach, qancha yetkazilgani va qancha bloklangani hisoboti beriladi.

3. **✨ Telegram Premium Custom Emoji Qo'llab-quvvatlashi**:
   - Agar sizda Telegram Premium maxsus emojilari (Custom Emoji IDs) bo'lsa, ularni `.env` ga kiritishingiz mumkin:
     ```env
     EMOJI_LIGHTNING=5368324170671202286
     EMOJI_PIN=
     EMOJI_CHART=
     EMOJI_BULB=
     EMOJI_ANTENNA=
     EMOJI_TAG=
     ```
   - Agar bo'sh qoldirilsa, avtomatik tarzda standart yuqori sifatli emojilar (`⚡️`, `📌`, `📊`, `💡`, `📡`, `🏷`) ishlatiladi.

4. **🟢 Yashil Rangli Asosiy Tugmalar (Visual Green Accents)**:
   - Eng muhim va asosiy harakat tugmalari yashil indikatorlar (`🟢`, `✅`) bilan ajratib ko'rsatilgan:
     - `🟢 🚀 Hoziroq kanalga chiqarish` — postni darhol chop etish
     - `🟢 🔥 Top 10 ta AI Yangilik` — mijozlar uchun asosiy katalog
     - `🟢 📢 Reklama (Broadcast) yuborish` — marketing xabarlari
     - `🟢 🔍 Hozir yangilik qidirish` — zudlik bilan tahlil
     - `🟢 Rejim: Avtomatik (Faol)` — avtomatik rejim belgisi

5. **📱 Mijozlar Menyu va Top 10 Katalogi**:
   - Oddiy foydalanuvchilar botga kirganda (`/start`):
     - `🟢 🔥 Top 10 ta AI Yangilik` — raqamli tugmalar (`[ 1 ] [ 2 ] ... [ 10 ]`) orqali eng sara yangiliklarni ochib o'qish imkoniyati.
     - `⚡️ Eng so'nggi yangilik` — oxirgi e'lon qilingan anons.
     - `📢 Rasmiy Kanalimiz` — kanalga to'g'ridan-to'g'ri obuna havolasi.

6. **📝 Toza va Chiroyli Abzaslar (Paragraph Spacing)**:
   - Sarlavha, qisqa mohiyat, `<blockquote expandable>` ochiluvchi tafsilotlar bloki va xulosalar orasida chiroyli bo'sh qatorlar joy tashlanishi to'liq kafolatlangan.

---

## 🚀 Qadam-baqadam Ishga Tushirish

### 1. Kanalga Botni Admin Qilish:
1. Telegramda kanalingiz sozlamalariga kiring (`Settings ➡️ Administrators`).
2. **Add Administrator (Admin qo'shish)** tugmasini bosib, botingizni qidiring.
3. Botga **"Post Messages" (Xabarlar yuborish)** huquqini bering va saqlang.

### 2. .env Faylini Sozlash:
`.env` faylida barcha kalitlar va kanalingiz havolasini tekshiring:
```env
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
CHANNEL_ID=-1002229527376
CHANNEL_USERNAME=@grand_mobile_anons
CHANNEL_LINK=https://t.me/grand_mobile_anons
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
ADMIN_ID=YOUR_TELEGRAM_ID
CHECK_INTERVAL_MINUTES=25
DEFAULT_MODE=moderation
```

### 3. Botni Ishga Tushirish:
```powershell
python main.py
```
Admin bo'lib botga `/start` yozganingizda to'liq boshqaruv paneli ochiladi!

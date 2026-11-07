# 🔍 NeuraTech AI Telegram Bot — Kod Auditi va Tuzatishlar Reja

> Ushbu fayl loyihada aniqlangan barcha xato va kamchiliklarni, ularning muhimlik darajasini va tuzatish holatini kuzatib borish uchun yaratilgan.
> Har bir band tuzatilgach, holati `❌ Kutilmoqda` dan `✅ Tuzatildi` ga o'zgartiriladi.

---

## 📋 Status jadvali

| # | Muammo | Muhimlik | Fayl | Holati |
|---|--------|----------|------|--------|
| 1 | Post yuborish muvaffaqiyatsiz bo'lsa ham "published" deb belgilanishi | 🔴 Kritik | `scheduler.py`, `bot_handlers.py` | ✅ Tuzatildi |
| 2 | `view_admin_panel` uchun himoyasiz dublikat handler | 🔴 Kritik | `bot_handlers.py` | ✅ Tuzatildi |
| 3 | `check_is_admin()` — birinchi kelgan admin bo'lib olishi | 🟠 Yuqori | `bot_handlers.py` | ✅ Tuzatildi |
| 4 | AI Maslahatchi/qidiruvda rate-limit yo'qligi | 🟠 Yuqori | `bot_handlers.py` | ✅ Tuzatildi |
| 5 | SQL `LIKE` so'rovida wildcard belgilar ekranlanmagani | 🟡 O'rta | `database.py` | ✅ Tuzatildi |
| 6 | Eskirgan `draft` postlar hech qachon tozalanmasligi | 🟡 O'rta | `database.py`, `scheduler.py` | ✅ Tuzatildi |
| 7 | Bazada "yetim" sozlama kalitlari (schema drift) | 🟡 O'rta | `bot_data.db` | ✅ Tuzatildi |
| 8.1 | README'da `/help` reklama qilingan, lekin handler yo'q | 🟢 Kichik | `bot_handlers.py` | ✅ Tuzatildi |
| 8.2 | `requirements.txt`da versiya yuqori chegarasi yo'q | 🟢 Kichik | `requirements.txt` | ✅ Tuzatildi |
| 8.3 | Avtomatik testlar yo'qligi | 🟢 Kichik | umumiy | ✅ Tuzatildi |
| 8.4 | Izchil bo'lmagan logging (`print` vs `logging`) | 🟢 Kichik | deyarli barcha fayllar | ✅ Tuzatildi |
| 8.5 | SQLite WAL rejimi yoqilmagani | 🟢 Kichik | `database.py` | ✅ Tuzatildi |
| 8.6 | `posts` jadvalida indekslar yo'qligi | 🟢 Kichik | `database.py` | ✅ Tuzatildi |
| 8.7 | Docker bind-mount orqali butun loyihaning ochiq bo'lishi | 🟢 Kichik | `docker-compose.yml` | ✅ Tuzatildi |
| 8.8 | `config.BOT_LINK` ishlatilmaydigan o'lik kod | 🟢 Kichik | `config.py` | ✅ Tuzatildi |

---

## 🔴 1. Post yuborish muvaffaqiyatsiz bo'lsa ham "published" deb belgilanishi

**Muammo:** `scheduler.py`dagi `check_due_scheduled_posts()` va `process_news_cycle()`, shuningdek `bot_handlers.py`dagi `cb_q_publish` va `cb_queue_pub_first` funksiyalarida post holati **kanalga yuborishga urinishdan OLDIN** `published` deb belgilanadi. Agar yuborish xatolik bilan tugasa (tarmoq, huquq, HTML xatosi va h.k.), post bazada abadiy "published" bo'lib qoladi, lekin kanalga hech qachon yetib bormaydi. `is_url_exists()` statusga qaramasdan tekshirgani uchun bu yangilik RSS orqali qayta kelsa ham "dublikat" deb chetlab o'tiladi — yangilik butunlay yo'qoladi.

**Yechim:** Statusni faqat yuborish **muvaffaqiyatli** bo'lgandan keyin `published`ga o'tkazish. Xato bo'lsa, post avvalgi holatida (`scheduled`/`draft`) qoladi va keyingi siklda qayta urinilishi yoki admin alert qilinishi kerak.

---

## 🔴 2. `view_admin_panel` uchun himoyasiz dublikat handler

**Muammo:** `bot_handlers.py`da bir xil `callback_data == "view_admin_panel"` uchun ikkita handler ro'yxatdan o'tgan — biri (1053-qator atrofida) admin tekshiruvisiz, ikkinchisi (1278-qator atrofida) `check_is_admin` bilan. Aiogram'da birinchi mos kelgan handler ishlaydi, shuning uchun himoyasiz versiya doimo ishga tushadi va ikkinchisi hech qachon chaqirilmaydi — himoya "yozilgan lekin ishlamaydi".

**Yechim:** Himoyasiz dublikatni olib tashlash, faqat `check_is_admin` tekshiruvi bilan ishlaydigan versiyani qoldirish.

---

## 🟠 3. `check_is_admin()` — birinchi kelgan admin bo'lib olishi mumkin

**Muammo:** Agar `settings.admin_id` bazada mavjud bo'lmasa, funksiya uni chaqirgan **har qanday** foydalanuvchini avtomatik admin qilib tayinlaydi. Bu funksiya oddiy foydalanuvchi oqimlarida ham chaqiriladi (`cmd_user_top`, `cb_user_refresh_top`, `handle_incoming_messages`), ya'ni agar `admin_id` sozlamasi biror sababdan yo'qolsa, botga shu payt yozgan tasodifiy odam doimiy admin bo'lib qoladi.

**Yechim:** Avtomatik tayinlashni config'dagi `INITIAL_ADMIN_ID`ga bog'lab qo'yish — faqat shu ID mos kelsa avtomatik tayinlash, aks holda hech kim o'zini admin qila olmasin.

---

## 🟠 4. AI Maslahatchi va qidiruvda rate-limit yo'qligi

**Muammo:** Har qanday foydalanuvchi cheksiz miqdorda AI so'rovi yuborishi mumkin, bu esa yangiliklarni formatlash uchun mo'ljallangan umumiy Gemini kvotasini tugatib qo'yishi mumkin.

**Yechim:** Har bir foydalanuvchi uchun oddiy vaqt oralig'i asosidagi (cooldown) limitni joriy qilish.

---

## 🟡 5. SQL `LIKE` so'rovida wildcard belgilar ekranlanmagani

**Muammo:** `is_url_exists()` funksiyasida `%` va `_` belgilari SQL `LIKE`da махсус belgi hisoblanadi, lekin ekranlanmagan. Bu ba'zi URL'larni noto'g'ri "dublikat" deb hisoblashga olib kelishi mumkin.

**Yechim:** `%` va `_` belgilarini `ESCAPE` bilan qochirish.

---

## 🟡 6. Eskirgan `draft` postlar hech qachon tozalanmasligi

**Muammo:** `cleanup_old_posts()` faqat `rejected` (14 kun) va `published` (45 kun) postlarni tozalaydi, `draft` holatidagilarni tegmaydi. Haqiqiy bazada tekshirilganda 17 ta post bir necha haftadan beri `draft` holatida "osilib" turgani aniqlandi.

**Yechim:** Belgilangan muddatdan (masalan 7 kun) ortiq `draft` holatida qolgan postlarni avtomatik `rejected`ga o'tkazish.

---

## 🟡 7. Bazada "yetim" sozlama kalitlari (schema drift)

**Muammo:** `settings` jadvalida eski formatdagi (`feed_enabled_google_ai`) va yangi formatdagi (`feed_google_ai_enabled`) kalitlar bir vaqtda mavjud — bu eski kod versiyasidan qolgan iflos yozuvlar.

**Yechim:** Bazadan ishlatilmayotgan eski formatdagi kalitlarni tozalovchi bir martalik migratsiya qo'shish.

---

## 🟢 8. Kichik kamchiliklar

- **8.1** — `/help` buyrug'i handler sifatida qo'shiladi.
- **8.2** — `requirements.txt`da versiyalar yuqori chegara bilan cheklanadi (`>=x,<y`).
- **8.3** — Asosiy funksiyalar (duplicate detection, caption trimming, admin check) uchun `pytest` testlari qo'shiladi.
- **8.4** — `print()` o'rniga markaziy `logging` ishlatiladi.
- **8.5** — `PRAGMA journal_mode=WAL` yoqiladi.
- **8.6** — `posts` jadvaliga `status`, `scheduled_at`, `source_name` ustunlari uchun indekslar qo'shiladi.
- **8.7** — `docker-compose.yml`da bind-mount faqat kerakli fayllarga (`bot_data.db`, `.env`) cheklanadi.
- **8.8** — ishlatilmayotgan `BOT_LINK` o'zgaruvchisi olib tashlanadi yoki haqiqiy joyda qo'llaniladi.

---

## ✅ Bajarilgan ishlar jurnali

_(Har bir tuzatish bajarilgach shu yerga qisqacha yozib boriladi)_

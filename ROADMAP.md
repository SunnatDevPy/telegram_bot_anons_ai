# 🗺 NeuraTech AI Bot — Rivojlantirish Yo'l Xaritasi (Roadmap)

Ushbu hujjat **NeuraTech AI & IT Telegram Bot** loyihasini yanada barqaror, avtonom va professional darajaga olib chiqish bo'yicha bosqichma-bosqich rivojlantirish rejasini o'z ichiga oladi.

---

## 📌 Rejalashtirilgan Bosqichlar Ro'yxati

```mermaid
flowchart LR
    P1[1-Bosqich: Monitoring & Alert] --> P2[2-Bosqich: Kanal Inline Tugmalari]
    P2 --> P3[3-Bosqich: Baza Avto-Backup]
    P3 --> P4[4-Bosqich: Postlar Navbati (Queue)]
    P4 --> P5[5-Bosqich: Postni Qo'lda Tahrirlash]
    P5 --> P6[6-Bosqich: RSS Manbalar Boshqaruvi]
    P6 --> P7[7-Bosqich: Gemini Kalitlar Balanseri]
```

---

### 1-Bosqich: 🚨 Monitoring & Salomatlik Tizimi (Admin Alerts & Healthcheck)
> **Holati:** ✅ Bajarildi va Serverda Faol

- [x] **Admin Error Alert (Avtomatik Ogohlantirish):**
  - Gemini API kalitlarining barchasida kvota tugasa (429) yoki xatolik bersa — adminga shaxsiy xabar: *"⚠️ Diqqat: Gemini API kvotasi tugadi, xabarlar Google Translate fallback rejimiga o'tdi"*.
  - Telegram kanalga post chiqarishda huquq yoki tarmoq muammosi bo'lsa darhol xabar yuborish.
- [x] **`/status` va `/health` buyruqlari (Tezkor Diagnostika):**
  - Server parametrlari (RAM, CPU, Uptime).
  - Gemini API kalitlari holati (parallel ping, qaysi biri ishlayapti, qaysi biri kvotada).
  - Oxirgi chop etilgan post vaqti va navbatda kutilayotgan postlar soni.
  - SQLite ma'lumotlar bazasi hajmi va obunachilar statistikasi.

---

### 2-Bosqich: 📢 Kanal Postlariga Interaktiv Tugmalar (Inline Buttons)
> **Holati:** ✅ Bajarildi va Serverda Faol

- [x] Post ostiga ixcham inline tugmalar bloki:
  - 🔗 **`Asl maqola (Manba) ↗`** — yangilikning rasmiy manziliga o'tish.
  - 🤖 **`AI bilan muhokama qilish`** — botning "AI Maslahatchi" menyusiga yo'naltiruvchi deep-link (`t.me/neuratech_news_bot?start=discuss_POSTID`). Maqola bo'yicha maxsus kontekstli savol-javob rejimi.
  - 💬 **`Fikr bildirish`** — kanal muhokama guruhiga o'tish havolasi (agar guruh ulangan bo'lsa).
- [x] Tugmalarni `.env` va **Admin panel** (`🔘 Kanal tugmalari`) orqali yoqish/o'chirish imkoniyati.

---

### 3-Bosqich: 💾 Ma'lumotlar Bazasini Avto-Zaxiralash (Telegram Backup)
> **Holati:** ✅ Bajarildi va Serverda Faol

- [x] **Kunlik Avto-Backup:** Har kecha Toshkent vaqti bilan soat 03:00 da `bot_data.db` faylini adminga shaxsiy xabar qilib jo'natish.
- [x] **`/backup` buyrug'i:** Admin istalgan vaqtda bitta buyruq orqali bazaning eng so'nggi nusxasini yuklab olishi.
- [x] **Admin panelda tugma:** `💾 Baza Nusxasi (/backup)` tugmasi orqali to'g'ridan-to'g'ri Telegram chatga yuklab olish.
- [x] **ACID Online Snapshot & ZIP Siqish:** Baza ishlayotgan paytda buzilishlarsiz xavfsiz nusxa olish va hajmini 80-90% gacha siqish.

---

### 4-Bosqich: 📋 Postlar Navbatini Boshqarish (Queue Manager)
> **Maqsad:** 10 daqiqalik intervallar bilan navbatda turgan xabarlarni nazorat qilish.

- [ ] Admin panelda **`📋 Postlar Navbati`** tugmasi.
- [ ] Yaqin soatlarda chiqishi rejalashtirilgan yangiliklar ro'yxati (Sarlavha, rejalashtirilgan vaqt).
- [ ] Keraksiz yoki eskirib qolgan xabarni bitta tugma bilan navbatdan chiqarib tashlash (`🗑 O'chirish`).
- [ ] Navbatdagi birinchi xabarni kutmasdan zudlik bilan chiqarish (`🚀 Hoziroq chiqarish`).

---

### 5-Bosqich: ✏️ Moderatsiyada Postni Qo'lda Tahrirlash (Manual Edit)
> **Maqsad:** Admin qoralamani kanalga chiqarishdan oldin uning matniga o'zgartirish kirita olishi.

- [ ] Adminga kelgan qoralama ostiga **`✏️ Matnni tahrirlash`** tugmasi.
- [ ] Admin yangi matn yuborganda, bot avtomatik HTML teglarini tekshirib, yangilangan matn bilan ko'rsatishi va tasdiqlashni so'rashi.

---

### 6-Bosqich: 📡 RSS Manbalarini Dinamik Boshqarish (Feed Management)
> **Maqsad:** Vaqtincha ishlamayotgan yoki 403 xatolik berayotgan manbalarni kodga kirmasdan boshqarish.

- [ ] Admin panelda **`📡 Manbalar ro'yxati`** bo'limi.
- [ ] Har bir manba holati (Faol / Nofaol) va oxirgi muvaffaqiyatli olingan xabarlar soni.
- [ ] Istalgan manbani (masalan, Cloudflare to'siq qo'ygan manbalarni) 1 ta tugma bilan o'chirib/yoqib qo'yish.

---

### 7-Bosqich: ⚡️ Gemini Multi-Key Hovuzini Optimallashtirish (Load Balancing)
> **Maqsad:** 2-3 ta Gemini API kalitini teng va aqlli taqsimlash.

- [ ] **Round-Robin (Ketma-ket aylantirish):** Faqat bitta kalitga doimiy urilmasdan, so'rovlarni 1-kalit, 2-kalit, 3-kalit o'rtasida teng taqsimlash (yuklamani 3 barobar pasaytiradi).
- [ ] **Avto-Failover:** Agar biror kalit 429 olsa, uni 5 daqiqaga keshda "uyqu"ga qo'yib, faqat tirik kalitlarga so'rov yuborish.

---

## 🔑 .env dagi API Kalitlar Haqida Muhim Eslatma

* `.env` da hozir 3 ta kalit kiritilgan:
  - `GEMINI_API_KEY` (Asosiy)
  - `GEMINI_API_KEY_1`
  - `GEMINI_API_KEY_2`
* **Bir-birini yopib ketadimi?** **HA!** Bot birinchi kalitdan javob ololmasa (429 kvota tugasa yoki 20s timeout bo'lsa), avtomatik tarzda 2-kalitga, undan keyin 3-kalitga o'tadi.
* **Eng muhim tavsiya:** Bu 3 ta kalit haqiqatan bir-birining kvotasini to'ldirishi uchun, ular **alohida Google akkountlardan** (yoki alohida Google Cloud loyihalaridan) olingan bo'lishi kerak. Agar 3 ta kalit bitta akkountdan olingan bo'lsa, ularning 15 RPM kvotasi umumiy bo'lishi mumkin.

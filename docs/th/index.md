---
title: สร้างบอท LINE ด้วย Python ไม่ต้องสมัคร Developer — OkLine
description: สร้างบอทด้วยบัญชี LINE ส่วนตัวของคุณเองด้วย Python — QR login ส่ง/รับข้อความ E2EE Letter Sealing ไม่ต้องสมัคร developer ไม่ต้อง webhook ไม่มีลิมิต 200 ข้อความ/เดือน
---

# OkLine — เขียนบอทด้วยบัญชี LINE ส่วนตัวของคุณเอง

**OkLine** คือ Python SDK แบบ **unofficial** สำหรับทำงานอัตโนมัติกับ **บัญชี LINE
ส่วนตัวของคุณเอง** — ล็อกอินด้วย QR code ใน terminal, ส่ง/รับข้อความในนามของคุณเอง,
ถอดรหัส E2EE *Letter Sealing* ได้ทั้งช่องทางส่งและรับ **โดยไม่ต้องสมัคร developer
ไม่ต้องมี Official Account และไม่ต้องเปิด webhook**

OkLine จำลอง protocol ของ **LINE Chrome extension ตัวจริง** (`CHROMEOS` 3.7.2)
แบบครบถ้วน รวมถึง header `X-Hmac` ที่เกตเวย์บังคับใช้ (คำนวณด้วย `ltsm.wasm`
ตัวจริงของ LINE ผ่าน Node bridge เล็ก ๆ) ทำให้ request ที่ส่งออกไปเหมือนกับของ
client จริง byte-to-byte

> ✅ **ตรวจสอบความเข้ากันได้กับ LINE Chrome extension 3.7.2 แล้ว —
> กันยายน 2026 (v2.9.2)** · ผ่านเทสต์ออฟไลน์ 703 เคส และ live test กับบัญชีจริง

## ทำไมต้อง OkLine

ใครเคยค้นหาว่า "ส่งข้อความ line ด้วย python ฟรี" หรือ "เขียนบอท line อัตโนมัติ
บัญชีตัวเอง" น่าจะเจอคำตอบเดิม ๆ ว่าต้องไปสมัคร developers.line.biz สร้าง provider
สร้าง channel รับ webhook แล้วโดนลิมิต push message — ทั้งที่สิ่งที่เราอยากทำแค่
"ส่งข้อความจากบัญชีของตัวเอง" เท่านั้น OkLine เปลี่ยนโจทย์นี้ใหม่ทั้งหมด:

- **line api ไม่ต้องสมัคร developer** — ใช้บัญชี LINE ที่คุณมีอยู่แล้ว ล็อกอินด้วย
  QR ผ่าน terminal ไม่ต้องมี channel access token ไม่ต้องมี bot channel
- **ส่งในนามตัวคุณเอง** — ถึงเพื่อน กลุ่ม และแชทของตัวคุณเองจริง ๆ ไม่ใช่บอท
  แปลกหน้าที่คนต้องแอดก่อน
- **ไม่ต้องมี webhook / ngrok / เซิร์ฟเวอร์สาธารณะ** — รับข้อความแบบ polling
  (SSE stream) จากเครื่องของคุณเองได้เลย
- **ส่งได้ไม่จำกัด** — ไม่มีโควตา ~200 push messages ต่อเดือน เพราะใช้บัญชี
  ของคุณเอง
- **E2EE Letter Sealing ครบวงจร** — เข้ารหัสตอนส่ง ถอดรหัสตอนรับ ทั้งแชท 1:1
  และกลุ่ม รวมถึงไฟล์มีเดียที่ HMAC-verified (จุดที่ library ยุคก่อนอย่าง linepy
  ล้มเลิก)
- **Bot framework + CLI** — `@bot.on_message` สำหรับเขียนบอทเต็มรูปแบบ และ
  `python -m okline` สำหรับเรียกใช้จาก shell ได้ทันที

## ติดตั้ง

```bash
pip install okline
# อยากให้วาด QR ล็อกอินใน terminal เป็นภาพ? ติดตั้ง extra เพิ่ม
pip install "okline[qr]"
```

สิ่งที่ต้องมี:

| อย่าง | ทำไมต้องมี |
|-------|------------|
| **Python 3.9+** | รัน library |
| **Node.js 18+ ใน PATH** | เซ็นลายเซ็น `X-Hmac` ที่เกตเวย์ LINE บังคับ ผ่าน `ltsm.wasm` ที่ bundle มากับแพ็กเกจ (ตรวจด้วย `node --version`) |

> ถ้าติดตั้ง Node ไว้ที่ตำแหน่งไม่ปกติ กำหนด path ได้ด้วยตัวแปร environment
> `LINE_NODE=/full/path/to/node`

## เริ่มต้นใน 3 นาที

**1) ล็อกอินครั้งเดียว** — สแกน QR ด้วยแอป LINE (**ตั้งค่า › เพิ่มเพื่อน › QR
code**) แล้วยืนยัน PIN บนมือถือ:

```bash
okline login
```

เซสชัน (รวมคีย์ E2EE) จะถูกบันทึกลง `./tokens.json` ให้คำสั่งและสคริปต์อื่นใช้ซ้ำ
โดยไม่ต้องสแกนใหม่อีก

**2) ส่งข้อความแรกจาก Python:**

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")  # โหลดเซสชัน + คีย์ E2EE คืนมา
print(api.get_profile()["displayName"])
api.send_text("u0123456789abcdef0123456789abcdef", "สวัสดีจาก Python")
```

**3) บอท echo ใน 3 บรรทัด:**

```python
from okline import OkLine, Bot

api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api)
bot.on_message(lambda ctx: ctx.reply(f"คุณพิมพ์ว่า: {ctx.text}"))
bot.run()
```

## แล้ว Messaging API ทางการล่ะ

| | **OkLine** (unofficial) | **Messaging API** (ทางการ) |
|---|---|---|
| บัญชีที่ใช้ | **บัญชีส่วนตัวของคุณ** | บอทแยกต่างหาก ผู้รับต้องแอบเพื่อนก่อน |
| ต้องสมัคร developer | **ไม่ต้อง** | ต้อง (developers.line.biz + provider + channel) |
| รับข้อความ | **Polling — ไม่ต้องมี webhook, ngrok, เซิร์ฟเวอร์** | Webhook เท่านั้น (ต้องมี HTTPS สาธารณะ) |
| push message ฟรี | **ไม่จำกัด** | ราว ๆ 200 ข้อความ/เดือน |
| E2EE Letter Sealing | **ส่งและถอดรหัสได้** | ไม่มี |

## ความเสี่ยงที่ควรรู้ (พูดตรง ๆ)

OkLine เป็น unofficial SDK ไม่ได้เกี่ยวข้องกับ LINE Corporation และการทำงาน
อัตโนมัติกับบัญชีส่วนตัว **ขัดกับข้อกำหนดการใช้บริการของ LINE จริง ๆ** — ไม่มีใคร
รับประกันว่าบัญชีจะไม่ถูกจำกัดหรือระงับ (LINE เคยบังคับให้โปรเจกต์ลักษณะเดียวกัน
ลบโค้ดออกไปแล้วในปี 2014) ข้อแนะนำคือใช้กับบัญชีของตัวเองเท่านั้น อย่าส่งสแปม
ตั้ง rate ให้เหมาะสม และเก็บ token กับคีย์ E2EE ให้ดีเหมือนรหัสผ่าน
(ดูรายละเอียดที่ [SECURITY.md](https://github.com/NiceDayZc/OkLine/blob/main/SECURITY.md))

## หน้าไทยทั้งหมด

| หน้า | เนื้อหา |
|------|---------|
| [ล็อกอินด้วย QR ทีละขั้น](./qr-login.md) | สแกน → PIN → token บันทึกอัตโนมัติ, การ refresh token |
| [LINE Notify ทางเลือก](./line-notify.md) | ส่งแจ้งเตือนถึงตัวเองฟรีไม่จำกัด พร้อมสคริปต์ `notify.py` |

## เอกสารภาษาอังกฤษ (ฉบับเต็ม)

รายละเอียดเชิงลึกทั้งหมดอยู่ใน [เอกสารภาษาอังกฤษ](../index.md):

- [Getting started](../getting-started.md) — ติดตั้ง, `okline login`, interactive menu
- [Authentication](../authentication.md) — QR/e-mail login, token refresh, logout
- [Sending messages](../messaging.md) · [Media](../media.md) — ข้อความ, สติกเกอร์, รูป, ไฟล์
- [E2EE / Letter Sealing](../e2ee.md) — เข้ารหัส/ถอดรหัส 1:1 และกลุ่ม
- [Receiving events](../receiving-events.md) · [Bots](../bots.md) — รับข้อความ, bot framework
- [CLI](../cli.md) · [Cookbook](../cookbook.md) — คำสั่งทั้งหมด, ตัวอย่าง 22 สคริปต์
- [Troubleshooting](../troubleshooting.md) — ปัญหาที่พบบ่อย (FAQ)

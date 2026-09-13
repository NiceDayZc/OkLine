---
title: สอนเขียนบอท LINE ด้วย Python บัญชีส่วนตัว (QR Login) — OkLine
description: สอนล็อกอิน LINE ด้วย QR code ใน terminal ด้วย Python — สแกน ยืนยัน PIN token บันทึกอัตโนมัติใช้ซ้ำได้ ไม่ต้องใส่รหัสผ่าน ไม่ต้องสมัคร developer ด้วย OkLine
---

# สอนล็อกอินด้วย QR ใน terminal — บัญชี LINE ส่วนตัว + Python

[← หน้าหลัก (ไทย)](./index.md)

จะเขียนบอท line ด้วย python บนบัญชีส่วนตัว ปัญหาแรกที่เจอเสมอคือ "ล็อกอินยังไง"
— Messaging API ทางการไม่มีทางล็อกอินบัญชีส่วนตัวให้เลย ส่วน library เก่าอย่าง
linepy ใช้ e-mail/password ซึ่ง flow พังไปนานแล้ว

OkLine ล็อกอินแบบเดียวกับ **LINE Chrome extension ตัวจริง** คือ **QR code**:
คุณไม่ต้องใส่รหัสผ่านใด ๆ ทั้งสิ้น แค่สแกน QR ที่วาดอยู่ใน terminal ด้วยแอป LINE
บนมือถือ ยืนยัน PIN หนึ่งครั้ง แล้วระบบจะบันทึก token ไว้ใช้ซ้ำอัตโนมัติ —
ไม่ต้องสแกนใหม่ในการรันครั้งถัด ๆ ไป

บทความนี้ไล่ตั้งแต่การติดตั้งจนถึงการ refresh token ทีละขั้น ทุกตัวอย่างโค้ด
รันได้จริงกับ OkLine v2.9.2

## 0. ของที่ต้องมี

```bash
pip install okline
# วาด QR ใน terminal เป็นภาพ (แนะนำ) — ติดตั้ง extra:
pip install "okline[qr]"
```

- **Python 3.9+**
- **Node.js 18+ ใน PATH** — ทุก request ต้องเซ็น header `X-Hmac` ซึ่ง OkLine
  คำนวณด้วย `ltsm.wasm` ตัวจริงของ LINE ผ่าน Node bridge เล็ก ๆ ที่ bundle มา
  ในแพ็กเกจ ตรวจสอบด้วย:

```bash
node --version      # ต้องเป็น v18 ขึ้นไป
```

ถ้า Node อยู่ที่ตำแหน่งไม่ปกติ (เช่น ติดตั้งผ่าน nvm บนเครื่องที่ cron หาไม่เจอ)
ให้ชี้ path ตรง ๆ ด้วยตัวแปร environment:

```bash
export LINE_NODE=/full/path/to/node
```

## 1. ล็อกอินด้วยคำสั่งเดียว: `okline login`

วิธีที่ง่ายที่สุด — รันคำสั่งเดียว:

```bash
okline login
```

แล้วทำตามนี้:

1. QR code จะถูกวาดเป็น ASCII อยู่ใน terminal
2. เปิดแอป LINE บนมือถือ → **ตั้งค่า › เพิ่มเพื่อน › QR code** → สแกน QR นั้น
3. มือถือจะขึ้นหน้ายืนยัน พร้อม **PIN 6 หลัก** แสดงอยู่บน terminal — กดยืนยัน
   PIN ให้ตรงกันบนมือถือ

ถ้าสำเร็จจะเห็นประมาณนี้:

```text
Logged in as ชื่อของคุณ  (u0123...)
E2EE keys ready: yes
Session saved to tokens.json — reused by every other command.
```

เพียงเท่านี้ก็จบ — ไฟล์ `./tokens.json` เก็บทั้ง token และ **คีย์ E2EE (Letter
Sealing)** ของบัญชีคุณ คำสั่งอื่น ๆ ของ CLI และ `OkLine.from_tokens_file` จะ
โหลดไฟล์นี้ขึ้นมาใช้เองอัตโนมัติ

> ⚠️ `tokens.json` เท่ากับรหัสผ่านของบัญชี LINE คุณ อย่า commit ลง repo
> อย่าแปะลงที่ไหน และอย่าแชร์ให้ใคร (โปรเจกต์มี `.gitignore` กันไฟล์นี้อยู่แล้ว)

## 2. ล็อกอินด้วย QR จาก Python

ถ้าอยากควบคุม flow เองในโค้ด (เช่น ทำ tool ให้คนอื่นใช้) OkLine เปิด
callback ให้ทุกขั้นตอน:

```python
from okline import OkLine
from okline.qrterm import print_qr

api = OkLine()
result = api.qr_login(
    on_qr=lambda url: print_qr(url),  # วาด QR ใน terminal (ให้ผู้ใช้สแกน)
    on_pin=lambda pin: print("PIN:", pin),  # แสดง PIN รอให้กดยืนยันบนมือถือ
    wait_seconds=180,  # รอการสแกนนานแค่ไหน
)
print("ล็อกอินสำเร็จ:", bool(result.access_token))

api.save_tokens("tokens.json")  # บันทึกเซสชัน (token + คีย์ E2EE)
```

`api.qr_login(...)` จะขับเคลื่อนทั้ง flow ไปจนจบ **และ** โหลดคีย์ E2EE ของเซสชัน
นี้ด้วย ดังนั้น `save_tokens()` ที่ตามมาจะเก็บคีย์ไปในไฟล์เดียวกัน — แชทที่เปิด
Letter Sealing เลยใช้งานได้ข้ามการรันโดยไม่ต้องสแกน QR ซ้ำ

ถ้าไม่ได้ติดตั้ง `okline[qr]` ก็ยังล็อกอินได้ — แค่เปลี่ยน `on_qr` เป็นการ
พิมพ์ URL แล้วเอาไป generate QR ที่อื่น:

```python
api.qr_login(
    on_qr=lambda url: print("เอา URL นี้ไปทำ QR แล้วสแกน:", url),
    on_pin=lambda pin: print("ยืนยัน PIN บนมือถือ:", pin),
)
```

### เบื้องหลัง flow ทำงานยังไง

OkLine วิ่งตามลำดับเดียวกับ extension จริง:

```text
createSession → createQrCode → (สร้างคีย์ Curve25519 ภายใน WASM)
  → แสดง QR  =  callbackUrl + ?secret=<pubkey>&e2eeVersion=1
  → checkQrCodeVerified (คุณสแกน) → verifyCertificate / ขั้นตอน PIN
  → checkPinCodeVerified (คุณยืนยัน PIN) → qrCodeLoginV2 → ได้ token
```

จุดสำคัญคือ `?secret=…&e2eeVersion=1` ที่ต่อท้าย URL ของ QR — ขาดไปแอป LINE
จะขึ้น "เกิดข้อผิดพลาด" หลังสแกน OkLine สร้าง keypair แบบ Curve25519 ภายใน
`ltsm.wasm` ตัวจริงแล้วต่อให้เองอัตโนมัติ คุณจึงแค่ render URL ที่ `on_qr`
ส่งมาให้

## 3. ใช้เซสชันซ้ำ — ไม่ต้องสแกนอีก

หลังจากล็อกอินครั้งแรก ทุกการรันถัดไปแค่โหลดไฟล์เซสชัน:

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")  # คืนทั้ง token และคีย์ E2EE
print(api.get_profile()["displayName"])
print("E2EE พร้อม:", api.e2ee.is_ready())  # True — แชทเข้ารหัสใช้ได้เลย
```

ห้ามเขียนไฟล์ JSON เองด้วยมือ — `save_tokens` / `from_tokens_file` ใช้ชื่อ
field ที่ถูกต้องและพกคีย์ E2EE มาด้วย ซึ่ง `json.dump` ของ
`{accessToken, refreshToken}` เองทำไม่ได้

เมื่อสร้าง client ด้วย `from_tokens_file` OkLine จะ **บันทึกไฟล์คืนอัตโนมัติ**
ทุกครั้งที่ token ถูก refresh เซสชันในไฟล์จึงไม่เน่า

## 4. ข้ามขั้น PIN ในการล็อกอินครั้งถัด ๆ ไป

`qr_login` คืนค่า `certificate` — เก็บไว้แล้วส่งกลับในครั้งถัดไป เครื่องที่ LINE
รู้จักแล้วจะข้ามขั้นยืนยัน PIN (`verifyCertificate` ผ่านทันที):

```python
result = api.qr_login(on_qr=print_qr, certificate=saved_certificate)
```

`save_tokens` ก็เขียน certificate ลงไฟล์เซสชันให้แล้ว ดังนั้น `from_tokens_file`
พกมาให้อยู่แล้วเหมือนกัน

## 5. การ refresh token

**อัตโนมัติ** — ถ้ามี `refresh_token` อยู่ ตอนที่เซิร์ฟเวอร์ตอบ `401` มา OkLine
จะ refresh token แล้ว retry request เดิมให้เบา ๆ โดยคุณไม่ต้องทำอะไร และถ้า
client มาจากไฟล์เซสชัน token ใหม่จะถูกเขียนกลับลงไฟล์ด้วย

**เรียกเอง** เมื่อไรก็ได้:

```python
new_access = api.auth.refresh_access_token()
```

**ต่ออายุล่วงหน้าตามเวลา** — ทุกครั้งที่ล็อกอิน/refresh เซิร์ฟเวอร์จะบอกว่าควร
renew เมื่อไร (`durationUntilRefreshInSec`) extension จริงตั้ง timer รอ
renew — OkLine พอร์ตมาเป็น option:

```python
api = OkLine.from_tokens_file("tokens.json", auto_refresh_schedule=True)
```

เปิดไว้แล้วจะมี background timer ต่ออายุ token เงียบ ๆ ก่อนหมดอายุ, ต่ออายุ
ซ้ำไปเรื่อย ๆ ตาม schedule ใหม่, เขียนกลับไฟล์เซสชัน และต่อ SSE stream ใหม่
ด้วย token สด (เหมือน extension ทุกประการ) ปิดด้วย `api.cancel_refresh_schedule()`
(หรือ `api.close()`)

> รายละเอียด error: ถ้า refresh โดน gateway code **10202** ระบบจะ retry ตาม
> `refreshApiRetryPolicy` ของเซิร์ฟเวอร์ให้เอง แต่ถ้าโดน **10201** แปลว่า session
> ถูก kickout ต้องล็อกอินใหม่ด้วย QR อีกครั้ง

## 6. เคล็ดลับการแสดง QR

- **พื้นหลังสว่าง (โหมด light)?** สีกลับ: `print_qr(url, invert=True)`
- **Windows อ่าน QR ไม่ออก?** รัน `chcp 65001` ก่อนเพื่อเปิด UTF-8 แล้วค่อย
  ล็อกอิน
- **อยากได้ QR แบบภาพใน terminal** — ติดตั้ง `pip install "okline[qr]"`

## 7. ออกจากระบบ

ยกเลิกเซสชันฝั่งเซิร์ฟเวอร์:

```python
api.auth.logout()
```

หรือจาก CLI — `okline logout` จะยกเลิกเซสชัน **และ** ลบ `tokens.json` ในเครื่อง
ให้ด้วย นอกจากนี้ยังถอนอุปกรณ์ได้จาก **แอป LINE → ตั้งค่า › บัญชี › อุปกรณ์ที่
ล็อกอิน**

## สรุป

- ล็อกอินครั้งเดียวด้วย `okline login` (สแกน QR → ยืนยัน PIN) แล้วทุกอย่าง
  ใช้ `tokens.json` ต่อเนื่อง
- ใน Python ใช้ `api.qr_login(on_qr=print_qr, on_pin=...)` ตอนที่ต้องควบคุม
  flow เอง
- Token refresh ทั้งแบบ reactive (401) และ proactive (`auto_refresh_schedule`)
- ต้องมี Node.js 18+ (หรือชี้ `LINE_NODE`) เพราะทุก request ต้องเซ็น `X-Hmac`

ขั้นต่อไป: [ส่งข้อความ (EN)](../messaging.md) · [รับข้อความ/เขียนบอท (EN)](../bots.md)
· [LINE Notify ทางเลือก (ไทย)](./line-notify.md) · [การยืนยันตัวตนฉบับเต็ม (EN)](../authentication.md)

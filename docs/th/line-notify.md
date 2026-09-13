---
title: LINE Notify ทางเลือก Python ส่งแจ้งเตือนฟรี — OkLine
description: LINE Notify ปิดตัว 31 มี.ค. 2025 — ส่งแจ้งเตือนถึงตัวเองบน LINE ด้วย Python ฟรีไม่จำกัด ด้วย OkLine บัญชีส่วนตัว ไม่ต้องสมัคร developer ไม่ต้อง webhook
---

# LINE Notify ปิดตัวแล้ว — ส่งแจ้งเตือนถึงตัวเองด้วย Python ฟรีไม่จำกัด

[← หน้าหลัก (ไทย)](./index.md)

## เกิดอะไรขึ้นกับ LINE Notify

**LINE Notify ปิดตัวลงแล้วตั้งแต่ 31 มีนาคม 2025** — บริการที่นักพัฒนาไทยใช้
ส่งแจ้งเตือนหาตัวเองบน LINE ฟรี ๆ ผ่าน `https://notify-api.line.me/api/notify`
ด้วย pattern แบบนี้:

```python
import requests

requests.post(
    "https://notify-api.line.me/api/notify",
    headers={"Authorization": "Bearer <access token>"},
    data={"message": "เซิร์ฟเวอร์เริ่มต้นสำเร็จ"},
)
```

ตอนนี้ endpoint ข้างบนตายแล้ว สคริปต์แจ้งเตือนทั้งหลายที่เคยรันได้ (cron แจ้งเตือน
backup, สคริปต์เช็คราคาหุ้น/คริปโต, แจ้งเตือนเซิร์ฟเวอร์) เงียบหมดโดยไม่มี error
ให้เห็นชัดเจน บทความนี้แนะนำทางเลือกที่ส่งได้ **ฟรี ไม่จำกัดจำนวน** ด้วย Python
เหมือนเดิม — ผ่าน **บัญชี LINE ส่วนตัวของคุณเอง** ด้วย [OkLine](./index.md)

## ทางเลือกที่ 1: Messaging API (ทางการ)

ทางการแนะนำให้ย้ายไปใช้ **Messaging API** แต่สำหรับ use case "แจ้งเตือนตัวเอง"
มันหนักเกินจำเป็น:

- ต้อง**สมัคร developer** ที่ developers.line.biz สร้าง provider + Messaging
  API channel
- ต้อง**แอดบอทเป็นเพื่อน**ก่อน บอทจึงจะส่งถึงคุณได้ — ข้อความมาในนามบอท
  ไม่ใช่ในนามคุณเอง
- แพ็กเกจฟรีส่ง push ได้**ราว ๆ 200 ข้อความต่อเดือน** — ถ้าแจ้งเตือนถี่หน่อย
  (เช่น ทุก 5 นาที) โควตาหมดกลางเดือน
- ถ้าจะรับข้อความตอบมาด้วย ต้อง**เปิด webhook สาธารณะ** (โดเมน + HTTPS หรือ
  ngrok)

## ทางเลือกที่ 2: บัญชีส่วนตัวของคุณเอง + OkLine

OkLine เป็น unofficial Python SDK ที่ทำงานกับ **บัญชี LINE ส่วนตัวของคุณเอง**
โดยตรง — ล็อกอินด้วย QR ใน terminal แล้วส่งข้อความในนามของคุณเองได้เลย:

| | LINE Notify (ปิดตัวแล้ว) | Messaging API (ทางการ) | **OkLine** (unofficial) |
|---|---|---|---|
| สถานะ | ปิดตัว 31 มี.ค. 2025 | ใช้ได้ | ใช้ได้ (v2.9.2, live test ก.ย. 2026) |
| ค่าใช้จ่าย | ฟรี | ฟรี ~200 push/เดือน | **ฟรี ไม่จำกัด** |
| ต้องสมัคร developer | ไม่ต้อง | ต้อง | **ไม่ต้อง** |
| ต้องแอดเพื่อน/ติดตามบอทก่อน | ต้อง | ต้อง | **ไม่ต้อง — ส่งในนามตัวคุณเอง** |
| ต้องเปิด webhook | ไม่ต้อง | ต้อง (ถ้าจะรับข้อความ) | **ไม่ต้อง** |
| รูปแบบข้อความ | text + sticker + รูป | ครบทุกแบบ (flex, ฯลฯ) | text, sticker, รูป, วิดีโอ, ไฟล์ |

ข้อแลกเปลี่ยนที่ต้องรู้: OkLine เป็น unofficial SDK — การทำงานอัตโนมัติกับบัญชี
ส่วนตัว**ขัดกับข้อกำหนดการใช้บริการของ LINE** มีความเสี่ยงที่บัญชีจะถูกจำกัดหรือ
ระงับ (ดู[หน้าหลัก](./index.md)) สำหรับ use case แจ้งเตือนตัวเองปริมาณ
ปกติ ความเสี่ยงจะอยู่ระดับต่ำ แต่ไม่มีใครการันตีได้ — ตัดสินใจด้วยความเข้าใจ
ความเสี่ยงนะครับ

## เตรียมความพร้อม (ครั้งเดียว)

```bash
pip install okline
node --version        # ต้องมี Node.js 18+ ใน PATH (ใช้เซ็น X-Hmac)
okline login          # สแกน QR ด้วยแอป LINE → ยืนยัน PIN → ได้ tokens.json
```

รายละเอียดขั้นตอนล็อกอิน ดูที่ [สอนล็อกอินด้วย QR](./qr-login.md) — แค่ทำครั้ง
เดียว ครั้งต่อไปโหลด `tokens.json` ได้เลย และ token จะถูก refresh อัตโนมัติ
(เขียนกลับไฟล์ให้ด้วย) จึงตั้ง cron ทิ้งไว้ได้ยาว ๆ

## สคริปต์ notify.py (พร้อมใช้)

เซฟเป็น `notify.py`:

```python
#!/usr/bin/env python3
"""notify.py — ส่งแจ้งเตือนถึงตัวเองบน LINE ฟรี ไม่จำกัด (ทดแทน LINE Notify)

การใช้งาน:
    python notify.py "backup เสร็จแล้ว"
    python notify.py --to mom "ฝากซื้อกาแฟด้วย"
    python notify.py --tokens-file /path/tokens.json "เซิร์ฟเวอร์รีสตาร์ท"
"""

from __future__ import annotations

import argparse

from okline import OkLine


def main() -> None:
    p = argparse.ArgumentParser(description="ส่งแจ้งเตือน LINE ถึงตัวเอง (ทดแทน LINE Notify)")
    p.add_argument("message", help="ข้อความที่จะส่ง")
    p.add_argument(
        "--tokens-file",
        default="tokens.json",
        help="ไฟล์เซสชัน (ค่าเริ่มต้น tokens.json — สร้างด้วย `okline login`)",
    )
    p.add_argument(
        "--to",
        default=None,
        help="ผู้รับ: mid ของเพื่อน/กลุ่ม (u... / c...) — เว้นว่าง = ส่งถึงตัวเอง",
    )
    args = p.parse_args()

    # โหลดเซสชัน (token + คีย์ E2EE) — ถ้า token หมดอายุจะถูก refresh
    # และเขียนกลับไฟล์ให้อัตโนมัติ
    api = OkLine.from_tokens_file(args.tokens_file)
    try:
        # ถ้าไม่ระบุผู้รับ ให้ส่งถึงตัวเอง — ข้อความจะไปอยู่ในแชทส่วนตัว
        # ของตัวคุณเอง (ห้องเดียวกับที่ใช้บันทึกโน้ตถึงตัวเองในแอป)
        to = args.to or api.get_profile()["mid"]

        res = api.send_text(to, args.message)
        msg_id = res.get("id") if isinstance(res, dict) else res
        print(f"ส่งแล้ว (message id: {msg_id})")
    finally:
        api.close()  # ปิด Node bridge ที่ใช้เซ็น X-Hmac


if __name__ == "__main__":
    main()
```

ทดลอง:

```bash
python notify.py "ทดสอบแจ้งเตือนจาก Python"
```

ไม่กี่วินาทีข้อความจะเด้งเข้ามือถือของคุณเอง — ในนามของคุณเอง ไม่ใช่บอท

> ถ้าแชทเป้าหมายเปิด Letter Sealing (E2EE) แล้ว `send_text` ไม่ผ่าน ให้เปลี่ยน
> เป็น `api.send_encrypted_text(to, args.message)` (รองรับแชท 1:1) — ไฟล์เซสชัน
> จาก `okline login` พกคีย์ E2EE มาให้แล้ว

## ตัวอย่างการใช้งานจริง

**แจ้งเตือนจาก cron** (สคริปต์สำรองข้อมูลทุกวัน 02:00 แล้วส่งผลลัพธ์):

```bash
0 2 * * * cd /home/me/tools && /usr/bin/python3 backup.py && \
  /usr/bin/python3 notify.py "backup สำเร็จ $(date +\%d/\%m/\%Y)" || \
  /usr/bin/python3 notify.py "backup ล้มเหลว!"
```

**แจ้งเตือนในสคริปต์ Python** — ทดแทน `requests.post` เดิมแค่สองบรรทัด:

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
api.send_text(api.get_profile()["mid"], "เซิร์ฟเวอร์เริ่มต้นสำเร็จ")
```

เดิม (LINE Notify) vs ใหม่ (OkLine):

```python
# เดิม — ใช้การไม่ได้แล้วตั้งแต่ 31 มี.ค. 2025
import requests

requests.post(
    "https://notify-api.line.me/api/notify",
    headers={"Authorization": "Bearer <token>"},
    data={"message": "เซิร์ฟเวอร์เริ่มต้นสำเร็จ"},
)

# ใหม่ — OkLine กับบัญชีส่วนตัวของคุณเอง
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
api.send_text(api.get_profile()["mid"], "เซิร์ฟเวอร์เริ่มต้นสำเร็จ")
```

**แจ้งเตือนด่วนจาก shell** — ถ้าล็อกอินไว้แล้ว ไม่ต้องเขียน Python เลยก็ได้:

```bash
okline send u0123456789abcdef0123456789abcdef "backup เสร็จแล้ว"
```

## ข้อควรระวัง

- **ToS** — บัญชีส่วนตัวทำงานอัตโนมัติขัดข้อกำหนด LINE จริง ใช้กับบัญชีตัวเอง
  อย่าส่งสแปม และตั้ง rate ให้สมเหตุสมผล (แจ้งเตือนสิบกว่าครั้งต่อวันไม่มีปัญหา
  ระดับพันครั้งต่อชั่วโมงคนละเรื่อง)
- **เก็บ `tokens.json` ให้ดี** — ไฟล์นี้เท่ากับรหัสผ่านบัญชีคุณ อย่า commit ลง repo
- **หลายเครื่อง / หลายสคริปต์** — แชร์ไฟล์เซสชันเดียวกันได้ แต่ให้ระวังการ
  refresh พร้อมกันจน token เก่าใช้ไม่ได้ แนะนำให้แยกไฟล์ต่อเครื่อง
- **ถ้าโดน kickout** (gateway code 10201) หรือเปลี่ยนรหัสผ่าน/อุปกรณ์ — แค่
  `okline login` ใหม่อีกครั้ง

## สรุป

LINE Notify จากไปแล้ว แต่ use case "แจ้งเตือนตัวเองบน LINE ด้วย Python ฟรี
ไม่จำกัด" ยังอยู่ — Messaging API ก็ทำได้แต่ต้องแลกด้วยการสมัคร developer,
แอดบอท, และโควตา ~200 push/เดือน ส่วน OkLine ใช้บัญชีส่วนตัวของคุณเอง
ล็อกอินครั้งเดียวด้วย QR แล้วส่งได้ไม่จำกัด ทั้งถึงตัวเอง เพื่อน และกลุ่ม
(แลกด้วยความเสี่ยง ToS ที่ต้องยอมรับ)

ขั้นต่อไป: [หน้าหลัก (ไทย)](./index.md) · [สอนล็อกอินด้วย QR (ไทย)](./qr-login.md)
· [Cookbook ตัวอย่าง 22 สคริปต์ (EN)](../cookbook.md)

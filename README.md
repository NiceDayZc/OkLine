# OkLine

[![PyPI](https://img.shields.io/pypi/v/okline.svg)](https://pypi.org/project/okline/)
[![Docs](https://img.shields.io/badge/docs-nicedayzc.github.io%2FOkLine-blue.svg)](https://nicedayzc.github.io/OkLine/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-703%20passing-brightgreen.svg)](tests/)
[![Endpoints](https://img.shields.io/badge/endpoints-77-blue.svg)](docs/ENDPOINTS.md)

**✅ Verified against LINE Chrome extension 3.7.2 — September 2026 (v2.9.2)**

OkLine is an **unofficial Python SDK for LINE Messenger** that lets you automate
**your own personal account**: log in by scanning a **QR code** in your terminal,
send and receive messages and media as yourself, and decrypt **E2EE Letter
Sealing** — with no developer account, no bot channel, and no webhook. It
faithfully reproduces the API of the official **LINE Chrome extension**
(`CHROMEOS` 3.7.2), including the mandatory `X-Hmac` signature computed by
LINE's real `ltsm.wasm`, so requests are byte-for-byte what the real client
sends.

Log in with a QR code — no developer account, no channel access token:

```python
from okline import OkLine
from okline.qrterm import print_qr  # ASCII QR in the terminal (pip install qrcode)

api = OkLine()
api.qr_login(on_qr=print_qr)  # scan the QR with the LINE app, confirm the PIN
api.save_tokens("session.json")  # session (incl. E2EE keys) — reused forever
api.send_text("c…group…mid", "hi from python")
```

Or pick up an existing session by token:

```python
from okline import OkLine

api = OkLine(access_token="…", refresh_token="…")
print(api.get_profile())
api.send_text("u0123456789abcdef0123456789abcdef", "hello from python")
```

## ภาษาไทย

OkLine คือ Python SDK (ไม่เป็นทางการ) สำหรับทำ**บอท LINE บัญชีส่วนตัว** —
ล็อกอินด้วย QR code ในเทอร์มินัล ส่ง-รับข้อความและไฟล์ในนามตัวคุณเอง
และถอดรหัส E2EE Letter Sealing ได้ **โดยไม่ต้องสมัคร developer account
ไม่ต้องมี channel หรือ webhook** จึงเป็น line notify ทางเลือกสำหรับ
ส่งข้อความ LINE ด้วย Python ฟรีแบบไม่จำกัด — อ่านคู่มือฉบับภาษาไทยได้ที่
[เอกสารภาษาไทย](https://nicedayzc.github.io/OkLine/th/)

## Features

- **All 77 endpoints** — typed methods, or call any of them generically.
- **QR & e‑mail login** — QR rendered as ASCII right in your terminal.
- **`X-Hmac` signing** — handled automatically via the bundled WASM module.
- **Bot framework** — `@bot.on_message`, typed models, session persistence.
- **E2EE Letter Sealing** — send and decrypt sealed messages and media (1:1 and group).
- **Response recording** — capture, redact and export every request/response.
- **CLI** — `python -m okline …` to call any endpoint from the shell.

## Install

```bash
pip install okline
```

Or as an isolated CLI tool with [uv](https://docs.astral.sh/uv/) — the `qr`
extra bundles the terminal QR renderer:

```bash
uv tool install "okline[qr]"
```

The bundled `ltsm.wasm` (for `X-Hmac` signing) ships inside the wheel, so that's
all you need from Python. With a plain `pip` install, optionally
`pip install qrcode` to render the QR-login code in your terminal.

**Prerequisites**

- **Python 3.9+**
- **Node.js 18+** on your `PATH` — required to compute the mandatory `X-Hmac`
  request signature (the real `ltsm.wasm` runs through a tiny Node bridge;
  [details](docs/architecture.md)). Check with `node --version`.

Verify it works:

```bash
python -m okline version
```

<details>
<summary>Install from source instead</summary>

```bash
git clone https://github.com/NiceDayZc/okline.git
cd okline
pip install -e .          # editable install of the okline package + deps
```
</details>

**First login** (do this once; the session is then reusable):

```bash
okline login                 # scan the QR with the LINE app — saves tokens.json
```

Then just run `okline` for an **interactive, menu-driven UI** (pick actions by
number), or use any of the ~30 subcommands directly:

```bash
okline                       # interactive menu (soft colours, no setup)
okline whoami
okline send <mid> "hello"
okline contacts --search soda
okline chatlog <chat-mid>    # reads and decrypts recent messages
okline -h                    # full command list
```

## Quick start

```python
from okline import OkLine, Bot

# log in once, reuse the session forever
api = OkLine()
api.auth.qr_login(on_qr=print)  # scan the QR with your phone
api.save_tokens("session.json")

# next time
api = OkLine.from_tokens_file("session.json")
api.send_text("c…group…mid", "hi from python")

# a 3-line echo bot
bot = Bot(api)
bot.on_message(lambda ctx: ctx.reply(f"you said: {ctx.text}"))
bot.run()
```

From the shell — `okline login` once, then everything reuses the session:

```bash
okline login                 # scan the QR; saves tokens.json
okline send <mid> "hello"
okline call Talk.TalkService.getProfile "[2]"
```

## OkLine vs line-bot-sdk (official) vs linepy

| | **OkLine** (unofficial) | **line-bot-sdk** (official) | **linepy** (unofficial, abandoned) |
|---|---|---|---|
| Account type | **Your personal LINE account** | A separate bot/official account users must befriend | Personal account |
| Login | **QR code (ASCII in terminal) or e-mail** | Channel access token (requires developer registration, provider, channel) | E-mail/password (flows broken; QR undocumented) |
| Developer registration | **None** | Required (developers.line.biz) | None |
| Send as yourself to your contacts | **Yes** | No — bot sends to followers only, by opaque userId | Partially (protocol rot) |
| Receive messages | **Polling — no public webhook, no ngrok, no server** | Webhook only (public HTTPS endpoint required) | Polling (broken) |
| E2EE Letter Sealing | **Yes — send and decrypt, incl. HMAC-verified media** | No (server-side bots read plaintext) | No (fails with TalkException code 82) |
| Push limits | **Unlimited (your own account)** | ~200 free push msgs/month | n/a |
| Protocol fidelity | **Byte-for-byte LINE Chrome extension (CHROMEOS 3.7.2) incl. X-Hmac via real ltsm.wasm** | Official REST API | Stale thrift defs (akad-era) |
| Bot framework & CLI | **Yes (`@bot.on_message`, `python -m okline`)** | SDK only (build your own) | Selfbot scripts |
| Maintenance | **Active, live-tested (v2.9.2, Sept 2026)** | Active (official) | Last release Jan 2018 / ~2015 |
| ToS risk | Yes — personal-account automation violates LINE ToS | No | Yes |

More detail on the [comparison page](https://nicedayzc.github.io/OkLine/comparison/).

## FAQ

**How do I send a LINE message from Python without a developer account?**
`pip install okline`, log in once by scanning a QR code in your terminal, then
`api.send_text(mid, "hello")`. No provider, no channel, no channel access token,
no webhook — OkLine talks to LINE as your own logged-in account.
[QR-login guide](https://nicedayzc.github.io/OkLine/qr-login/)

**How do I log in to LINE from Python with a QR code?**
`api.qr_login(on_qr=print_qr)` renders the login QR as ASCII right in your
terminal; scan it with the LINE app, confirm the PIN, and the session (including
your E2EE keys) is saved to JSON and auto-refreshed on later runs. Or just run
`okline login`. [QR-login guide](https://nicedayzc.github.io/OkLine/qr-login/)

**How can my bot send messages as ME, to my own contacts and groups?**
OkLine addresses your actual contact list and group chats — search contacts,
list groups, and send to any chat you can see in the app. The official Messaging
API can only push to opaque user IDs of users who befriended the bot, which is
where the `'to' field is invalid` errors come from.

**How do I read my own LINE chats from Python (without a public webhook or ngrok)?**
OkLine receives events the same way the real client does — as the logged-in
account — so there is no public HTTPS endpoint, no ngrok, and no server to run.
`okline chatlog <chat-mid>` reads and decrypts recent history in one command.

**LINE Notify shut down (March 31, 2025) — what's the free, unlimited replacement in Python?**
Send to your own account (or any of your chats) with OkLine — free and
unlimited, because it's your own account rather than a service with a monthly
push cap. The [LINE Notify replacement
guide](https://nicedayzc.github.io/OkLine/line-notify-replacement/) shows a
drop-in replacement for the old `requests.post`-to-notify-bot pattern.

**How do I decrypt letter-sealed (E2EE) LINE messages and media in Python?**
OkLine implements Letter Sealing end to end: E2EE keys are captured at login,
`api.decrypt_message(msg)` opens sealed text, sealed media is decrypted with
HMAC verification, and `api.send_encrypted_text(to, text)` sends sealed — for
1:1 and group chats. [E2EE guide](https://nicedayzc.github.io/OkLine/e2ee/)

**What replaces linepy / carpedm20-LINE in 2026?**
OkLine: the same idea — automate your own personal account over LINE's Thrift
API — but maintained and live-tested against the current Chrome-extension
protocol, including the `X-Hmac` signature and Letter Sealing that broke the
old libraries. See the table above and the
[comparison page](https://nicedayzc.github.io/OkLine/comparison/).

**Will my LINE account get banned for using an unofficial API?**
Honestly: it's a real risk. Automating a personal account violates LINE's Terms
of Service, and LINE Corporation forced code removal from the earlier carpedm20
project in 2014. OkLine cannot remove that risk — use only your own account,
keep volumes human-like, and treat tokens as secrets. See the candid discussion
on the [FAQ page](https://nicedayzc.github.io/OkLine/faq/).

## Documentation

Full docs at **[nicedayzc.github.io/OkLine](https://nicedayzc.github.io/OkLine/)**
(source in [`docs/`](docs/index.md)):

| Guide | |
|-------|---|
| [Getting started](docs/getting-started.md) | install, `okline login`, the menu, first Python call |
| [Authentication](docs/authentication.md) | token reuse, e‑mail (RSA), QR login, refresh, logout |
| [Sending messages](docs/messaging.md) | text, stickers, location, contacts, flex, reactions |
| [Media](docs/media.md) · [E2EE](docs/e2ee.md) | send images/files · encrypt & decrypt (1:1 + group) |
| [Receiving events](docs/receiving-events.md) · [Bots](docs/bots.md) | the SSE stream · the bot framework |
| [Recording](docs/recording.md) | paste / export every response |
| [CLI](docs/cli.md) · [Cookbook](docs/cookbook.md) | every `okline` command · copy‑paste recipes |
| [Architecture](docs/architecture.md) | the protocol, `X-Hmac`, module map |
| [Endpoint reference](docs/ENDPOINTS.md) | all 77 endpoints with their fields |
| [Troubleshooting](docs/troubleshooting.md) · [Contributing](docs/contributing.md) | |

## Disclaimer

OkLine is **unofficial** and not affiliated with LINE Corporation. Use it only
with your own account and in compliance with LINE's
[Terms of Service](https://terms.line.me/line_terms). Treat tokens like
passwords — see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)

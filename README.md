# OkLine

**A high-level Python client / SDK for the LINE Chrome messaging API.**

[![PyPI](https://img.shields.io/pypi/v/okline.svg)](https://pypi.org/project/okline/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-428%20passing-brightgreen.svg)](tests/)
[![Endpoints](https://img.shields.io/badge/endpoints-77-blue.svg)](docs/ENDPOINTS.md)

OkLine reproduces the API of the official **LINE Chrome extension** (`CHROMEOS`
3.7.2) — send and receive messages, log in by **QR** or **e‑mail**, and automate
your own account from Python. The protocol is reproduced faithfully, including
the mandatory `X-Hmac` signature (computed by LINE's real `ltsm.wasm`), so
requests are byte‑for‑byte what the real client sends.

```python
from okline import OkLine

api = OkLine(access_token="…", refresh_token="…")
print(api.get_profile())
api.send_text("u0123456789abcdef0123456789abcdef", "hello from python")
```

## Features

- **All 77 endpoints** — typed methods, or call any of them generically.
- **QR & e‑mail login** — QR rendered as ASCII right in your terminal.
- **`X-Hmac` signing** — handled automatically via the bundled WASM module.
- **Bot framework** — `@bot.on_message`, typed models, session persistence.
- **Response recording** — capture, redact and export every request/response.
- **CLI** — `python -m okline …` to call any endpoint from the shell.

## Install

```bash
pip install okline
```

The bundled `ltsm.wasm` (for `X-Hmac` signing) ships inside the wheel, so that's
all you need from Python. Optionally `pip install qrcode` to render the QR-login
code in your terminal.

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
python -m okline qr-login --save tokens.json     # scan the QR with the LINE app
python -m okline profile --tokens-file tokens.json
```

## Quick start

```python
from okline import OkLine, Bot

# log in once, reuse the session forever
api = OkLine()
api.auth.qr_login(on_qr=print)        # scan the QR with your phone
api.save_tokens("session.json")

# next time
api = OkLine.from_tokens_file("session.json")
api.send_text("c…group…mid", "hi from python")

# a 3-line echo bot
bot = Bot(api)
bot.on_message(lambda ctx: ctx.reply(f"you said: {ctx.text}"))
bot.run()
```

From the shell:

```bash
python -m okline qr-login --save tokens.json
python -m okline call Talk.TalkService.getProfile "[2]" --tokens-file tokens.json
python -m okline send <mid> "hello" --tokens-file tokens.json
```

## Documentation

| Guide | |
|-------|---|
| [Getting started](docs/getting-started.md) | install, first call, the `OkLine` object |
| [Authentication](docs/authentication.md) | token reuse, e‑mail (RSA), QR login, refresh |
| [Sending messages](docs/messaging.md) | text, stickers, location, media, reactions |
| [Receiving events](docs/receiving-events.md) | the SSE stream and a simple bot |
| [Building bots & helpers](docs/bots.md) | bot framework, typed models, sessions |
| [Recording](docs/recording.md) | paste / export every response |
| [CLI](docs/cli.md) | every `python -m okline` command |
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

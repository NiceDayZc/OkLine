# OkLine documentation

**OkLine** is a complete, high-level Python client and CLI for the API used by the
official **LINE Chrome extension** (`CHROMEOS` 3.7.2). It reproduces the real
protocol faithfully — gateway, headers, payloads, the mandatory `X-Hmac`
signature (computed by LINE's own `ltsm.wasm`), QR + e-mail login, E2EE *Letter
Sealing*, the SSE event stream and OBS media — wrapped in a friendly Python API,
a typed method per endpoint, a polished interactive menu, and a ~30-command CLI.
Every request and response is recorded so you can inspect or paste it.

> **Unofficial.** Not affiliated with LINE Corporation. Use it only with your
> own account and in compliance with LINE's Terms of Service. Treat tokens and
> E2EE keys like passwords.

## Features

- **All 77 Thrift endpoints**, each as a typed method — or call any of them generically with `api.call(...)`.
- **Mandatory `X-Hmac` request signing**, handled automatically (via a tiny Node bridge running LINE's real `ltsm.wasm`).
- **QR login** (scan with the LINE app, confirm a PIN) and **e-mail (RSA) login**, both with token auto-refresh.
- **Media send** — images, video, audio and files (`send_image` / `send_video` / `send_audio` / `send_file`) over OBS.
- **E2EE Letter Sealing** for 1:1 **and** groups — encrypt *and* decrypt (V1 + V2 wire formats), with keys that **persist across sessions** so you never re-scan a QR.
- **Bot framework** — `@bot.on_message`, `@bot.command`, `@bot.on(OpType)`; messages are auto-decrypted for you.
- **Full recording** — `api.last`, `api.history`, `api.dump()`, and export to text / JSON / HAR (secrets redacted by default).
- **Interactive menu** (just run `okline`) plus a complete **CLI** of ~30 commands.
- **22 runnable examples** in the [cookbook](./cookbook.md) to copy-paste from.

## Start here

Install (needs **Python 3.9+** and **Node.js 18+** on your PATH):

```bash
pip install okline
# optional: render the login QR inline in your terminal
pip install "okline[qr]"
```

Log in once — scan the QR with the LINE app and confirm the PIN. This saves a
session (including your E2EE keychain) to `./tokens.json`, which every other
command reuses automatically:

```bash
okline login
```

Now use it from Python:

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")   # restores tokens + E2EE keys
print(api.get_profile()["displayName"])
api.send_text("u0123456789abcdef0123456789abcdef", "hello from python")
```

New to OkLine? Start with **[Getting started](./getting-started.md)**.

## Table of contents

| Page | What's inside |
|------|---------------|
| [Getting started](./getting-started.md) | Install, `okline login`, the interactive menu, your first Python call |
| [Authentication](./authentication.md) | QR login, e-mail (RSA) login, token reuse & refresh, logout |
| [Sending messages](./messaging.md) | Text, replies, stickers, location, contacts, flex, reactions |
| [Media](./media.md) | Send images, video, audio and files via OBS |
| [E2EE / Letter Sealing](./e2ee.md) | Encrypt & decrypt 1:1 and group messages, cross-session keys |
| [Receiving events](./receiving-events.md) | The SSE stream, operations, a simple echo bot |
| [Building bots & helpers](./bots.md) | Bot framework, typed models, session, rate limiting |
| [Recording](./recording.md) | Capture, paste, redact and export every request/response |
| [CLI](./cli.md) | The `okline` interactive menu and ~30 subcommands |
| [Cookbook](./cookbook.md) | 22 ready-to-run examples |
| [Architecture](./architecture.md) | The protocol, `X-Hmac`, the module map |
| [Troubleshooting](./troubleshooting.md) | Common errors and fixes (FAQ) |
| [Contributing](./contributing.md) | Dev setup, tests, adding endpoints |
| [Endpoint reference](./ENDPOINTS.md) | Every endpoint with its argument fields |

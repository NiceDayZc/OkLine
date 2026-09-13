---
title: "LINE Python FAQ — OkLine, Unofficial LINE Python SDK"
description: "Answers for LINE automation in Python: send without a developer account, QR login, E2EE decryption, linepy replacement, and honest ban-risk facts."
---

# Frequently asked questions

[← docs home](./index.md)

> ✅ Verified against LINE Chrome extension 3.7.2 — September 2026 (v2.9.2)

The questions below are the ones OkLine exists to answer — the ones where the
usual answer ("just use line-bot-sdk") doesn't fit. Each links to the guide page
with the full story.

## 1. How do I send a LINE message from Python without a developer account?

`pip install okline`, log in once by scanning a QR code in your terminal, then
call `api.send_text(...)` — as yourself, from Python:

```bash
pip install okline
okline login      # scan the QR with the LINE app, confirm the PIN
```

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")  # created by `okline login`
api.send_text("u0123456789abcdef0123456789abcdef", "hello from python")
```

There is no provider to create at developers.line.biz, no channel, no channel
access token and no webhook. OkLine talks to the same API the official LINE
Chrome extension uses (`CHROMEOS` 3.7.2), so LINE sees your own logged-in
account — not a bot. Requirements: Python 3.9+ and Node.js 18+ on your PATH
(Node computes the mandatory `X-Hmac` request signature via LINE's real
`ltsm.wasm`).

Full walkthrough: [Getting started](./getting-started.md) ·
[QR login](./qr-login.md).

## 2. How do I log in to LINE from Python with a QR code?

OkLine renders the login QR as ASCII blocks right in your terminal:

```python
from okline import OkLine
from okline.qrterm import print_qr

api = OkLine()
result = api.qr_login(
    on_qr=print_qr,  # draw the QR — scan it
    on_pin=lambda pin: print("PIN:", pin),  # confirm on your phone
    wait_seconds=180,
)
print("logged in:", bool(result.access_token))
api.save_tokens("tokens.json")  # reuse forever, E2EE keys included
```

Under the hood this drives the extension's real flow — `createSession` →
`createQrCode` (with the mandatory `?secret=<curve25519 pubkey>&e2eeVersion=1`
on the QR URL) → `checkQrCodeVerified` → PIN confirmation → `qrCodeLoginV2` —
and the returned session (tokens + certificate + E2EE keychain) can be saved
and reloaded so you only ever scan once. The second login can even skip the
PIN by reusing the certificate.

The full guide, with token persistence and troubleshooting:
[LINE QR Code Login from Python](./qr-login.md).

## 3. How can my bot send messages as ME, to my own contacts and groups?

The official Messaging API can't do this: a bot/official account sends **as the
bot**, only to users who added it as a friend, and it addresses them by opaque
userIds that you can only learn through a webhook. People hit this as the
famous `'to' field is invalid` error when they try to paste a friend's mid into
`line-bot-sdk`.

OkLine addresses the account you actually use. Your contacts, your groups, your
display name on the message:

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
me = api.get_profile()
print(me["displayName"])

ids = api.get_all_contact_ids()  # your real contact list
groups = api.get_all_chat_mids()  # your groups and rooms
api.send_text("c0123456789abcdef...", "see you at 7")  # your group, from you
```

E2EE chats work too — see [E2EE / Letter Sealing](./e2ee.md).

## 4. How do I read my own LINE chats from Python (no webhook, no ngrok)?

With the official API, inbound messages are webhook-only — you need a public
HTTPS endpoint, which means a server or a tunnel like ngrok just to see your own
messages. OkLine doesn't: it **polls** the SSE operation stream, exactly like
the Chrome extension does, so a plain script on your laptop receives messages:

```python
from okline import OkLine, Bot

api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api)


@bot.on_message
def echo(ctx):
    print(ctx.sender, "said", ctx.text)  # auto-decrypted if E2EE
    ctx.reply("you said: " + ctx.text)


bot.run()  # blocks; Ctrl-C to stop
```

For history instead of live messages: `api.get_recent_messages(chat_mid, 50)`
returns the last messages of any chat (decrypted when your E2EE keys are
loaded). See [Receiving events](./receiving-events.md) ·
[Building bots](./bots.md).

## 5. LINE Notify shut down (March 31, 2025) — what's the free, unlimited replacement in Python?

LINE Notify's API was discontinued on **March 31, 2025**; every notify token
stopped working overnight, and the ~1,500 projects built on it (server alerts,
cron jobs, home automation) went silent. The official suggested replacement —
the Messaging API — is a poor fit for *personal* notifications: you must create
a bot, friend it with your own account, live with the free tier's push quota
(around 200 messages/month at the time of writing — check current limits), and
your "notification" arrives from a bot, not from you.

OkLine is the drop-in replacement: message **yourself**, unlimited, free, as
your own account:

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
api.send_text(api.get_profile()["mid"], "backup finished")  # to yourself
```

There is a full migration guide with a reusable `notify.py` script:
[LINE Notify replacement](./line-notify-replacement.md).

## 6. How do I decrypt letter-sealed (E2EE) LINE messages and media in Python?

Letter Sealing is LINE's end-to-end encryption: the server only ever sees
ciphertext. This is what killed the old unofficial libraries — when a chat
requires E2EE, the server rejects a plain send with TalkException **code 82**
("can not send using plain mode"), and there was no working Python
implementation to handle it.

OkLine implements the real protocol, live-verified for **encrypt and decrypt,
1:1 and groups, V1 and V2 wire formats, including sealed media**:

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")  # session carries the E2EE keys
print(api.e2ee.is_ready())  # True

api.send_encrypted_text(to, "this is sealed")  # encrypt
plain = api.decrypt_message(received)  # decrypt (safe on plain too)
```

You rarely even need to think about it: if a chat requires sealing, a plain
`api.send_text(to, ...)` is auto-sealed and retried, and the
[Bot framework](./bots.md) decrypts incoming messages before your handler sees
them. Full details: [E2EE / Letter Sealing](./e2ee.md).

## 7. What replaces linepy / carpedm20-LINE in 2026?

linepy's last release was January 2018; carpedm20's LINE stopped around 2015.
Both rotted the same way: the protocol moved (new gateway, mandatory `X-Hmac`
signing, E2EE everywhere) and the stale thrift definitions could not follow —
broken logins, broken QR, and code 82 on any sealed chat.

OkLine is the maintained successor for the same use case — automating your own
account:

- faithful reproduction of the LINE Chrome extension API (`CHROMEOS` 3.7.2),
  including `X-Hmac` via LINE's real `ltsm.wasm`,
- QR and e-mail login, token refresh, session persistence,
- E2EE send and decrypt (see #6),
- a bot framework (`@bot.on_message`), a ~30-command CLI, 703 offline tests,
  live-tested September 2026.

```bash
pip install okline     # you used to `pip install line` (carpedm20) / `linepy`
```

There is a call-by-call migration table:
[Migrate from linepy to OkLine](./migrate-from-linepy.md).

## 8. Will my LINE account get banned for using an unofficial API?

Honestly: **it's a real risk, and you should decide with open eyes.** Automating
a personal account is a violation of LINE's Terms of Service — this is not a
grey area. LINE Corporation has enforced this before: in 2014 it forced the
carpedm20/LINE project to remove its code. OkLine is unofficial and not
affiliated with LINE; by using it you accept that LINE may at any time suspend,
restrict or terminate an account that uses it, and that a protocol change on
their side can break the SDK. Use an account whose loss you can afford; do not
use OkLine on an account that matters to your business.

What OkLine does to stay on the right side of the line:

- **Your own account only.** The login flow is QR/e-mail for an account you
  control — there is no mechanism here to operate other people's accounts.
- **No credential harvesting.** OkLine never collects, phones home or embeds
  credentials; tokens stay in your local `tokens.json` (see
  [SECURITY.md](https://github.com/NiceDayZc/OkLine/blob/main/SECURITY.md)).
- **Rate discipline.** Send like a human. OkLine ships a token bucket
  (`api.transport.rate_limiter = RateLimiter(rate=5, per=1.0)`) and the
  `broadcast` CLI command is rate-limited and asks before starting. If you hit
  `EXCESSIVE_ACCESS`(4) / `ABUSE_BLOCK`(35) / `CONGESTION_CONTROL`(58), you are
  sending too fast — slow down.
- **Session longevity.** One login, a persisted session, proactive token
  renewal — fewer logins look less like churn than repeated scripted logins.

What OkLine cannot do is make the risk zero. If you need a guaranteed-safe,
ToS-clean integration — a business broadcasting to customers, for example — use
the official [line-bot-sdk](https://github.com/line/line-bot-sdk-python); that
is what it is for (see [Comparison](./comparison.md) for the honest split).
Read the [disclaimer in the README](https://github.com/NiceDayZc/OkLine/blob/main/README.md) before you start.

## 9. Can Claude / an AI agent read and reply to my LINE chats?

Yes — OkLine's bot framework auto-decrypts incoming messages and hands them to
your code, so wiring an LLM into the loop is a few lines:

```python
import anthropic
from okline import OkLine, Bot

llm = anthropic.Anthropic()
api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api)


@bot.on_message
def reply(ctx):
    if not ctx.text:
        return
    resp = llm.messages.create(
        model="claude-opus-5",
        max_tokens=1024,
        system="You are a helpful assistant replying on LINE. Be brief.",
        messages=[{"role": "user", "content": ctx.text}],
    )
    answer = "".join(b.text for b in resp.content if b.type == "text")
    if answer:
        ctx.reply(answer)


bot.run()
```

There is also a minimal MCP-server sketch (expose `read recent chats` and
`send reply` as tools to Claude or any MCP client) on the guide page:
[Run an AI agent / MCP server on your own LINE account](./ai-agent-mcp.md).

---

**Next:** [Comparison](./comparison.md) · [QR login](./qr-login.md) ·
[LINE Notify replacement](./line-notify-replacement.md) ·
[Migrate from linepy](./migrate-from-linepy.md) ·
[AI agents / MCP](./ai-agent-mcp.md) · [Troubleshooting](./troubleshooting.md)

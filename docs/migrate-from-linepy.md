---
title: "Migrate from linepy to OkLine — Unofficial LINE Python SDK"
description: "linepy is dead (2018). Migrate from linepy / carpedm20-LINE to OkLine, the maintained unofficial LINE Python SDK — QR login, E2EE, working protocol."
---

# Migrate from linepy to OkLine

[← docs home](./index.md)

> ✅ Verified against LINE Chrome extension 3.7.2 — September 2026 (v2.9.2)

If you arrived here from `linepy` (or the original `carpedm20/LINE`, installed
back then as `pip install line`), you already know the shape of the problem:
the code you wrote for it no longer logs in, no longer polls, and dies with
TalkException code 82 the moment a chat requires encryption. This page maps
your existing calls onto OkLine, the maintained successor.

```bash
pip install okline          # instead of: pip install line   /   pip install linepy
```

## Why linepy died

- **Protocol rot.** linepy's last release was **January 2018**; carpedm20/LINE
  stopped around 2015. Their thrift definitions are from the `gd2.line.naver.jp`
  / akad era. LINE has since moved the whole surface — a new gateway
  (`line-chrome-gw.line-apps.com`), mandatory `X-Hmac` request signing
  (computed by LINE's `ltsm.wasm`), and new login flows. Old code sends
  requests the server no longer accepts: logins fail, QR was never really
  documented, and polling breaks.
- **E2EE / code 82.** LINE turned on **Letter Sealing** (end-to-end encryption)
  by default across chats. A plain `sendMessage` into a sealed chat is rejected
  with TalkException **code 82** ("can not send using plain mode") — the
  symptom reported in linepy's issue tracker (line-py #64) with no fix, because
  implementing it means the full E2EE protocol. Reading sealed messages is
  equally impossible: the text arrives as `chunks` ciphertext no one decrypts.

OkLine reproduces the protocol the LINE **Chrome extension** (CHROMEOS 3.7.2)
actually uses — the same gateway, headers, `X-Hmac` signature, QR + e-mail
login, SSE operation stream and E2EE — so it keeps up where the old libraries
froze: 703 offline tests, live-tested September 2026, active maintenance.

## The migration, in one example

A typical linepy echo bot:

```python
# linepy (broken)
from linepy import LineClient, LinePoll

client = LineClient("email@example.com", "password")

poll = LinePoll(client)
poll.execute()  # event loop


def RECEIVE_MESSAGE(op):
    msg = op.message
    client.sendMessage(msg.from_, "you said: " + msg.text)


poll.addCallbackFunc(RECEIVE_MESSAGE)
```

The same bot on OkLine:

```python
# OkLine (works)
from okline import OkLine, Bot

api = OkLine.from_tokens_file("tokens.json")  # made once by `okline login`
bot = Bot(api)


@bot.on_message
def echo(ctx):
    if ctx.text:  # already decrypted if E2EE
        ctx.reply(f"you said: {ctx.text}")


bot.run()  # event loop
```

Differences worth noting:

- **Login is a QR scan, not a password.** Run `okline login` once, scan, and
  the saved session (tokens + E2EE keychain) is reused forever — no password
  sitting in your source. (E-mail login exists too:
  `api.auth.email_login(...)`.)
- **Encrypted messages just work.** The bot framework decrypts incoming
  Letter-Sealed messages before your handler sees `ctx.text`, and sends into
  sealed chats are auto-encrypted.
- **`ctx.reply(...)`** picks the right destination (DM sender or group) — no
  manual `msg.from_` handling.

## Call-by-call mapping

| linepy / carpedm20 (`client.` unless noted) | OkLine (`api.` unless noted) | Notes |
|---|---|---|
| `from linepy import LineClient, LinePoll` | `from okline import OkLine, Bot` | `Bot` replaces `LinePoll` + callbacks |
| `LineClient(email, password)` | `OkLine()` then `api.auth.email_login(email, password)` | or QR: `api.qr_login(on_qr=print_qr)` — recommended |
| `LineClient(authToken=tok)` | `OkLine(access_token=tok, refresh_token=...)` | pass the refresh token so it auto-renews |
| — (no equivalent) | `OkLine.from_tokens_file("tokens.json")` | the recommended way: full session incl. E2EE keys |
| `client.getProfile()` | `api.get_profile()` | same dict shape (`mid`, `displayName`, …) |
| `client.sendMessage(to, text)` | `api.send_text(to, text)` | auto-seals when the chat requires E2EE |
| `client.sendSticker(to, pkgId, stkId)` | `api.send_sticker(to, pkg, stk)` | |
| `client.sendImage(to, path)` | `api.send_image(to, path)` | |
| `client.sendVideo(to, path)` | `api.send_video(to, path)` | |
| `client.sendAudio(to, path)` | `api.send_audio(to, path)` | |
| `client.sendFile(to, path)` | `api.send_file(to, path)` | |
| `client.getAllContactIds()` | `api.get_all_contact_ids()` | |
| `client.getContacts(ids)` | `api.get_contacts(ids)` | auto-chunked past 100 mids |
| `client.getGroupIdsJoined()` | `api.get_all_chat_mids()` | groups **and** rooms |
| `client.getRoomIdsJoined()` | `api.get_all_chat_mids()` | same call — filter by mid prefix if you care |
| `client.leaveGroup(mid)` | `api.leave_chat(mid)` | also works for rooms |
| `client.inviteIntoGroup(mid, contactIds)` | `api.invite_into_chat(mid, contactIds)` | |
| `client.blockContact(mid)` | `api.block_contact(mid)` | |
| `client.unblockContact(mid)` | `api.unblock_contact(mid)` | |
| `client.getRecentMessages(id, count)` | `api.get_recent_messages(id, count)` | |
| `client.sendContact(to, mid)` | `api.send_contact(to, mid)` | |
| `LinePoll(client)` + `poll.execute()` + `addCallbackFunc` | `Bot(api)` + `@bot.on_message` / `@bot.command("x")` / `@bot.on(OpType.X)` | see [bots](./bots.md) |
| raw long-poll ops | `for op in api.ops.iter_operations(): ...` | SSE stream, auto-reconnect — see [receiving events](./receiving-events.md) |
| — (impossible: code 82) | `api.send_encrypted_text(to, text)` / `api.decrypt_message(msg)` | Letter Sealing, 1:1 and groups |
| — | `python -m okline ...` / `okline ...` | ~30-command CLI, no linepy equivalent |

Two linepy habits to unlearn:

1. **Don't store your password in the script.** Use `okline login` once and
   `from_tokens_file` after that; the session auto-refreshes and is written
   back to disk. Guard `tokens.json` like a password.
2. **Don't build your own poll loop.** `Bot` (or `iter_operations()`) already
   handles reconnection, the resume cursor, and E2EE decryption; layering the
   old `while True: poll.execute()` pattern on top buys you nothing.

## What you get that linepy never had

- **E2EE Letter Sealing** — encrypt *and* decrypt, 1:1 and groups, sealed
  media included ([e2ee](./e2ee.md)).
- **QR login in the terminal** ([qr-login](./qr-login.md)).
- **No webhook needed** — polling over SSE, works behind NAT from a laptop.
- **A CLI** — `okline send`, `okline chatlog`, `okline watch --echo`, …
  ([cli](./cli.md)).
- **22 runnable examples** ([cookbook](./cookbook.md)) and full request/response
  recording ([recording](./recording.md)).

## A candid note on risk

linepy users already lived the informal side of this: personal-account
automation violates LINE's ToS, and LINE forced code removal from the
carpedm20 project back in 2014. Nothing about OkLine changes that legal
posture — what it changes is reliability and honesty about it. Read the
[ban-risk FAQ entry](./faq.md#8-will-my-line-account-get-banned-for-using-an-unofficial-api)
and the [comparison with line-bot-sdk](./comparison.md) before you port
anything that matters.

---

**Next:** [Getting started](./getting-started.md) · [Bots](./bots.md) ·
[Comparison](./comparison.md) · [FAQ](./faq.md)

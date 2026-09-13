---
title: "OkLine vs line-bot-sdk vs linepy — Which LINE Python Library?"
description: "OkLine vs line-bot-sdk vs linepy compared: personal account vs bot channel, QR login, E2EE Letter Sealing, webhooks vs polling, push limits, ToS risk."
---

# OkLine vs line-bot-sdk vs linepy — which LINE Python library?

[← docs home](./index.md)

> ✅ Verified against LINE Chrome extension 3.7.2 — September 2026 (v2.9.2)

Three Python libraries can talk to LINE, and they answer different questions:

- **[line-bot-sdk](https://github.com/line/line-bot-sdk-python)** (official) —
  for a *bot/official account* your customers befriend. ToS-clean, supported,
  webhook-based.
- **OkLine** (this project, unofficial) — for automating *your own personal
  account* from Python: QR login, send/receive as yourself, E2EE.
- **[linepy](https://github.com/line/linepy)** (unofficial, abandoned) — the
  pre-2018 personal-account library; its protocol no longer works.

## The comparison

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

*(Free-tier push limits change often — check
[developers.line.biz](https://developers.line.biz) for the current number.)*

## The official-vs-unofficial split, honestly

This is the part the search results won't tell you straight, so here it is:

**Personal-account automation violates LINE's Terms of Service.** OkLine is
unofficial and not affiliated with LINE Corporation. LINE has enforced this
before — in 2014 it forced the carpedm20/LINE project to take its code down.
An account that uses OkLine can in principle be restricted or suspended, and a
protocol change on LINE's side can break the SDK at any time. There is no
support contract and no SLA; there is a test suite, a live-tested protocol, and
honest documentation (see the [ban-risk FAQ entry](./faq.md#8-will-my-line-account-get-banned-for-using-an-unofficial-api)).

Given that, why does OkLine exist at all? Because the official API **cannot
answer the other half of the questions**. The Messaging API has no way to send
as yourself to your own contacts, no way to read your own chats without a
public webhook, and no E2EE — a server-side bot reads plaintext by design. For
personal automation — notifying yourself, backing up your chats, running an
agent on your own messages — there is no official path at all. OkLine fills
that gap, with the risk stated up front.

## When to use which

**Use line-bot-sdk when…**

- you are a business broadcasting to customers, or building any service for
  other people,
- you need verified sender identity, rich menus, LINE Login, or official
  support,
- ToS cleanliness and long-term stability matter more than personal-account
  access — for example, anything running on an account you can't afford to
  lose.

**Use OkLine when…**

- you want to automate **your own account**: notify yourself (the
  [LINE Notify replacement](./line-notify-replacement.md) use case), back up
  your chats, auto-reply in your own groups,
- you need to read your own chats from Python without a server, a webhook or
  ngrok,
- you need E2EE Letter Sealing send/decrypt,
- you accept the ToS risk on a throwaway-able account and keep the volume
  human-like.

**Use linepy when…** you enjoy archaeology. Its logins and its polling are
broken against the current protocol and it cannot handle E2EE (code 82); if
you have linepy code, port it — the mapping table is in
[Migrate from linepy to OkLine](./migrate-from-linepy.md).

One more axis worth naming: **maintenance**. An unofficial SDK is only as good
as its last verification against the live protocol. OkLine stamps that on every
release — verified against the Chrome extension 3.7.2, live-tested September
2026 (v2.9.2) — and its 703 offline tests pin the protocol details (see
[architecture](./architecture.md) for how the port was made).

---

**Next:** [FAQ](./faq.md) · [Getting started](./getting-started.md) ·
[QR login](./qr-login.md) · [LINE Notify replacement](./line-notify-replacement.md)

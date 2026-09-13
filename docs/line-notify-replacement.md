---
title: "LINE Notify Replacement: Send LINE Messages to Yourself from Python — OkLine"
description: "LINE Notify shut down March 31 2025. Send LINE messages to yourself from Python free and unlimited with OkLine — no developer account, no webhook."
---

# LINE Notify replacement: send LINE messages to yourself from Python

[← docs home](./index.md)

> ✅ Verified against LINE Chrome extension 3.7.2 — September 2026 (v2.9.2)

## What broke on March 31, 2025

[LINE Notify](https://notify-bot.line.me) was a simple, beloved service: get a
token, then `POST https://notify-api.line.me/api/notify` and the message
appeared in your LINE app from "LINE Notify". Server alerts, cron summaries,
home-automation pings, RSS digests — an entire ecosystem of personal
notification scripts ran on it, many of them in Python.

On **March 31, 2025** the service was discontinued. Every token stopped
working; the endpoint now refuses all requests. Nothing changed in your code —
the service simply went away, and the ~1,500 GitHub repos built on it went
silent with it.

What you were left with, officially, was the **Messaging API** — which is a
poor replacement for *personal* notifications:

- You must register at developers.line.biz, create a provider and a Messaging
  API channel, and get a channel access token — ceremony that LINE Notify
  never asked for.
- You must **friend your own bot** with your own account before it can message
  you at all.
- The free tier allows roughly **200 push messages per month** (re-check the
  current number — LINE's pricing has changed repeatedly). A script that pings
  you ten times a day burns through that in three weeks.
- The message arrives **from a bot**, not from you — a separate conversation,
  a separate notification profile.

If you're sending notifications *to yourself, from your own scripts*, all of
that is friction for a worse result.

## The replacement: OkLine

OkLine automates **your own personal LINE account**, so "notify me" becomes
"send a message to myself" — free, unlimited, arriving from your own name in
your chat with yourself:

```bash
pip install okline
okline login     # once: scan the QR, confirm the PIN — saves tokens.json
```

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
api.send_text(api.get_profile()["mid"], "backup finished")  # to yourself
```

No developer account, no bot channel, no webhook, no monthly cap — and because
it's your own account, the session (including your E2EE keychain) persists in
`tokens.json` and is reused forever. Requires Python 3.9+ and Node.js 18+ on
your PATH (for `X-Hmac` request signing).

## Migrating: requests → OkLine

Your old LINE Notify code probably looked like one of these:

```python
# old — dead since March 31, 2025
import requests

requests.post(
    "https://notify-api.line.me/api/notify",
    headers={"Authorization": "Bearer " + NOTIFY_TOKEN},
    data={"message": "backup finished"},
)
```

```bash
# old — the curl one-liner version
curl -H "Authorization: Bearer $NOTIFY_TOKEN" \
     -d "message=backup finished" \
     https://notify-api.line.me/api/notify
```

The OkLine replacement is five lines, one time:

```python
# new
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")  # created once by `okline login`
api.send_text(api.get_profile()["mid"], "backup finished")
api.close()
```

To find `api.get_profile()["mid"]` once and avoid the extra request, cache it:

```python
ME = "u0123456789abcdef0123456789abcdef"  # your own mid, from `okline whoami`
api.send_text(ME, "backup finished")
```

What you lose: the sticker/`stickerPackageId` parameters and the
`imageThumbnail`/`imageFullImage` URL parameters of the old Notify API. What
OkLine offers instead is strictly more: stickers
(`api.send_sticker(me, "11537", "52002744")`), real image/video/file sends from
your disk (`api.send_image(me, "chart.png")` — see [media](./media.md)),
messages to any of your contacts or groups, and E2EE. See
[messaging](./messaging.md) for the full send surface.

## A reusable `notify.py`

Drop-in module with a self-message function, session reuse and auto-refresh —
the LINE Notify use case, minus the token:

```python
#!/usr/bin/env python3
"""notify.py — a tiny LINE Notify replacement built on OkLine.

python notify.py "backup finished"
"""

from __future__ import annotations

import sys

from okline import OkLine

TOKENS = "tokens.json"  # created once by `okline login`
_api: OkLine | None = None  # one client per process


def _client() -> OkLine:
    """Reuse one session per process.

    ``from_tokens_file`` restores the access/refresh tokens AND the E2EE
    keychain, and — because the client came from a session file — OkLine
    automatically writes refreshed tokens back to it whenever the server asks
    for a renewal (gateway code 119 / HTTP 401 trigger this transparently).
    So the stored session stays valid across runs.
    """
    global _api
    if _api is None:
        _api = OkLine.from_tokens_file(TOKENS)
    return _api


def notify(text: str) -> None:
    """Send a LINE message to yourself (the old `POST /api/notify`)."""
    api = _client()
    api.send_text(api.get_profile()["mid"], text)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Thai / emoji on Windows
    notify(" ".join(sys.argv[1:]) or "hello from okline")
```

Then, anywhere in your codebase:

```python
from notify import notify

notify("cron job started")
notify(f"disk usage {pct:.0f}%")
notify("nightly backup finished")
```

For proactive renewal (a background timer that renews the token before it goes
stale, mirroring the Chrome extension's own schedule), load the client with:

```python
_api = OkLine.from_tokens_file(TOKENS, auto_refresh_schedule=True)
```

See [authentication](./authentication.md#proactive-renewal-schedule-auto_refresh_schedule)
for what the schedule does.

### Going further than Notify ever could

- **Alert on incoming messages**, not just send: `okline notify --keyword
  urgent` watches your chats and prints alerts — or use the
  [bot framework](./bots.md) to trigger anything on arrival.
- **Notify a group** instead of yourself: `api.send_text("c1234...", "deploy
  done")` lands in your team group, from you.
- **From cron**, the CLI alone is enough (no Python file needed):

  ```bash
  okline send u0123456789abcdef0123456789abcdef "backup finished"
  ```

## Notes

- **Read this first:** automating a personal account is against LINE's Terms of
  Service. OkLine is unofficial; keep the volume human-like and use an account
  you can afford to lose — the candid risk framing is in the
  [FAQ](./faq.md#8-will-my-line-account-get-banned-for-using-an-unofficial-api).
- You are sending **to your own mid**, so messages land in LINE's chat with
  yourself — the same place LINE Notify messages used to arrive, minus the
  middleman.
- Thai readers: คู่มือฉบับภาษาไทยอยู่ที่
  [LINE Notify ทางเลือก Python — ส่งแจ้งเตือนฟรี](th/line-notify.md).

---

**Next:** [Getting started](./getting-started.md) · [QR login](./qr-login.md) ·
[Messaging](./messaging.md) · [Bots](./bots.md) · [FAQ](./faq.md)

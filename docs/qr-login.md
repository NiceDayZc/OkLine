---
title: "LINE QR Code Login from Python — OkLine, Unofficial LINE SDK"
description: "LINE QR code login from Python: OkLine draws the QR in your terminal, you scan and confirm a PIN, and the session persists — no password needed."
---

# LINE QR code login from Python

[← docs home](./index.md)

> ✅ Verified against LINE Chrome extension 3.7.2 — September 2026 (v2.9.2)

Logging in to LINE from Python is the step every tutorial hand-waves — the
official SDK assumes you already have a channel access token, and the old
unofficial libraries documented it nowhere. OkLine makes it a one-liner: it
renders the login QR **as ASCII blocks directly in your terminal**, you scan
it with the LINE app, confirm a PIN, and you're in. No password, no developer
registration, no pasting tokens out of browser devtools.

## The fastest path: the CLI

```bash
pip install okline
# optional: prettier inline QR
pip install "okline[qr]"

okline login
```

That's the whole flow: the QR is drawn, you scan it with the LINE app (the QR
reader is under the Friends tab), a PIN appears, you confirm it on your phone,
your profile prints, and the session — tokens, certificate and E2EE keychain —
is saved to `./tokens.json`. Every later `okline` command (and every Python
snippet below) reuses that file automatically. Options:
`okline login --save mysession.json` (elsewhere), `--wait 240` (longer to
scan), `--invert` (light-background terminal).

## From Python

`api.qr_login(...)` drives the same flow in your own code. Render the QR with
`print_qr`, show the PIN when it comes, and save the session at the end:

```python
from okline import OkLine
from okline.qrterm import print_qr

api = OkLine()
result = api.qr_login(
    on_qr=lambda url: print_qr(url),  # draw the QR — scan it
    on_pin=lambda pin: print("Confirm this PIN on your phone:", pin),
    wait_seconds=180,  # how long to wait for you
)
print("logged in:", bool(result.access_token))

api.save_tokens("tokens.json")  # reuse forever; includes the E2EE keychain
```

`on_qr` hands you the login URL — render exactly that (it already carries the
mandatory `secret` parameter, see below). Without the `qrcode` package
installed, `print_qr` prints the raw URL instead; paste it into any QR
generator and scan that.

Once `tokens.json` exists you never scan again:

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")  # tokens + E2EE keys restored
print(api.get_profile()["displayName"])
print("E2EE ready:", api.e2ee.is_ready())  # True
```

## What actually happens under the hood

QR login is the LINE Chrome extension's `SecondaryQrCodeLogin` flow, and OkLine
reproduces it step for step:

```
createSession → createQrCode → (generate a Curve25519 key inside the WASM)
  → show QR  =  callbackUrl + ?secret=<pubkey>&e2eeVersion=1
  → checkQrCodeVerified (you scan) → verifyCertificate / PIN flow
  → checkPinCodeVerified (you confirm the PIN) → qrCodeLoginV2 → tokens
```

The pieces that matter:

- **The `?secret=…&e2eeVersion=1` query on the QR URL is mandatory.** Without
  it, the LINE app shows "an error occurred" the moment you scan. OkLine
  generates the Curve25519 keypair *inside LINE's real `ltsm.wasm`* and appends
  it to the URL automatically — so always render the URL your `on_qr` callback
  receives, never a `createQrCode` response you assembled yourself.
- **The PIN step is a device confirmation.** First login on a new "device"
  (i.e. your OkLine session) asks you to confirm a PIN in the LINE app; after
  that the issued **certificate** identifies the device.
- **`qr_login` also unwraps your E2EE (Letter Sealing) keychain** during the
  flow, which is why a `save_tokens()` right after it produces a session file
  that can decrypt your sealed chats forever (see
  [E2EE](./e2ee.md)).

## Token persistence and refresh

The `LoginResult` from `qr_login` carries `access_token`, `refresh_token`,
`certificate` and `mid`. `api.save_tokens("tokens.json")` writes them (plus the
E2EE keychain) to disk; `OkLine.from_tokens_file(...)` restores everything.

Refresh is automatic: while a `refresh_token` is set, a credential failure
(HTTP 401 or gateway code 119 `MUST_REFRESH_V3_TOKEN`) transparently renews the
access token and replays the request — and when the client was built with
`from_tokens_file`, the refreshed token is **written back to the file**, so the
stored session never goes stale on you. If the refresh token itself is revoked
(server kicked you out), you get a `LineAuthError` and simply re-run
`okline login`.

For long-running daemons you can additionally arm the extension's proactive
renewal timer, so the token renews *before* it expires:

```python
api = OkLine.from_tokens_file("tokens.json", auto_refresh_schedule=True)
```

Details (retry policy, codes 10201/10202) in
[authentication](./authentication.md#token-refresh).

## Certificate reuse: skipping the PIN on later logins

`qr_login` returns a `certificate` — proof that LINE has already confirmed this
"device". Save it once and pass it on the next fresh login to skip the PIN
step (`verifyCertificate` succeeds for a known device):

```python
result = api.qr_login(on_qr=print_qr, certificate=saved_certificate)
```

In practice you rarely need this by hand: `save_tokens()` writes the
certificate into your session file and `from_tokens_file` carries it, so a
restored session already behaves like a known device. It matters when you want
a *fresh* login (new tokens) without the PIN dance.

## Troubleshooting QR login

- **"Node.js not found" / error code 10005 (`REQUEST_INVALID_HMAC`)** — every
  request, including login, must be signed with `X-Hmac`, which OkLine computes
  by running LINE's real `ltsm.wasm` through a small Node bridge. Install
  **Node.js 18+** and check `node --version` in the same shell you run OkLine
  from.
- **Node installed somewhere unusual?** Point OkLine at it with
  `export LINE_NODE=/full/path/to/node` (Windows:
  `set LINE_NODE=C:\path\to\node.exe`), or
  `OkLine(config=LineConfig(node_path="..."))`.
- **Phone shows "an error occurred" after scanning** — the QR URL was missing
  its `secret` parameter, which happens if you rendered a URL other than the
  one `on_qr` gave you, or you're on an old version: `pip install -U okline`.
- **QR unreadable** — light background: `print_qr(url, invert=True)` (or
  `okline login --invert`). Garbled blocks on Windows: run `chcp 65001` first,
  or use Windows Terminal / PowerShell 7. Make it bigger with
  `print_qr(url, style="full")`. No inline QR at all: `pip install "okline[qr]"`.
- **Need more time to scan?** `wait_seconds=...` (Python) or `--wait 240`
  (CLI) extends the wait for both the scan and the PIN.
- **The PIN never arrives** — check the LINE app's notification for a login
  confirmation request; on some phones you must open LINE manually to see it.

More in [troubleshooting](./troubleshooting.md).

## Where to go next

- Send your first message: [getting started](./getting-started.md)
- The session file and refresh, in depth: [authentication](./authentication.md)
- What the E2EE keychain in `tokens.json` buys you: [E2EE](./e2ee.md)
- The protocol behind `X-Hmac` and `ltsm.wasm`: [architecture](./architecture.md)

---

**Next:** [Getting started](./getting-started.md) · [Authentication](./authentication.md) ·
[LINE Notify replacement](./line-notify-replacement.md) · [FAQ](./faq.md)

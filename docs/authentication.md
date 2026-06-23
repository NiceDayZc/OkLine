# Authentication & login

[← docs home](./index.md)

Every OkLine request is signed with an `X-Hmac` header, so **Node.js 18+ must be
on your PATH** (OkLine runs a bundled WASM module through a tiny Node bridge to
compute the signature — see [architecture](./architecture.md)). You also need
Python 3.9+.

There are three ways to get an authenticated `OkLine`:

1. **[Reuse a saved session](#1-reuse-a-saved-session-recommended)** — log in once,
   then load the same file forever (this also restores your E2EE keys).
2. **[QR-code login](#2-qr-code-login)** — scan a code with the LINE app, no password.
3. **[E-mail + password](#3-e-mail--password-rsa)** — the classic RSA credential flow.

> 🔒 **Tokens are credentials.** An access token (and a saved session file) grants
> full access to your LINE account. Never commit, log, paste or share them.
> OkLine redacts them by default in recorded output, and `tokens.json` is matched
> by the project `.gitignore`.

---

## 1. Reuse a saved session (recommended)

The easiest, most reliable pattern: log in **once**, save a session file, then
load it on every later run. The session file keeps your access token, refresh
token, certificate, mid — **and the unwrapped E2EE (Letter Sealing) keychain** —
so encrypted messages keep working across runs without re-scanning a QR code.

### Save a session after logging in

```python
from okline import OkLine
from okline.qrterm import print_qr

api = OkLine()
api.qr_login(on_qr=print_qr,
             on_pin=lambda pin: print("Confirm this PIN on your phone:", pin))

api.save_tokens("tokens.json")   # writes credentials + E2EE keychain
```

`api.qr_login(...)` (on the `OkLine` object) drives the QR flow **and** loads
your E2EE keys for this session, so a following `save_tokens()` persists them.

### Reuse it next time — instant, no scan

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")   # restores tokens AND E2EE keys
print(api.get_profile())
print("E2EE ready:", api.e2ee.is_ready())       # True — encrypted chats work
```

That is all you need. **Prefer this over hand-writing JSON** — `save_tokens` /
`from_tokens_file` use the right field names and carry the E2EE keychain, which a
manual `json.dump` of `{accessToken, refreshToken}` cannot.

> When a client is built with `from_tokens_file`, OkLine **auto-saves** the file
> whenever the access token is refreshed (see [Token refresh](#token-refresh)),
> so the stored session stays valid.

The CLI does exactly this for you: `okline login` logs in once and saves
`./tokens.json`; every other command reuses it automatically. See [CLI](./cli.md).

---

## 2. QR-code login

Scan a QR with the LINE app on your phone — no password needed. OkLine renders
the QR as ASCII right in your terminal and drives the whole flow:

```python
from okline import OkLine
from okline.qrterm import print_qr

api = OkLine()
result = api.qr_login(
    on_qr=lambda url: print_qr(url),          # draw the QR (scan it)
    on_pin=lambda pin: print("PIN:", pin),    # show the PIN to confirm
    wait_seconds=180,                         # how long to wait for you
)
print("logged in:", bool(result.access_token))

api.save_tokens("tokens.json")   # so you don't have to scan again next time
```

What happens under the hood:

```
createSession → createQrCode → (generate a Curve25519 key inside the WASM)
  → show QR  =  callbackUrl + ?secret=<pubkey>&e2eeVersion=1
  → checkQrCodeVerified (you scan) → verifyCertificate / PIN flow
  → checkPinCodeVerified (you confirm the PIN) → qrCodeLoginV2 → tokens
```

The **`?secret=…&e2eeVersion=1`** on the QR URL is mandatory — without it the LINE
app shows "an error occurred" after scanning. OkLine generates the Curve25519
keypair inside the real `ltsm.wasm` and appends it automatically, so you only
render the URL `on_qr` hands you.

**Light-background terminal?** Pass `print_qr(url, invert=True)`. **QR garbled on
Windows?** Run `chcp 65001` first. For an inline graphical QR, install the extra:
`pip install "okline[qr]"`.

### Skip the PIN on later logins

`qr_login` returns a `certificate`. Save it and pass it next time to skip the PIN
step (`verifyCertificate` succeeds for a known device):

```python
result = api.qr_login(on_qr=print_qr, certificate=saved_certificate)
```

> `result.certificate` is also written into your session file by `save_tokens`,
> so loading with `from_tokens_file` carries it for you.

---

## 3. E-mail + password (RSA)

This mirrors the extension exactly: it fetches an RSA key, encrypts your
credentials and calls `loginV2`.

```python
from okline import OkLine

api = OkLine()
result = api.auth.email_login("me@example.com", "secret", with_e2ee=False)

if result.success:
    print("access token:", result.access_token[:12], "…")
    print(api.get_profile())
elif result.type == 3:   # REQUIRE_DEVICE_CONFIRM
    print("Confirm this PIN on your phone:", result.pin_code)
else:
    print("needs verification:", result.type, result.display_message)
```

`email_login` returns a `LoginResult` with fields: `.success`, `.type`
(`LoginResultType`), `.access_token`, `.refresh_token`, `.certificate`, `.mid`,
`.pin_code`, `.verifier`, `.display_message`, `.raw`. On success the tokens are
adopted into the client automatically, so you can call API methods right away.

`with_e2ee=True` (the default) negotiates Letter Sealing (E2EE). The RSA password
blob is built and encrypted for you in
[`okline/crypto.py`](../okline/crypto.py).

After a successful login you can persist the session the same way:

```python
api.save_tokens("tokens.json")
```

---

## Token refresh

Automatic: if a `refresh_token` is set, a `401` from the server triggers a token
refresh and the original request is retried transparently. When the client was
built with `from_tokens_file`, the refreshed token is also written back to the
file.

You can also refresh manually:

```python
new_access = api.auth.refresh_access_token()
```

To build a client directly from tokens you already hold (auto-refresh enabled as
soon as a refresh token is present):

```python
api = OkLine(access_token="...", refresh_token="...")
```

---

## Logout

Invalidate the current session on the server:

```python
api.auth.logout()
```

From the CLI, `okline logout` does this **and** deletes the local `tokens.json`.
You can also remove the device from **LINE app → Settings → Account → logged-in
devices**.

---

See also: [Getting started](./getting-started.md) · [Messaging](./messaging.md) ·
[Receiving events / bots](./bots.md) · [CLI](./cli.md).

# Troubleshooting & FAQ

[← docs home](./index.md)

## "Node.js not found" / `REQUEST_INVALID_HMAC` (code 10005)

Every request needs the `X-Hmac` signature, computed by Node + `ltsm.wasm`.

- Install **Node.js 18+** and make sure `node --version` works in the same shell.
- Non-standard path? `set LINE_NODE=C:\path\to\node.exe` (Windows) /
  `export LINE_NODE=/path/to/node`, or
  `OkLine(config=LineConfig(node_path="..."))`.
- For offline unit tests only, you can disable signing with
  `OkLine(config=LineConfig(enable_hmac=False))` (requests will then be rejected
  by the real server — this is for mocked tests).

## `okline: command not found`

The `okline` script is installed into your Python environment's `Scripts/`
(Windows) or `bin/` (macOS/Linux) directory. If your shell can't find it:

- Run the module form instead — it always works:
  ```bash
  python -m okline whoami
  ```
- Or add the Scripts/bin directory to your `PATH`. On Windows it's usually
  `...\PythonXX\Scripts` (or the venv's `Scripts`); `pip show -f okline` lists
  where the script landed.

## `[encrypted]` messages / can't read Letter-Sealed text

If a received message shows up as `[encrypted]` (or its `text` is empty while it
has a `chunks` field), your E2EE keys aren't loaded.

- **Log in once** so the keychain is captured and saved:
  ```bash
  okline login
  ```
  This writes `./tokens.json` **including** the E2EE keychain; later runs reuse
  it automatically.
- In code, reuse that session and check readiness:
  ```python
  api = OkLine.from_tokens_file("tokens.json")
  print(api.e2ee.is_ready())          # must be True to encrypt/decrypt
  msg = api.decrypt_message(received)  # returns plaintext `text`
  ```
- If `is_ready()` is `False`, the keychain wasn't loaded — re-run `okline login`
  (scan QR → confirm PIN) to refresh it. E2EE keys load during `qr_login` and
  persist via `save_tokens` / `from_tokens_file`.

## The phone shows "an error occurred" after scanning the QR

The QR must carry `?secret=<curve25519 pubkey>&e2eeVersion=1`. OkLine adds this
automatically in `auth.qr_login` — make sure you render the URL passed to your
`on_qr` callback (not some other URL), and that you're on a current version.

## The QR is unreadable in the terminal

- Light-background terminal: `print_qr(url, invert=True)`.
- Windows console garbling the blocks: run `chcp 65001` first, or use Windows
  Terminal / PowerShell 7.
- Make it bigger: `print_qr(url, style="full")` (double width).
- No inline QR at all? Install the optional extra: `pip install "okline[qr]"`.

## `UnicodeEncodeError` / garbled non-ASCII (Thai, emoji, …)

The CLI already forces UTF-8 output, so `okline ...` prints non-ASCII text
correctly. In **your own scripts**, a legacy console encoding (e.g. Windows
cp1252) can still raise `UnicodeEncodeError`. Fix it once at startup:

```python
import sys
sys.stdout.reconfigure(encoding="utf-8")   # Python 3.7+
```

or set the environment variable before launching Python:

```bash
# Windows
set PYTHONUTF8=1
# macOS / Linux
export PYTHONUTF8=1
```

`api.print_last()` is already UTF-8 safe and degrades gracefully on a console
that can't encode a character.

## A response is `None` or a key is missing (`KeyError`)

The gateway wraps results as `{"message":"OK","data":...}`; OkLine unwraps
`.data`. If you call the transport very directly you may see the envelope. Turn
on raw logging to see exactly what came back:

```bash
LINE_DEBUG=1 python your_script.py
```

A non-`OK` envelope is raised as `LineApiError` (with `.code`, `.reason`).

## `getChats` / `getContacts` — `Invalid Length` (code 6)

The gateway rejects more than 100 mids in one `getChats`/`getContactsV2` call.
OkLine now **auto-chunks** these requests at 100 mids and merges the results, so
you can pass arbitrarily long lists. If you still hit this, **upgrade** to the
latest version (`pip install -U okline`).

## `401` / token expired

- Pass a `refresh_token` so OkLine auto-refreshes on `401`:
  `OkLine(access_token=..., refresh_token=...)`.
- Or refresh manually: `api.auth.refresh_access_token()`.
- If you loaded the client with `OkLine.from_tokens_file(...)`, the refreshed
  token is written back to the session file automatically.
- If the session was revoked (logged out on another device), log in again
  (`okline login`).

## `LineMustUpgradeError` / `MUST_UPGRADE`

The server wants a newer client version. The bundled app version is `3.7.2`; if
LINE forces an upgrade you may need a newer `ltsm.wasm` + version string from a
fresh extension build (`LineConfig(app_version=...)`, `ltsm_origin=...`).

## Reading errors

```python
from okline import LineApiError, enums

try:
    api.send_text(to, "hi")
except LineApiError as e:
    print(e.code, e.reason, e.metadata)
    # map a numeric code to a name:
    print(enums.ErrorCode(e.code).name if e.code is not None else "?")
```

Common `ErrorCode`s: `AUTHENTICATION_FAILED`(1), `NOT_AUTHORIZED_DEVICE`(8),
`NOT_FRIEND`(36), `MUST_UPGRADE`(50), `EXPIRED_REVISION`(52),
`MUST_REFRESH_V3_TOKEN`(119). Full list in
[`okline/enums.py`](../okline/enums.py).

## Long-poll / SSE seems to hang

That's expected — `iter_operations()` and the verify long-polls block until
something happens or the server times out. Use a thread, or set
`qr_login(wait_seconds=...)`.

## Rate limits / abuse blocks

`EXCESSIVE_ACCESS`(4), `ABUSE_BLOCK`(35), `CONGESTION_CONTROL`(58) mean you're
sending too fast or tripping anti-abuse. Slow down and only use your own
account. You can pace requests automatically with the built-in token bucket:

```python
from okline.ratelimit import RateLimiter
api.transport.rate_limiter = RateLimiter(rate=5, per=1.0)   # ~5 req/s
```

## Still stuck?

Capture a redacted transcript and inspect it:

```python
api.save_log("debug.txt")          # secrets masked by default
print(api.last.pretty())
```

See [recording](./recording.md) for the full transcript/HAR options.

# Session & Login (OkLine)

How to get a live `OkLine` client: check Node.js first, reuse `tokens.json` if present, otherwise run a QR login the user completes on their phone. SECURITY: `tokens.json` IS the user's entire LINE account — never print token values, never commit it, never copy it off the machine. See ./errors.md for the full error-code table.

## 0. Prerequisite: Node.js (check before anything else)

Every request is signed (X-Hmac) by running LINE's real `ltsm.wasm` through a Node bridge. Without Node, EVERY call fails with `LineApiError` code 10005 (REQUEST_INVALID_HMAC). Check first:

```bash
node --version   # must print v18 or higher
```

In Python: `from okline.hmac_signer import LtsmBridge; LtsmBridge.is_available()`. If Node is installed at a non-standard path, point at it with the env var `LINE_NODE=/full/path/to/node` (checked by `hmac_signer.py`). Code 10006 (REQUEST_MUST_UPGRADE, raised as `LineMustUpgradeError`) means the okline package itself is too old for the server — upgrade it, do not debug your code.

## 1. Check for an existing session BEFORE logging in

Resolution order (matches `examples/_common.py`): `./tokens.json` in the repo root, then `~/tokens.json`. The repo at `/Users/nicedayzc/Documents/Codex/2026-09-12/new-chat-2/OkLine` may already hold a live `tokens.json`.

```python
import os
from okline import OkLine

def load_session():
    """Return a verified (api, path) or (None, None)."""
    for p in ("tokens.json", os.path.expanduser("~/tokens.json")):
        if not os.path.exists(p):
            continue
        api = OkLine.from_tokens_file(p)   # restores E2EE keychain too
        prof = api.get_profile()           # raises if the session is dead
        print(f"session OK: {prof.get('displayName')} ({prof.get('mid')})")
        print("e2ee ready:", api.e2ee.is_ready())
        return api, p
    return None, None

api, path = load_session()
# ... use api ...
api.close()   # releases the Node bridge subprocess
```

`get_profile()` is the cheap liveness probe. A restored session auto-refreshes its access token on 119/401 and auto-saves back to the same file (`from_tokens_file` wires `_session_path`). Pass `OkLine.from_tokens_file(path, auto_refresh_schedule=True)` to also arm the proactive background renewal timer (fires at `tokenIssueTimeEpochSec + durationUntilRefreshInSec`, re-arms after each renewal; cancelled by `api.close()`).

Failure modes when probing:
- `LineAuthError` with code 1/7/8 — session is dead (1 = auth failed, 7 = account deleted/gone, 8 = device not authorized). Re-login (section 3).
- `LineLoginRequired` — no usable credentials at all; re-login.
- Refresh rejected with AUTH_INVALID_REQUEST (gateway 10201, raised as `LineAuthError("token refresh rejected (AUTH_INVALID_REQUEST): re-login required")`) — hard kickout; re-login.
- E2EE restore failure is NON-fatal (logged as a warning; `e2ee.is_ready()` returns False) — everything works except sealed-media/decryption; a fresh QR login brings keys back.

## 2. Token refresh flows (what happens automatically)

- Auto on 119/401: the transport's refresh hook calls `auth.refresh_access_token()` with a single-flight lock, then retries the original call. If the session came from `from_tokens_file`, the rotated tokens are persisted automatically.
- Manual: `api.auth.refresh_access_token()` → returns the new access token. Gateway 10202 (AUTH_RETRY_REQUIRED) is retried with the server's jittered exponential backoff (client-capped at 60 s per sleep); exhausting the budget raises `LineAuthError`.
- Proactive (opt-in): `auto_refresh_schedule=True` as above. A failed scheduled renewal only logs — the old token keeps working until the 119/401 path fires.

You normally never call refresh yourself; if you see 119s in logs, the auto path already handled them.

## 3. QR login — the callbackUrl handoff pattern

`api.qr_login(on_qr=..., on_pin=..., wait_seconds=180)` drives the full secondary-device flow. Key fact for agents: `on_qr` receives the FULL `callbackUrl` which ALREADY contains the required `?secret=<curve25519 pubkey>&e2eeVersion=1` (okline appends it — a QR of the bare URL fails on the phone). A phone OPENING that URL in a browser/safari triggers the same approval flow as scanning the QR, so you can either render the QR in the terminal (`okline.qrterm.qr_to_ascii` / `print_qr`, needs `pip install qrcode`) or just hand the URL to the user to open on their phone.

Flow: phone scans/approves → if `verifyCertificate` succeeds (returning device — the certificate stored in `tokens.json` is reused) login completes with NO PIN; first-ever login → a PIN is issued (`on_pin`) and the user must confirm it in the LINE app; then tokens are issued. `wait_seconds` bounds the whole poll budget (default 180 s; the CLI uses `--wait`).

### Ready-to-run background login script

Write this to a file, run it in the background, and poll the log. Markers make the state machine trivially parseable.

```python
# login_bg.py — run: python3 login_bg.py > login.log 2>&1
import sys
from okline import OkLine
from okline.qrterm import qr_to_ascii

api = OkLine(record=False)

def on_qr(url):
    print(f"LOGIN_URL: {url}", flush=True)          # hand this to the user
    try:
        print(qr_to_ascii(url), flush=True)          # scannable in terminal
    except ModuleNotFoundError:
        print("(pip install qrcode for an inline QR — the URL above still works)", flush=True)

def on_pin(pin):
    print(f"LOGIN_PIN: {pin}", flush=True)           # user confirms in LINE app

res = api.qr_login(on_qr=on_qr, on_pin=on_pin, wait_seconds=300)
if not res.access_token:
    print(f"LOGIN_FAILED: {res.display_message or res.type}", flush=True)
    sys.exit(1)

api.save_tokens("tokens.json")                       # exports E2EE keychain too
prof = api.get_profile()
print(f"LOGIN_OK: {prof.get('displayName')} ({prof.get('mid')})", flush=True)
print(f"E2EE_READY: {api.e2ee.is_ready()}", flush=True)
api.close()
```

```bash
python3 login_bg.py > login.log 2>&1 &   # or run_in_background
sleep 5 && cat login.log                  # LOGIN_URL + QR appear within seconds
grep -q LOGIN_OK login.log                # poll until this succeeds
```

Use the facade `api.qr_login(...)` (NOT `api.auth.qr_login(...)`) — the facade then adopts the E2EE login material into `api.e2ee` automatically, so `save_tokens` exports the Letter-Sealing keychain. After `LOGIN_OK`, throw the client away and rebuild via `OkLine.from_tokens_file("tokens.json")` in your real script (or keep using the login process). ALWAYS call `api.save_tokens("tokens.json")` after login — an unsaved session dies with the process.

Interactive alternative when a TTY exists: `python3 -m okline login --save tokens.json --wait 300` (prints QR + PIN, saves, prints "Logged in as ..."). Then `python3 -m okline whoami` to verify.

## 4. E-mail login (with device-confirm)

`api.auth.email_login(email, password)` implements the RSA loginV2 flow. A first-time login returns type 3 (REQUIRE_DEVICE_CONFIRM); pass `confirm_device=True` to drive the continuation to completion (v2.8+): okline long-polls `/api/talk/long-polling/LF1` (E2EE path) or `/JQ` (plain path), then re-logs-in with the exchanged verifier. `on_pin` receives the 6-digit code the user must confirm on their primary device (on the E2EE path the PIN doubles as the LF1 session secret — it is generated locally, not by the server).

```python
from okline import OkLine

api = OkLine(record=False)
res = api.auth.email_login(
    "user@example.com", "the-password",
    confirm_device=True,                      # REQUIRED for first login
    on_pin=lambda pin: print(f"Confirm this PIN on your phone: {pin}", flush=True),
    wait_seconds=180,
)
if not res.success or not res.access_token:
    raise SystemExit(f"email login failed: {res.display_message or res.type}")
api.save_tokens("tokens.json")
api.close()
```

`api.auth.email_login_ladder(email, password, confirm_device=True, on_pin=...)` runs the extension's full 3-tier strategy (stored certificate → E2EE → plain, with the specific fallback codes handled between tiers). Per-e-mail certificates live only in-process (`auth.email_certificates`); across runs the global certificate in `tokens.json` is the reuse path.

Failure modes: HTTP 410 during the device-confirm poll raises `LineAuthError` ("PIN code timeout") — the user took too long; restart the login. `LineAuthError` codes 1/7/8 → credentials/account problem, re-login or stop. TalkException codes 89/94/97 during the E2EE tier mean the account's primary device does not support E2EE login — use the ladder or `with_e2ee=False`.

## 5. Logout and certificate reuse

- Logout: `api.auth.logout()` calls `Talk.AuthService.logoutV2` and invalidates the session server-side. DESTRUCTIVE — ask the user first (see SKILL.md safety rules). After it, delete/ignore the old `tokens.json` and re-login from scratch.
- Certificate reuse: every successful login returns a `certificate` which `save_tokens` persists. On a later QR login from the same machine, `verifyCertificate` succeeds and the PIN step is SKIPPED — the user only scans and taps approve. This is why you should always save via `save_tokens` and reuse via `from_tokens_file` rather than logging in fresh each run.
- Raw tokens: `OkLine(access_token="...", refresh_token="...")` works but skips the E2EE keychain and the auto-save path — prefer the file.

## 6. Quick decision table

| Situation | Do this |
|---|---|
| `tokens.json` exists, `get_profile()` OK | Use it; `api.close()` when done |
| Profile raises LineAuthError 1/7/8 or 10201 kickout | Re-login (QR section 3) |
| 10005 on first call | Node missing — `node --version`, fix PATH or `LINE_NODE` |
| `LineMustUpgradeError` (10006) | `pip install -U okline` |
| No session anywhere | Background QR script; hand the LOGIN_URL to the user |
| User prefers e-mail + password | `email_login(..., confirm_device=True, on_pin=...)` |
| PIN poll raises 410 timeout | Restart the login; user was too slow |

Next: send/read with the live client — see ./messaging.md and ./reading.md; raw CLI usage in the "How to run things" section of ../SKILL.md and docs/cli.md in the repo.

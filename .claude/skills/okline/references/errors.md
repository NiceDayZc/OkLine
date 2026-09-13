# Error handling — exception classes, LINE error codes, and what to do about each

Every failure in OkLine is a subclass of `LineError` (`okline.exceptions`). Inspect before you react: most "errors" are already auto-handled inside the library, and the rest sort into exactly four actions — re-login, slow down, fix your arguments, or surface to the user. Never loop on a retry; never print exception `raw`/`metadata` blindly (it can echo request bodies).

## Exception hierarchy

| Class | Raised when | Key attributes |
|---|---|---|
| `LineError` | Base of everything | — |
| `LineConfigError` | Client mis-configured (missing token, bad host) | — |
| `LineTransportError` | Network failure, timeout, HTTP 5xx after retries | `.status`, `.body` |
| `HmacSignerError` (`okline.hmac_signer`) | Node bridge cannot start or a sign command fails (Node missing/crashed) | — |
| `LineApiError` | Any Thrift application error from the LINE backend | `.code`, `.reason`, `.metadata`, `.path`, `.status`, `.raw` |
| `LineAuthError` | Session-level auth failure — inner codes 1/7/8 (path-scoped, see below), HTTP 401/403, or a failed token refresh | same as `LineApiError` |
| `LineLoginRequired` (subclass of `LineAuthError`) | No credentials loaded at all — you called an API method before any login | same |
| `LineMustUpgradeError` (subclass of `LineApiError`) | Server demands a newer client (envelope 10006) | same |

All of `LineError`, `LineApiError`, `LineAuthError`, `LineConfigError` are re-exported from the package root: `from okline import LineApiError`.

## Reading an error

```python
from okline import OkLine, LineApiError
from okline import enums

api = OkLine.from_tokens_file("tokens.json")
try:
    api.send_text("u123", "hi")
except LineApiError as e:
    print(e.code, e.reason, e.path, e.status)
    print(enums.ErrorCode(e.code).name if e.code is not None else "?")
```

Set `LINE_DEBUG=1` in the environment to dump every response status/body to stderr, or after the fact call `api.save_log("debug.txt")` (secrets masked) and `api.last.pretty()`.

## Scope rule you must know before classifying

The auth classification (codes 1/7/8 → `LineAuthError`, code 119 → auto-refresh) applies ONLY on `/api/talk/thrift/Talk*` paths, excluding the `Talk/ChannelService` and `Talk/E2EEKeyBackupService` sub-services — the exact scope of the extension's interceptor. On Chat/Relation/Buddy/LoginQrCode paths the same numeric codes raise a plain `LineApiError`. HTTP 401/403 → `LineAuthError` is unscoped. When in doubt, read `e.code` yourself instead of trusting the class.

## TalkException inner codes (`.code` on `LineApiError`)

Auto-handled — you should never see these unless the internal fix fails:

| Code | Name | What happened / what the library does |
|---|---|---|
| 6 | `INVALID_LENGTH` | More than 100 mids in one `getContacts`/`getChats` call. Auto-chunked at 100 and merged — pass arbitrarily long lists. If you still see it, `pip install -U okline`. |
| 82 | `E2EE_RETRY_ENCRYPT` | Plain send into a Letter-Sealed chat ("can not send using plain mode"). Auto re-sealed and re-sent ONCE for text and location (media placeholders are never re-sealed — their bytes go through OBS). If it surfaces: `api.e2ee.is_ready()` was False — see ./messaging.md. |
| 115 | `SHOULD_RETRY` | Transient. Retried within `LineConfig(max_retries=2)`. If it surfaces, wait and retry once. |
| 119 | `MUST_REFRESH_V3_TOKEN` | Credential expiry. Auto: token renewed via the refresh hook and the request replayed once; refreshed tokens re-save to the session file. Surfaces as `LineAuthError` only when refresh itself fails — then follow the 10201/10202 rows below. |

E2EE family — all handled inside `api.e2ee.send_with_retry` (4 attempts = original + 3 transparent retries, mirroring the extension):

| Code | Name | Library behavior | If it still surfaces |
|---|---|---|---|
| 84 | `E2EE_UPDATE_RECEIVER_KEY` | Reset cached negotiation, re-negotiate, retry | Call `api.e2ee.reset_negotiation(to)`, retry your send once; if repeated, re-login to refresh the keychain |
| 86 | `E2EE_INVALID_VERSION` | same as 84 | Upgrade okline (protocol version bump), then re-login |
| 87 / 88 | sender / receiver `E2EE_DISABLED` | same as 84 | 88 = the peer disabled Letter Sealing; retry sends plain or ask the user |
| 90 | `E2EE_RECEIVER_NOT_ALLOWED` | same as 84 | Peer's primary device refuses sealed messages — surface to user |
| 99 | `E2EE_RECREATE_GROUP_KEY` | Reset, re-register the group key (`registerE2EEGroupKey`), retry in the same budget | Retry once; if repeated, re-login |
| 122 | `REFRESH_MEDIA_FLOW` | Reset negotiation and RE-RAISE immediately (no retry) — deliberate: the next send re-negotiates the media flow | Simply retry your send once |

Auth and abuse codes — these reach YOU:

| Code | Name | Class | Action |
|---|---|---|---|
| 1 | `AUTHENTICATION_FAILED` | `LineAuthError` | Session is dead. Re-login (`api.qr_login(...)` then `api.save_tokens("tokens.json")`). Never auto-loop; tell the user. |
| 7 | `NOT_AVAILABLE_USER` | `LineAuthError` | The account is gone (deleted/suspended). Re-login will not help — stop and surface to the user. |
| 8 | `NOT_AUTHORIZED_DEVICE` | `LineAuthError` | Device certificate revoked. Re-login required. |
| 4 | `EXCESSIVE_ACCESS` | `LineApiError` | SLOW DOWN. Install the RateLimiter (below), pause minutes, and ask the user before continuing. Ban signal. |
| 35 | `ABUSE_BLOCK` | `LineApiError` | Same as 4 but stronger — stop sending entirely, surface to the user. Ban risk is real. |
| 58 | `CONGESTION_CONTROL` | `LineApiError` | Server overloaded / anti-abuse. Back off, add the RateLimiter. |
| 0 | `ILLEGAL_ARGUMENT` | `LineApiError` | Your request was malformed (bad argument). NOT auth. Fix the call; do not retry. |
| 9 / 10 / 36 | `INVALID_MID` / `NOT_A_MEMBER` / `NOT_FRIEND` | `LineApiError` | Bad target: nonexistent mid, you left/were removed from the group, or recipient is not a friend. Fix the target; do not retry. |
| 11 | `INCOMPATIBLE_APP_VERSION` | `LineApiError` | Also the code the server uses to reject `send_flex` from personal accounts (flex is official-accounts-only — never use it). Otherwise: upgrade okline. |
| 28 | `NOT_YOUR_MESSAGE` | `LineApiError` | You can only `unsend_message` your own messages. |
| 64 | `MESSAGE_NOT_FOUND` | `LineApiError` | react/unsend target already gone. Do not retry. |

Full enum: `okline.enums.ErrorCode`.

## Gateway envelope codes (outer, the `qU` family)

The gateway wraps the real Thrift exception: `{"code": 10051, "message": "RESPONSE_ERROR", "data": {"name": "TalkException", "code": 82, ...}}`. OkLine surfaces the INNER TalkException code as `e.code` whenever one is present — so you normally classify on the tables above. The outer codes matter in these cases:

| Code | Name | Class raised | Action |
|---|---|---|---|
| 10004 | `REQUEST_NEED_LOGIN` | `LineApiError` | Seen on `/api/auth/tokenRefresh` when the access token is not attached. Internal detail; if you see it, upgrade okline. |
| 10005 | `REQUEST_INVALID_HMAC` | `LineApiError` | The X-Hmac signature is missing/bad. Almost always Node is unavailable or the bridge is broken — see the Node section below. |
| 10006 | `REQUEST_MUST_UPGRADE` | `LineMustUpgradeError` | LINE demands a newer client. `pip install -U okline`; if a new release does not exist yet, surface to the user and stop. Do not fight it. |
| 10051 | `RESPONSE_ERROR` | (inner code decides) | Generic wrapper; classify on `e.code` (the inner TalkException code). |
| 10052 | `RESPONSE_HTTP_ERROR` | inner `statusCode` becomes `e.code` AND `e.status` | Nested transport failure. Notable: `status=410` during QR/device-confirm login polls = PIN code timeout (see below). |
| 99999 | `UNKNOWN_ERROR` | `LineApiError` | Auto-retried within `max_retries`. If surfaced: wait, retry the call ONCE. |
| 10201 | `AUTH_INVALID_REQUEST` (on tokenRefresh) | `LineAuthError` ("token refresh rejected... re-login required") | Hard kickout — the refresh token is invalid (session revoked / logged out elsewhere). NOTHING recovers; re-login. |
| 10202 | `AUTH_RETRY_REQUIRED` (on tokenRefresh) | `LineAuthError` only after the server-provided retry budget is exhausted | Server temporarily refused the refresh. Wait several minutes, retry; consider proactive renewal so tokens never go stale. |

## HTTP statuses

- **401 / 403** → `LineAuthError`. On 401 the transport first tries the refresh hook and replays the request once (never on tokenRefresh itself — that would recurse); you only see the exception when refresh failed. Follow the 10201/10202 actions.
- **410** during a login poll (QR verify or the email/type-3 device-confirm PIN poll) → `LineAuthError("PIN code timeout ...", status=410)`. Terminal: the user took too long to enter the PIN. Restart the login flow from scratch — do not re-poll.
- **408** during a long-poll is normal (poll window elapsed); the library keeps waiting.
- **5xx / network errors** → `LineTransportError` after `max_retries` internal attempts. Pause a few seconds, retry once; if it persists, surface to the user.

## Node / X-Hmac failures (HmacSignerError and code 10005)

Every gateway request must carry `X-Hmac`, computed by Node 18+ running LINE's `ltsm.wasm`. Two distinct failures:

1. `HmacSignerError("Node.js not found (...)")` — raised locally on the FIRST api call (the bridge starts lazily): no `node` on PATH. Fix: install Node 18+, or point at a binary via the `LINE_NODE` env var or `OkLine(config=LineConfig(node_path="/path/to/node"))`.
2. `LineApiError` code 10005 `REQUEST_INVALID_HMAC` — the bridge ran but the server rejected the signature. Fix: `pip install -U okline` (bad signature version); check that Node is ≥18 (`node --version`).

`LineConfig(enable_hmac=False)` exists ONLY for offline mocked tests — the real server rejects every unsigned request.

## Rate limiting recipe

On code 4, 35, or 58 — or preemptively before any bulk job — install the token-bucket limiter. It blocks transparently inside the transport:

```python
from okline import OkLine, RateLimiter

api = OkLine.from_tokens_file("tokens.json")
api.transport.rate_limiter = RateLimiter(rate=3, per=1.0)  # ~3 requests/sec, burst 3
api.send_text("u123", "hi")  # acquires a token first
```

Use `rate=3` for sustained background work; `rate=5` is the practical ceiling. Never run tight loops of sends even with the limiter — this is the user's personal account (ToS/ban risk). See the safety rules in ../SKILL.md.

## Decision tree — on exception X, do Y

```text
LineLoginRequired
  -> no session loaded: run a login flow (see ./session.md), save tokens, retry the call once.
LineAuthError
  -> code 7 (NOT_AVAILABLE_USER): account gone. Stop. Surface to user.
  -> status == 410 during login: PIN timed out. Restart the login flow.
  -> otherwise (codes 1/8, refresh kickout 10201, exhausted 10202):
     session is dead. Tell the user, qr_login + save_tokens. Never auto-retry in a loop.
LineMustUpgradeError
  -> pip install -U okline; if no fix exists, surface to user and stop.
HmacSignerError or LineApiError code 10005
  -> fix Node (install / LINE_NODE / node_path), then retry the call.
LineApiError code in {4, 35, 58}
  -> STOP sending. Install RateLimiter, wait minutes, ask the user before continuing.
LineApiError code in {0, 6, 9, 10, 11, 28, 36, 64}
  -> your request is wrong (bad target/argument/expectation). Fix it; do not retry.
LineApiError code 82
  -> E2EE not ready (is_ready() False): re-login once to capture the keychain (./session.md).
LineApiError code 122
  -> retry the same send once (the library already reset the stale media flow).
LineApiError code 115 or 99999, or LineTransportError
  -> transient, internal retries exhausted: pause a few seconds, retry ONCE, then surface.
Anything else (LineApiError with an unmapped code)
  -> print e.code + enums.ErrorCode(e.code).name, read e.reason, decide; check docs/troubleshooting.md in the repo.
```

See ./messaging.md for the send paths that auto-handle 82 and the full sealed-send retry semantics, and ./media.md for sealed-media download.

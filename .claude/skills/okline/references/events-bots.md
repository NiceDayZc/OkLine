# Live events, bots, and long-running watchers

How to receive LINE operations in real time: `api.ops.iter_operations()` (high-level), `api.ops.stream()` (raw SSE), the `Bot` framework, and background watcher processes you can start, poll, and stop. Setup (tokens, Node requirement, `OkLine.from_tokens_file`) is in `./session.md`; send calls used by `ctx.reply(...)` are in `./messaging.md`; error codes in `./errors.md`.

## The two event sources

`OkLine` exposes `api.ops` (an `OperationReceiver`). It has two streaming methods:

- `api.ops.iter_operations(reconnect=True, keepalive=False)` — yields `Operation` objects (`.type`, `.message`, `.param1/2/3`, `.revision`, `.raw`). This is what you want 95% of the time.
- `api.ops.stream(reconnect=True, keepalive=False)` — yields raw `SSEEvent` objects (`.event`, `.data`, `.id`) when you need the SSE control events themselves.

Both are infinite generators that follow the `localRev` resume cursor:

- On the first open the cursor is seeded from `getLastOpRevision`, so a fresh stream delivers **only new operations** — no backlog replay. To resume from an earlier point, set `api.ops.local_rev = <int>` before the first call; to persist a resume point across watcher restarts, read `api.ops.local_rev` when stopping and save it. Re-delivered (at-or-below-cursor) operations are dropped automatically, so a resume neither gaps nor duplicates.
- `api.get_last_op_revision()` calls the same endpoint on demand if you want the current cursor without opening a stream.
- `reconnect=True` (default) reopens on disconnect with exponential backoff (`min(2**n * 1s, 60s)`, reset after any productive connection) and **never gives up** — an unattended `for op in api.ops.iter_operations(): ...` hangs forever by design. See "Stopping a stream" below.
- `keepalive=True` starts a daemon thread pinging `getServerTime` every ~20 s and arms a 30 s silence watchdog (per-read socket timeout) so a silently dead connection (NAT/middlebox timeout, no FIN) is detected and reopened. Use it for any watcher meant to run for hours. Caveat: the pings are real requests sharing the transport and consuming rate-limiter tokens.
- Tune backoff via attributes: `api.ops.backoff_start = 0.0` (immediate reconnects), `api.ops.backoff_max = 600.0` (extension parity).

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
try:
    for op in api.ops.iter_operations(reconnect=True, keepalive=True):
        print(op.type, op.revision, (op.message or {}).get("text"))
finally:
    api.close()
```

## Raw SSE and control events

`stream()` yields named SSE events. Control events: `ping`, `reconnect`, `connInfoRevision`, `talkException`, `fullSync`, `partialFullSync`. The default unnamed events arrive as `event == "message"` with `.data` being a list of operation dicts (or a single dict). `iter_operations()` already skips the control events and tracks `nextRevision` from `fullSync`/`partialFullSync` — only use `stream()` when you need to observe those yourself (e.g. reacting to `talkException`).

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
try:
    for ev in api.ops.stream(reconnect=True, keepalive=True):
        if ev.event in ("ping", "reconnect", "connInfoRevision",
                        "fullSync", "partialFullSync"):
            continue  # transport bookkeeping; resume cursor is auto-tracked
        if ev.event == "talkException":
            print("server exception event:", ev.data)
            continue
        # ev.event == "message": ev.data is a batch of operation dicts
        for o in (ev.data if isinstance(ev.data, list) else [ev.data]):
            print(o.get("type"), (o.get("message") or {}).get("text"))
finally:
    api.close()
```

## Stopping a stream from an agent script (read this before writing any watcher)

An agent-run script must bound its own runtime — but the obvious patterns fail:

- **A plain `TimeoutError` (or any `Exception`) raised while the stream is blocked is swallowed.** `stream(reconnect=True)` wraps the event loop in `except Exception`, logs "SSE stream error", and reconnects. Your script never exits; it just loops.
- Therefore the stop signal must be a **custom class deriving from `BaseException`** (not `Exception`), which passes through that clause. Combine with `signal.alarm` to raise it after a wall-clock budget (Unix only — no `signal.alarm` on Windows; there, run the watcher as a background process and kill it instead).

```python
import signal

from okline import OkLine


class StopWatch(BaseException):
    """Must derive from BaseException: stream(reconnect=True) catches
    Exception around the event loop and treats it as a mere disconnect,
    so a TimeoutError would be swallowed and the stream would reconnect."""


def _stop(signum, frame):
    raise StopWatch()


api = OkLine.from_tokens_file("tokens.json")
signal.signal(signal.SIGALRM, _stop)
signal.alarm(60)  # watch for 60 seconds, then stop
try:
    for op in api.ops.iter_operations(reconnect=True, keepalive=True):
        if op.message:
            print(op.message.get("from"), op.message.get("text"))
except StopWatch:
    print("done; resume cursor:", api.ops.local_rev)
finally:
    signal.alarm(0)  # cancel any pending alarm
    api.close()
```

Do not rely on the 30 s keepalive read timeout to end the loop — that timeout feeds the reconnect loop, it does not propagate to you.

## Bot framework

`okline.Bot` (source: `okline/bot.py`) wraps `iter_operations` and dispatches to registered handlers. Handler exceptions are caught and logged (`logging.getLogger("okline.bot")`) — one bad handler never kills the loop, but that also means failures are invisible unless logging is on: call `logging.basicConfig(level=logging.INFO)`.

```python
import logging

from okline import Bot, OkLine
from okline.enums import OpType

logging.basicConfig(level=logging.INFO)

api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api, ignore_self=True, auto_mark_read=False)

@bot.on_message                 # every incoming message
def handle(ctx):
    print(ctx.sender, "->", ctx.text)
    ctx.reply(f"you said: {ctx.text}")

@bot.command("ping")            # text starting with "/ping" (command_prefix)
def ping(ctx):
    ctx.reply("pong")

@bot.on(OpType.NOTIFIED_READ_MESSAGE, OpType.NOTIFIED_INVITE_INTO_GROUP)
def events(ctx):                # non-message operations
    print("op", ctx.type, ctx.op.param1, ctx.op.param2)

bot.run(reconnect=True, keepalive=True)  # blocks; Ctrl-C to stop
api.close()
```

Constructor flags and dispatch rules:

- `ignore_self=True` (default) — skips operations whose message `from` equals your own mid (your sends echoed back). Leave it on or every `ctx.reply` triggers your handler again.
- `auto_mark_read=False` (default) — set `True` to send read receipts (`ctx.mark_read()` calls `api.send_chat_checked`). This tells the other party you read the message; only enable when the user wants that.
- `command_prefix = "/"` — a message whose text starts with the prefix routes to the matching `@bot.command("name")` handler and returns; otherwise all `on_message` handlers run in registration order.
- `ctx` (`MessageContext`) fields: `ctx.text` (plaintext — sealed messages are auto-decrypted when `api.e2ee.is_ready()` and `chunks` is present), `ctx.sender`, `ctx.to`, `ctx.content_type`, `ctx.is_group`, `ctx.reply_target` (the group/room if the message is in one, else the sender), `ctx.reply(text)`, `ctx.reply_sticker(package_id, sticker_id)`, `ctx.mark_read()`, `ctx.op`, `ctx.api`. For `@bot.on(OpType.X)` handlers the context is an `EventContext`: only `ctx.op`, `ctx.type`, `ctx.api`, `ctx.bot` — no message fields.

Common OpType values (full list: `okline.enums.OpType`): `SEND_MESSAGE=25`, `RECEIVE_MESSAGE=26` (the only type that reaches `on_message`), `NOTIFIED_INVITE_INTO_GROUP=13`, `NOTIFIED_LEAVE_GROUP=15`, `NOTIFIED_KICKOUT_FROM_GROUP=19`, `NOTIFIED_READ_MESSAGE=55`.

### Auto-reply pattern

Mirrors `examples/autoreply.py` and `python3 -m okline autoreply --rule kw=reply`:

```python
import logging

from okline import Bot, OkLine

logging.basicConfig(level=logging.INFO)

RULES = {"hello": "hi there!", "ping": "pong"}

api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api)

@bot.on_message
def autoreply(ctx):
    if not ctx.text:
        return
    for kw, reply in RULES.items():
        if kw in ctx.text.lower():
            ctx.reply(reply)
            return

bot.run(keepalive=True)
```

**Safety: an auto-reply bot sends messages to whoever writes first — i.e. third parties. Only run one against rules and recipients the user explicitly asked for**, and throttle: install `api.transport.rate_limiter = RateLimiter(rate=3, per=1.0)` (from `okline`) before `bot.run` so a keyword storm cannot turn into a send storm.

### Keyword-notify pattern

Mirrors `examples/notify.py` / `python3 -m okline notify --keyword urgent` — read-only, so it is the safest thing to run unattended:

```python
import logging

from okline import Bot, OkLine

logging.basicConfig(level=logging.INFO)

KEYWORD, GROUP_ONLY = "urgent", False

api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api)

@bot.on_message
def alert(ctx):
    if GROUP_ONLY and not ctx.is_group:
        return
    if KEYWORD and KEYWORD.lower() not in (ctx.text or "").lower():
        return
    kind = "group" if ctx.is_group else "dm"
    print(f"[{kind}] {ctx.sender}: {ctx.text or '<non-text>'}")

bot.run(keepalive=True)
```

## Background watcher (start, poll, stop)

For anything longer than a couple of minutes, do not hold the stream open in your own tool call. Run a self-contained watcher as a background process that appends JSONL to a log file; poll the log; stop the process by PID. Never log token values — the script below logs only message metadata. Save it as e.g. `watch_bg.py` next to `tokens.json`:

```python
#!/usr/bin/env python3
"""okline background watcher — append incoming messages to a JSONL log.

Start:  nohup python3 watch_bg.py >/dev/null 2>&1 & echo $! > /tmp/okwatch.pid
Poll:   tail -n 50 ~/okline-watch.log
Stop:   kill $(cat /tmp/okwatch.pid)
"""
import json
import os
import signal
import time

from okline import OkLine
from okline.enums import OpType

LOG = os.path.expanduser("~/okline-watch.log")


class StopWatch(BaseException):
    """BaseException so stream()'s `except Exception` reconnect loop
    cannot swallow it (SIGTERM/SIGINT must actually stop this process)."""


def _stop(signum, frame):
    raise StopWatch()


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

api = OkLine.from_tokens_file("tokens.json")
try:
    with open(LOG, "a", buffering=1) as log:  # line-buffered: poll-friendly
        for op in api.ops.iter_operations(reconnect=True, keepalive=True):
            if op.type != OpType.RECEIVE_MESSAGE or not op.message:
                continue
            msg = op.message
            try:
                msg = api.decrypt_message(msg)  # safe on plain messages too
            except Exception:
                pass  # keep the raw message; decryption failures are per-msg
            log.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "chat": msg.get("to"),
                "sender": msg.get("from"),
                "text": msg.get("text") or f"<contentType {msg.get('contentType', 0)}>",
            }, ensure_ascii=False) + "\n")
except StopWatch:
    pass
finally:
    with open(LOG, "a") as log:  # resume cursor for a gap-free restart
        log.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "resume_local_rev": api.ops.local_rev}) + "\n")
    api.close()
```

Operating it (the agent-side loop):

```bash
# start (from the directory containing tokens.json and watch_bg.py)
nohup python3 watch_bg.py >/dev/null 2>&1 & echo $! > /tmp/okwatch.pid
# poll new messages
tail -n 50 ~/okline-watch.log
# stop
kill $(cat /tmp/okwatch.pid)
```

To resume without gaps or duplicates after a stop, seed the cursor before the first stream open: read the last `resume_local_rev` line from the log and set `api.ops.local_rev` to it near the top of the script (after `from_tokens_file`, before the loop).

## Service notices and misc

- `api.ops.lan_notice(lang, country, next_seq=None)` — `GET /api/lan/notice`, localized service notices/banners. Returns `{"documents": [...], "nextSeq": N}`; keep calling with the returned `nextSeq` until it is absent/`None`:

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
page, docs = api.ops.lan_notice("en", "US"), []
docs += page.get("documents", [])
while page.get("nextSeq") is not None:
    page = api.ops.lan_notice("en", "US", page["nextSeq"])
    docs += page.get("documents", [])
api.close()
```
- `api.ops.request_reconnect()` — tears down the active stream so a `reconnect=True` loop reopens it with fresh credentials. You normally never call this: `OkLine` itself calls it after a background token renewal. Know it exists when debugging "stream went quiet after hours".
- `api.ops.long_poll(session_id, endpoint="LF1"|"JQ", timeout_ms=...)` — the login device-confirm long-polls, not a general events fallback. Do not use it for watching; use SSE.

## Failure modes

- Every request fails with `LineApiError` code **10005 REQUEST_INVALID_HMAC** — Node.js 18+ missing (the X-Hmac signer needs it). Fix the environment, not the code (`LINE_NODE` overrides the binary). See `./session.md`.
- `LineAuthError` codes **{1, 7, 8}** — session is dead (logged out elsewhere / account gone). Stop the watcher; the user must re-login. Code **10004 / HTTP 401** mid-stream: the token was auto-refreshed; the stream is reopened for you — if it stays quiet, `request_reconnect()` or restart the watcher. Code **119** is handled internally.
- `LineApiError` **EXCESSIVE_ACCESS (4) / ABUSE_BLOCK (35)** — the server is rate-limiting you. Stop, install `RateLimiter`, slow the bot down; a reply-per-message is fine, tight reply loops are not.
- Watcher "stops logging" — three distinct causes: (1) connection silently died → fixed by `keepalive=True`; (2) reconnect loop retrying with backoff (check stderr/log you redirected) → transient, it comes back; (3) auth death (codes above) → it will not come back, re-login.
- Handler errors vanish — `Bot._safe` catches `Exception` and logs via the `okline.bot` logger. `logging.basicConfig(level=logging.INFO)` before `bot.run` to see them.
- Script won't exit despite a timeout — you raised an `Exception` subclass (see "Stopping a stream"); derive from `BaseException`.
- `signal.alarm` does not exist on Windows — use the background-process pattern there.

More patterns: `docs/receiving-events.md`, `docs/bots.md`, `examples/watch.py`, `examples/autoreply.py`, `examples/notify.py` in the repo.

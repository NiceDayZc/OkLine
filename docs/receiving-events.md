# Receiving events

[← docs home](./index.md)

> **Most bots should use the [Bot framework](./bots.md) instead of this page.**
> The `Bot` class wraps the stream below, auto-decrypts encrypted messages, and
> gives you a tidy `ctx.reply(...)`. Read this page when you want the raw event
> stream, or to handle operation types the bot framework doesn't model.

Incoming activity — new messages, invitations, read receipts, reactions, and so
on — arrives as a stream of **operations**. OkLine exposes it through `api.ops`
([`okline/operations.py`](../okline/operations.py)) over the modern
Server-Sent-Events (SSE) transport, with automatic reconnect.

## Iterate over operations

`iter_operations()` blocks, yields one `Operation` at a time, and reconnects on
its own if the stream drops:

```python
from okline import OkLine, enums

api = OkLine.from_tokens_file("tokens.json")

for op in api.ops.iter_operations():  # blocks forever; Ctrl-C to stop
    if op.type == enums.OpType.RECEIVE_MESSAGE and op.message:
        msg = op.message
        sender = msg.get("from")
        text = msg.get("text")
        print(f"[{sender}] {text!r}")
        if text:
            api.send_text(sender, f"you said: {text}")
```

### The `localRev` resume cursor

The SSE connect carries the extension's full query set — `version=3.7.2`,
`localRev`, `language` (the X-LAL form of your locale),
`lastPartialFullSyncs` (JSON), `fullSyncRequestReason` (on the connect that
asks for a full sync) and `legyHost` when configured
(`LineConfig(legy_host=...)`, which also adds the `X-Legy-Host` header to
gateway requests). `localRev` is the resume cursor:

- it is seeded from `getLastOpRevision` before the first open (or from
  `OperationReceiver(local_rev=...)` if you want to resume a saved position),
- updated from every received operation's `revision` and from
  `fullSync`/`partialFullSync` events' `nextRevision`,
- re-sent on every reconnect, and stale re-delivered operations (revision
  <= the cursor) are dropped.

So a dropped stream resumes exactly where it left off instead of re-opening
blind. You can read the current cursor at any time via `api.ops.local_rev`.

> **Note:** the extension authenticates its EventSource with session cookies;
> Python has no session cookie, so OkLine sends header auth (`X-Line-Access` +
> `X-Hmac`) on the SSE request instead — a documented, live-verified
> deviation.

Each `Operation` has these fields:

| Field | Meaning |
|-------|---------|
| `type` | an `OpType` integer (see the table below) |
| `revision` | the sync cursor for this op |
| `param1` / `param2` / `param3` | operation-specific values (mids, flags, …) |
| `message` | the message dict, present on message ops |
| `reqSeq`, `checksum` | request metadata |
| `raw` | the original operation dict, untouched |

> **Note:** the `message` on a `RECEIVE_MESSAGE` op may be **encrypted** (its
> text is empty and the ciphertext is in `message["chunks"]`). The raw stream
> does **not** decrypt for you — call `api.decrypt_message(op.message)`, or just
> use the [Bot framework](./bots.md), which decrypts automatically.

## Raw SSE events

For control events (keep-alives, re-sync notices) drop down to `stream()`, which
yields `SSEEvent(event, data, id)`:

```python
for ev in api.ops.stream():
    if ev.event == "ping":
        continue  # keep-alive
    if ev.event in ("fullSync", "partialFullSync"):
        ...  # the server wants you to re-sync
    else:
        ...  # default events carry operations
```

Named events you may see: `ping`, `connInfoRevision`, `reconnect`,
`talkException`, `fullSync`, `partialFullSync`. (`iter_operations()` already
skips `ping`, `reconnect`, and `connInfoRevision` for you.)

## Common `OpType` values

From `okline.enums.OpType`:

| Value | Name | Meaning |
|------:|------|---------|
| 25 | `SEND_MESSAGE` | a message you sent (echoed back) |
| 26 | `RECEIVE_MESSAGE` | someone sent you a message |
| 55 | `NOTIFIED_READ_MESSAGE` | a message you sent was read |
| 5 | `NOTIFIED_ADD_CONTACT` | someone added you as a contact |
| 122 | `NOTIFIED_UPDATE_CHAT` | a chat's settings changed |
| 124 | `NOTIFIED_INVITE_INTO_CHAT` | you were invited to a chat |
| 130 | `NOTIFIED_ACCEPT_CHAT_INVITATION` | someone joined a chat |
| 140 | `NOTIFIED_SEND_REACTION` | someone reacted to a message |

The full list (~150 values) is in [`okline/enums.py`](../okline/enums.py).

## Disabling auto-reconnect

Pass `reconnect=False` to stop after the first disconnect (useful in tests or
short-lived scripts):

```python
for op in api.ops.iter_operations(reconnect=False):
    handle(op)
```

## Reconnect backoff

Consecutive *failed* connections (an open error, or a connection that yields no
events at all) back off exponentially before the next attempt:
`min(2**n * backoff_start, backoff_max)` seconds — 1 s, 2 s, 4 s, … capped at
60 s by default (the extension caps at 600 s; pass
`OperationReceiver(..., backoff_max=600)` for exact parity). A connection that
yielded at least one event resets the counter, so a stream that *was* healthy
reconnects immediately. Tune or disable on the receiver:

```python
from okline.operations import OperationReceiver

api.ops = OperationReceiver(api.transport, backoff_start=1.0, backoff_max=60.0)
# backoff_start=0 restores immediate reconnects (pre-2.9 behaviour)
```

> **Deviation:** the extension gives up after 144 consecutive failed attempts;
> OkLine keeps retrying forever with the capped delay.

## Keepalive pings (`keepalive=True`)

The extension wires a ping interceptor onto its SSE transport (a 20 s interval
with a 10 s spare window). Python `requests` has no EventSource ping frame, so
the port runs a daemon thread that issues a cheap `getServerTime` call every
~20 s while the stream is active. It is **opt-in** — the pings are real
requests that share the HTTP session and the rate-limiter budget:

```python
for op in api.ops.iter_operations(keepalive=True):
    handle(op)

# or on the Bot framework
bot.run(keepalive=True)
```

The thread starts with the first event, survives reconnects, is stopped when
the generator is closed or exhausted, and never kills the stream on a failed
ping.

## Mid-stream token renewal

If you enabled the [proactive token-renewal schedule](./authentication.md#proactive-renewal-schedule-auto_refresh_schedule),
a successful background renewal tears the active SSE connection down and
reopens it with the fresh token (`OperationReceiver.request_reconnect()` —
the extension's `tT.renewToken` does `readyState === OPENED && connect()`).
You never need to call it yourself unless you refresh the token by other means
mid-stream; it is a no-op when no reconnecting stream is active.

## Long-poll utility endpoints

The `LF1`/`JQ` long-poll endpoints are **login PIN-verification** polls in the
extension (`checkPinCodeVerifiedForEmailWithE2EE` / `checkPinCodeVerifiedForEmail`),
not an operation-receive fallback — OkLine's email-login device-confirm flow uses
them. They remain available as a generic blocking round-trip:

```python
api.get_last_op_revision()  # current sync cursor
api.ops.long_poll(session_id, endpoint="LF1")  # one blocking round-trip
```

## Service notices (lan/notice)

Localized service notices/banners are paged off the `lan.notice` endpoint:

```python
page = api.ops.lan_notice(lang="en", country="JP")  # -> {documents, nextSeq}
```

> **Tip:** combine receiving with [recording](./recording.md) — every reply you
> send is captured too, so you can replay a whole session.

---

**Next:** [Building bots](./bots.md) · [Sending messages](./messaging.md)

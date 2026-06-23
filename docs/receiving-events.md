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

for op in api.ops.iter_operations():            # blocks forever; Ctrl-C to stop
    if op.type == enums.OpType.RECEIVE_MESSAGE and op.message:
        msg = op.message
        sender = msg.get("from")
        text = msg.get("text")
        print(f"[{sender}] {text!r}")
        if text:
            api.send_text(sender, f"you said: {text}")
```

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
        continue                       # keep-alive
    if ev.event in ("fullSync", "partialFullSync"):
        ...                            # the server wants you to re-sync
    else:
        ...                            # default events carry operations
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

## Long-poll fallback

The classic long-poll endpoints are still available if you need them:

```python
api.get_last_op_revision()                       # current sync cursor
api.ops.long_poll(session_id, endpoint="LF1")    # one blocking round-trip
```

> **Tip:** combine receiving with [recording](./recording.md) — every reply you
> send is captured too, so you can replay a whole session.

---

**Next:** [Building bots](./bots.md) · [Sending messages](./messaging.md)

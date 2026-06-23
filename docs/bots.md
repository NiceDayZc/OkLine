# Building bots

[← docs home](./index.md)

OkLine ships a small, batteries-included **Bot framework**. It streams the
operation feed for you, decrypts end-to-end-encrypted messages transparently,
routing each event to handlers you register with decorators — and gives message
handlers a one-line `ctx.reply(...)`.

If you only need the raw event stream, see
[Receiving events](./receiving-events.md). Otherwise, start here.

## A complete echo bot

```python
from okline import OkLine, Bot

api = OkLine.from_tokens_file("tokens.json")   # log in once, reuse forever
bot = Bot(api)

@bot.on_message
def echo(ctx):
    if ctx.text:
        ctx.reply(f"you said: {ctx.text}")

bot.run()                                      # blocks; Ctrl-C to stop
```

That's a working bot. Run it, then message your account from another phone — it
replies in the same conversation (DMs go back to the sender; group messages reply
in the group).

Don't have a `tokens.json` yet? Run `okline login` once (see
[Authentication](./authentication.md)), or call `api.auth.qr_login(...)` and
`api.save_tokens("tokens.json")`.

## Handlers

Register handlers with decorators. All are optional and you can mix them.

| Decorator | Fires on | Context |
|-----------|----------|---------|
| `@bot.on_message` | every incoming text/message | `MessageContext` |
| `@bot.command("name")` | a message starting with `/name` | `MessageContext` |
| `@bot.on(OpType.X, ...)` | one or more operation types | `EventContext` |

```python
from okline import enums

@bot.command("ping")          # triggers on a message like "/ping"
def ping(ctx):
    ctx.reply("pong")

@bot.on(enums.OpType.NOTIFIED_INVITE_INTO_CHAT)
def on_invited(ctx):
    print("invited into", ctx.op.param1, "by", ctx.op.param2)
```

The command prefix defaults to `/`; change it with `bot.command_prefix = "!"`.
If a message matches a registered command, that command runs *instead of* the
general `@bot.on_message` handlers.

## `MessageContext`

Message handlers receive a `MessageContext` (`ctx` above):

| Member | Meaning |
|--------|---------|
| `ctx.text` | the message text. **Auto-decrypted** for E2EE messages |
| `ctx.sender` | mid of who sent it (the `from` field) |
| `ctx.to` | the conversation mid (a person, group, room, or square) |
| `ctx.is_group` | `True` for a group/room/square, `False` for a 1:1 DM |
| `ctx.content_type` | the message's content type (0 = text) |
| `ctx.message` | the raw message dict |
| `ctx.reply(text)` | reply in the same conversation |
| `ctx.reply_sticker(pkg, id)` | reply with a sticker |
| `ctx.mark_read()` | send a read receipt for this message |

`ctx.reply()` picks the right destination automatically: in a group it replies
to the group, in a DM it replies to the sender.

## A keyword auto-reply bot

A slightly bigger example: greet on a keyword, only in DMs, and answer a
`/help` command.

```python
from okline import OkLine, Bot

api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api)

RULES = {
    "hello": "Hi there! 👋",
    "price": "DM me 'buy' to get started.",
    "hours": "We're open 9-5, Mon-Fri.",
}

@bot.on_message
def auto_reply(ctx):
    if not ctx.text:
        return                      # skip stickers, images, etc.
    key = ctx.text.strip().lower()
    if key in RULES:
        ctx.reply(RULES[key])

@bot.command("help")
def help_cmd(ctx):
    ctx.reply("Try saying: " + ", ".join(RULES))

bot.run()
```

## Bot options

```python
bot = Bot(api, ignore_self=True, auto_mark_read=False)
```

- `ignore_self` (default `True`) — don't dispatch messages your own account
  sent, so the bot never replies to itself.
- `auto_mark_read` (default `False`) — send a read receipt for every incoming
  message before your handlers run.

Handler exceptions are caught and logged, so one bad handler never kills the
loop. `bot.run()` blocks and reconnects automatically; pass
`bot.run(reconnect=False)` to stop after a disconnect.

## Under the hood

`Bot` is a thin layer over `api.ops.iter_operations()` (see
[Receiving events](./receiving-events.md)). When a message arrives encrypted,
the bot calls `api.decrypt_message(...)` for you — which needs E2EE keys loaded
for this session. See [End-to-end encryption](./e2ee.md) for how those keys are
restored from your session file.

---

**Next:** [Sending messages](./messaging.md) · [Recording](./recording.md)

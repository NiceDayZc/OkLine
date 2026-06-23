# Cookbook

[← docs home](./index.md)

Short, copy-pasteable recipes. Most have a CLI form and a Python form — use
whichever fits. They all assume you have logged in once (`okline login`) so a
session exists at `./tokens.json`; the Python snippets load it with
`OkLine.from_tokens_file("tokens.json")`.

## Log in once and reuse

Log in by QR a single time; every later command and script reuses the saved
session (E2EE keys included) automatically.

```bash
okline login          # scan the QR, confirm the PIN -> writes ./tokens.json
okline whoami         # reuses the session, no re-scan
```

```python
from okline import OkLine
api = OkLine.from_tokens_file("tokens.json")   # instant; refreshes token if needed
print(api.get_profile())
```

## Send a photo to a group

```bash
okline send c0123...group --image party.jpg
```

```python
api.send_image("c0123...group", "party.jpg")
```

See [media](./media.md) for video/audio/file and the details.

## Export contacts to CSV

The built-in command writes a two-column (`mid`, `name`) CSV:

```bash
okline contacts --csv contacts.csv
okline contacts --json contacts.json
okline contacts --search alice          # filter by name, print to screen
```

For a richer export (display name, status message, official flag), use the
bundled example script:

```bash
python examples/export_contacts.py -o friends.csv
python examples/export_contacts.py -o friends.json --format json
```

## Auto-reply to a keyword

```bash
okline autoreply --rule "hello=hi there!" --rule "bye=see ya" --ignore-case
```

Or in Python with the bot framework:

```python
from okline import OkLine, Bot
api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api)

@bot.on_message
def reply(ctx):
    if ctx.text and "hello" in ctx.text.lower():
        ctx.reply("hi there!")

bot.run()   # blocks; Ctrl-C to stop
```

## Read and decrypt a chat

`okline chatlog` prints recent messages and decrypts Letter-Sealed ones inline:

```bash
okline chatlog u0123...friend -n 50
```

In Python, `decrypt_message` returns the plaintext (and leaves plain messages
untouched):

```python
for m in api.get_recent_messages("u0123...friend", 50):
    print(api.decrypt_message(m).get("text"))
```

See [Letter Sealing](./e2ee.md) for how the keys load and persist.

## Back up a chat to JSON

Pages backwards through history and dumps the raw messages to a file:

```bash
okline backup c0123...group -n 500 -o group.json
```

```python
import json
msgs = api.get_recent_messages("c0123...group", 200)
json.dump(msgs, open("group.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
```

## Broadcast to several chats

Send the same text to many targets, rate-limited, with a confirmation prompt
(add `--yes` to skip it):

```bash
okline broadcast "Meeting moved to 3pm" --to c0111... c0222... u0333... --rate 3 --yes
```

```python
from okline.ratelimit import RateLimiter
api.transport.rate_limiter = RateLimiter(rate=3, per=1.0)   # ~3 msgs/sec
for mid in ["c0111...", "c0222...", "u0333..."]:
    api.send_text(mid, "Meeting moved to 3pm")
```

> Use broadcast responsibly — only message chats and people who expect it. LINE
> will block accounts for spam (`EXCESSIVE_ACCESS` / `ABUSE_BLOCK`).

## See also

- [CLI reference](./cli.md)
- [Sending messages](./messaging.md) and [media](./media.md)
- [Building bots](./bots.md)

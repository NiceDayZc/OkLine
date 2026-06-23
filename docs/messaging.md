# Sending messages

[← docs home](./index.md)

This page covers everything you can **send**: text, replies, stickers, locations,
contacts, flex bubbles, and media (images, video, audio, files). It also covers
**reactions** and **unsend**. For receiving messages, see
[Receiving events](./receiving-events.md) and [Building bots](./bots.md).

Every send takes the destination **mid** as its first argument.

## mids: who you're sending to

A **mid** is a 33-character ID. Its first letter tells OkLine what kind of
conversation it is, so you never set the type by hand. Matching is
**case-insensitive**.

| Prefix | Type | Example mid |
|--------|------|-------------|
| `U` | a person (1:1 chat) | `U0123456789abcdef0123456789abcdef` |
| `C` | a group | `C0123456789abcdef0123456789abcdef` |
| `R` | a room | `R0123456789abcdef0123456789abcdef` |
| `S` | a square (open) chat | `S0123456789abcdef0123456789abcdef` |

You get mids from `api.get_profile()` (your own), `api.get_contacts(...)`,
`api.get_groups(...)`, or from incoming events. From the CLI, `okline contacts`
and `okline groups` list them.

## The quick senders

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")   # see Authentication
to = "U0123456789abcdef0123456789abcdef"

# plain text
api.send_text(to, "hello from python")

# reply to a specific message (quotes it in the chat)
api.reply_text(to, "got it!", related_message_id="14000000000000050")

# a sticker (package id + sticker id)
api.send_sticker(to, package_id="11537", sticker_id="52002734")

# a location pin
api.send_location(to, 35.6586, 139.7454,
                  title="Tokyo Tower", address="Minato, Tokyo")

# share a contact
api.send_contact(to, contact_mid="U....", display_name="Alice")

# a Flex bubble (LINE's rich message format)
api.send_flex(to, "Hello Flex!", {
    "type": "bubble",
    "body": {
        "type": "box", "layout": "vertical",
        "contents": [{"type": "text", "text": "Hello Flex!"}],
    },
})
```

Each call returns the server's response — the persisted message dict, which
includes its real `id` (you'll need that `id` to react or unsend later):

```python
sent = api.send_text(to, "hi")
message_id = sent["id"]
print("sent as", message_id)
```

> **Tip:** from the CLI you can also pass a **contact's display name** and OkLine
> resolves it to a mid: `okline send Alice "hello"`. See [CLI](./cli.md).

## Sending media (image, video, audio, file)

Media is uploaded to LINE's object store (OBS) and then posted as a message that
references it — OkLine does both steps for you. Pass a **file path** or raw
**bytes**:

```python
api.send_image(to, "photo.jpg")
api.send_video(to, "clip.mp4", duration_ms=8000)
api.send_audio(to, "voice.m4a", duration_ms=3000)
api.send_file(to,  "report.pdf")

# bytes also work; give a name so the recipient sees a sensible filename
with open("photo.jpg", "rb") as fh:
    api.send_image(to, fh.read(), name="vacation.jpg")
```

Notes:

- `duration_ms` (video/audio) is the clip length in milliseconds; it's optional
  and only affects how the player shows the length.
- `name` defaults to the file's basename (or `image.jpg` / `video.mp4` /
  `audio.m4a` / `file.bin` when you pass bytes).
- This is the **V1 (non-encrypted) upload flow**. For end-to-end encrypted
  chats, see encrypted send below.

## Encrypted (Letter Sealing) send

To send into an end-to-end-encrypted chat, use:

```python
api.send_encrypted_text(to, "this is sealed")
```

This works for both 1:1 and group chats, and OkLine also **auto-seals**
automatically when the server says a chat requires encryption. The full E2EE
story (loading keys, decrypting, group sealing) lives on its own page:
→ [End-to-end encryption](./e2ee.md).

## Reactions and unsend

React to a message by its `id` (one of six predefined reactions):

```python
from okline import enums

api.react("14000000000000050", enums.PredefinedReactionType.LOVE)
# choices: NICE (2), LOVE (3), FUN (4), AMAZING (5), SAD (6), OMG (7)

api.cancel_reaction("14000000000000050")   # remove your reaction
```

The `reaction` argument accepts the enum (as above) or its plain integer, e.g.
`api.react(mid_id, 3)`.

Recall a message **you sent** (the "Unsend" feature):

```python
api.unsend_message("14000000000000050")
```

## Building a message by hand

The quick senders cover the common cases. When you need custom
`contentMetadata` (for example, @mentions), build the `Message` dict yourself
and pass it to `send_message`:

```python
from okline import Message

msg = Message.text(to, "hi @everyone", content_metadata={
    "MENTION": '{"MENTIONEES":[{"S":"3","E":"13","M":"U...."}]}',
})
api.send_message(msg)
```

`Message` factories: `text`, `sticker`, `location`, `contact`, `flex`,
`image`, `video`, `audio`, `file`, and `media_ref` (reference an OBS object you
already uploaded). See [`okline/models.py`](../okline/models.py).

## Marking chats read / hidden

```python
api.send_chat_checked(to, last_message_id="14000000000000050")  # mark read
api.set_chat_hidden_status(to, last_message_id="...", hidden=True)
api.send_chat_removed(to, last_message_id="...")                 # remove from list
```

## See exactly what was sent

Every request and response is recorded. After any send you can print the last
exchange as a readable HTTP transcript:

```python
api.send_text(to, "hi")
print(api.last.pretty())
```

→ [Recording](./recording.md)

---

**Next:** [Receiving events](./receiving-events.md) · [Building bots](./bots.md)

# Sending media (images, video, audio, files)

[← docs home](./index.md)

OkLine can send images, videos, audio clips and arbitrary files to any chat. Each
helper takes the destination **mid** and a **path** (or raw `bytes`):

```python
api.send_image("c0123...group", "photo.jpg")
api.send_video("u0123...friend", "clip.mp4")
api.send_audio("u0123...friend", "note.m4a")
api.send_file("c0123...group",  "report.pdf")
```

Each returns the server's response for the sent message (with its real `id`). You
can override the displayed filename with `name=`, and pass `duration_ms=` for
video/audio:

```python
api.send_file(to, "report.pdf", name="Q3-report.pdf")
api.send_video(to, "clip.mp4", duration_ms=8000)
```

You can also pass bytes you already have in memory:

```python
api.send_image(to, open("photo.jpg", "rb").read(), name="photo.jpg")
```

## From the CLI

`okline send` takes `--image` or `--file` instead of a text argument:

```bash
okline send c0123...group --image pic.jpg
okline send u0123...friend --file doc.pdf
```

As with every command, `okline send <name>` resolves a unique contact display
name to its mid, so you can write `okline send "Alice" --image pic.jpg`.

## How it works

Media is a two-step flow (the LINE V1 "OBS" upload):

1. A **placeholder message** of the right content type (IMAGE / VIDEO / AUDIO /
   FILE) is posted with `sendMessage`, which returns a message `id`.
2. The file bytes are then uploaded to LINE's object storage (OBS) at
   `/r/talk/m/<messageId>`, attaching them to that message.

This all happens inside the one `send_image` / `send_file` / … call — you don't
drive the two steps yourself.

## Media and Letter Sealing

Media uses the V1 (non-E2EE) flow, so a file sent into a **Letter-Sealed DM is
not sealed** — only text is end-to-end encrypted. The bytes go through OBS
unsealed even if the chat's text messages are encrypted. If that matters for your
use case, send sensitive content as text via
[`send_encrypted_text`](./e2ee.md) instead.

Media send works for chats that allow plain mode (most groups and ordinary DMs).

## See also

- [Sending messages](./messaging.md) — text, stickers, location, flex
- [Letter Sealing](./e2ee.md) — end-to-end encrypted text
- [Cookbook](./cookbook.md) — "send a photo to a group"

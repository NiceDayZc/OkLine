# Sending media (images, video, audio, files)

[← docs home](./index.md)

OkLine can send images, videos, audio clips and arbitrary files to any chat. Each
helper takes the destination **mid** and a **path** (or raw `bytes`):

```python
api.send_image("c0123...group", "photo.jpg")
api.send_video("u0123...friend", "clip.mp4")
api.send_audio("u0123...friend", "note.m4a")
api.send_file("c0123...group", "report.pdf")
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

## OBS authentication (the extension's headerMapper)

Raw OBS requests never carry your ordinary access token. OkLine mirrors the
extension's `FD` headerMapper exactly:

- **`/r/myhome/` URLs** (timeline/VOOM covers) authenticate with the
  **channel access token** (`X-Line-ChannelToken`, issued lazily via
  `issueChannelToken` and cached).
- **Every other private OBS resource** authenticates with an **encrypted
  access token** (`X-Line-Access`, type `OBS_GENERAL`, acquired lazily via
  `acquireEncryptedAccessToken`) plus `X-Line-Application:
  "CHROMEOS\t3.7.2\tChrome_OS\t"`.
- **Public objects** are fetched with no auth header at all
  (`download_object(..., public=True)`).

Tokens are acquired and cached automatically — you never supply them.

## X-Talk-Meta, /info.obs and /playback.obs

E2EE media downloads from `/r/talk/em` paths require the `X-Talk-Meta` header —
a base64(JSON({message: base64(thrift blob)})) wrapper around the message id.
OkLine builds it for you (`okline.obs.build_talk_meta(message_id)`; pass
`message_id=` to `download_object` / `playback_info`).

Two OBS metadata endpoints are available for inspecting objects:

```python
api.obs.resource_info("/r/talk/m/<mid>/<oid>")  # GET <path>/info.obs
api.obs.playback_info(path, message_id="msg-id")  # GET <path>/playback.obs
# playback sends modelName="CHROMEOS", networkType="WiFi", lang=<locale>
```

Downloads can also be routed to a specific CDN host
(`download_object(..., cdn="cdn_profile")` or `host="..."`).

## Media and Letter Sealing

Since 2.8.0 OkLine implements the extension's **V2 sealed-media flow**: when
the chat's negotiated media flow is V2, the file blob is end-to-end encrypted
(HKDF `FileEncryption` AES-CTR + HMAC) and the key material (`ENC_KM`) is
sealed into the message ciphertext. In chats that only allow the V1 flow, media
is sent unsealed — text is still always sealeable. See
[Letter Sealing](./e2ee.md#sealed-media) for the details.

Media send works for chats that allow plain mode (most groups and ordinary DMs).

### Downloading sealed media

One call handles the whole receive side (decrypt the message for `ENC_KM`,
fetch the object with `X-Talk-Meta`, decrypt the blob):

```python
msg = api.get_recent_messages(chat_mid, 10)[0]  # a sealed IMAGE/VIDEO/... message
data = api.e2ee.download_sealed_media(msg)  # -> plaintext bytes
data, info = api.e2ee.download_sealed_media(msg, info=True)  # + name/mime/size
```

The pieces it wires together (`api.decrypt_message`, `api.obs.object_info`,
`api.obs.download_object(..., message_id=...)`, `okline.e2ee_crypto.decrypt_blob`)
remain public if you need the manual flow.

## See also

- [Sending messages](./messaging.md) — text, stickers, location, flex
- [Letter Sealing](./e2ee.md) — end-to-end encrypted text
- [Cookbook](./cookbook.md) — "send a photo to a group"

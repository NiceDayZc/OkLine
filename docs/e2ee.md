# Letter Sealing (end-to-end encryption)

[← docs home](./index.md)

LINE's **Letter Sealing** encrypts message text end to end: the gateway only ever
sees ciphertext, and only you and the other party can read it. OkLine implements
the real protocol — it has been live-verified for both **1:1** chats **and**
**groups**, for **encrypt** and **decrypt**, in both the **V1** and **V2** wire
formats.

> Letter Sealing covers **text** (and location) messages, and — since 2.8.0 —
> the **V2 sealed-media flow** for images, video, audio and files (see
> [Sealed media](#sealed-media) below and [media](./media.md)).

## How the keys work

Your E2EE private keys live in a keychain that is unwrapped **during QR login**
(`okline login` / `api.qr_login(...)`). Two things follow from that:

- Log in by QR, then use E2EE **in the same process** — the keys are loaded
  automatically right after `qr_login`.
- The keys **persist across sessions**: `api.save_tokens(...)` exports the
  keychain into your session file, and `OkLine.from_tokens_file(...)` restores it.
  So once you have logged in once, Letter Sealing keeps working on later runs
  **without a fresh QR scan**.

```python
from okline import OkLine

# first run: log in by QR, save the session (keychain included)
api = OkLine()
api.qr_login(on_qr=print, on_pin=lambda pin: print("PIN:", pin))
api.save_tokens("tokens.json")

# any later run: keys come back from the file, no QR needed
api = OkLine.from_tokens_file("tokens.json")
print(api.e2ee.is_ready())  # True
```

> ⚠️ The exported keychain is **private-key material**. Guard `tokens.json` as
> carefully as your password and keep it out of version control.

`api.e2ee.is_ready()` tells you whether the keys are loaded. If it returns
`False`, log in with `okline login` (or `api.qr_login(...)`) to populate them.

## Send an encrypted message

```python
api.send_encrypted_text("u0123456789abcdef0123456789abcdef", "this is sealed")
```

This works for a **1:1** DM **and for groups** — including minting the very
first shared key of a group that has never had an encrypted message: OkLine
implements `registerE2EEGroupKey` (generate a curve key, wrap it once per
member, upload, unwrap), exactly like the extension.

The sealing version is chosen from the peer's negotiated `specVersion`:
spec-1 peers receive **V1**-framed messages, everyone else **V2**. You never
pick a version yourself.

### Automatic sealing (code 82)

You usually don't have to think about it. If the conversation *requires* Letter
Sealing, the server rejects a plain `send_text` with error **code 82**
("can not send using plain mode"). When your E2EE keys are ready, OkLine catches
that, encrypts the text, and re-sends it automatically:

```python
api.send_text(to, "hi")  # if the chat demands E2EE, this is auto-sealed and retried
```

You can also force sealing up front with `encrypt=True` on the low-level call:

```python
from okline import Message

api.send_message(Message.text(to, "hi"), encrypt=True)
```

### Send-error retry (codes 84 / 86 / 87 / 88 / 90 / 99)

Every sealed send goes through `api.e2ee.send_with_retry(...)`, mirroring the
extension's E2EE send-error semantics: on `E2EE_UPDATE_RECEIVER_KEY`(84),
`E2EE_INVALID_VERSION`(86), sender/receiver `E2EE_DISABLED`(87/88) or
`E2EE_RECEIVER_NOT_ALLOWED`(90) the cached negotiation is dropped and the send
retried transparently — up to 3 times (4 attempts total), matching the
extension's send loop; on `E2EE_RECREATE_GROUP_KEY`(99) the group key is
additionally re-registered first. `REFRESH_MEDIA_FLOW`(122) resets the cached
negotiation and re-raises immediately (the caller re-negotiates the media flow
before resending). Anything else — or a retry budget exhausted — raises to your
code.

## Decrypt a received message

Received sealed messages carry their ciphertext in a `chunks` field. Pass the raw
message dict to `api.decrypt_message(...)`; the plaintext comes back in `text`.
Non-sealed messages are returned unchanged, so it is always safe to call:

```python
plain = api.decrypt_message(msg)
print(plain.get("text"))
```

V1 vs V2 framing and 1:1 vs group routing are detected automatically — you do not
choose a version or a mode. After decryption, `REPLACE` (unsend/reply markers)
and sealed-media key material (`ENC_KM` / `FILE_NAME`) are restored into
`contentMetadata`, so decrypted messages carry everything the official client
shows.

Your **own** sealed messages decrypt too: when you read a chat back
(`get_recent_messages`, `okline chatlog`, bots over history, …) the messages
*you* sent are re-derived against the recipient's public key, so both sides of
the conversation come back in plaintext.

Inside a bot, decryption is automatic: `ctx.text` is already the decrypted text.

```python
from okline import OkLine, Bot

api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api)


@bot.on_message
def handle(ctx):
    print(ctx.sender, "said:", ctx.text)  # decrypted for you


bot.run()
```

## Read a sealed chat from the CLI

`okline chatlog` prints a chat's recent messages and **decrypts E2EE messages
inline** when your keys are loaded:

```bash
okline chatlog u0123456789abcdef0123456789abcdef -n 50
```

If you have not logged in by QR, sealed lines show
`[encrypted — run \`okline login\` to load keys]` instead of the text. Run
`okline login` once and they will decrypt.

## Sealed media

Since 2.8.0 OkLine implements the extension's **V2 sealed-media flow** for
images, video, audio and files:

- `Message.image/video/audio/file(..., enc_km=...)` accept a key-material value;
  `api.e2ee.generate_enc_km()` mints a fresh one (base64 of 32 random bytes).
- The `ENC_KM` / `FILE_NAME` metadata is sealed *into* the message ciphertext
  (not left in `contentMetadata`) and restored on decrypt.
- The file blob itself is end-to-end encrypted with keys derived via
  HKDF-SHA256 (info `FileEncryption`) — AES-CTR plus an appended HMAC-SHA256
  tag (`okline.e2ee_crypto.encrypt_blob` / `decrypt_blob`).

Sealing only happens when the negotiated media flow for that content type is
V2 (best-effort check), and only for sealable content types
(text/location/image/video/file/audio) whose type is in the negotiated
`allowedTypes` — matching the extension's `encryptMessage` gating.

## Limitations

- The extension's server-side `function.e2ee` configuration check (sealing is
  disabled entirely when the server turns the flag off) is not replicated —
  it needs live settings; OkLine relies on the content-type/allowedTypes gating
  above.
- Chunked (multi-part) sealed uploads use the single-blob path; the chunk-hash
  list variant of the extension's uploader is not implemented.

## See also

- [Sending messages](./messaging.md)
- [Sending media](./media.md)
- [Building bots](./bots.md)

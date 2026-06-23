# Letter Sealing (end-to-end encryption)

[← docs home](./index.md)

LINE's **Letter Sealing** encrypts message text end to end: the gateway only ever
sees ciphertext, and only you and the other party can read it. OkLine implements
the real protocol — it has been live-verified for both **1:1** chats **and**
**groups**, for **encrypt** and **decrypt**, in both the **V1** and **V2** wire
formats.

> Letter Sealing covers **text** (and location) messages. Images, video, audio
> and files use a separate flow and are **not** sealed — see [media](./media.md).

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
print(api.e2ee.is_ready())   # True
```

> ⚠️ The exported keychain is **private-key material**. Guard `tokens.json` as
> carefully as your password and keep it out of version control.

`api.e2ee.is_ready()` tells you whether the keys are loaded. If it returns
`False`, log in with `okline login` (or `api.qr_login(...)`) to populate them.

## Send an encrypted message

```python
api.send_encrypted_text("u0123456789abcdef0123456789abcdef", "this is sealed")
```

This works for a **1:1** DM. For groups, the same crypto is used as long as a
group shared key already exists (see [Limitations](#limitations)).

### Automatic sealing (code 82)

You usually don't have to think about it. If the conversation *requires* Letter
Sealing, the server rejects a plain `send_text` with error **code 82**
("can not send using plain mode"). When your E2EE keys are ready, OkLine catches
that, encrypts the text, and re-sends it automatically:

```python
api.send_text(to, "hi")   # if the chat demands E2EE, this is auto-sealed and retried
```

You can also force sealing up front with `encrypt=True` on the low-level call:

```python
from okline import Message
api.send_message(Message.text(to, "hi"), encrypt=True)
```

## Decrypt a received message

Received sealed messages carry their ciphertext in a `chunks` field. Pass the raw
message dict to `api.decrypt_message(...)`; the plaintext comes back in `text`.
Non-sealed messages are returned unchanged, so it is always safe to call:

```python
plain = api.decrypt_message(msg)
print(plain.get("text"))
```

V1 vs V2 framing and 1:1 vs group routing are detected automatically — you do not
choose a version or a mode.

Inside a bot, decryption is automatic: `ctx.text` is already the decrypted text.

```python
from okline import OkLine, Bot

api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api)

@bot.on_message
def handle(ctx):
    print(ctx.sender, "said:", ctx.text)   # decrypted for you

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

## Limitations

- **Brand-new group keys are not created.** OkLine fetches and unwraps an
  *existing* group shared key, but it does not implement `registerE2EEGroupKey`
  — i.e. minting the very first key for a group that has never had an encrypted
  message. Encrypting into such a group raises an error
  ("no E2EE group shared key … first-message key creation is not implemented").
  Once any client has established a group key, OkLine can encrypt and decrypt.
- Only **text** (and location) is sealed; media is sent unencrypted.

## See also

- [Sending messages](./messaging.md)
- [Sending media](./media.md)
- [Building bots](./bots.md)

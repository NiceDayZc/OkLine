---
name: okline
description: >-
  Control the user's own LINE Messenger account from this machine via OkLine
  (Python). Use whenever the user mentions LINE in an automation, bot, or
  messaging context: send or read LINE messages, LINE bot, LINE login / QR
  login, LINE notify, read my chats or chat history, list LINE contacts or
  groups, send stickers/images/files, download LINE media, LINE E2EE, react or
  unsend, auto-reply or watch for new messages, ส่งข้อความ line, อ่านแชท line,
  บอทไลน์, ล็อกอิน line, ห้องแชท line, ส่งสติกเกอร์ line.
---

# OkLine — personal LINE account automation

This skill gives full control of the user's own LINE account from this
machine: log in (QR/email), send text/stickers/media to any chat, read and
decrypt chat history, manage contacts and groups, download media (including
E2EE-sealed), and run bots that react to incoming messages — through a Python
API (`from okline import OkLine`) and a CLI (`python3 -m okline`). Before
doing anything, run the environment check below, then follow the decision
tree to the right reference file.

## Environment check

Run this once at the start of any LINE task. All three must pass (or a login
must be performed for the third):

```bash
python3 - <<'CHECK'
import os, shutil, subprocess, sys
try:
    import okline
    print(f"[ok]   okline {okline.__version__} importable (python {sys.version.split()[0]})")
except ImportError as e:
    print(f"[FAIL] okline not importable: {e} — run from the repo checkout or pip install okline")
node = shutil.which("node")
if not node:
    print("[FAIL] node not on PATH — every request fails with code 10005 REQUEST_INVALID_HMAC")
else:
    v = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    ok = int(v.lstrip("v").split(".")[0]) >= 18
    print(f"[{'ok' if ok else 'FAIL'}]   node {v} (need 18+; override binary with LINE_NODE)")
for p in ("tokens.json", os.path.expanduser("~/tokens.json")):
    if os.path.exists(p):
        print(f"[ok]   session file: {os.path.abspath(p)}")
        break
else:
    print("[--]   no tokens.json — login needed, see ./references/session.md")
CHECK
```

A live session may already exist at
`/Users/nicedayzc/Documents/Codex/2026-09-12/new-chat-2/OkLine/tokens.json` —
prefer it over logging in again. Probe liveness with `api.get_profile()`
before assuming a session works.

## Golden rules — read before touching anything

- `tokens.json` **is the user's entire LINE account**. Never print token
  values, never commit it, never copy it off this machine.
- Personal-account automation violates LINE's ToS; ban risk is real. Do only
  what the user asked, at human speed.
- **Never send to a third party** without an explicit user instruction naming
  the recipient. Read-only ops are safe; ask before unsend/leave/block/kick
  or any destructive op.
- Rate discipline: no tight loops of sends. Before any multi-send, install
  `api.transport.rate_limiter = RateLimiter(rate=3, per=1.0)`. On
  `EXCESSIVE_ACCESS` (code 4) or `ABUSE_BLOCK` (35): stop and slow down.

## Capability decision tree

Read the target file before writing code for that area.

- **Session / login / token problems** → `./references/session.md`
  - Restore: `api = OkLine.from_tokens_file("tokens.json")`; probe:
    `api.get_profile()`.
  - Login: `api.qr_login(on_qr=...)` — `on_qr` gets the full callbackUrl
    (already contains `?secret=`); hand the URL to the user or render it with
    `okline.qrterm.print_qr(url)`. Always `api.save_tokens(...)` after.
    E-mail + password login (`api.auth.email_login`) is in the same file.
  - `LineAuthError` codes 1/7/8 = re-login needed; 10005 = Node missing.
- **Send anything** → `./references/messaging.md`
  - `api.send_text(mid, "hi")`, `api.send_image(to, "pic.jpg")`,
    `api.react(message_id, PredefinedReactionType.NICE)`.
  - Read `res["id"]` from every send (needed for react/unsend). Never use
    `send_flex` — the server rejects it (code 11, official accounts only).
- **Read chats / history / search** → `./references/reading.md`
  - `api.get_message_boxes()` → chat ids; `api.get_recent_messages(mid, 20)`;
    `api.decrypt_message(msg)` on every message you read (safe on any
    message, sealed or not).
- **Media: send files, download images/video/audio, avatars, covers** →
  `./references/media.md`
  - Plain: `api.obs.download_object("talk", sid, oid, message_id=msg_id)`.
  - Sealed: `api.e2ee.download_sealed_media(msg, info=True)` after
    `decrypt_message`.
- **Contacts / groups / rooms / profile & settings edits** → `./references/contacts-groups.md`
  - `api.get_all_contact_ids()` + `api.get_contacts(ids)` (auto-chunks at
    100); `api.find_contact_by_userid("line_id")`.
  - Mid shapes: user `u...`, group `c...`, room `r...`.
- **Bots / auto-reply / watching for new messages** → `./references/events-bots.md`
  - `from okline import Bot` → `@bot.on_message` handler with `ctx.text`
    (auto-decrypted), `ctx.reply(...)`; `bot.run(keepalive=True)` blocks.
  - In scripts, stop the stream with `signal.alarm` + a custom
    `BaseException` — a plain `TimeoutError` is swallowed by the stream loop.
- **Any error / error code / rate limit** → `./references/errors.md`
  - Most codes (82 re-seal, 119 refresh, E2EE 84–99; 6 never occurs because
  contacts/chats calls auto-chunk at 100 mids) are handled internally — do
  not retry them yourself. 122 = resend once;
  4/35 = slow down; 1/7/8/10201 = re-login.

## Quick reference

| Call | What it does | Details |
|---|---|---|
| `OkLine.from_tokens_file("tokens.json")` | Restore session incl. E2EE keychain | `./references/session.md` |
| `api.get_profile()` | Own profile; liveness probe | `./references/session.md` |
| `api.qr_login(on_qr=..., on_pin=...)` | QR login (URL handoff) | `./references/session.md` |
| `api.send_text(mid, text)` | Send text (auto code-82 re-seal) | `./references/messaging.md` |
| `api.send_image(to, "pic.jpg")` | Send image (also video/audio/file) | `./references/messaging.md` |
| `api.react(msg_id, PredefinedReactionType.NICE)` | React to a message | `./references/messaging.md` |
| `api.unsend_message(msg_id)` | Recall a message (ask first) | `./references/messaging.md` |
| `api.get_message_boxes(limit=50)` | List chats with ids | `./references/reading.md` |
| `api.get_recent_messages(chat_mid, n)` | Newest n messages of a chat | `./references/reading.md` |
| `api.decrypt_message(msg)` | Decrypt any message, sealed or not | `./references/reading.md` |
| `api.get_all_contact_ids()` / `api.get_contacts(ids)` | List contacts (chunks at 100) | `./references/contacts-groups.md` |
| `api.find_contact_by_userid("id")` | Look up by LINE ID | `./references/contacts-groups.md` |
| `api.get_all_chat_mids()` / `api.get_chats(ids)` | List groups/rooms + members | `./references/contacts-groups.md` |
| `api.e2ee.is_ready()` / `api.send_encrypted_text(to, text)` | E2EE status / send sealed | `./references/messaging.md` |
| `api.obs.download_object(service, sid, oid, ...)` | Download media from OBS | `./references/media.md` |
| `Bot(api)` + `@bot.on_message` | Event-driven bot | `./references/events-bots.md` |
| `RateLimiter(rate=3, per=1.0)` on `api.transport.rate_limiter` | Rate discipline for multi-sends | `./references/errors.md` |

## How to run things

For anything beyond one call, use a python3 heredoc from the repo root (where
`tokens.json` lives) and always close the client — it releases the Node
bridge:

```bash
cd /Users/nicedayzc/Documents/Codex/2026-09-12/new-chat-2/OkLine && python3 - <<'EOF'
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
try:
    me = api.get_profile()
    print("logged in as:", me.get("displayName"))
finally:
    api.close()
EOF
```

For quick one-offs prefer the CLI (`python3 -m okline <cmd>`; `okline menu`
for the interactive TUI). The CLI resolves contact **names** to mids:

```bash
cd /Users/nicedayzc/Documents/Codex/2026-09-12/new-chat-2/OkLine
python3 -m okline whoami                       # session liveness probe
python3 -m okline find "Alice"                 # resolve a name to a mid
python3 -m okline send "Alice" "hi there"      # send by name
python3 -m okline send Alice --image pic.jpg   # image / --file / --sticker P S
python3 -m okline boxes                        # list chats
python3 -m okline chatlog MID -n 20            # decrypted chat log
python3 -m okline watch --echo                 # echo incoming messages
python3 -m okline selftest                     # environment self-check
```

Long-running flows (QR login waiting for the user's phone, background
watchers/bots) must not block your turn — run them in the background and poll
marker lines (e.g. `LOGIN_URL: ...`). Ready-made scripts and the exact
patterns are in `./references/session.md` (login) and
`./references/events-bots.md` (watchers/autoreply/notify).

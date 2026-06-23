# Command-line interface

[← docs home](./index.md)

OkLine is a full LINE client in your terminal. After `pip install okline` an
`okline` console script is on your PATH:

```bash
okline login                 # scan a QR once — saves ./tokens.json
okline whoami                # every command reuses the saved session
okline send <mid> "hello"
okline contacts --search soda
```

The equivalent module form works too:

```bash
python -m okline <command> [options]
```

> **Requirements:** Python 3.9+ and **Node.js 18+ on your PATH** (used to sign
> requests with `X-Hmac`). The CLI checks for Node before logging in and tells
> you how to install it if it's missing.

## The interactive menu

Running `okline` with **no arguments** opens a soft-coloured, numbered menu — pick
actions by typing a number, no commands to memorise. On first run with no saved
session it goes straight to QR login.

```bash
okline            # opens the interactive menu (or `okline menu`)
```

## How a command finds your session

Every command resolves credentials in this order: `--token` → `--tokens-file`
→ the `LINE_ACCESS_TOKEN` env var → the default `./tokens.json` (created by
`okline login`). When a **session file** is used it is loaded so your E2EE keys
come back too, which is why `chatlog`, `send --encrypt`, and the watch/bot
commands work after a single `okline login`.

## Global options

These work on every subcommand:

| Option | Meaning |
|--------|---------|
| `--token T` | access token (or env `LINE_ACCESS_TOKEN`) |
| `--refresh R` | refresh token (or env `LINE_REFRESH_TOKEN`) |
| `--tokens-file PATH` | session JSON to load (default `./tokens.json`) |
| `--show-secrets` | do **not** redact tokens/secrets in transcripts |

Top-level (no subcommand):

| Option | Meaning |
|--------|---------|
| `--version`, `-V` | print the OkLine version and exit |
| `-h`, `--help` | show help; `okline <cmd> -h` for one command |

**Exit codes:** `0` success, `1` request/runtime error, `2` bad arguments.

---

## Session & identity

```bash
okline login                       # scan a QR, save ./tokens.json (alias: qr-login)
okline login --save mysession.json # save somewhere else
okline login --wait 240            # wait up to 240s for scan + PIN
okline login --invert              # for light-background terminals
okline logout                      # invalidate server session + delete tokens.json
okline whoami                      # your profile + account stats
okline profile                     # your full profile (JSON)
okline profile <mid>               # look up another user by mid
okline version                     # print the OkLine version
```

`login` draws the QR, waits for you to scan and confirm the PIN, loads your E2EE
keys, prints your profile, and saves the session. **`qr-login` is an alias for
`login`.** (There is no `profile --raw` flag — use `okline call … --raw` for a
full transcript.)

## People & contacts

```bash
okline contacts                    # list every contact: "<mid>  <name>"
okline contacts --search soda      # filter by display-name substring
okline contacts --csv out.csv      # export to CSV
okline contacts --json out.json    # export to JSON
okline find soda                   # find a contact's mid by display name
okline search <userid>             # look up a user by their public LINE ID
okline search <userid> --add       # ...and add them as a friend
okline add <userid-or-mid>         # add a friend by LINE ID or mid
okline block list                  # list blocked contacts
okline block add <mid>             # block a contact
okline block remove <mid>          # unblock
okline favorites list              # list favorite chats
okline favorites add <mid>         # favorite a chat
okline favorites remove <mid>      # unfavorite
```

## Groups & chats

```bash
okline groups                      # list your groups (+ how many you're invited to)
okline members <group-mid>         # list a group's members, with names
okline accept <chat-mid>           # accept a group invitation
okline leave <chat-mid>            # leave a group / room
```

Mids start with `U` (user), `C` (group) or `R` (room) and are case-insensitive.

## Messaging

```bash
okline send <to> "hi there"                 # text
okline send <to> "secret" --encrypt          # E2EE (Letter Sealing) text
okline send <to> --image photo.jpg           # image
okline send <to> --file report.pdf           # any file
okline send <to> --sticker 11537 52002744    # sticker: PKG STK
okline send <to> --location 35.68 139.76 --title "Tokyo Station"
okline react <message-id> LOVE               # NICE|LOVE|FUN|AMAZING|SAD|OMG
okline unsend <message-id>                   # recall your message
okline broadcast "hello all" --to <mid> <mid> ...   # one text to many chats
okline set-name "New Name"                   # change your display name
okline set-status "on vacation"              # change your status message
```

`<to>` accepts a **mid** directly, or a **unique contact name** — OkLine resolves
the name to its mid (it errors if the name is ambiguous or unknown). `react`
defaults to `NICE` if you omit the reaction.

`broadcast` paces sends with a rate limiter and asks before starting:

```bash
okline broadcast "sale today!" --to C123... C456... --rate 2 --yes
```

`--rate` is messages per second (default `3`); `--yes` skips the confirmation
prompt. If LINE rate-limits or blocks the account mid-run, broadcast stops.

## Reading history

```bash
okline boxes                       # list your message boxes (with unread counts)
okline boxes --limit 50
okline chatlog <chat-mid>          # recent messages (decrypts E2EE if keys loaded)
okline chatlog <chat-mid> -n 100   # last 100 messages
okline backup <chat-mid>           # save recent messages to <chat-mid>.json
okline backup <chat-mid> -n 1000 -o thread.json   # page back further, custom file
```

`chatlog` automatically decrypts Letter-Sealed messages when your E2EE keys are
loaded (i.e. after `okline login` / loading a session). Without keys it prints a
placeholder telling you to run `okline login`.

## Live / bots

These stream the live SSE operation feed and run until `Ctrl-C`:

```bash
okline watch                       # print incoming messages live
okline watch --echo                # ...and echo each one back
okline autoreply --rule "hi=hello" --rule "ping=pong"   # keyword auto-reply bot
okline autoreply --rule "HELP=see docs" --ignore-case
okline notify                      # alert on incoming messages
okline notify --keyword urgent     # only when text contains a keyword
okline notify --group-only         # only group messages
okline notify --dm-only            # only direct messages
```

`autoreply` takes one or more `--rule "keyword=reply"`; add `--ignore-case` for
case-insensitive matching. For programmatic bots see [bots](./bots.md).

## Advanced

### `endpoints` — list every endpoint

```bash
okline endpoints                   # all endpoint keys + their /api/ paths
okline endpoints message           # filter keys by substring
```

### `call` — hit any endpoint directly

```bash
okline call <Namespace.Service.method> [argsJSON] [--raw] [--no-auth]
```

`argsJSON` is the positional Thrift args as a **JSON array** (default `[]`).
`--raw` prints the full HTTP transcript; `--no-auth` skips the token requirement
for pre-login endpoints.

```bash
# read your profile
okline call Talk.TalkService.getProfile "[2]"

# send a message
okline call Talk.TalkService.sendMessage \
  '[0,{"to":"u0123...","text":"hi","contentType":0,"contentMetadata":{}}]'

# a pre-login call (no token) with the full request/response transcript
okline call LoginQrCode.SecondaryQrCodeLoginService.createSession "[{}]" \
  --no-auth --raw
```

Output is the decoded JSON response, or with `--raw` the full transcript (secrets
redacted unless you pass `--show-secrets`).

### `selftest` — verify endpoints against the real server

Calls every **read-only** endpoint (discovering your mid / first contact / first
chat as it goes) and prints a pass/fail table. State-changing endpoints
(sendMessage, block, leave, …) are **not** run.

```bash
okline selftest
okline selftest --verbose --save selftest.txt
```

Exit code is `0` if everything passed, `1` if any endpoint failed. `--save`
writes the full transcript to a file.

---

See also: [Authentication](./authentication.md) · [Messaging](./messaging.md) ·
[Receiving events / bots](./bots.md) · [Recording](./recording.md) ·
[Troubleshooting](./troubleshooting.md).

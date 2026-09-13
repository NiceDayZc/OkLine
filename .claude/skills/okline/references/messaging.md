# Sending messages, reactions, and unsend

Every send returns the persisted message dict — read `res["id"]` from it; you need that id to react, unsend, or download media later. If a call returns something dict-like without an `id`, treat the send as failed and surface the raw response to the user. Prerequisites (session bootstrap, Node requirement) live in ./session.md — never send anything until `OkLine.from_tokens_file("tokens.json")` has succeeded.

## The send surface

All methods below are on the `OkLine` client. Argument orders verified against `okline/services/messaging.py`, `okline/client.py`, and `okline/models.py` (v2.9.2).

- `api.send_text(to, text, **kw)` — plain text. Extra kwargs pass through to `Message.text` (see the reply pattern below).
- `api.reply_text(to, text, related_message_id)` — quotes the target message in the chat (sets `relatedMessageId` + `messageRelationType=REPLY` + `relatedMessageServiceCode=1`).
- `api.send_sticker(to, package_id, sticker_id, version=1)` — ids are strings, e.g. `("11537", "52002734")`.
- `api.send_location(to, latitude, longitude, title="", address="")` — lat/lon are floats; title shows as the pin label.
- `api.send_contact(to, contact_mid, display_name="")` — shares a contact card.
- `api.send_image(to, file, *, name=None)` / `api.send_video(..., duration_ms=0)` / `api.send_audio(..., duration_ms=0)` / `api.send_file(to, file, *, name=None)` — `file` is a path (str/Path) or raw bytes. V1 flow: placeholder message first, then the bytes upload to OBS under that message id. This is the **non-E2EE** path — in a Letter-Sealed chat the peer receives a placeholder without decryptable media; sealed-media sending is covered in ./media.md.
- `api.send_encrypted_text(to, text, **kw)` — seals up front (1:1 chats). Equivalent to `send_text(..., encrypt=True)`.
- `api.unsend_message(message_id)` — recall one of **your own** messages. Destructive: ask the user before calling (see ./errors.md for the full error-code map).

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
try:
    to = "u0123456789abcdef0123456789abcdef"  # a real mid
    res = api.send_text(to, "hello from the agent")
    print("sent, message id:", res["id"])

    # quote a reply to that exact message
    api.reply_text(to, "quoting myself", related_message_id=res["id"])

    api.send_sticker(to, "11537", "52002734")
    api.send_location(to, 35.6586, 139.7454, title="Tokyo Tower", address="Minato, Tokyo")
    api.send_contact(to, "u...peer mid...", display_name="Alice")
    api.send_image(to, "/tmp/cat.jpg")          # or raw bytes: open(p,"rb").read()
    api.send_video(to, "/tmp/clip.mp4", duration_ms=15000)
finally:
    api.close()
```

### `send_flex` is server-restricted — do not use

`api.send_flex(to, alt_text, contents)` exists and builds a valid FLEX message, but the **server rejects it with code 11** (`INCOMPATIBLE_APP_VERSION`, surfaced as `LineApiError`) for personal accounts: flex bubbles are official-accounts-only. Do not attempt it, do not retry it, and do not "fix" the payload — when the user asks for rich/card messages, send text (or an image) instead and say flex is unavailable on personal accounts. The example in `docs/messaging.md` showing `send_flex` predates this finding; trust the code 11 behavior.

### Low level: `send_message` and the `Message` factories

When a convenience method doesn't cover a case, build the dict with a `Message` factory and send it. Every factory takes `to` plus optional `from_mid`/`msg_id`/`created_time` (omit them — E2EE sealed sends must not carry `from`).

```python
from okline import OkLine
from okline.models import Message
from okline.enums import MessageRelationType

api = OkLine.from_tokens_file("tokens.json")
try:
    # the reply pattern from scratch (what reply_text does internally)
    msg = Message.text(
        "u0123456789abcdef0123456789abcdef",
        "manual reply",
        related_message_id="14000000000000050",
        message_relation_type=int(MessageRelationType.REPLY),
    )
    api.send_message(msg)                 # plain send
    api.send_message(msg, encrypt=True)   # seal up front (skips the code-82 round trip)
finally:
    api.close()
```

Factories: `Message.text`, `.sticker`, `.location`, `.contact`, `.flex`, `.image`, `.video`, `.audio`, `.file`, `.media_ref` (references an already-uploaded OBS object). `mid_to_type(mid)` infers `toType` from the mid prefix automatically.

## Resolving names to mids

Mid prefixes: `u...` = user, `c...` = group/chat, `r...` = room (also `s...` = square/open-chat). Prefix matching is case-insensitive (`U` and `u` both work). Resolution options, cheapest first:

1. **The user gave you a mid** — starts with u/c/r (case-insensitive) and is ≥ 20 chars: use it verbatim.
2. **The user gave a LINE ID** (the searchable `@id`): `api.find_contact_by_userid(line_id)` returns a single Contact dict on hit and **raises `LineApiError` on a miss** (code 5 `Cannot find` for a well-formed unknown id; code 0 `userid is invalid` for a malformed one) — wrap it in try/except, never `or {}`.
3. **The user gave a display name**: fetch contacts and substring-match — this is exactly what the CLI does (`okline/__main__.py::_resolve_to`). Ambiguous matches must go back to the user; never pick one silently.

```python
from okline import OkLine
from okline.exceptions import LineApiError

api = OkLine.from_tokens_file("tokens.json")
try:
    # by LINE @id (raises LineApiError when the id is unknown/invalid)
    try:
        contact = api.find_contact_by_userid("alice_lin") or {}
        print(contact.get("mid"), contact.get("displayName"))
    except LineApiError:
        print("no such LINE id")

    # by display name — mirrors the CLI's unique-substring rule
    ids = api.get_all_contact_ids() or []          # list of mids
    names = {}
    for i in range(0, len(ids), 100):              # server caps 100 mids per call (code 6)
        res = api.get_contacts(ids[i : i + 100]) or {}
        for mid, w in res.get("contacts", {}).items():
            c = w.get("contact", w) if isinstance(w, dict) else {}
            names[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
    needle = "alice"
    matches = [(m, n) for m, n in names.items() if needle.lower() in n.lower()]
    if len(matches) == 1:
        print("unique match:", matches[0])
    else:
        print("ambiguous or none:", matches[:8])   # ask the user to disambiguate
finally:
    api.close()
```

`api.get_contacts(mids)` auto-chunks at 100 mids and merges, so passing the full list in one call is also fine — the explicit loop above just shows the chunk size. `getContactsV2`/`getChats` reject >100 mids with code 6 `INVALID_LENGTH`; if you ever see code 6 from a contacts/chats call, you bypassed the chunking — don't.

Prefer the CLI when the user just wants something sent to "Alice": `python3 -m okline send "Alice" "hi"` resolves the name, shows the resolved contact, and sends. It fails loudly on zero or multiple matches. Full CLI coverage: the "How to run things" section of ../SKILL.md and docs/cli.md in the repo.

```bash
python3 -m okline send "Alice" "hi"
python3 -m okline send u0123456789abcdef0123456789abcdef "direct by mid"
python3 -m okline send "Alice" --image /tmp/cat.jpg
python3 -m okline send "Alice" --sticker 11537 52002734
python3 -m okline send "Alice" --location 35.6586 139.7454 --title "Tokyo Tower"
python3 -m okline send "Alice" "sealed" --encrypt
```

## Reactions

`PredefinedReactionType` values (from `okline/enums.py`): `NICE=2`, `LOVE=3`, `FUN=4`, `AMAZING=5`, `SAD=6`, `OMG=7`. `api.react(message_id, reaction)` defaults to NICE; `api.cancel_reaction(message_id)` removes your reaction. Both take the **message id**, not the mid of the chat.

```python
from okline import OkLine
from okline.enums import PredefinedReactionType

api = OkLine.from_tokens_file("tokens.json")
try:
    res = api.send_text("u0123456789abcdef0123456789abcdef", "react to this")
    api.react(res["id"], PredefinedReactionType.LOVE)
    api.cancel_reaction(res["id"])
finally:
    api.close()
```

```bash
python3 -m okline react 14000000000000050 LOVE   # name required, default NICE
python3 -m okline react 14000000000000050        # NICE
```

## Unsend

`api.unsend_message(message_id)` recalls a message **you** sent (the server refuses others). It is destructive and visible to everyone in the chat — get an explicit user instruction naming the message before calling. Note the CLI `python3 -m okline unsend ID` is a no-op stub in v2.9.2; use the Python method.

## Auto-sealing (code 82) and E2EE retry behavior

You usually don't manage sealing yourself. The flow inside `send_message`:

1. Send plain. If the chat requires Letter Sealing, the server rejects with **code 82** (`E2EE_RETRY_ENCRYPT`, "can not send using plain mode").
2. If `api.e2ee.is_ready()` and the content type is sealable — **text and location** — the message is sealed and re-sent automatically. Media placeholders (IMAGE/VIDEO/AUDIO/FILE) are never re-sealed this way; their bytes go through OBS with the E2EE media flow instead (./media.md).
3. Sealed sends go through `api.e2ee.send_with_retry(msg)`, which transparently retries (up to 3 extra attempts) on codes **84, 86, 87, 88, 90** (reset E2EE negotiation, re-negotiate, retry) and **99** (group chats: re-register the group key, retry). Code **122** (`REFRESH_MEDIA_FLOW`) resets the cached negotiation and **re-raises immediately** — catch it and simply retry the send once yourself. Any other code, or a retry budget exhaustion, propagates to you.

Practical rules:

- If code 82 escapes to you, E2EE wasn't ready — check `api.e2ee.is_ready()`; keys come from the login session (./session.md, ./media.md).
- Pre-seal with `encrypt=True` / `send_encrypted_text` when you already know the chat is sealed (saves a round trip).
- Codes 84–99 escaping `send_with_retry` mean key negotiation itself failed repeatedly — run `api.e2ee.roundtrip(to, text)` to self-test and report; don't loop sends at a failing chat.

## Broadcast / multi-send — always with a RateLimiter

Sending the same or similar content to many chats fast is the classic ban trigger (`EXCESSIVE_ACCESS=4`, `ABUSE_BLOCK=35`). Install a `RateLimiter` on the transport first, stop immediately on an abuse code, and only ever send to recipients the user explicitly named.

```python
from okline import OkLine
from okline.enums import ErrorCode
from okline.exceptions import LineApiError
from okline.ratelimit import RateLimiter

api = OkLine.from_tokens_file("tokens.json")
try:
    api.transport.rate_limiter = RateLimiter(rate=3, per=1.0)  # 3 sends/sec max
    abuse = {int(ErrorCode.EXCESSIVE_ACCESS), int(ErrorCode.ABUSE_BLOCK)}
    targets = ["u...", "c..."]  # only mids the user explicitly named
    for mid in targets:
        try:
            api.send_text(mid, "the message the user asked for")
            print("sent ->", mid)
        except LineApiError as exc:
            print("FAIL ->", mid, exc)
            if getattr(exc, "code", None) in abuse:
                print("LINE rate-limited/blocked this account — stopping.")
                break
finally:
    api.close()
```

The CLI equivalent is rate-limited the same way and prompts for confirmation (skip with `--yes` — don't, unless the user already listed the recipients and text in their instruction):

```bash
python3 -m okline broadcast "message text" --to u... c... --rate 3
```

If either abuse code appears even with a limiter installed, stop and tell the user their account is being throttled — do not retry, do not backoff-and-hammer.

## Error quick reference for sends

| Code | Meaning | Action |
|---|---|---|
| 6 | `INVALID_LENGTH` (>100 mids in getContacts/getChats) | Use `api.get_contacts` (auto-chunks); don't hand-build the call |
| 11 | Flex rejected (official-accounts-only) | Don't use `send_flex`; send text/image instead |
| 4 / 35 | `EXCESSIVE_ACCESS` / `ABUSE_BLOCK` | Stop sending immediately; install a RateLimiter before resuming |
| 82 | Chat requires sealing | Automatic for text/location; verify `api.e2ee.is_ready()` |
| 84/86/87/88/90/99 | E2EE negotiation/key errors | Handled inside `send_with_retry`; if they escape, self-test with `api.e2ee.roundtrip` |
| 122 | `REFRESH_MEDIA_FLOW` | Retry the send once; negotiation refreshes on the next attempt |
| 119 / 401 | Token expired | Auto-refreshed and replayed once; if it escapes, see ./session.md |

For LineAuthError {1,7,8} (re-login needed) and anything else, see ./errors.md.

## Safety contract for every send

- Never send to a third party unless the user explicitly named the recipient and the content. When in doubt, show the drafted message and target, and ask.
- No tight loops of sends — a `RateLimiter` is mandatory for anything above a handful of messages.
- Unsend and anything else destructive requires an explicit user instruction first.

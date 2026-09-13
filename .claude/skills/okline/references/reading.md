# Reading chats: enumerate, fetch, decrypt, search, back up, read receipts

Reading is the safest part of the LINE API — every call here is read-only except `mark_as_read` / `send_chat_checked` (it changes the "read" state other people see; ask the user first). Bootstrap every snippet with the client below. Node.js 18+ must be on PATH (the X-Hmac signer needs it — without it every call fails with code 10005). See ./session.md for login; here, always reuse an existing session.

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")  # restores E2EE keys too
# ... your reads ...
api.close()  # releases the Node bridge
```

## 1. Enumerate chats (message boxes)

`api.get_message_boxes(limit=100)` is the chat list (keyword-only args: `active_only=True`, `unread_only=False`, `with_unread_count=True`, `min_chat_id=None` for paging). Result shape:

```python
res = api.get_message_boxes(limit=50)
for box in res.get("messageBoxes", []):
    print(box.get("id"), "unread=", box.get("unreadCount", 0))
```

- Box `id` == the chat mid you pass to every per-chat read (`u...` 1:1, `c...` group, `r...` room).
- `unread_only=True` lists only chats with unread messages. `active_only=True` (default) hides archived/inactive chats — pass `active_only=False` if a chat you expect is missing.
- Page with `min_chat_id=<last box id>` when the account has more than `limit` chats.
- Shell equivalent: `python3 -m okline boxes`.

Resolving names (do this once, cache the map — it costs one `getAllContactIds` plus chunked `getContactsV2` calls; `api.get_contacts` chunks at 100 automatically and merges):

```python
def contact_names(api):
    names = {}
    for mid, wrapper in (api.get_contacts(api.get_all_contact_ids() or []).get("contacts", {}) or {}).items():
        c = wrapper.get("contact", wrapper) if isinstance(wrapper, dict) else {}
        names[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
    me = api.get_profile() or {}
    names[me.get("mid")] = me.get("displayName") or "me"
    return names
```

Group names come from `api.get_chats([chat_mid])` (cross-ref ./messaging.md for chat/member operations). By LINE ID (`@something`): `api.find_contact_by_userid("line_id")` -> single Contact dict or a `LineApiError`. Shell: `python3 -m okline find QUERY` (name substring) and `python3 -m okline search USERID`.

## 2. Per-chat reads

Three fetchers, all returning raw message dicts:

- `api.get_recent_messages(chat_mid, n)` — last `n` messages, **newest first**. Default `n=50`. This is your default read.
- `api.get_previous_messages(chat_mid, end_message_id, delivered_time, count=100)` — the page *older than* the message identified by `(end_message_id, delivered_time)`, also newest-first within the page. Use it to walk back for backups. The cursor comes from the oldest message you hold: `msg["id"]` and `int(msg.get("deliveredTime") or msg.get("createdTime") or 0)` — fall back to `createdTime` because not every message carries `deliveredTime`.
- `api.get_messages_by_ids(ids)` — fetch specific messages by id. CAUTION: the wire arg shape is **inferred, not confirmed** (the extension registers the endpoint but never calls it); it may raise or return nothing on the real server. Do not build anything critical on it — read recent/previous pages and filter by `id` instead.

Message dicts: `id`, `from`, `to`, `text` (only for unsealed text), `contentType` (int; 0 text, 1 image, 2 video, 3 audio, 7 sticker, 13 contact, 14 file, 15 location — `okline.enums.ContentType`), `createdTime`/`deliveredTime` (ms epoch), `contentMetadata` (dict — `OID`/`SID` for media objects, `STKPKGID`/`STKID` for stickers, `e2eeVersion` on sealed), `chunks` (list — present on Letter-Sealed messages), `location`.

Reading more than ~200: `getRecentMessagesV2` with huge `n` is wasteful — fetch 200, then page with `get_previous_messages` (see the transcript dumper below).

## 3. decrypt_message semantics

`api.decrypt_message(msg)` is **safe on any message dict** — internally `is_e2ee_message` checks `chunks` + `contentMetadata.e2eeVersion`; a non-sealed message is returned unchanged. Call it unconditionally; never branch on your own sealed-detection.

On a sealed message it returns a copy with:

- `text` / `location` restored from the plaintext,
- `contentMetadata.REPLACE` restored (the unsend/unpick UI payload),
- `contentMetadata.ENC_KM` and `FILE_NAME` restored for sealed media (this is exactly what `api.e2ee.download_sealed_media` consumes — see ./media.md),
- `chunks` cleared to `[]` and a `_decrypted: True` marker set.

It decrypts **your own sealed messages** read back from history, and both 1:1 and group messages (V1 and V2 framing), provided the E2EE keychain is loaded — `OkLine.from_tokens_file` restores it; a bare `OkLine(access_token=...)` does not, and `api.e2ee.is_ready()` is `False` (re-login or load keys; see ./session.md).

Decrypt can still fail per message (peer re-keyed, group key rotated before your key sync). Always wrap it:

```python
try:
    plain = api.decrypt_message(msg)
except Exception:
    plain = dict(msg, text="[encrypted — could not decrypt]")
```

E2EE errors (codes 84–99) come back as `LineApiError`; for history reads, catching broadly per message and marking is correct — do not let one bad message abort a transcript.

## 4. Searching history

There is no server-side message search. Search = page messages into memory and filter client-side. For "find messages containing X in chat Y over the last N":

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
try:
    chat_mid, needle, want = "c1234...", "invoice", 2000
    msgs = api.get_recent_messages(chat_mid, min(want, 200)) or []
    while len(msgs) < want and msgs:
        oldest = msgs[-1]
        page = api.get_previous_messages(
            chat_mid,
            oldest.get("id"),
            int(oldest.get("deliveredTime") or oldest.get("createdTime") or 0),
            count=min(want - len(msgs), 200),
        ) or []
        if not page:
            break
        msgs.extend(page)
    hits = []
    for m in msgs:
        try:
            text = api.decrypt_message(m).get("text") or ""
        except Exception:
            continue  # undecryptable sealed message — cannot match
        if needle.lower() in text.lower():
            hits.append((m.get("createdTime"), m.get("from"), text))
    for ts, sender, text in hits:
        print(ts, sender, text)
finally:
    api.close()
```

Searching *by sender*: filter on `m.get("from")` against a resolved mid (section 1). Searching across all chats: iterate `get_message_boxes()` ids — this is many API calls; keep `limit` modest and expect rate limiting (see Failure modes).

## 5. CLI shortcuts

```bash
python3 -m okline boxes                       # chat list: box id + unread count
python3 -m okline chatlog u1234... -n 100     # last 100 messages, E2EE-decrypted inline,
                                             # names resolved, oldest-first output
python3 -m okline backup c5678... -n 1000 -o group.json   # page back and dump raw JSON
```

`chatlog` prints `[encrypted — could not decrypt]` / `[encrypted — run okline login to load keys]` for sealed messages it cannot open — those strings are your signal the session's E2EE keys are stale or missing. Prefer `chatlog` for quick looks, Python for anything filtered. Full CLI coverage: the "How to run things" section of ../SKILL.md and docs/cli.md in the repo.

## 6. Backup export to JSON

The CLI `backup` pages backwards and writes raw (still-sealed) message dicts. Prefer decrypting during export when you can, so the file is useful without keys — but treat the exported file as sensitive chat content: keep it local, never commit it (same discipline as tokens.json).

```python
import json
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
try:
    chat_mid, want = "c5678...", 1000
    raw = api.get_recent_messages(chat_mid, min(want, 200)) or []
    while len(raw) < want and raw:
        oldest = raw[-1]
        page = api.get_previous_messages(
            chat_mid,
            oldest.get("id"),
            int(oldest.get("deliveredTime") or oldest.get("createdTime") or 0),
            count=min(want - len(raw), 200),
        ) or []
        if not page:
            break
        raw.extend(page)
    out = []
    for m in raw:
        try:
            m = api.decrypt_message(m)  # no-op on plain messages
        except Exception:
            m = dict(m, text="[encrypted — could not decrypt]")
        out.append(m)
    path = f"{chat_mid}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"saved {len(out)} messages -> {path}")
finally:
    api.close()
```

Paging stops early (fewer than `want`) when `get_previous_messages` returns `[]` — that is the start of history, not an error.

## 7. Read receipts

Two directions:

- **Who read my messages**: `api.get_message_read_range(chat_ids)` — pass a list of chat mids, get back a list of `{chatId, ranges}` where `ranges` maps each member mid to `[{startMessageId, endMessageId, startTime, endTime}]` — the contiguous span that member has read. Use the sender's own message ids to interpret "read up to X".

```python
from okline import OkLine

def show_read_state(api, chat_mid):
    msgs = api.get_recent_messages(chat_mid, 10) or []
    if not msgs:
        print("no messages in this chat")
        return
    last_id = msgs[0]["id"]  # newest message id in the box
    for entry in api.get_message_read_range([chat_mid]) or []:
        for member, spans in (entry.get("ranges") or {}).items():
            read_to = spans[-1]["endMessageId"] if spans else None
            print(member, "read up to", read_to, "meets newest" if read_to == last_id else "behind")

api = OkLine.from_tokens_file("tokens.json")
try:
    show_read_state(api, "c5678...")
finally:
    api.close()
```

- **Mark a chat read** (visible to others as the blue checkmark — get explicit user confirmation before calling): `api.send_chat_checked(chat_mid, last_message_id)` (alias `api.mark_as_read`). `last_message_id` is the newest message id of that chat (e.g. `msgs[0]["id"]` from a recent fetch, or the id from a just-received operation). Do not call it in a loop over history; once per chat, at the current newest message, is the semantic.

## 8. Dump a chat to a readable transcript (complete)

Boxes -> resolve names -> page back -> decrypt -> chronological text transcript with timestamps, senders, and content markers. This is the snippet to reach for whenever the user says "show me the conversation".

```python
import sys
import time
from okline import OkLine
from okline.enums import ContentType

def load_names(api):
    names = {}
    ids = api.get_all_contact_ids() or []
    for mid, wrapper in (api.get_contacts(ids).get("contacts", {}) or {}).items():
        c = wrapper.get("contact", wrapper) if isinstance(wrapper, dict) else {}
        names[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
    me = api.get_profile() or {}
    names[me.get("mid")] = me.get("displayName") or "me"
    return names

def label(m):
    if m.get("text"):
        return m["text"]
    ct = m.get("contentType") or 0
    meta = m.get("contentMetadata") or {}
    try:
        kind = ContentType(int(ct)).name.lower()
    except ValueError:
        kind = f"type{ct}"
    if kind == "sticker":
        return f"[sticker {meta.get('STKPKGID')}/{meta.get('STKID')}]"
    if kind in ("image", "video", "audio", "file"):
        return f"[{kind}: OID={meta.get('OID')}]"
    if kind == "location" and m.get("location"):
        loc = m["location"]
        return f"[location {loc.get('latitude')},{loc.get('longitude')} {loc.get('title') or ''}]".rstrip()
    return f"[{kind}]"

def main(chat_mid, count):
    api = OkLine.from_tokens_file("tokens.json")
    try:
        names = load_names(api)
        msgs = api.get_recent_messages(chat_mid, min(count, 200)) or []
        while len(msgs) < count and msgs:
            oldest = msgs[-1]
            page = api.get_previous_messages(
                chat_mid,
                oldest.get("id"),
                int(oldest.get("deliveredTime") or oldest.get("createdTime") or 0),
                count=min(count - len(msgs), 200),
            ) or []
            if not page:
                break
            msgs.extend(page)
        for m in reversed(msgs):  # newest-first -> chronological
            try:
                m = api.decrypt_message(m)
            except Exception:
                m = dict(m, text="[encrypted — could not decrypt]")
            sender = names.get(m.get("from")) or m.get("from") or "?"
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(m.get("createdTime") or 0) / 1000))
            print(f"{ts} {sender}: {label(m)}")
        print(f"\n{len(msgs)} message(s)")
    finally:
        api.close()

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 200)
```

Run as `python3 transcript.py c5678... 500`. For live arrival instead of history, use the Bot / operation stream (./events-bots.md); for the media behind `[image: OID=...]` markers, see ./media.md (plain and sealed).

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `LineApiError` code **10005** REQUEST_INVALID_HMAC on every call | Node.js missing / not on PATH | Ensure `node >= 18` on PATH (or set `LINE_NODE`); see ./session.md |
| `LineAuthError` codes **1/7/8** | session dead / account gone | Full re-login (qr_login) — see ./session.md |
| Code **119 / 401** mid-read | token expired | Handled automatically — the client refreshes and re-saves tokens.json; only act if it repeats |
| Sealed text shows as `[encrypted — run okline login to load keys]` / `api.e2ee.is_ready()` False | keychain not loaded | Use `OkLine.from_tokens_file` (not a bare token); if still False, re-login to re-register keys (./session.md) |
| One message `[encrypted — could not decrypt]` but others open | peer re-keyed / group key rotated | Not fatal — skip it; a fresh E2EE handshake on next send usually resyncs (./messaging.md) |
| `get_messages_by_ids` fails or returns nothing | endpoint arg shape is inferred, unverified against the wire | Use `get_recent_messages` + `get_previous_messages` paging instead |
| Box missing from `get_message_boxes()` | chat inactive or archived | Pass `active_only=False` |
| Code **4** EXCESSIVE_ACCESS / **35** ABUSE_BLOCK while paging many chats | rate limiting | Slow down; install `api.transport.rate_limiter = RateLimiter(rate=3, per=1.0)` (`from okline.ratelimit import RateLimiter`); back off and retry — history reads of many chats should be batched, not looped hot |

General error-code table: ./errors.md. Sending (including reacting/unsending on messages you just read): ./messaging.md. Never print token values or export session contents anywhere — tokens.json and chat dumps stay on this machine.

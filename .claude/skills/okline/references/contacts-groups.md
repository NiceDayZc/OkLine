# Contacts, Groups, Rooms & Profile

Mid shapes: users are `u...`, groups (modern "chats") are `c...`, legacy rooms are `r...`. Get a client first (all snippets assume it): `from okline import OkLine; api = OkLine.from_tokens_file("tokens.json")` and `api.close()` when done. For sending to any of these mids see ./messaging.md.

All methods below return **raw wire dicts** unless noted. Wrap them with `okline.entities` dataclasses (`Contact`, `Group`, `Room`, `Profile`) for attribute access — each keeps the original payload on `.raw`.

## Listing and resolving contacts

```python
from okline import OkLine
from okline.entities import Contact, parse_contacts

api = OkLine.from_tokens_file("tokens.json")
ids = api.get_all_contact_ids() or []          # list[str] of contact mids
res = api.get_contacts(ids)                    # auto-chunked at 100/call
contacts: dict[str, Contact] = parse_contacts(res)   # {mid: Contact}
for mid, c in sorted(contacts.items(), key=lambda kv: kv[1].name.lower()):
    print(mid, c.name, c.status_message, "OFFICIAL" if c.is_official else "")
api.close()
```

- `api.get_contacts(mids)` calls `getContactsV2` and returns `{"contacts": {mid: {"contact": {...}}}}`. It chunks at 100 mids per request automatically (server rejects more with code 6 `INVALID_LENGTH`), so pass any count.
- `Contact.name` is the right display name — it prefers `displayNameOverridden` (the user's local nickname) over `displayName`, matching the official client. `Contact.is_official` (`capableBuddy`) marks official accounts.
- Name-search a contact (what the CLI's `find` does — `python3 -m okline find QUERY`):

```python
from okline import OkLine
from okline.entities import parse_contacts

api = OkLine.from_tokens_file("tokens.json")
q = "alice"
hits = {m: c for m, c in parse_contacts(api.get_contacts(api.get_all_contact_ids() or [])).items()
        if q in c.name.lower()}
print(hits or "no match")
api.close()
```

Resolving a name to a mid is always two steps (list + filter). There is no server-side search-by-name; `find_contact_by_userid` matches the LINE ID (`userid`), not the display name.

## Finding users by ID / phone

```python
from okline import OkLine
from okline.exceptions import LineApiError

api = OkLine.from_tokens_file("tokens.json")
try:
    c = api.find_contact_by_userid("some_line_id") or {}   # by LINE userid, NOT name
except LineApiError:
    c = {}          # unknown id (code 5 "Cannot find") or malformed (code 0)
if isinstance(c, dict) and c.get("mid"):
    print(c["mid"], c.get("displayName"), c.get("statusMessage"))
else:
    print("not found")
api.close()
```

- Returns a single Contact dict on a hit; a miss **raises `LineApiError`** (code 5 `Cannot find` for a well-formed unknown id, code 0 `userid is invalid` for a malformed one) — catch it rather than testing for `None`. Also raised when the user disallows search-by-userid.
- `api.find_contacts_by_phone(phones)` takes an iterable of **international** numbers, e.g. `["+819012345678"]`; spaces/`+` allowed. Returns a mapping of phone -> Contact.
- To fetch the *full* Contact of a known mid (status, relation, picture): `api.get_contacts([mid])` — not `get_profile`, which is your own profile only.
- `api.find_and_add_contacts_by_mid(mids, contact_type=ContactType.MID)` exists but its positional arg order is **inferred, not wire-confirmed** (the extension never invokes it). Prefer `find_contact_by_userid` + `add_friend_by_mid`; if you must use it, verify the result before trusting it.

## Adding friends, blocking, favorites, hide

```python
from okline import OkLine
from okline.exceptions import LineApiError

api = OkLine.from_tokens_file("tokens.json")
mid = None
try:
    c = api.find_contact_by_userid("some_line_id") or {}
    mid = c.get("mid")
except LineApiError:
    pass  # unknown or malformed LINE id
if mid:
    api.add_friend_by_mid(mid)          # RelationService.addFriendByMid
api.close()
```

Failure modes of `add_friend_by_mid` — it raises `okline.exceptions.LineApiError`; compare `exc.code` against `okline.enums.AddFriendResult`: `1` INVALID_TARGET_USER, `2` AGE_VALIDATION, `3` TOO_MANY_FRIENDS, `4` TOO_MANY_REQUESTS (back off), `5` MALFORMED_REQUEST. The success-response shape is not modeled, so don't inspect fields on it.

Related endpoints (all verified in `services/contacts.py`):

- `api.block_contact(mid)` / `api.unblock_contact(mid)` / `api.get_blocked_contact_ids()` — **ask the user before blocking**; it is a destructive/visible action.
- `api.set_favorite(mid, True)` / `api.set_favorite(mid, False)` — favorite a *contact* (a `ContactSetting.FAVORITE` update).
- `api.hide_contact(mid, True)` — hide from the friend list (`False` unhides).
- `api.update_contact_setting(mid, flag, value)` — raw escape hatch; `flag` is an `okline.enums.ContactSetting` int, `value` a string (e.g. `"true"`). Use it for `CONTACT_SETTING_NOTIFICATION_DISABLE` (mute) or `CONTACT_SETTING_DISPLAY_NAME_OVERRIDE` (rename locally), which have no helper.
- `api.get_favorite_mids()` — favorited contact mids.
- Recommendations: `api.get_recommendation_ids()`, `api.get_blocked_recommendation_ids()`, `api.block_recommendation(mid)` (dismiss a suggestion). Feed recommendation mids through `get_contacts` for names.
- `api.get_buddy_detail(buddy_mid)` — official-account details (`Talk.BuddyService`).
- `api.get_target_profile_notice(target_user_mid)` — profile notice shown before adding someone.

## Groups and rooms

Modern LINE groups are unified **chats** (`Chat` with `type` GROUP = 0, ROOM = 1, PEER = 2 per `okline.enums.ChatType`); legacy multi-person rooms still use separate room endpoints. Prefer the chat endpoints.

### Listing groups and reading members

```python
from okline import OkLine
from okline.entities import Group, parse_contacts

api = OkLine.from_tokens_file("tokens.json")
chats = api.get_all_chat_mids() or {}          # {memberChatMids: [...], invitedChatMids: [...]}
member_mids = chats.get("memberChatMids", [])
invited = chats.get("invitedChatMids", [])     # groups you're invited to but haven't accepted
groups = [Group.from_dict(g) for g in api.get_chats(member_mids).get("chats", []) or []]
for g in groups:
    print(g.chat_mid, g.member_count, g.name)

# members of one group, with names:
g = groups[0]
names = parse_contacts(api.get_contacts(g.member_mids))   # get_contacts chunks at 100
for mid in g.member_mids:
    print(" ", mid, names.get(mid).name if names.get(mid) else "(unknown)")
api.close()
```

- `api.get_chats(chat_mids, with_members=True, with_invitees=True)` returns `{"chats": [Chat]}` and chunks at 100 per call automatically. Members live inside each chat dict at `extra.groupExtra.memberMids` (invitees at `extra.groupExtra.inviteeMids`) — `Group.from_dict` extracts both into `.member_mids` / `.invitee_mids`, so use it rather than digging in the raw dict.
- `invitedChatMids` are pending invitations: pair with `accept_chat_invitation` / `reject_chat_invitation` (below).
- CLI equivalents: `python3 -m okline groups` and `python3 -m okline members <chat_mid>`.
- Legacy rooms: `api.get_rooms(room_mids)` -> list of Room dicts; `Room.from_dict` gives `.mid` and `.member_mids`. Room mids start with `r`.

### Creating a group

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
res = api.create_chat("Weekend Trip", ["u1234...", "u5678..."])   # alias: api.create_group
chat = res.get("chat") if isinstance(res, dict) else None
print(chat["chatMid"] if chat else res)
api.close()
```

`create_chat(name, target_user_mids, *, chat_type=ChatType.GROUP)` returns `{"chat": Chat}` — the new `chat.chatMid` is what you pass to send/invite/kick. **Only create groups or invite people when the user explicitly named the members.**

### Membership operations (chat model)

All take the chat mid; all are visible/destructive to other members — get explicit user instruction first.

- Invite: `api.invite_into_chat(chat_mid, [mid, ...])`
- Kick: `api.kick_from_chat(chat_mid, [mid, ...])` (wire: `deleteOtherFromChat`)
- Cancel a pending invite: `api.cancel_chat_invitation(chat_mid, [mid, ...])`
- Leave: `api.leave_chat(chat_mid)` (wire: `deleteSelfFromChat`) — irreversible without a re-invite
- Accept/reject an incoming invitation: `api.accept_chat_invitation(chat_mid)` / `api.reject_chat_invitation(chat_mid)`
- Legacy room variants: `api.invite_into_room(room_mid, contact_ids)`, `api.leave_room(room_mid)`

### Renaming, favorites, chat settings

- Rename: `api.rename_chat(chat_mid, "New name")` — sends a minimal skeleton `{chatMid, chatName, type}` with `UpdateChatRequestAttribute.NAME`.
- Favorite a chat (pins it): `api.set_chat_favorite(chat_mid, <epoch_ms>)`; pass `0` to un-favorite. The timestamp is stringified on the wire — pass the int, the method handles it. Note this favorites a *chat*; to favorite a *contact* use `set_favorite` above.
- Disallow join-by-ticket: `api.set_chat_prevented_join_by_ticket(chat_mid, True)`.
- Full-entity escape hatch: `api.update_chat(chat_dict, int(UpdateChatRequestAttribute.X))` — pass the **complete** Chat dict from `get_chats` with your change applied. The minimal-skeleton helpers above do not round-trip sub-structs like `picturePath`/`extra`; use the full entity when changing those.

## Profile and settings (own account)

`api.get_profile()` returns **your own** Profile dict (`mid`, `displayName`, `statusMessage`, `picturePath`, `regionCode`, ...) and side-effect updates the client's cached mid. It cannot fetch another user's profile — for any other mid use `api.get_contacts([mid])`. `Profile.from_dict(api.get_profile())` gives typed access.

```python
from okline import OkLine
from okline.entities import Profile

api = OkLine.from_tokens_file("tokens.json")
me = Profile.from_dict(api.get_profile())
print(me.mid, me.display_name, me.status_message, me.region_code)
api.set_display_name("New Name")          # visible to everyone — ask the user first
api.set_status_message("Working on it")   # sticon/mention metadata via meta= param
api.close()
```

- `api.set_display_name(name)` and `api.set_status_message(message)` are the helpers; `api.update_profile_attributes({int(ProfileAttribute.X): value})` is the raw form (`ProfileAttribute.DISPLAY_NAME=2`, `STATUS_MESSAGE=16`, ...).
- `api.get_settings()` -> your notification/privacy settings; `api.get_settings_attributes2([int(a) for a in attrs])` reads selected `SettingsAttribute`s; `api.update_settings_attributes2(attrs, settings)` writes them.
- Read-only extras: `api.get_server_time()`, `api.get_configurations("JP")` (first positional arg is the region code).
- Changing your display name/status is account-visible — confirm with the user before writing.

## Failure modes and errors

Catch `from okline.exceptions import LineApiError, LineAuthError` and inspect `.code`:

- `LineApiError` code `6` (`INVALID_LENGTH`) — too many mids in one call. `get_contacts`/`get_chats` already chunk at 100, so seeing this means you passed a hand-built list to a raw endpoint; chunk it yourself.
- Codes `4` (`EXCESSIVE_ACCESS`) / `35` (`ABUSE_BLOCK`) — slow down: install `api.transport.rate_limiter = RateLimiter(rate=3, per=1.0)` (from `okline.ratelimit`) and add sleeps. Never loop these calls tightly.
- `LineAuthError` codes `{1, 7, 8}` or `10004`/401 — session invalid/gone; re-login (see the skill's session/auth reference). Code 119 is auto-refreshed in place.
- `get_chats` returning `{"chats": []}` — chat mid not found (wrong/kicked/left). Check membership via `get_all_chat_mids()` first.
- `add_friend_by_mid` rejections — read `exc.code` against `AddFriendResult` (list above); `4` means back off, `2` age validation, `3` friend limit.
- Names showing `(unknown)` — the mid isn't a contact (e.g. a group member who isn't your friend); `get_contacts` still returns their public profile.

## Safety

Read ops (list, get_chats, get_contacts, find) are safe to run freely. Ask the user before: `add_friend_by_mid`, `block_contact`, `hide_contact`, `kick_from_chat`, `invite_into_chat`, `leave_chat`, `create_chat`, and any profile write. Never add, invite, or message a third party the user didn't explicitly name. Rate-limit every loop over contacts/groups.

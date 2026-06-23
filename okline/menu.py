"""Interactive, menu-driven OkLine console — the full client by number.

Run ``okline`` with no arguments (or ``okline menu``) for a soft-coloured
terminal UI: a categorised, numbered menu you drive by typing numbers, no
commands to memorise.  On first use it goes straight to QR login and saves the
session.  Every CLI capability is reachable here.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Union

from . import ui
from ._util import reconfigure_stdout_utf8

# a menu item is either a leaf (label, action) or a submenu (label, [items])
Action = Callable[[Any], None]
Item = tuple[str, Union[Action, "list[Item]"]]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _names(api: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    ids = api.get_all_contact_ids() or []
    for i in range(0, len(ids), 100):
        res = api.get_contacts(ids[i : i + 100])
        for mid, w in (res.get("contacts", {}) or {}).items():
            c = w.get("contact", w) if isinstance(w, dict) else {}
            out[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
    return out


def _resolve_to(api: Any, to: str) -> str | None:
    """A mid, or a (unique) contact-name match -> its mid."""
    if not to:
        return None
    if to[:1].lower() in ("u", "c", "r") and len(to) >= 20:
        return to
    matches = [(m, n) for m, n in _names(api).items() if to.lower() in n.lower()]
    if len(matches) == 1:
        print(ui.dim(f"  -> {matches[0][1]} ({matches[0][0]})"))
        return matches[0][0]
    if not matches:
        print(ui.warn(f"  no contact matching {to!r}"))
    else:
        print(
            ui.warn(f"  {len(matches)} match {to!r}: ") + ", ".join(n for _, n in matches[:8])
        )
    return None


def _ask_to(api: Any, label: str = "send to (mid or name)") -> str | None:
    return _resolve_to(api, ui.prompt(label))


def _kv(rows: list[tuple[str, Any]]) -> None:
    ui.table([[ui.dim(k), str(v) if v is not None else ""] for k, v in rows])


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------
def _qr_login(path: str) -> Any | None:
    from .client import OkLine
    from .hmac_signer import LtsmBridge
    from .qrterm import print_qr

    if not LtsmBridge.is_available():
        print(
            ui.warn("  Node.js 18+ is required to sign requests (X-Hmac).")
            + "\n"
            + ui.dim("  Install from https://nodejs.org, run `node --version`, then retry.")
        )
        return None
    api = OkLine(record=False)
    print(
        "\n"
        + ui.title("Scan this QR with the LINE app")
        + ui.dim("  (Add friends › QR)")
        + "\n"
    )

    def on_qr(url: str) -> None:
        try:
            print_qr(url)
        except ModuleNotFoundError:
            print(url, "\n" + ui.dim("(pip install qrcode for an inline QR)"))

    try:
        res = api.auth.qr_login(
            on_qr=on_qr,
            on_pin=lambda pin: print(
                "\n" + ui.accent(f"  {ui.GLYPH['arrow']}  Confirm this PIN: {pin}") + "\n"
            ),
        )
    except Exception as exc:
        print(ui.warn(f"login failed: {exc}"))
        api.close()
        return None
    if not res.access_token:
        print(ui.warn("login did not complete."))
        api.close()
        return None
    info = getattr(api.auth, "last_e2ee_login", None)
    if info:
        try:
            api.e2ee.load_from_login(info["curve_key_id"], info["metadata"])
        except Exception:
            pass
    api.save_tokens(path)
    print(ui.ok(f"Logged in — session saved to {path}.") + "\n")
    return api


def _ensure_session(args: Any) -> Any | None:
    import os

    from .__main__ import _make_client

    api = _make_client(args)
    if api.tokens.access_token:
        return api
    api.close()
    path = getattr(args, "tokens_file", None) or "tokens.json"
    if os.path.exists(path):
        from .client import OkLine

        return OkLine.from_tokens_file(path)
    print(ui.dim("  No saved session — starting QR login…\n"))
    try:
        return _qr_login(path)
    except KeyboardInterrupt:
        return None


# ---------------------------------------------------------------------------
# actions — me & account
# ---------------------------------------------------------------------------
def act_whoami(api: Any) -> None:
    p = api.get_profile()
    chats = api.get_all_chat_mids() or {}
    _kv(
        [
            ("name", p.get("displayName")),
            ("mid", p.get("mid")),
            ("user id", p.get("userid")),
            ("status", p.get("statusMessage")),
            ("contacts", len(api.get_all_contact_ids() or [])),
            (
                "groups",
                f"{len(chats.get('memberChatMids', []))}"
                f" (+{len(chats.get('invitedChatMids', []))} invited)",
            ),
            ("favorites", len(api.get_favorite_mids() or [])),
            ("blocked", len(api.get_blocked_contact_ids() or [])),
            ("e2ee", "ready" if api.e2ee.is_ready() else "off"),
        ]
    )


def act_my_profile(api: Any) -> None:
    print(json.dumps(api.get_profile(), ensure_ascii=False, indent=2))


def act_set_name(api: Any) -> None:
    name = ui.prompt("new display name")
    if name:
        api.set_display_name(name)
        print(ui.ok(f"display name -> {name!r}"))


def act_set_status(api: Any) -> None:
    msg = ui.prompt("new status message")
    api.set_status_message(msg)
    print(ui.ok(f"status -> {msg!r}"))


def act_settings(api: Any) -> None:
    s = api.get_settings()
    if isinstance(s, dict):
        _kv([(k, v) for k, v in list(s.items())[:30]])
    else:
        print(s)


def act_logout(api: Any) -> None:
    if ui.prompt("really log out + delete the session?", "n").lower() not in ("y", "yes"):
        return
    try:
        api.auth.logout()
    except Exception as exc:
        print(ui.warn(f"  server logout failed: {exc}"))
    import os

    path = getattr(api, "_session_path", None) or "tokens.json"
    if os.path.exists(path):
        os.remove(path)
        print(ui.ok(f"removed {path}"))
    print(ui.dim("  restart `okline` to log in again."))


# ---------------------------------------------------------------------------
# actions — contacts
# ---------------------------------------------------------------------------
def act_contacts(api: Any) -> None:
    q = ui.prompt("filter by name (blank = all)").lower()
    rows = sorted(_names(api).items(), key=lambda kv: kv[1].lower())
    if q:
        rows = [(m, n) for m, n in rows if q in n.lower()]
    ui.table([[ui.dim(m), n] for m, n in rows[:300]])
    print(ui.dim(f"  {len(rows)} contact(s)"))


def act_find(api: Any) -> None:
    q = ui.prompt("name to find").lower()
    if not q:
        return
    ui.table([[ui.dim(m), n] for m, n in _names(api).items() if q in n.lower()])


def act_profile_of(api: Any) -> None:
    mid = ui.prompt("user mid")
    if not mid:
        return
    name = _names(api).get(mid, "(not in your contacts)")
    print(ui.title(f"  {name}") + ui.dim(f"  {mid}"))
    try:
        detail = api.get_buddy_detail(mid) or {}
        if isinstance(detail, dict):
            _kv([(k, v) for k, v in detail.items() if v not in (None, "", [], {})])
    except Exception as exc:
        print(ui.dim(f"  (no buddy detail: {exc})"))


def act_add_friend(api: Any) -> None:
    who = ui.prompt("mid (U…) or LINE ID")
    if not who:
        return
    mid: str | None = who if (who[:1].lower() == "u" and len(who) >= 20) else None
    if mid is None:
        c = api.find_contact_by_userid(who) or {}
        mid = c.get("mid") if isinstance(c, dict) else None
        if not mid:
            print(ui.warn("  not found"))
            return
        print(ui.dim(f"  -> {c.get('displayName')} ({mid})"))
    api.add_friend_by_mid(mid)
    print(ui.ok(f"added {mid}"))


def act_search_user(api: Any) -> None:
    uid = ui.prompt("LINE ID (e.g. nb.vtg)")
    if not uid:
        return
    c = api.find_contact_by_userid(uid) or {}
    if not isinstance(c, dict) or not c.get("mid"):
        print(ui.warn("  not found"))
        return
    _kv(
        [
            ("mid", c.get("mid")),
            ("name", c.get("displayName")),
            ("status", c.get("statusMessage")),
        ]
    )
    if ui.prompt("add as friend?", "n").lower() in ("y", "yes"):
        api.add_friend_by_mid(c["mid"])
        print(ui.ok("added"))


def act_block(api: Any) -> None:
    sub = ui.prompt("(l)ist / (b)lock / (u)nblock", "l").lower()[:1]
    if sub == "l":
        names = _names(api)
        ui.table(
            [[ui.dim(m), names.get(m, "")] for m in (api.get_blocked_contact_ids() or [])]
        )
    elif sub == "b":
        mid = ui.prompt("mid to block")
        if mid:
            api.block_contact(mid)
            print(ui.ok("blocked"))
    elif sub == "u":
        mid = ui.prompt("mid to unblock")
        if mid:
            api.unblock_contact(mid)
            print(ui.ok("unblocked"))


def act_favorites(api: Any) -> None:
    sub = ui.prompt("(l)ist / (a)dd / (r)emove", "l").lower()[:1]
    if sub == "l":
        names = _names(api)
        ui.table([[ui.dim(m), names.get(m, "")] for m in (api.get_favorite_mids() or [])])
    elif sub in ("a", "r"):
        mid = _ask_to(api, "chat/contact mid or name")
        if mid:
            api.set_chat_favorite(mid, 1 if sub == "a" else 0)
            print(ui.ok("favorited" if sub == "a" else "unfavorited"))


def act_export_contacts(api: Any) -> None:
    fmt = ui.prompt("format (csv/json)", "csv").lower()
    path = ui.prompt("output file", f"contacts.{fmt}")
    rows = sorted(_names(api).items(), key=lambda kv: kv[1].lower())
    if fmt == "json":
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                [{"mid": m, "name": n} for m, n in rows], fh, ensure_ascii=False, indent=2
            )
    else:
        import csv

        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["mid", "name"])
            w.writerows(rows)
    print(ui.ok(f"wrote {len(rows)} contacts -> {path}"))


# ---------------------------------------------------------------------------
# actions — groups & chats
# ---------------------------------------------------------------------------
def act_groups(api: Any) -> None:
    from .entities import Group

    chats = api.get_all_chat_mids() or {}
    member = chats.get("memberChatMids", [])
    print(
        ui.dim(f"  member {len(member)}   invited {len(chats.get('invitedChatMids', []))}\n")
    )
    rows = []
    for g in api.get_chats(member).get("chats", []) if member else []:
        grp = Group.from_dict(g)
        rows.append([ui.dim(grp.chat_mid), f"({grp.member_count})", grp.name])
    ui.table(rows)


def act_members(api: Any) -> None:
    from .entities import Group

    gid = ui.prompt("group mid")
    if not gid:
        return
    chats = api.get_chats([gid]).get("chats", [])
    if not chats:
        print(ui.warn("  group not found"))
        return
    grp = Group.from_dict(chats[0])
    print(ui.title(f"  {grp.name}") + ui.dim(f"  ({grp.member_count} members)\n"))
    names: dict[str, str] = {}
    for i in range(0, len(grp.member_mids), 100):
        res = api.get_contacts(grp.member_mids[i : i + 100])
        for mid, w in (res.get("contacts", {}) or {}).items():
            c = w.get("contact", w) if isinstance(w, dict) else {}
            names[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
    ui.table([[ui.dim(mid), names.get(mid, "")] for mid in grp.member_mids])


def act_leave(api: Any) -> None:
    gid = _ask_to(api, "group mid to leave")
    if gid and ui.prompt(f"leave {gid}?", "n").lower() in ("y", "yes"):
        api.leave_chat(gid)
        print(ui.ok("left"))


def act_accept(api: Any) -> None:
    gid = ui.prompt("group mid to accept invitation")
    if gid:
        api.accept_chat_invitation(gid)
        print(ui.ok("accepted"))


def act_boxes(api: Any) -> None:
    boxes = api.get_message_boxes(limit=20)
    rows = []
    for b in boxes.get("messageBoxes", []) if isinstance(boxes, dict) else []:
        if isinstance(b, dict):
            rows.append([ui.dim(str(b.get("id"))), f"unread={b.get('unreadCount', '?')}"])
    ui.table(rows)


# ---------------------------------------------------------------------------
# actions — messaging
# ---------------------------------------------------------------------------
def _sent(res: Any) -> None:
    print(ui.ok("sent") + ui.dim(f"  id={res.get('id') if isinstance(res, dict) else res}"))


def act_send_text(api: Any) -> None:
    to = _ask_to(api)
    if not to:
        return
    text = ui.prompt("message")
    if text:
        _sent(api.send_text(to, text))


def act_send_sticker(api: Any) -> None:
    to = _ask_to(api)
    if not to:
        return
    pkg = ui.prompt("package id", "11537")
    stk = ui.prompt("sticker id", "52002734")
    _sent(api.send_sticker(to, pkg, stk))


def act_send_location(api: Any) -> None:
    to = _ask_to(api)
    if not to:
        return
    lat = float(ui.prompt("latitude", "35.6586"))
    lon = float(ui.prompt("longitude", "139.7454"))
    _sent(api.send_location(to, lat, lon, title=ui.prompt("title", "")))


def act_send_media(api: Any) -> None:
    to = _ask_to(api)
    if not to:
        return
    path = ui.prompt("path to image/file")
    if not path:
        return
    is_img = path.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "gif", "webp")
    _sent(api.send_image(to, path) if is_img else api.send_file(to, path))


def act_react(api: Any) -> None:
    from .enums import PredefinedReactionType

    mid = ui.prompt("message id")
    if not mid:
        return
    r = ui.prompt("reaction NICE/LOVE/FUN/AMAZING/SAD/OMG", "NICE").upper()
    try:
        api.react(mid, int(PredefinedReactionType[r]))
        print(ui.ok("reacted"))
    except KeyError:
        print(ui.warn("  unknown reaction"))


def act_unsend(api: Any) -> None:
    mid = ui.prompt("message id to unsend")
    if mid:
        api.unsend_message(mid)
        print(ui.ok("unsent"))


def act_reply(api: Any) -> None:
    to = _ask_to(api)
    if not to:
        return
    rel = ui.prompt("related (replied-to) message id")
    text = ui.prompt("reply text")
    if rel and text:
        _sent(api.reply_text(to, text, rel))


def act_broadcast(api: Any) -> None:
    from .enums import ErrorCode
    from .exceptions import LineApiError

    text = ui.prompt("message to broadcast")
    raw = ui.prompt("targets (space-separated mids/names)")
    if not text or not raw:
        return
    targets = [t for t in (_resolve_to(api, x) for x in raw.split()) if t]
    if not targets or ui.prompt(f"send to {len(targets)} target(s)?", "n").lower() not in (
        "y",
        "yes",
    ):
        return
    abuse = {int(ErrorCode.EXCESSIVE_ACCESS), int(ErrorCode.ABUSE_BLOCK)}
    ok = 0
    for mid in targets:
        try:
            api.send_text(mid, text)
            ok += 1
            print(ui.dim(f"  sent -> {mid}"))
        except LineApiError as exc:
            print(ui.warn(f"  FAIL -> {mid}: {exc}"))
            if getattr(exc, "code", None) in abuse:
                print(ui.warn("  rate-limited — stopping."))
                break
    print(ui.ok(f"{ok}/{len(targets)} delivered"))


# ---------------------------------------------------------------------------
# actions — read & history
# ---------------------------------------------------------------------------
def _print_log(api: Any, msgs: list, names: dict[str, str]) -> None:
    for m in reversed(msgs):
        if not isinstance(m, dict):
            continue
        text = m.get("text")
        if m.get("chunks"):
            if api.e2ee.is_ready():
                try:
                    text = api.decrypt_message(m).get("text")
                except Exception:
                    text = ui.dim("[encrypted]")
            else:
                text = ui.dim("[encrypted — log in to load keys]")
        who = names.get(m.get("from") or "") or (m.get("from") or "")[:10]
        print(f"  {ui.dim(who.rjust(14))}  {text or ui.dim('<non-text>')}")


def act_chatlog(api: Any) -> None:
    cid = ui.prompt("chat mid")
    if not cid:
        return
    n = int(ui.prompt("how many", "30") or 30)
    print()
    _print_log(api, api.get_recent_messages(cid, n) or [], _names(api))


def act_recent_raw(api: Any) -> None:
    cid = ui.prompt("chat mid")
    if not cid:
        return
    msgs = api.get_recent_messages(cid, int(ui.prompt("how many", "10") or 10)) or []
    print(json.dumps(msgs, ensure_ascii=False, indent=2))


def act_search_messages(api: Any) -> None:
    cid = ui.prompt("chat mid")
    kw = ui.prompt("keyword").lower()
    if not cid or not kw:
        return
    names = _names(api)
    hits = []
    for m in api.get_recent_messages(cid, 200) or []:
        text = m.get("text") if isinstance(m, dict) else None
        if text and kw in text.lower():
            hits.append(m)
    print(ui.dim(f"  {len(hits)} match(es)\n"))
    _print_log(api, hits, names)


def act_backup(api: Any) -> None:
    cid = ui.prompt("chat mid")
    if not cid:
        return
    out = ui.prompt("output file", f"{cid}.json")
    msgs = api.get_recent_messages(cid, 200) or []
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(msgs, fh, ensure_ascii=False, indent=2)
    print(ui.ok(f"saved {len(msgs)} messages -> {out}"))


# ---------------------------------------------------------------------------
# actions — live & bots
# ---------------------------------------------------------------------------
def _run_bot(api: Any, on_msg: Action, banner: str) -> None:
    from .bot import Bot

    bot = Bot(api)
    bot.on_message(on_msg)
    print(ui.dim(f"  {banner}  (Ctrl-C to return)"))
    try:
        bot.run()
    except KeyboardInterrupt:
        pass


def act_watch(api: Any) -> None:
    echo = ui.prompt("echo replies?", "n").lower() in ("y", "yes")

    def on_msg(ctx: Any) -> None:
        where = "group" if ctx.is_group else "dm"
        print(f"  {ui.dim('[' + where + ']')} {ui.dim(ctx.sender)}: {ctx.text!r}")
        if echo and ctx.text:
            ctx.reply(f"you said: {ctx.text}")

    _run_bot(api, on_msg, "watching")


def act_autoreply(api: Any) -> None:
    rules: dict[str, str] = {}
    print(ui.dim("  enter rules as  keyword=reply  (blank line to start)"))
    while True:
        line = ui.prompt("rule")
        if not line:
            break
        if "=" in line:
            k, v = line.split("=", 1)
            rules[k.strip().lower()] = v.strip()
    if not rules:
        return

    def on_msg(ctx: Any) -> None:
        if not ctx.text:
            return
        low = ctx.text.lower()
        for kw, reply in rules.items():
            if kw in low:
                ctx.reply(reply)
                print(ui.dim(f"  matched {kw!r} -> replied"))
                break

    _run_bot(api, on_msg, f"auto-reply ({len(rules)} rules)")


def act_notify(api: Any) -> None:
    kw = ui.prompt("alert keyword (blank = all)").lower()

    def on_msg(ctx: Any) -> None:
        if kw and kw not in (ctx.text or "").lower():
            return
        where = "group" if ctx.is_group else "dm"
        print(f"  {ui.dim('[' + where + ']')} {ctx.sender}: {ctx.text or '<non-text>'}")

    _run_bot(api, on_msg, "notifying")


# ---------------------------------------------------------------------------
# actions — E2EE
# ---------------------------------------------------------------------------
def act_e2ee_status(api: Any) -> None:
    _kv(
        [
            ("ready", "yes" if api.e2ee.is_ready() else "no"),
            ("keys loaded", len(getattr(api.e2ee, "my_keys", {}))),
            ("latest key id", getattr(api.e2ee, "latest_key_id", None)),
            ("my mid", getattr(api.e2ee, "my_mid", None)),
        ]
    )
    if not api.e2ee.is_ready():
        print(ui.dim("  log in with the menu's QR (restart) to load E2EE keys."))


def act_e2ee_send(api: Any) -> None:
    to = _ask_to(api, "encrypt & send to (mid or name)")
    if not to:
        return
    text = ui.prompt("message")
    if text:
        _sent(api.send_encrypted_text(to, text))


def act_e2ee_decrypt(api: Any) -> None:
    if not api.e2ee.is_ready():
        print(ui.warn("  E2EE keys not loaded — log in first."))
        return
    cid = ui.prompt("chat mid (to find the latest sealed message)")
    if not cid:
        return
    sealed = next(
        (
            m
            for m in reversed(api.get_recent_messages(cid, 20) or [])
            if isinstance(m, dict) and m.get("chunks")
        ),
        None,
    )
    if not sealed:
        print(ui.dim("  no encrypted message found in that chat"))
        return
    out = api.decrypt_message(sealed)
    print(ui.ok(f"text = {out.get('text')!r}"))


def act_e2ee_roundtrip(api: Any) -> None:
    if not api.e2ee.is_ready():
        print(ui.warn("  E2EE keys not loaded — log in first."))
        return
    to = _ask_to(api, "peer mid or name (a real contact)")
    if not to:
        return
    got = api.e2ee.roundtrip(to, "OkLine roundtrip ✓")
    print((ui.ok if got == "OkLine roundtrip ✓" else ui.warn)(f"recovered = {got!r}"))


# ---------------------------------------------------------------------------
# actions — advanced / dev
# ---------------------------------------------------------------------------
def act_call(api: Any) -> None:
    from .endpoints import THRIFT_ENDPOINTS

    ep = ui.prompt("endpoint (Namespace.Service.method)")
    if ep not in THRIFT_ENDPOINTS:
        print(ui.warn("  unknown endpoint — use 'list endpoints'"))
        return
    raw = ui.prompt("args as JSON array", "[]")
    try:
        args = json.loads(raw)
    except ValueError as exc:
        print(ui.warn(f"  bad JSON: {exc}"))
        return
    print(json.dumps(api.transport.call(ep, args), ensure_ascii=False, indent=2))


def act_list_endpoints(api: Any) -> None:
    from .endpoints import THRIFT_ENDPOINTS

    grep = ui.prompt("filter (blank = all)").lower()
    keys = [k for k in sorted(THRIFT_ENDPOINTS) if grep in k.lower()]
    for k in keys:
        print(f"  {k}")
    print(ui.dim(f"  {len(keys)} endpoint(s)"))


def act_selftest(api: Any) -> None:
    from .selftest import print_results, run_selftest

    fails = print_results(run_selftest(api))
    print((ui.warn if fails else ui.ok)(f"{fails} failure(s)"))


def act_recording(api: Any) -> None:
    print(ui.dim(f"  {len(api.history)} exchange(s) recorded this session"))
    if ui.prompt("save a log?", "n").lower() in ("y", "yes"):
        fmt = ui.prompt("format (text/json/har)", "text")
        path = ui.prompt("file", f"okline_log.{'har' if fmt == 'har' else fmt}")
        api.save_log(path, fmt=fmt)
        print(ui.ok(f"saved -> {path}"))


# ---------------------------------------------------------------------------
# menu tree
# ---------------------------------------------------------------------------
def _menu() -> list[Item]:
    return [
        (
            "Me & account",
            [
                ("Who am I  ·  stats", act_whoami),
                ("My profile (raw JSON)", act_my_profile),
                ("Set display name", act_set_name),
                ("Set status message", act_set_status),
                ("Account settings", act_settings),
                ("Log out (+ delete session)", act_logout),
            ],
        ),
        (
            "Contacts & people",
            [
                ("List / search contacts", act_contacts),
                ("Find a contact by name", act_find),
                ("Profile of a mid", act_profile_of),
                ("Add a friend", act_add_friend),
                ("Search a user by LINE ID", act_search_user),
                ("Block / unblock", act_block),
                ("Favorites", act_favorites),
                ("Export contacts (CSV/JSON)", act_export_contacts),
            ],
        ),
        (
            "Groups & chats",
            [
                ("List my groups", act_groups),
                ("Group members", act_members),
                ("Leave a group", act_leave),
                ("Accept an invitation", act_accept),
                ("Message boxes", act_boxes),
            ],
        ),
        (
            "Send a message",
            [
                ("Text", act_send_text),
                ("Sticker", act_send_sticker),
                ("Location", act_send_location),
                ("Image / file", act_send_media),
                ("Reply to a message", act_reply),
                ("React to a message", act_react),
                ("Unsend a message", act_unsend),
                ("Broadcast to several", act_broadcast),
            ],
        ),
        (
            "Read & history",
            [
                ("Chat log (decrypts E2EE)", act_chatlog),
                ("Recent messages (raw)", act_recent_raw),
                ("Search messages in a chat", act_search_messages),
                ("Back up a chat to JSON", act_backup),
            ],
        ),
        (
            "Live & bots",
            [
                ("Watch incoming (live)", act_watch),
                ("Auto-reply bot", act_autoreply),
                ("Keyword notifier", act_notify),
            ],
        ),
        (
            "E2EE / Letter Sealing",
            [
                ("Status", act_e2ee_status),
                ("Send an encrypted message", act_e2ee_send),
                ("Decrypt a chat's latest sealed message", act_e2ee_decrypt),
                ("Round-trip self-test", act_e2ee_roundtrip),
            ],
        ),
        (
            "Advanced / developer",
            [
                ("Call any endpoint", act_call),
                ("List endpoints", act_list_endpoints),
                ("Self-test (read-only)", act_selftest),
                ("Recording / save log", act_recording),
            ],
        ),
    ]


def _header(api: Any) -> list[str]:
    try:
        p = api.get_profile() or {}
    except Exception:
        p = {}
    name = p.get("displayName") or "?"
    mid = p.get("mid") or ""
    return [
        ui.bold(name) + ui.dim("   " + (mid[:20] + ui.GLYPH["ell"] if len(mid) > 20 else mid)),
        ui.dim("e2ee ") + (ui.accent("ready") if api.e2ee.is_ready() else ui.dim("off")),
    ]


def _run(api: Any, title: str, items: list[Item], header: list[str], *, root: bool) -> None:
    while True:
        ui.clear()
        print()
        ui.panel(header, head=title)
        print()
        ui.menu([label for label, _ in items], quit_label="Quit" if root else "Back")
        choice = ui.prompt("\n choose")
        if choice in ("0", "q", "quit", "exit"):
            if root:
                print(ui.dim("bye"))
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(items)):
            continue
        label, target = items[int(choice) - 1]
        if isinstance(target, list):
            _run(api, label, target, header, root=False)
            continue
        ui.clear()
        print()
        ui.rule(label)
        print()
        try:
            target(api)
        except KeyboardInterrupt:
            print(ui.dim("  cancelled"))
        except Exception as exc:
            print(ui.warn(f"  error: {exc}"))
        ui.pause()


def interactive(args: Any) -> int:
    """Entry point for ``okline`` / ``okline menu``."""
    reconfigure_stdout_utf8()
    api = _ensure_session(args)
    if api is None:
        print(ui.dim("no session — bye."))
        return 1
    try:
        _run(api, "OkLine  ·  LINE in your terminal", _menu(), _header(api), root=True)
    finally:
        api.close()
    return 0

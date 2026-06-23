"""Interactive, menu-driven OkLine console — pick actions by number.

Run ``okline`` with no arguments (or ``okline menu``) for a soft-coloured
terminal UI: a numbered menu you drive by typing ``1``/``2``/``3`` …, no commands
to memorise.  On first use it offers to log in by QR and saves the session.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import ui


def _names(api: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    ids = api.get_all_contact_ids() or []
    for i in range(0, len(ids), 100):
        res = api.get_contacts(ids[i:i + 100])
        for mid, w in (res.get("contacts", {}) or {}).items():
            c = w.get("contact", w) if isinstance(w, dict) else {}
            out[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
    return out


# -- session ----------------------------------------------------------------
def _qr_login(path: str) -> Optional[Any]:
    from .client import OkLine
    from .qrterm import print_qr
    api = OkLine(record=False)
    print("\n" + ui.title("Scan this QR with the LINE app")
          + ui.dim("  (Settings › Add friends › QR code)") + "\n")

    def on_qr(url: str) -> None:
        try:
            print_qr(url)
        except ModuleNotFoundError:
            print(url, "\n" + ui.dim("(pip install qrcode for an inline QR)"))

    try:
        res = api.auth.qr_login(
            on_qr=on_qr,
            on_pin=lambda pin: print(
                "\n" + ui.accent(f"  {ui.GLYPH['arrow']}  Confirm this PIN: {pin}") + "\n"))
    except Exception as exc:  # noqa: BLE001
        print(ui.warn(f"login failed: {exc}")); api.close(); return None
    if not res.access_token:
        print(ui.warn("login did not complete.")); api.close(); return None
    info = getattr(api.auth, "last_e2ee_login", None)
    if info:
        try:
            api.e2ee.load_from_login(info["curve_key_id"], info["metadata"])
        except Exception:  # noqa: BLE001
            pass
    api.save_tokens(path)
    print(ui.ok(f"Logged in — session saved to {path}.") + "\n")
    return api


def _ensure_session(args: Any) -> Optional[Any]:
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
    # no session yet — go straight to QR login (that is the whole point)
    print(ui.dim("  No saved session — starting QR login…\n"))
    try:
        return _qr_login(path)
    except KeyboardInterrupt:
        return None


# -- actions ----------------------------------------------------------------
def act_whoami(api: Any) -> None:
    p = api.get_profile()
    chats = api.get_all_chat_mids() or {}
    ui.table([
        [ui.dim("name"), p.get("displayName") or ""],
        [ui.dim("mid"), p.get("mid") or ""],
        [ui.dim("user id"), p.get("userid") or ""],
        [ui.dim("status"), p.get("statusMessage") or ""],
        [ui.dim("contacts"), str(len(api.get_all_contact_ids() or []))],
        [ui.dim("groups"), f"{len(chats.get('memberChatMids', []))}"
         f" (+{len(chats.get('invitedChatMids', []))} invited)"],
        [ui.dim("favorites"), str(len(api.get_favorite_mids() or []))],
        [ui.dim("blocked"), str(len(api.get_blocked_contact_ids() or []))],
        [ui.dim("e2ee"), ui.accent("ready") if api.e2ee.is_ready() else ui.dim("off")],
    ])


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
    hits = [(m, n) for m, n in _names(api).items() if q in n.lower()]
    ui.table([[ui.dim(m), n] for m, n in hits])


def act_send(api: Any) -> None:
    to = ui.prompt("send to (mid)")
    if not to:
        return
    text = ui.prompt("message")
    if not text:
        return
    enc = ui.prompt("encrypt (E2EE)?", "n").lower() in ("y", "yes")
    res = (api.send_encrypted_text if enc else api.send_text)(to, text)
    print(ui.ok("sent") + ui.dim(f"  id={res.get('id') if isinstance(res, dict) else res}"))


def act_groups(api: Any) -> None:
    from .entities import Group
    chats = api.get_all_chat_mids() or {}
    member = chats.get("memberChatMids", [])
    print(ui.dim(f"  member {len(member)}   invited {len(chats.get('invitedChatMids', []))}") + "\n")
    rows = []
    for g in (api.get_chats(member).get("chats", []) if member else []):
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
        print(ui.warn("  group not found")); return
    grp = Group.from_dict(chats[0])
    print(ui.title(f"  {grp.name}") + ui.dim(f"  ({grp.member_count} members)") + "\n")
    names: Dict[str, str] = {}
    for i in range(0, len(grp.member_mids), 100):
        res = api.get_contacts(grp.member_mids[i:i + 100])
        for mid, w in (res.get("contacts", {}) or {}).items():
            c = w.get("contact", w) if isinstance(w, dict) else {}
            names[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
    ui.table([[ui.dim(mid), names.get(mid, "")] for mid in grp.member_mids])


def act_chatlog(api: Any) -> None:
    cid = ui.prompt("chat mid")
    if not cid:
        return
    n = int(ui.prompt("how many", "30") or 30)
    names = _names(api)
    print()
    for m in reversed(api.get_recent_messages(cid, n) or []):
        if not isinstance(m, dict):
            continue
        text = m.get("text")
        if m.get("chunks"):
            if api.e2ee.is_ready():
                try:
                    text = api.decrypt_message(m).get("text")
                except Exception:  # noqa: BLE001
                    text = ui.dim("[encrypted]")
            else:
                text = ui.dim("[encrypted — log in to load keys]")
        who = names.get(m.get("from") or "") or (m.get("from") or "")[:10]
        print(f"  {ui.dim(who.rjust(14))}  {text or ui.dim('<non-text>')}")


def act_search(api: Any) -> None:
    uid = ui.prompt("LINE ID (e.g. nb.vtg)")
    if not uid:
        return
    c = api.find_contact_by_userid(uid) or {}
    if not isinstance(c, dict) or not c.get("mid"):
        print(ui.warn("  not found")); return
    ui.table([[ui.dim("mid"), c.get("mid")],
              [ui.dim("name"), c.get("displayName")],
              [ui.dim("status"), c.get("statusMessage") or ""]])
    if ui.prompt("add as friend?", "n").lower() in ("y", "yes"):
        api.add_friend_by_mid(c["mid"]); print(ui.ok("added"))


def act_block(api: Any) -> None:
    sub = ui.prompt("(l)ist / (b)lock / (u)nblock", "l").lower()[:1]
    if sub == "l":
        names = _names(api)
        ui.table([[ui.dim(m), names.get(m, "")] for m in (api.get_blocked_contact_ids() or [])])
    elif sub == "b":
        mid = ui.prompt("mid to block")
        if mid:
            api.block_contact(mid); print(ui.ok("blocked"))
    elif sub == "u":
        mid = ui.prompt("mid to unblock")
        if mid:
            api.unblock_contact(mid); print(ui.ok("unblocked"))


def act_react(api: Any) -> None:
    from .enums import PredefinedReactionType
    mid = ui.prompt("message id")
    if not mid:
        return
    if ui.prompt("(r)eact / (u)nsend", "r").lower()[:1] == "u":
        api.unsend_message(mid); print(ui.ok("unsent")); return
    r = ui.prompt("reaction NICE/LOVE/FUN/AMAZING/SAD/OMG", "NICE").upper()
    try:
        api.react(mid, int(PredefinedReactionType[r])); print(ui.ok("reacted"))
    except KeyError:
        print(ui.warn("  unknown reaction"))


def act_watch(api: Any) -> None:
    from .bot import Bot
    echo = ui.prompt("echo replies?", "n").lower() in ("y", "yes")
    bot = Bot(api)

    @bot.on_message
    def _on(ctx):  # noqa: ANN001
        where = "group" if ctx.is_group else "dm"
        print(f"  {ui.dim('[' + where + ']')} {ui.dim(ctx.sender)}: {ctx.text!r}")
        if echo and ctx.text:
            ctx.reply(f"you said: {ctx.text}")

    print(ui.dim("  watching… (Ctrl-C to return to the menu)"))
    try:
        bot.run()
    except KeyboardInterrupt:
        pass


def act_setprofile(api: Any) -> None:
    what = ui.prompt("set (n)ame or (s)tatus", "n").lower()[:1]
    val = ui.prompt("new value")
    if not val:
        return
    if what == "s":
        api.set_status_message(val); print(ui.ok("status updated"))
    else:
        api.set_display_name(val); print(ui.ok("name updated"))


def act_backup(api: Any) -> None:
    import json
    cid = ui.prompt("chat mid")
    if not cid:
        return
    out = ui.prompt("output file", f"{cid}.json")
    msgs = api.get_recent_messages(cid, 200) or []
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(msgs, fh, ensure_ascii=False, indent=2)
    print(ui.ok(f"saved {len(msgs)} messages -> {out}"))


# -- menu loop --------------------------------------------------------------
MENU: List[Tuple[str, Optional[Callable[[Any], None]]]] = [
    ("Who am I  ·  stats", act_whoami),
    ("Contacts  ·  list / search", act_contacts),
    ("Find a contact by name", act_find),
    ("Send a message", act_send),
    ("Groups  ·  list", act_groups),
    ("Group members", act_members),
    ("Chat log  ·  reads & decrypts E2EE", act_chatlog),
    ("Search a user by LINE ID", act_search),
    ("Block / unblock", act_block),
    ("React / unsend a message", act_react),
    ("Watch incoming  ·  live", act_watch),
    ("Set name / status", act_setprofile),
    ("Backup a chat to JSON", act_backup),
]


def _menu_loop(api: Any) -> None:
    try:
        p = api.get_profile() or {}
    except Exception:  # noqa: BLE001
        p = {}
    name = p.get("displayName") or "?"
    mid = p.get("mid") or ""
    header = [
        ui.bold(name) + ui.dim("   " + (mid[:20] + ui.GLYPH["ell"] if len(mid) > 20 else mid)),
        ui.dim("e2ee ") + (ui.accent("ready") if api.e2ee.is_ready() else ui.dim("off")),
    ]
    while True:
        ui.clear()
        print()
        ui.panel(header, head="OkLine  ·  LINE in your terminal")
        print()
        ui.menu([label for label, _ in MENU])
        choice = ui.prompt("\n choose")
        if choice in ("0", "q", "quit", "exit"):
            print(ui.dim("bye"))
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(MENU)):
            continue
        label, action = MENU[int(choice) - 1]
        ui.clear()
        print()
        ui.rule(label)
        print()
        try:
            if action:
                action(api)
        except KeyboardInterrupt:
            print(ui.dim("  cancelled"))
        except Exception as exc:  # noqa: BLE001
            print(ui.warn(f"  error: {exc}"))
        ui.pause()


def interactive(args: Any) -> int:
    """Entry point for ``okline`` / ``okline menu``."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    api = _ensure_session(args)
    if api is None:
        print(ui.dim("no session — bye."))
        return 1
    try:
        _menu_loop(api)
    finally:
        api.close()
    return 0

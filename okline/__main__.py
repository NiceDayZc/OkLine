"""OkLine command-line interface — a full LINE client in your terminal.

Just install and go::

    pip install okline
    okline login                       # scan the QR once (saves tokens.json)
    okline whoami                      # every command reuses the saved session
    okline send <mid> "hello"
    okline contacts --search soda
    okline chatlog <chat-mid>          # reads (and decrypts) recent messages

Auth resolution for every command: ``--token`` / ``--tokens-file`` / the
``LINE_ACCESS_TOKEN`` env var, else the default ``tokens.json`` in the current
directory (created by ``okline login``).  A restored session also brings back
your E2EE keys, so ``chatlog`` / ``send --encrypt`` work without re-scanning.

Run ``okline -h`` for the full command list, or ``okline <cmd> -h`` for one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import __version__
from ._util import reconfigure_stdout_utf8
from .client import OkLine, _safe_print
from .endpoints import THRIFT_ENDPOINTS


# -- helpers ----------------------------------------------------------------
def _load_tokens_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        print(f"warning: could not read tokens file {path!r}: {exc}", file=sys.stderr)
        return {}


def _make_client(args: argparse.Namespace, *, record: bool = True) -> OkLine:
    """Build a client from --token / --tokens-file / env / the default tokens.json.

    When a session *file* is used, load via ``from_tokens_file`` so the E2EE keys
    are restored too (enables ``chatlog`` decryption and ``send --encrypt``).
    """
    access = getattr(args, "token", None) or os.environ.get("LINE_ACCESS_TOKEN")
    refresh = getattr(args, "refresh", None) or os.environ.get("LINE_REFRESH_TOKEN")
    tokens_file = getattr(args, "tokens_file", None)
    redact = not getattr(args, "show_secrets", False)
    if not tokens_file and not access and os.path.exists("tokens.json"):
        tokens_file = "tokens.json"  # sensible default after `okline login`
    if tokens_file and not access and os.path.exists(tokens_file):
        return OkLine.from_tokens_file(tokens_file, record=record, redact=redact)
    if tokens_file:
        data = _load_tokens_file(tokens_file)
        access = access or data.get("accessToken") or data.get("access_token")
        refresh = refresh or data.get("refreshToken") or data.get("refresh_token")
    return OkLine(access_token=access, refresh_token=refresh, record=record, redact=redact)


def _go(args: argparse.Namespace, fn) -> int:
    """Run ``fn(api)`` with a client, print errors cleanly, always close."""
    api = _make_client(args)
    try:
        rv = fn(api)
        return 0 if rv is None else int(rv)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        api.close()


def _print_json(value: Any) -> None:
    _safe_print(json.dumps(value, ensure_ascii=False, indent=2))


def _contact_names(api: OkLine) -> dict[str, str]:
    """``{mid: display name}`` for every contact (fetched in chunks)."""
    ids = api.get_all_contact_ids() or []
    out: dict[str, str] = {}
    for i in range(0, len(ids), 100):
        res = api.get_contacts(ids[i : i + 100])
        for mid, w in (res.get("contacts", {}) or {}).items():
            c = w.get("contact", w) if isinstance(w, dict) else {}
            out[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
    return out


def _need_auth(api: OkLine) -> None:
    if not api.tokens.access_token:
        raise SystemExit(
            "error: no session — run `okline login` first (or pass --token / --tokens-file)"
        )


def _node_ok() -> bool:
    """Friendly up-front check that Node.js (needed for X-Hmac) is on PATH."""
    from .hmac_signer import LtsmBridge

    if LtsmBridge.is_available():
        return True
    print(
        "OkLine needs Node.js 18+ on your PATH to sign requests (X-Hmac).\n"
        "  Install it from https://nodejs.org , then check:  node --version\n"
        "  (installed elsewhere? set  LINE_NODE=/full/path/to/node )",
        file=sys.stderr,
    )
    return False


def _resolve_to(api: OkLine, to: str) -> str:
    """Accept a mid directly, or resolve a (unique) contact name to its mid."""
    if to[:1].lower() in ("u", "c", "r") and len(to) >= 20:
        return to
    matches = [(m, n) for m, n in _contact_names(api).items() if to.lower() in n.lower()]
    if len(matches) == 1:
        print(f"  -> {matches[0][1]} ({matches[0][0]})")
        return matches[0][0]
    if not matches:
        raise SystemExit(f"no contact matching {to!r} — pass a mid, or try `okline find`")
    raise SystemExit(
        f"{len(matches)} contacts match {to!r}: "
        + ", ".join(n for _, n in matches[:8])
        + " — be more specific or use the mid"
    )


# -- session / identity -----------------------------------------------------
def cmd_login(args: argparse.Namespace) -> int:
    if not _node_ok():
        return 2
    from .qrterm import print_qr

    api = OkLine(record=False, redact=not args.show_secrets)

    def show_qr(url: str) -> None:
        print("\nScan with the LINE app (Settings > Add friends > QR code):\n")
        try:
            print_qr(url, border=2, invert=args.invert)
        except ModuleNotFoundError:
            print(url, "\n(pip install qrcode for an inline QR)")
        print("\nor open:", url, "\n")

    def show_pin(pin: str) -> None:
        print(f"\n>>> Confirm this PIN on your phone:  {pin}\n")

    try:
        res = api.auth.qr_login(on_qr=show_qr, on_pin=show_pin, wait_seconds=args.wait)
        if not res.access_token:
            print(
                f"login did not complete: {res.display_message or res.type}", file=sys.stderr
            )
            return 1
        # load E2EE keys for this session, then persist everything
        info = getattr(api.auth, "last_e2ee_login", None)
        if info:
            try:
                api.e2ee.load_from_login(info["curve_key_id"], info["metadata"])
            except Exception:
                pass
        path = args.save or "tokens.json"
        api.save_tokens(path)
        prof = api.get_profile()
        name = prof.get("displayName") if isinstance(prof, dict) else None
        mid = prof.get("mid") if isinstance(prof, dict) else None
        print(f"\nLogged in as {name}  ({mid})")
        print(f"E2EE keys ready: {'yes' if api.e2ee.is_ready() else 'no'}")
        print(f"Session saved to {path} — reused by every other command.")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        api.close()


def cmd_whoami(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        p = api.get_profile()
        chats = api.get_all_chat_mids() or {}
        print(f"name      : {p.get('displayName')}")
        print(f"mid       : {p.get('mid')}")
        print(f"userid    : {p.get('userid')}")
        print(f"region    : {p.get('regionCode')}")
        print(f"status    : {p.get('statusMessage')}")
        print(f"contacts  : {len(api.get_all_contact_ids() or [])}")
        print(
            f"groups    : {len(chats.get('memberChatMids', []))}"
            f" (+{len(chats.get('invitedChatMids', []))} invited)"
        )
        print(f"favorites : {len(api.get_favorite_mids() or [])}")
        print(f"blocked   : {len(api.get_blocked_contact_ids() or [])}")
        print(f"e2ee ready: {'yes' if api.e2ee.is_ready() else 'no'}")

    return _go(args, run)


def cmd_profile(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        if args.mid:
            names = _contact_names(api)
            print(f"name   : {names.get(args.mid) or '(not in your contacts)'}")
            print(f"mid    : {args.mid}")
            try:
                detail = api.get_buddy_detail(args.mid) or {}
            except Exception:
                detail = {}
            if isinstance(detail, dict):
                for k, v in detail.items():
                    if v not in (None, "", [], {}):
                        print(f"{k:7}: {v}")
        else:
            _print_json(api.get_profile())

    return _go(args, run)


def cmd_version(args: argparse.Namespace) -> int:
    print(f"OkLine {__version__} (emulates LINE CHROMEOS 3.7.2)")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    import os

    def run(api: OkLine):
        if api.tokens.access_token:
            try:
                api.auth.logout()
                print("logged out (server session invalidated)")
            except Exception as exc:
                print(f"server logout failed (continuing): {exc}", file=sys.stderr)

    code = _go(args, run)
    path = getattr(args, "tokens_file", None) or "tokens.json"
    if os.path.exists(path):
        try:
            os.remove(path)
            print(f"removed local session {path}")
        except OSError as exc:
            print(f"could not remove {path}: {exc}", file=sys.stderr)
    return code


def cmd_menu(args: argparse.Namespace) -> int:
    from .menu import interactive

    return interactive(args)


# -- contacts / people ------------------------------------------------------
def cmd_contacts(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        names = _contact_names(api)
        rows = sorted(names.items(), key=lambda kv: kv[1].lower())
        if args.search:
            q = args.search.lower()
            rows = [(m, n) for m, n in rows if q in n.lower()]
        if args.csv or args.json:
            data = [{"mid": m, "name": n} for m, n in rows]
            if args.json:
                with open(args.json, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                print(f"wrote {len(data)} contacts -> {args.json}")
            if args.csv:
                import csv

                with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=["mid", "name"])
                    w.writeheader()
                    w.writerows(data)
                print(f"wrote {len(data)} contacts -> {args.csv}")
            return
        for m, n in rows:
            print(f"{m}  {n}")
        print(f"\n{len(rows)} contact(s)", file=sys.stderr)

    return _go(args, run)


def cmd_find(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        q = args.query.lower()
        hits = [(m, n) for m, n in _contact_names(api).items() if q in n.lower()]
        for m, n in hits:
            print(f"{m}  {n}")
        if not hits:
            print(f"no contact matching {args.query!r}")

    return _go(args, run)


def cmd_search(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        c = api.find_contact_by_userid(args.userid) or {}
        if not isinstance(c, dict) or not c.get("mid"):
            print(f"no user found for {args.userid!r}")
            return 1
        print(f"mid    : {c.get('mid')}")
        print(f"name   : {c.get('displayName')}")
        print(f"status : {c.get('statusMessage')}")
        if args.add:
            api.add_friend_by_mid(c["mid"])
            print(f"added  : {c['mid']}")

    return _go(args, run)


def cmd_add(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        who = args.who
        if who[:1].lower() == "u" and len(who) >= 32:
            mid = who
        else:
            c = api.find_contact_by_userid(who) or {}
            mid = c.get("mid") if isinstance(c, dict) else None
            if not mid:
                print(f"no user found for {who!r}")
                return 1
            print(f"resolved: {who} -> {mid} ({c.get('displayName')})")
        api.add_friend_by_mid(mid)
        print(f"added: {mid}")

    return _go(args, run)


def cmd_block(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        if args.action == "list":
            names = _contact_names(api)
            for mid in api.get_blocked_contact_ids() or []:
                print(f"{mid}  {names.get(mid, '')}")
        elif args.action == "add":
            api.block_contact(args.mid)
            print(f"blocked {args.mid}")
        else:
            api.unblock_contact(args.mid)
            print(f"unblocked {args.mid}")

    return _go(args, run)


def cmd_favorites(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        if args.action == "list":
            names = _contact_names(api)
            for mid in api.get_favorite_mids() or []:
                print(f"{mid}  {names.get(mid, '')}")
        elif args.action == "add":
            api.set_chat_favorite(args.mid, 1)
            print(f"favorited {args.mid}")
        else:
            api.set_chat_favorite(args.mid, 0)
            print(f"unfavorited {args.mid}")

    return _go(args, run)


# -- groups / chats ---------------------------------------------------------
def cmd_groups(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        from .entities import Group

        _need_auth(api)
        chats = api.get_all_chat_mids() or {}
        member = chats.get("memberChatMids", [])
        print(
            f"member  : {len(member)}    invited : {len(chats.get('invitedChatMids', []))}\n"
        )
        if member:
            for g in api.get_chats(member).get("chats", []) or []:
                grp = Group.from_dict(g)
                print(f"{grp.chat_mid}  ({grp.member_count:>3})  {grp.name}")

    return _go(args, run)


def cmd_members(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        from .entities import Group

        _need_auth(api)
        chats = api.get_chats([args.group_mid]).get("chats", [])
        if not chats:
            print("group not found")
            return 1
        grp = Group.from_dict(chats[0])
        print(f"# {grp.name}  ({grp.member_count} members)\n")
        names: dict[str, str] = {}
        members = grp.member_mids
        for i in range(0, len(members), 100):
            res = api.get_contacts(members[i : i + 100])
            for mid, w in (res.get("contacts", {}) or {}).items():
                c = w.get("contact", w) if isinstance(w, dict) else {}
                names[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
        for mid in members:
            print(f"  {mid}  {names.get(mid, '(unknown)')}")

    return _go(args, run)


def cmd_leave(args: argparse.Namespace) -> int:
    return _go(args, lambda api: None)


def cmd_accept(args: argparse.Namespace) -> int:
    return _go(args, lambda api: None)


# -- messaging --------------------------------------------------------------
def cmd_send(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        args.to = _resolve_to(api, args.to)
        if args.image:
            res = api.send_image(args.to, args.image)
        elif args.file:
            res = api.send_file(args.to, args.file)
        elif args.sticker:
            res = api.send_sticker(args.to, args.sticker[0], args.sticker[1])
        elif args.location:
            res = api.send_location(
                args.to, args.location[0], args.location[1], title=args.title or ""
            )
        elif args.text:
            res = (api.send_encrypted_text if args.encrypt else api.send_text)(
                args.to, args.text
            )
        else:
            print(
                "error: provide TEXT or --image/--file/--sticker/--location", file=sys.stderr
            )
            return 2
        mid = res.get("id") if isinstance(res, dict) else res
        print("sent; message id:", mid)

    return _go(args, run)


def cmd_react(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        from .enums import PredefinedReactionType

        _need_auth(api)
        api.react(args.message_id, int(PredefinedReactionType[args.reaction]))
        print("ok")

    return _go(args, run)


def cmd_unsend(args: argparse.Namespace) -> int:
    return _go(args, lambda api: None)


def cmd_broadcast(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        from .ratelimit import RateLimiter

        _need_auth(api)
        api.transport.rate_limiter = RateLimiter(rate=args.rate, per=1.0)
        print(f"sending to {len(args.to)} target(s): {args.message!r}")
        if not args.yes and input("proceed? [y/N] ").strip().lower() != "y":
            print("cancelled")
            return
        from .enums import ErrorCode
        from .exceptions import LineApiError

        abuse = {int(ErrorCode.EXCESSIVE_ACCESS), int(ErrorCode.ABUSE_BLOCK)}
        ok = 0
        for mid in args.to:
            try:
                api.send_text(mid, args.message)
                ok += 1
                print(f"  sent -> {mid}")
            except LineApiError as exc:
                print(f"  FAIL -> {mid}: {exc}")
                if getattr(exc, "code", None) in abuse:
                    print("  LINE rate-limited / blocked this account — stopping.")
                    break
            except Exception as exc:
                print(f"  FAIL -> {mid}: {exc}")
        print(f"\n{ok}/{len(args.to)} delivered")

    return _go(args, run)


def cmd_set_name(args: argparse.Namespace) -> int:
    return _go(args, lambda api: None)


def cmd_set_status(args: argparse.Namespace) -> int:
    return _go(args, lambda api: None)


# -- reading ----------------------------------------------------------------
def cmd_boxes(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        boxes = api.get_message_boxes(limit=args.limit)
        for b in boxes.get("messageBoxes", []) if isinstance(boxes, dict) else []:
            if isinstance(b, dict):
                print(f"{b.get('id')}  unread={b.get('unreadCount', '?')}")

    return _go(args, run)


def cmd_chatlog(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        names = _contact_names(api)
        msgs = api.get_recent_messages(args.chat_mid, args.count) or []
        for m in reversed(msgs):
            if not isinstance(m, dict):
                continue
            text = m.get("text")
            if m.get("chunks"):
                if api.e2ee.is_ready():
                    try:
                        text = api.decrypt_message(m).get("text")
                    except Exception:
                        text = "[encrypted — could not decrypt]"
                else:
                    text = "[encrypted — run `okline login` to load keys]"
            sender = m.get("from") or ""
            who = names.get(sender) or sender[:10]
            ct = m.get("contentType")
            body = text if text else (f"<{_ct_name(ct)}>" if ct else "<empty>")
            print(f"{who:>16}: {body}")

    return _go(args, run)


def _ct_name(ct: Any) -> str:
    try:
        from .enums import ContentType

        return ContentType(int(ct)).name.lower()
    except Exception:
        return f"type{ct}"


def cmd_backup(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        _need_auth(api)
        out = args.output or f"{args.chat_mid}.json"
        messages: list = []
        messages.extend(api.get_recent_messages(args.chat_mid, min(args.count, 200)) or [])
        while len(messages) < args.count and messages:
            oldest = messages[-1]
            page = (
                api.get_previous_messages(
                    args.chat_mid,
                    oldest.get("id"),
                    int(oldest.get("deliveredTime") or oldest.get("createdTime") or 0),
                    count=min(args.count - len(messages), 200),
                )
                or []
            )
            if not page:
                break
            messages.extend(page)
        messages = messages[: args.count]
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(messages, fh, ensure_ascii=False, indent=2)
        print(f"saved {len(messages)} messages -> {out}")

    return _go(args, run)


# -- live / bots ------------------------------------------------------------
def cmd_watch(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        from .bot import Bot

        _need_auth(api)
        bot = Bot(api)

        @bot.on_message
        def _on(ctx):
            where = "group" if ctx.is_group else "dm"
            print(f"[{where}] {ctx.sender}: {ctx.text!r}")
            if args.echo and ctx.text:
                ctx.reply(f"you said: {ctx.text}")

        print("watching for messages... (Ctrl-C to stop)")
        try:
            bot.run()
        except KeyboardInterrupt:
            print("\nstopped")

    return _go(args, run)


def cmd_autoreply(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        from .bot import Bot

        _need_auth(api)
        rules = {}
        for r in args.rule or []:
            if "=" in r:
                k, v = r.split("=", 1)
                rules[k.strip()] = v.strip()
        if not rules:
            print('error: add at least one --rule "keyword=reply"', file=sys.stderr)
            return 2
        bot = Bot(api)

        @bot.on_message
        def _on(ctx):
            if not ctx.text:
                return
            hay = ctx.text.lower() if args.ignore_case else ctx.text
            for kw, reply in rules.items():
                k = kw.lower() if args.ignore_case else kw
                if k in hay:
                    ctx.reply(reply)
                    print(f"matched {kw!r} -> replied")
                    break

        print(f"auto-reply running with {len(rules)} rule(s)... (Ctrl-C to stop)")
        try:
            bot.run()
        except KeyboardInterrupt:
            print("\nstopped")

    return _go(args, run)


def cmd_notify(args: argparse.Namespace) -> int:
    def run(api: OkLine):
        from .bot import Bot

        _need_auth(api)
        bot = Bot(api)

        @bot.on_message
        def _on(ctx):
            if args.group_only and not ctx.is_group:
                return
            if args.dm_only and ctx.is_group:
                return
            if args.keyword and (args.keyword.lower() not in (ctx.text or "").lower()):
                return
            where = "group" if ctx.is_group else "dm"
            print(f"[{where}] {ctx.sender}: {ctx.text or '<non-text>'}")

        print("watching... (Ctrl-C to stop)")
        try:
            bot.run()
        except KeyboardInterrupt:
            print("\nstopped")

    return _go(args, run)


# -- advanced ---------------------------------------------------------------
def cmd_endpoints(args: argparse.Namespace) -> int:
    keys = sorted(THRIFT_ENDPOINTS)
    if args.grep:
        keys = [k for k in keys if args.grep.lower() in k.lower()]
    for k in keys:
        print(f"{k:55}  /api/{THRIFT_ENDPOINTS[k]}")
    print(f"\n{len(keys)} endpoint(s)", file=sys.stderr)
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    try:
        parsed: list[Any] = json.loads(args.args) if args.args else []
    except ValueError as exc:
        print(f"error: args must be a JSON array: {exc}", file=sys.stderr)
        return 2
    if not isinstance(parsed, list):
        print("error: args JSON must be an array of positional thrift args", file=sys.stderr)
        return 2
    if args.endpoint not in THRIFT_ENDPOINTS:
        print(
            f"error: unknown endpoint {args.endpoint!r} (try `okline endpoints`)",
            file=sys.stderr,
        )
        return 2
    api = _make_client(args)
    try:
        result = api.transport.call(args.endpoint, parsed, require_auth=not args.no_auth)
        if args.raw and api.last:
            _safe_print(api.last.pretty(redact=not args.show_secrets))
        else:
            _print_json(result)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.raw and api.last:
            _safe_print(api.last.pretty(redact=not args.show_secrets))
        return 1
    finally:
        api.close()


def cmd_selftest(args: argparse.Namespace) -> int:
    from .selftest import print_results, run_selftest

    api = _make_client(args)
    if not api.tokens.access_token:
        print("error: selftest needs a session (okline login)", file=sys.stderr)
        return 2
    try:
        fails = print_results(run_selftest(api, verbose=args.verbose))
        if args.save:
            api.save_log(args.save, fmt="text", redact=not args.show_secrets)
            print(f"\nfull transcript saved to {args.save}")
        return 1 if fails else 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        api.close()


# -- parser -----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="okline", description="OkLine — a full LINE client in your terminal"
    )
    p.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"OkLine {__version__} (emulates LINE CHROMEOS 3.7.2)",
    )

    auth = argparse.ArgumentParser(add_help=False)
    auth.add_argument("--token", help="X-Line-Access token (or env LINE_ACCESS_TOKEN)")
    auth.add_argument("--refresh", help="refresh token (or env LINE_REFRESH_TOKEN)")
    auth.add_argument("--tokens-file", help="session JSON (default: ./tokens.json)")
    auth.add_argument(
        "--show-secrets",
        action="store_true",
        help="do not redact tokens/secrets in transcripts",
    )

    sub = p.add_subparsers(dest="command", required=False, metavar="<command>")

    def add(name, fn, help, *, parents=(auth,), aliases=()):
        s = sub.add_parser(name, parents=list(parents), help=help, aliases=list(aliases))
        s.set_defaults(func=fn)
        return s

    # interactive UI (also the default when run with no command)
    add("menu", cmd_menu, "interactive menu UI (the default — just run `okline`)")

    # session / identity
    s = add(
        "login", cmd_login, "scan a QR to log in and save the session", aliases=["qr-login"]
    )
    s.add_argument("--save", help="session file to write (default tokens.json)")
    s.add_argument("--wait", type=float, default=180.0, help="seconds to wait for scan/PIN")
    s.add_argument("--invert", action="store_true", help="invert QR colours (light terminal)")
    add("logout", cmd_logout, "log out and remove the local session file")
    add("whoami", cmd_whoami, "your profile + account stats")
    s = add("profile", cmd_profile, "print your profile, or another user's by mid")
    s.add_argument("mid", nargs="?", help="a user mid to look up (default: yourself)")
    add("version", cmd_version, "print the OkLine version")

    # contacts / people
    s = add("contacts", cmd_contacts, "list/search/export your contacts")
    s.add_argument("--search", help="filter by display-name substring")
    s.add_argument("--csv", help="write results to a CSV file")
    s.add_argument("--json", help="write results to a JSON file")
    s = add("find", cmd_find, "find a contact's mid by display name")
    s.add_argument("query")
    s = add("search", cmd_search, "find a user by public LINE ID (--add to friend)")
    s.add_argument("userid")
    s.add_argument("--add", action="store_true")
    s = add("add", cmd_add, "add a friend by mid or LINE ID")
    s.add_argument("who")
    s = add("block", cmd_block, "list / add / remove blocked contacts")
    s.add_argument("action", choices=["list", "add", "remove"])
    s.add_argument("mid", nargs="?")
    s = add("favorites", cmd_favorites, "list / add / remove favorite chats")
    s.add_argument("action", choices=["list", "add", "remove"])
    s.add_argument("mid", nargs="?")

    # groups / chats
    add("groups", cmd_groups, "list your groups (+ invited)")
    s = add("members", cmd_members, "list a group's members with names")
    s.add_argument("group_mid")
    s = add("leave", cmd_leave, "leave a group/room")
    s.add_argument("chat_mid")
    s = add("accept", cmd_accept, "accept a group invitation")
    s.add_argument("chat_mid")

    # messaging
    s = add("send", cmd_send, "send text / sticker / location / image / file")
    s.add_argument("to")
    s.add_argument("text", nargs="?")
    s.add_argument("--encrypt", action="store_true", help="send as an E2EE message")
    s.add_argument("--image")
    s.add_argument("--file")
    s.add_argument("--sticker", nargs=2, metavar=("PKG", "STK"))
    s.add_argument("--location", nargs=2, type=float, metavar=("LAT", "LON"))
    s.add_argument("--title", help="location title")
    s = add("react", cmd_react, "react to a message: okline react <id> LOVE")
    s.add_argument("message_id")
    s.add_argument(
        "reaction",
        nargs="?",
        default="NICE",
        choices=["NICE", "LOVE", "FUN", "AMAZING", "SAD", "OMG"],
    )
    s = add("unsend", cmd_unsend, "recall one of your messages")
    s.add_argument("message_id")
    s = add("broadcast", cmd_broadcast, "send one text to many chats")
    s.add_argument("message")
    s.add_argument("--to", nargs="+", required=True)
    s.add_argument("--rate", type=float, default=3.0)
    s.add_argument("--yes", action="store_true")
    s = add("set-name", cmd_set_name, "change your display name")
    s.add_argument("name")
    s = add("set-status", cmd_set_status, "change your status message")
    s.add_argument("text")

    # reading
    s = add("boxes", cmd_boxes, "list your message boxes")
    s.add_argument("--limit", type=int, default=20)
    s = add("chatlog", cmd_chatlog, "print a chat's recent messages (decrypts E2EE)")
    s.add_argument("chat_mid")
    s.add_argument("-n", "--count", type=int, default=30)
    s = add("backup", cmd_backup, "save a chat's recent messages to JSON")
    s.add_argument("chat_mid")
    s.add_argument("-n", "--count", type=int, default=200)
    s.add_argument("-o", "--output")

    # live / bots
    s = add("watch", cmd_watch, "print incoming messages live (--echo to reply)")
    s.add_argument("--echo", action="store_true")
    s = add("autoreply", cmd_autoreply, "keyword auto-reply bot (--rule kw=reply)")
    s.add_argument("--rule", action="append")
    s.add_argument("--ignore-case", action="store_true")
    s = add("notify", cmd_notify, "alert on incoming messages")
    s.add_argument("--keyword")
    s.add_argument("--group-only", action="store_true")
    s.add_argument("--dm-only", action="store_true")

    # advanced
    s = add("endpoints", cmd_endpoints, "list all 77 endpoint keys")
    s.add_argument("grep", nargs="?")
    s = add("call", cmd_call, "call any endpoint and print its response")
    s.add_argument("endpoint")
    s.add_argument("args", nargs="?", default="[]")
    s.add_argument("--raw", action="store_true")
    s.add_argument("--no-auth", action="store_true")
    s = add("selftest", cmd_selftest, "exercise every read-only endpoint")
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--save")
    return p


def main(argv: list[str] | None = None) -> int:
    reconfigure_stdout_utf8()  # Thai / emoji / box glyphs on any code page
    args = build_parser().parse_args(argv)
    if getattr(args, "func", None) is None:  # `okline` with no command -> menu
        from .menu import interactive

        return interactive(
            argparse.Namespace(token=None, refresh=None, tokens_file=None, show_secrets=False)
        )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

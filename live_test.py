#!/usr/bin/env python3
"""Detailed LIVE integration test for OkLine.

Runs the whole library against the **real** LINE servers using your own session
and prints a pass/fail report for every feature: identity, settings, contacts,
chats/rooms, messaging, media upload, reactions, E2EE keys, channel token,
recording and (optionally) the live event stream.

Read-only checks always run. **Write** checks (send text/image, react, unsend)
only run when you pass ``--to <mid>`` (and they send to that target - use your
own test group / a friend / yourself).

Usage
-----
    # 1) make sure you have a session (creates tokens.json)
    python -m okline qr-login --save tokens.json

    # 2) read-only sweep
    python live_test.py --tokens-file tokens.json

    # 3) full sweep incl. sending a text + image to a target chat
    python live_test.py --tokens-file tokens.json --to cXXXXXXXX --image pic.jpg

    # 4) also watch the live event stream for 15s
    python live_test.py --tokens-file tokens.json --listen 15

A full request/response transcript is saved to ``live_test_report.txt``.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Callable

from okline import OkLine, enums
from okline.entities import Group, Profile, parse_contacts


# ---------------------------------------------------------------------------
# tiny test runner
# ---------------------------------------------------------------------------
class Result:
    def __init__(
        self, name: str, ok: bool, status, ms: float, detail: str, skipped: bool = False
    ) -> None:
        self.name = name
        self.ok = ok
        self.status = status
        self.ms = ms
        self.detail = detail
        self.skipped = skipped


class Runner:
    def __init__(self, api: OkLine) -> None:
        self.api = api
        self.results: list[Result] = []

    def section(self, title: str) -> None:
        print(f"\n=== {title} ===")

    def check(
        self,
        name: str,
        fn: Callable[[], Any],
        *,
        summary: Callable[[Any], str] | None = None,
        ok: Callable[[Any], bool] | None = None,
    ) -> Any:
        t = time.monotonic()
        try:
            value = fn()
            ms = (time.monotonic() - t) * 1000
            status = getattr(self.api.last, "status", 200)
            det = summary(value) if summary else _summ(value)
            passed = ok(value) if ok else True
            self.results.append(Result(name, passed, status, ms, det))
            tag = "[OK ]" if passed else "[FAIL]"
            print(f"  {tag} {name:<46} {status!s:>3} {ms:6.0f}ms  {det}")
            return value
        except Exception as exc:
            ms = (time.monotonic() - t) * 1000
            status = getattr(self.api.last, "status", None)
            self.results.append(Result(name, False, status, ms, str(exc)[:160]))
            print(f"  [FAIL] {name:<45} {status!s:>3} {ms:6.0f}ms  {exc}")
            return None

    def skip(self, name: str, why: str) -> None:
        self.results.append(Result(name, True, None, 0, why, skipped=True))
        print(f"  [SKIP] {name:<45}      {why}")

    def summary(self) -> int:
        ran = [r for r in self.results if not r.skipped]
        ok = sum(1 for r in ran if r.ok)
        fails = [r for r in ran if not r.ok]
        skips = [r for r in self.results if r.skipped]
        print("\n" + "=" * 70)
        print(
            f"RESULT: {ok}/{len(ran)} checks passed   "
            f"({len(skips)} skipped, {len(fails)} failed)"
        )
        if fails:
            print("\nFailures:")
            for r in fails:
                print(f"  - {r.name}: {r.detail}")
        print("=" * 70)
        return len(fails)


def _summ(v: Any) -> str:
    if isinstance(v, list):
        return f"list[{len(v)}]"
    if isinstance(v, dict):
        ks = list(v)[:4]
        return "{" + ", ".join(ks) + ("..." if len(v) > 4 else "") + "}"
    return str(v)[:60]


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------
def run(
    api: OkLine, *, to: str | None, image: str | None, file: str | None, listen: int
) -> int:
    r = Runner(api)

    # --- identity ----------------------------------------------------------
    r.section("Identity & account")
    profile = r.check(
        "getProfile",
        lambda: api.get_profile(),
        summary=lambda p: (
            f"{p.get('displayName')} ({p.get('mid', '')[:10]}) {p.get('regionCode')}"
        ),
    )
    region = profile.get("regionCode", "") if isinstance(profile, dict) else ""
    my_mid = profile.get("mid") if isinstance(profile, dict) else None
    r.check(
        "getProfile -> Profile model",
        lambda: Profile.from_dict(profile),
        summary=lambda p: f"name={p.display_name!r}",
    )
    r.check("getSettings", lambda: api.get_settings())
    r.check(
        "getSettingsAttributes2", lambda: api.get_settings_attributes2([16, 33, 25, 60, 61])
    )
    r.check("getConfigurations", lambda: api.get_configurations(region=region))
    r.check("getServerTime", lambda: api.get_server_time())

    # --- contacts ----------------------------------------------------------
    r.section("Contacts")
    ids = r.check("getAllContactIds", lambda: api.get_all_contact_ids())
    first_contact = ids[0] if isinstance(ids, list) and ids else None
    if first_contact:
        cres = r.check("getContactsV2", lambda: api.get_contacts([first_contact]))
        r.check(
            "getContactsV2 -> Contact models",
            lambda: parse_contacts(cres),
            summary=lambda d: f"{len(d)} contact(s); first={next(iter(d.values())).name!r}",
        )
    else:
        r.skip("getContactsV2", "no contacts")
    r.check("getFavoriteMids", lambda: api.get_favorite_mids())
    r.check("getBlockedContactIds", lambda: api.get_blocked_contact_ids())
    r.check("getRecommendationIds", lambda: api.get_recommendation_ids())
    r.check("getBlockedRecommendationIds", lambda: api.get_blocked_recommendation_ids())

    # --- chats / rooms -----------------------------------------------------
    r.section("Chats, groups & rooms")
    chat_mids = r.check(
        "getAllChatMids",
        lambda: api.get_all_chat_mids(),
        summary=lambda d: (
            f"member={len(d.get('memberChatMids', []))} invited={len(d.get('invitedChatMids', []))}"
        ),
    )
    members = chat_mids.get("memberChatMids") if isinstance(chat_mids, dict) else []
    first_chat = members[0] if members else None
    if first_chat:
        gres = r.check("getChats", lambda: api.get_chats([first_chat]))

        def _g(d):
            chats = d.get("chats", []) if isinstance(d, dict) else []
            return Group.from_dict(chats[0]) if chats else None

        r.check(
            "getChats -> Group model",
            lambda: _g(gres),
            summary=lambda g: f"{g.name!r} members={g.member_count}" if g else "-",
        )
    else:
        r.skip("getChats", "no group chats")
    r.check(
        "getMessageBoxes",
        lambda: api.get_message_boxes(limit=5),
        summary=lambda d: (
            f"boxes={len(d.get('messageBoxes', [])) if isinstance(d, dict) else d}"
        ),
    )
    if first_chat:
        r.check("getMessageBoxesByIds", lambda: api.get_message_boxes_by_ids([first_chat]))
        r.check("getRecentMessagesV2", lambda: api.get_recent_messages(first_chat, 5))

    # --- e2ee / channel ----------------------------------------------------
    r.section("E2EE keys & channel")
    r.check("getE2EEPublicKeysEx", lambda: api.get_e2ee_public_keys_ex())
    r.check("getLastOpRevision", lambda: api.get_last_op_revision())
    r.check(
        "issueChannelToken",
        lambda: api.issue_channel_token(),
        summary=lambda d: (
            "channelAccessToken OK"
            if isinstance(d, dict) and d.get("channelAccessToken")
            else _summ(d)
        ),
    )

    # --- writes (only with --to) -------------------------------------------
    # NOTE: do NOT send to yourself — a self / Letter-Sealed conversation
    # rejects plain text with code 82 "can not send using plain mode" (needs
    # E2EE). Pass --to a chat that allows plain mode (most groups do).
    r.section("Messaging (write)")
    if to:
        sent = r.check(
            "send_text",
            lambda: api.send_text(to, "OkLine live test"),
            summary=lambda m: f"id={m.get('id') if isinstance(m, dict) else m}",
        )
        msg_id = sent.get("id") if isinstance(sent, dict) else None
        if msg_id:
            r.check(
                "react (LOVE)", lambda: api.react(msg_id, enums.PredefinedReactionType.LOVE)
            )
            r.check("cancel_reaction", lambda: api.cancel_reaction(msg_id))
            r.check("unsend_message", lambda: api.unsend_message(msg_id))
        else:
            r.skip("react/unsend", "send_text returned no id (E2EE chat? try a group)")
    else:
        r.skip(
            "send_text / react / unsend",
            "pass --to <chat mid> (NOT yourself; self/E2EE chats need encryption)",
        )

    # --- media (only with --to + --image / --file) -------------------------
    r.section("Media (write)")
    if to and image:
        r.check(
            "send_image",
            lambda: api.send_image(to, image),
            summary=lambda m: f"id={m.get('id') if isinstance(m, dict) else m}",
        )
    else:
        r.skip("send_image", "pass --to <chat> and --image to test")
    if to and file:
        r.check(
            "send_file",
            lambda: api.send_file(to, file),
            summary=lambda m: f"id={m.get('id') if isinstance(m, dict) else m}",
        )
    else:
        r.skip("send_file", "pass --to <chat> and --file to test")

    # --- E2EE (Letter Sealing) ---------------------------------------------
    r.section("E2EE / Letter Sealing")
    if api.e2ee.is_ready():
        print(f"  (E2EE keys loaded: {len(api.e2ee.my_keys)} key(s))")
        # decisive crypto check: encrypt to a peer then decrypt back via the same
        # (symmetric) send channel — proves encrypt + framing + decrypt all agree,
        # independent of the server or the other party. Needs a real peer key, so
        # use --to if given, else negotiate against ourselves is impossible; skip.
        if to and to[:1].lower() == "u" and to != my_mid:
            r.check(
                "e2ee roundtrip (encrypt->decrypt)",
                lambda: api.e2ee.roundtrip(to, "OkLine roundtrip ✓"),
                summary=lambda t: f"recovered={t!r}",
                ok=lambda t: t == "OkLine roundtrip ✓",
            )
        else:
            r.skip("e2ee roundtrip", "pass --to <a friend's uXX mid>")
        # you cannot message yourself, so E2EE send needs a real --to (a friend's
        # uXX DM).
        if to and to[:1].lower() == "u" and to != my_mid:
            r.check(
                "send_encrypted_text",
                lambda: api.send_encrypted_text(to, "OkLine E2EE test"),
                summary=lambda m: f"id={m.get('id') if isinstance(m, dict) else m}",
            )
        else:
            r.skip("send_encrypted_text", "pass --to <a friend's uXX mid> (self n/a)")

        # find a sealed message to decrypt, separately for 1:1 (u..) and group (c..)
        boxes = api.get_message_boxes(limit=20)
        box_list = boxes.get("messageBoxes", []) if isinstance(boxes, dict) else []

        def _find_sealed(prefix):
            ids = [
                b.get("id")
                for b in box_list
                if isinstance(b, dict) and str(b.get("id", ""))[:1].lower() == prefix
            ]
            for bid in ids[:8]:
                msgs = api.get_recent_messages(bid, 10) or []
                cand = [m for m in msgs if isinstance(m, dict) and m.get("chunks")]
                if cand:
                    return cand[-1]
            return None

        sealed_1to1 = _find_sealed("u")
        if sealed_1to1:
            r.check(
                "decrypt_message (1:1)",
                lambda: api.decrypt_message(sealed_1to1),
                summary=lambda m: f"text={(m.get('text') or '')[:40]!r}",
            )
        else:
            r.skip("decrypt_message (1:1)", "no sealed 1:1 message found in your DMs")

        sealed_group = _find_sealed("c")
        if sealed_group:
            r.check(
                "decrypt_message (group)",
                lambda: api.decrypt_message(sealed_group),
                summary=lambda m: f"text={(m.get('text') or '')[:40]!r}",
            )
        else:
            r.skip("decrypt_message (group)", "no sealed group message found")

        # cross-session: export keys -> save -> reload in a FRESH client (new
        # bridge process) -> E2EE still works, no QR re-scan.
        import os
        import tempfile

        xpath = os.path.join(tempfile.gettempdir(), "okline_xsession_test.json")
        api.save_tokens(xpath)
        api_x = api.__class__.from_tokens_file(xpath)
        r.check(
            "cross-session E2EE (reload keys)",
            lambda: api_x.e2ee.is_ready(),
            summary=lambda b: f"ready={b}, keys={len(api_x.e2ee.my_keys)}",
            ok=lambda b: bool(b),
        )
        if to and to[:1].lower() == "u" and to != my_mid:
            r.check(
                "cross-session roundtrip",
                lambda: api_x.e2ee.roundtrip(to, "xsession ✓"),
                summary=lambda t: f"recovered={t!r}",
                ok=lambda t: t == "xsession ✓",
            )
        api_x.close()
        try:
            os.remove(xpath)
        except OSError:
            pass
    else:
        r.skip(
            "E2EE send/decrypt",
            "keys not loaded — run with --qr (fresh QR login) to test E2EE",
        )

    # --- recording ---------------------------------------------------------
    r.section("Recording")
    r.check(
        "history captured",
        lambda: api.history,
        summary=lambda h: f"{len(h)} exchanges recorded",
    )
    r.check("save HAR log", lambda: api.save_log("live_test_report.har", fmt="har") or "saved")
    r.check(
        "save text log", lambda: api.save_log("live_test_report.txt", fmt="text") or "saved"
    )

    # --- live stream (optional) --------------------------------------------
    if listen > 0:
        r.section(f"Operation stream ({listen}s)")
        _listen(api, listen)
    else:
        r.section("Operation stream")
        r.skip("SSE stream", "pass --listen N to watch events")

    return r.summary()


def _listen(api: OkLine, seconds: int) -> None:
    import threading

    stop = time.monotonic() + seconds
    count = {"ops": 0, "events": 0}
    print(f"  listening {seconds}s (send yourself a message to see it)…")

    def worker():
        try:
            for ev in api.ops.stream(reconnect=False):
                count["events"] += 1
                if ev.event in ("ping", "connInfoRevision"):
                    continue
                payload = ev.data
                ops = payload.get("operations") if isinstance(payload, dict) else None
                n = len(ops) if isinstance(ops, list) else (1 if payload else 0)
                count["ops"] += n
                print(f"  <- event {ev.event!r} (+{n} ops)")
                if time.monotonic() > stop:
                    break
        except Exception as exc:
            print(f"  stream ended: {exc}")

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    while time.monotonic() < stop and th.is_alive():
        time.sleep(0.5)
    print(f"  got {count['events']} SSE events, ~{count['ops']} operations")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="OkLine live integration test")
    p.add_argument(
        "--tokens-file",
        default="tokens.json",
        help="session file from `okline qr-login --save` (default tokens.json)",
    )
    p.add_argument("--token", help="access token (overrides the file)")
    p.add_argument("--to", help="target mid for write tests (group/friend/yourself)")
    p.add_argument("--image", help="image path to test send_image")
    p.add_argument("--file", help="file path to test send_file")
    p.add_argument("--listen", type=int, default=0, help="watch the event stream N seconds")
    p.add_argument(
        "--qr",
        action="store_true",
        help="log in fresh via QR (enables E2EE tests this session)",
    )
    args = p.parse_args(argv)

    try:  # display names / messages may contain Thai or emoji
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    if args.qr:
        from okline.qrterm import print_qr

        api = OkLine()
        print("Scan this QR with the LINE app, then enter/confirm the PIN:\n")
        res = api.qr_login(
            on_qr=lambda u: print_qr(u), on_pin=lambda pin: print(f"\n>>> PIN: {pin}\n")
        )
        if not res.access_token:
            print("QR login did not complete.", file=sys.stderr)
            return 2
        try:
            api.save_tokens(args.tokens_file)
            print(f"(session saved to {args.tokens_file})")
        except Exception:
            pass
    elif args.token:
        api = OkLine(access_token=args.token, redact=True)
    else:
        try:
            api = OkLine.from_tokens_file(args.tokens_file)
        except FileNotFoundError:
            print(
                f"No session file {args.tokens_file!r}. Create one with:\n"
                f"    python -m okline qr-login --save {args.tokens_file}",
                file=sys.stderr,
            )
            return 2

    print("OkLine live test - app CHROMEOS 3.7.2")
    try:
        fails = run(api, to=args.to, image=args.image, file=args.file, listen=args.listen)
    finally:
        api.close()
    print("\nFull transcript: live_test_report.txt (HAR: live_test_report.har)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

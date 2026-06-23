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
from typing import Any, Callable, List, Optional

from okline import OkLine, enums
from okline.entities import Contact, Group, Profile, parse_contacts


# ---------------------------------------------------------------------------
# tiny test runner
# ---------------------------------------------------------------------------
class Result:
    def __init__(self, name: str, ok: bool, status, ms: float,
                 detail: str, skipped: bool = False) -> None:
        self.name = name
        self.ok = ok
        self.status = status
        self.ms = ms
        self.detail = detail
        self.skipped = skipped


class Runner:
    def __init__(self, api: OkLine) -> None:
        self.api = api
        self.results: List[Result] = []

    def section(self, title: str) -> None:
        print(f"\n=== {title} ===")

    def check(self, name: str, fn: Callable[[], Any], *,
              summary: Optional[Callable[[Any], str]] = None) -> Any:
        t = time.monotonic()
        try:
            value = fn()
            ms = (time.monotonic() - t) * 1000
            status = getattr(self.api.last, "status", 200)
            det = summary(value) if summary else _summ(value)
            self.results.append(Result(name, True, status, ms, det))
            print(f"  [OK ] {name:<46} {str(status):>3} {ms:6.0f}ms  {det}")
            return value
        except Exception as exc:  # noqa: BLE001
            ms = (time.monotonic() - t) * 1000
            status = getattr(self.api.last, "status", None)
            self.results.append(Result(name, False, status, ms, str(exc)[:160]))
            print(f"  [FAIL] {name:<45} {str(status):>3} {ms:6.0f}ms  {exc}")
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
        print(f"RESULT: {ok}/{len(ran)} checks passed   "
              f"({len(skips)} skipped, {len(fails)} failed)")
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
def run(api: OkLine, *, to: Optional[str], image: Optional[str],
        file: Optional[str], listen: int) -> int:
    r = Runner(api)

    # --- identity ----------------------------------------------------------
    r.section("Identity & account")
    profile = r.check("getProfile", lambda: api.get_profile(),
                      summary=lambda p: f"{p.get('displayName')} ({p.get('mid','')[:10]}) {p.get('regionCode')}")
    region = profile.get("regionCode", "") if isinstance(profile, dict) else ""
    my_mid = profile.get("mid") if isinstance(profile, dict) else None
    r.check("getProfile -> Profile model",
            lambda: Profile.from_dict(profile),
            summary=lambda p: f"name={p.display_name!r}")
    r.check("getSettings", lambda: api.get_settings())
    r.check("getSettingsAttributes2", lambda: api.get_settings_attributes2([16, 33, 25, 60, 61]))
    r.check("getConfigurations", lambda: api.get_configurations(region=region))
    r.check("getServerTime", lambda: api.get_server_time())

    # --- contacts ----------------------------------------------------------
    r.section("Contacts")
    ids = r.check("getAllContactIds", lambda: api.get_all_contact_ids())
    first_contact = ids[0] if isinstance(ids, list) and ids else None
    if first_contact:
        cres = r.check("getContactsV2", lambda: api.get_contacts([first_contact]))
        r.check("getContactsV2 -> Contact models",
                lambda: parse_contacts(cres),
                summary=lambda d: f"{len(d)} contact(s); first={next(iter(d.values())).name!r}")
    else:
        r.skip("getContactsV2", "no contacts")
    r.check("getFavoriteMids", lambda: api.get_favorite_mids())
    r.check("getBlockedContactIds", lambda: api.get_blocked_contact_ids())
    r.check("getRecommendationIds", lambda: api.get_recommendation_ids())
    r.check("getBlockedRecommendationIds", lambda: api.get_blocked_recommendation_ids())

    # --- chats / rooms -----------------------------------------------------
    r.section("Chats, groups & rooms")
    chat_mids = r.check("getAllChatMids", lambda: api.get_all_chat_mids(),
                        summary=lambda d: f"member={len(d.get('memberChatMids', []))} invited={len(d.get('invitedChatMids', []))}")
    members = chat_mids.get("memberChatMids") if isinstance(chat_mids, dict) else []
    first_chat = members[0] if members else None
    if first_chat:
        gres = r.check("getChats", lambda: api.get_chats([first_chat]))
        def _g(d):
            chats = d.get("chats", []) if isinstance(d, dict) else []
            return Group.from_dict(chats[0]) if chats else None
        r.check("getChats -> Group model", lambda: _g(gres),
                summary=lambda g: (f"{g.name!r} members={g.member_count}" if g else "-"))
    else:
        r.skip("getChats", "no group chats")
    r.check("getMessageBoxes", lambda: api.get_message_boxes(limit=5),
            summary=lambda d: f"boxes={len(d.get('messageBoxes', [])) if isinstance(d, dict) else d}")
    if first_chat:
        r.check("getMessageBoxesByIds", lambda: api.get_message_boxes_by_ids([first_chat]))
        r.check("getRecentMessagesV2", lambda: api.get_recent_messages(first_chat, 5))

    # --- e2ee / channel ----------------------------------------------------
    r.section("E2EE keys & channel")
    r.check("getE2EEPublicKeysEx", lambda: api.get_e2ee_public_keys_ex())
    r.check("getLastOpRevision", lambda: api.get_last_op_revision())
    r.check("issueChannelToken", lambda: api.issue_channel_token(),
            summary=lambda d: "channelAccessToken OK" if isinstance(d, dict) and d.get("channelAccessToken") else _summ(d))

    # --- writes (only with --to) -------------------------------------------
    r.section("Messaging (write)")
    target = to or my_mid
    if not to:
        print(f"  (no --to given; sending write-tests to yourself: {str(my_mid)[:12]}…)")
    if target:
        sent = r.check("send_text", lambda: api.send_text(target, "OkLine live test"),
                       summary=lambda m: f"id={m.get('id') if isinstance(m, dict) else m}")
        msg_id = sent.get("id") if isinstance(sent, dict) else None
        if msg_id:
            r.check("react (LOVE)", lambda: api.react(msg_id, enums.PredefinedReactionType.LOVE))
            r.check("cancel_reaction", lambda: api.cancel_reaction(msg_id))
            r.check("unsend_message", lambda: api.unsend_message(msg_id))
        else:
            r.skip("react/unsend", "no message id from send_text")
    else:
        r.skip("send_text", "no target mid")

    # --- media (only with --image / --file) --------------------------------
    r.section("Media (write)")
    if target and image:
        r.check("send_image", lambda: api.send_image(target, image),
                summary=lambda m: f"id={m.get('id') if isinstance(m, dict) else m}")
    else:
        r.skip("send_image", "pass --to and --image to test")
    if target and file:
        r.check("send_file", lambda: api.send_file(target, file),
                summary=lambda m: f"id={m.get('id') if isinstance(m, dict) else m}")
    else:
        r.skip("send_file", "pass --to and --file to test")

    # --- recording ---------------------------------------------------------
    r.section("Recording")
    r.check("history captured", lambda: api.history,
            summary=lambda h: f"{len(h)} exchanges recorded")
    r.check("save HAR log", lambda: api.save_log("live_test_report.har", fmt="har") or "saved")
    r.check("save text log", lambda: api.save_log("live_test_report.txt", fmt="text") or "saved")

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
        except Exception as exc:  # noqa: BLE001
            print(f"  stream ended: {exc}")

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    while time.monotonic() < stop and th.is_alive():
        time.sleep(0.5)
    print(f"  got {count['events']} SSE events, ~{count['ops']} operations")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="OkLine live integration test")
    p.add_argument("--tokens-file", default="tokens.json",
                   help="session file from `okline qr-login --save` (default tokens.json)")
    p.add_argument("--token", help="access token (overrides the file)")
    p.add_argument("--to", help="target mid for write tests (group/friend/yourself)")
    p.add_argument("--image", help="image path to test send_image")
    p.add_argument("--file", help="file path to test send_file")
    p.add_argument("--listen", type=int, default=0, help="watch the event stream N seconds")
    args = p.parse_args(argv)

    try:  # display names / messages may contain Thai or emoji
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    if args.token:
        api = OkLine(access_token=args.token, redact=True)
    else:
        try:
            api = OkLine.from_tokens_file(args.tokens_file)
        except FileNotFoundError:
            print(f"No session file {args.tokens_file!r}. Create one with:\n"
                  f"    python -m okline qr-login --save {args.tokens_file}", file=sys.stderr)
            return 2

    print(f"OkLine live test - app CHROMEOS 3.7.2")
    try:
        fails = run(api, to=args.to, image=args.image, file=args.file, listen=args.listen)
    finally:
        api.close()
    print("\nFull transcript: live_test_report.txt (HAR: live_test_report.har)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

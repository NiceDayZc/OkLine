#!/usr/bin/env python3
"""chatlog — pretty-print a chat's recent messages, decrypting E2EE when possible.

python examples/chatlog.py C1234...chatmid
python examples/chatlog.py U5678...usermid -n 50
"""

from __future__ import annotations

import argparse
import time

from _common import add_auth_args, all_contacts, contact_name, load


def _clock(ms) -> str:
    """createdTime (ms) -> 'HH:MM', or '--:--' if unavailable."""
    try:
        return time.strftime("%H:%M", time.localtime(int(ms) / 1000))
    except Exception:
        return "--:--"


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("chat_mid", help="the chat/group/user mid")
    p.add_argument("-n", "--count", type=int, default=30, help="how many messages")
    args = p.parse_args()
    api = load(args)
    try:
        ready = api.e2ee.is_ready()
        names = {mid: contact_name(w) for mid, w in all_contacts(api).items()}
        me = api.get_profile() or {}
        names.setdefault(me.get("mid"), me.get("displayName") or "me")
        msgs = api.get_recent_messages(args.chat_mid, args.count) or []
        for m in reversed(msgs):  # oldest first
            if m.get("chunks"):
                if ready:
                    try:
                        m = api.decrypt_message(m)
                    except Exception:
                        m = dict(m, text="[encrypted]")
                else:
                    m = dict(m, text="[encrypted — load E2EE keys]")
            sender = m.get("from") or ""
            who = names.get(sender) or sender or "?"
            text = m.get("text")
            if not text:
                ctype = m.get("contentType")
                text = f"[{ctype}]" if ctype else "[no text]"
            print(f"{_clock(m.get('createdTime'))} <{who}>: {text}")
        print(f"\n{len(msgs)} message(s)" + ("" if ready else "  (E2EE keys not loaded)"))
    finally:
        api.close()


if __name__ == "__main__":
    main()

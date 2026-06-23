#!/usr/bin/env python3
"""stats — a richer dashboard of your LINE account.

    python examples/stats.py
"""
from __future__ import annotations

import argparse

from _common import add_auth_args, load


def main() -> None:
    args = add_auth_args(argparse.ArgumentParser(description=__doc__)).parse_args()
    api = load(args)
    try:
        p = api.get_profile() or {}
        chats = api.get_all_chat_mids() or {}
        rows = [
            ("name", p.get("displayName")),
            ("mid", p.get("mid")),
            ("userid", p.get("userid")),
            ("region", p.get("regionCode")),
            ("status", p.get("statusMessage")),
            ("", ""),
            ("contacts", len(api.get_all_contact_ids() or [])),
            ("groups", len(chats.get("memberChatMids", []))),
            ("invited", len(chats.get("invitedChatMids", []))),
            ("favorites", len(api.get_favorite_mids() or [])),
            ("blocked", len(api.get_blocked_contact_ids() or [])),
            ("recommended", len(api.get_recommendation_ids() or [])),
            ("", ""),
            ("e2ee ready", "yes" if api.e2ee.is_ready() else "no"),
        ]
        width = max(len(k) for k, _ in rows)
        for k, v in rows:
            if not k:
                print()
            else:
                print(f"{k.ljust(width)} : {v}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

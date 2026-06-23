#!/usr/bin/env python3
"""whoami — print your LINE profile and a few account stats.

python examples/whoami.py
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, load


def main() -> None:
    args = add_auth_args(argparse.ArgumentParser(description=__doc__)).parse_args()
    api = load(args)
    try:
        p = api.get_profile()
        chats = api.get_all_chat_mids() or {}
        print(f"name      : {p.get('displayName')}")
        print(f"mid       : {p.get('mid')}")
        print(f"userid    : {p.get('userid')}")
        print(f"region    : {p.get('regionCode')}")
        print(f"status    : {p.get('statusMessage')}")
        print(f"contacts  : {len(api.get_all_contact_ids() or [])}")
        print(f"groups    : {len(chats.get('memberChatMids', []))}")
        print(f"invites   : {len(chats.get('invitedChatMids', []))}")
        print(f"favorites : {len(api.get_favorite_mids() or [])}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

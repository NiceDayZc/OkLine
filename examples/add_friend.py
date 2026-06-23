#!/usr/bin/env python3
"""add_friend — add a friend by mid (U...) or by public LINE ID.

python examples/add_friend.py U0123456789abcdef0123456789abcdef
python examples/add_friend.py nb.vtg
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("who", help="a user mid (U...) or a public LINE ID")
    args = p.parse_args()
    api = load(args)
    try:
        who = args.who
        if who[:1].lower() == "u" and len(who) >= 32:
            mid = who
        else:
            contact = api.find_contact_by_userid(who) or {}
            mid = contact.get("mid") if isinstance(contact, dict) else None
            if not mid:
                print(f"no user found for {who!r}")
                return
            print(f"resolved  : {who} -> {mid} ({contact.get('displayName')})")
        api.add_friend_by_mid(mid)
        print(f"added     : {mid}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

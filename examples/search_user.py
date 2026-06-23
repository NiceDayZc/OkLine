#!/usr/bin/env python3
"""search_user — find a user by their public LINE ID, optionally add them.

    python examples/search_user.py nb.vtg
    python examples/search_user.py nb.vtg --add
"""
from __future__ import annotations

import argparse

from _common import add_auth_args, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("userid", help="public LINE ID (e.g. nb.vtg)")
    p.add_argument("--add", action="store_true", help="add this user as a friend")
    args = p.parse_args()
    api = load(args)
    try:
        contact = api.find_contact_by_userid(args.userid) or {}
        if not isinstance(contact, dict) or not contact.get("mid"):
            print(f"no user found for {args.userid!r}")
            return
        mid = contact.get("mid")
        print(f"mid       : {mid}")
        print(f"name      : {contact.get('displayName')}")
        print(f"status    : {contact.get('statusMessage')}")
        if args.add:
            api.add_friend_by_mid(mid)
            print(f"added     : {mid}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

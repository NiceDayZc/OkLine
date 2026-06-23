#!/usr/bin/env python3
"""profile — show a user's profile by mid (display name, status, buddy detail).

python examples/profile.py U0123456789abcdef0123456789abcdef
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, all_contacts, contact_name, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("mid", help="user mid (U...) to look up")
    args = p.parse_args()
    api = load(args)
    try:
        mid = args.mid
        name = contact_name(all_contacts(api).get(mid, {}))
        print(f"name      : {name or '(not in your contacts)'}")
        print(f"mid       : {mid}")

        try:
            detail = api.get_buddy_detail(mid) or {}
        except Exception as e:
            detail = {}
            print(f"buddy     : (no detail: {e})")
        if isinstance(detail, dict):
            if detail.get("statusMessage"):
                print(f"status    : {detail.get('statusMessage')}")
            shown = {"statusMessage"}
            for k, v in detail.items():
                if k in shown or v in (None, "", [], {}):
                    continue
                print(f"{k:<10}: {v}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""find_contact — look up a friend's mid by (part of) their display name.

python examples/find_contact.py "Hardlyspeak"
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, all_contacts, contact_name, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("query", help="name (or part of it) to search for")
    args = p.parse_args()
    api = load(args)
    try:
        q = args.query.lower()
        hits = [
            (mid, name)
            for mid, name in ((m, contact_name(w)) for m, w in all_contacts(api).items())
            if q in name.lower()
        ]
        if not hits:
            print(f"no contact matching {args.query!r}")
            return
        for mid, name in hits:
            print(f"{mid}  {name}")
        print(f"\n{len(hits)} match(es)")
    finally:
        api.close()


if __name__ == "__main__":
    main()

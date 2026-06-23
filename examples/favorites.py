#!/usr/bin/env python3
"""favorites — list, add, or remove favorite chats.

python examples/favorites.py list
python examples/favorites.py add C1234567890abcdef
python examples/favorites.py remove C1234567890abcdef
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, all_contacts, contact_name, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("action", choices=("list", "add", "remove"))
    p.add_argument("mid", nargs="?", help="chat mid (for add/remove)")
    args = p.parse_args()
    api = load(args)
    try:
        if args.action == "list":
            ids = api.get_favorite_mids() or []
            if not ids:
                print("no favorites")
                return
            names = {mid: contact_name(w) for mid, w in all_contacts(api).items()}
            for mid in ids:
                print(f"{mid}  {names.get(mid, '')}")
            print(f"\n{len(ids)} favorite(s)")
        elif args.action == "add":
            if not args.mid:
                p.error("add requires a mid")
            api.set_chat_favorite(args.mid, 1)
            print(f"favorited {args.mid}")
        else:
            if not args.mid:
                p.error("remove requires a mid")
            api.set_chat_favorite(args.mid, 0)
            print(f"unfavorited {args.mid}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

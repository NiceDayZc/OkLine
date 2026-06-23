#!/usr/bin/env python3
"""block — list, add, or remove blocked contacts.

python examples/block.py list
python examples/block.py add U1234567890abcdef
python examples/block.py remove U1234567890abcdef
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, all_contacts, contact_name, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("action", choices=("list", "add", "remove"))
    p.add_argument("mid", nargs="?", help="contact mid (for add/remove)")
    args = p.parse_args()
    api = load(args)
    try:
        if args.action == "list":
            ids = api.get_blocked_contact_ids() or []
            if not ids:
                print("no blocked contacts")
                return
            names = {mid: contact_name(w) for mid, w in all_contacts(api).items()}
            for mid in ids:
                print(f"{mid}  {names.get(mid, '')}")
            print(f"\n{len(ids)} blocked")
        elif args.action == "add":
            if not args.mid:
                p.error("add requires a mid")
            api.block_contact(args.mid)
            print(f"blocked {args.mid}")
        else:
            if not args.mid:
                p.error("remove requires a mid")
            api.unblock_contact(args.mid)
            print(f"unblocked {args.mid}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

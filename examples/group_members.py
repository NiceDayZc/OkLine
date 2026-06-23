#!/usr/bin/env python3
"""group_members — list the members of a group, with their names.

    python examples/group_members.py C1234...groupmid
    python examples/group_members.py --list           # list your groups + mids
"""
from __future__ import annotations

import argparse

from okline import Group

from _common import add_auth_args, contact_name, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("group_mid", nargs="?", help="the group/chat mid (C…)")
    p.add_argument("--list", action="store_true", help="just list your groups + mids")
    args = p.parse_args()
    api = load(args)
    try:
        if args.list or not args.group_mid:
            ids = (api.get_all_chat_mids() or {}).get("memberChatMids", [])
            for g in (api.get_chats(ids).get("chats", []) if ids else []):
                grp = Group.from_dict(g)
                print(f"{grp.chat_mid}  ({grp.member_count:>3}) {grp.name}")
            return
        chats = api.get_chats([args.group_mid]).get("chats", [])
        if not chats:
            print("group not found"); return
        grp = Group.from_dict(chats[0])
        print(f"# {grp.name}  ({grp.member_count} members)\n")
        # resolve member names via getContactsV2
        names = {}
        members = grp.member_mids
        for i in range(0, len(members), 100):
            res = api.get_contacts(members[i:i + 100])
            for mid, w in (res.get("contacts", {}) or {}).items():
                names[mid] = contact_name(w)
        for mid in members:
            print(f"  {mid}  {names.get(mid, '(unknown)')}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

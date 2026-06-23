#!/usr/bin/env python3
"""groups — list your groups, or leave / accept an invitation.

python examples/groups.py list
python examples/groups.py leave C1234567890abcdef
python examples/groups.py accept C1234567890abcdef
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, load

from okline import Group


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("action", choices=("list", "leave", "accept"))
    p.add_argument("mid", nargs="?", help="group chat mid (for leave/accept)")
    args = p.parse_args()
    api = load(args)
    try:
        if args.action == "list":
            chats = api.get_all_chat_mids() or {}
            members = chats.get("memberChatMids", []) or []
            invited = chats.get("invitedChatMids", []) or []
            print(f"member  : {len(members)}")
            print(f"invited : {len(invited)}\n")
            res = api.get_chats(members) if members else {}
            for g in res.get("chats", []) or []:
                grp = Group.from_dict(g)
                print(f"{grp.chat_mid}  {grp.name}  ({grp.member_count} members)")
        elif args.action == "leave":
            if not args.mid:
                p.error("leave requires a mid")
            api.leave_chat(args.mid)
            print(f"left {args.mid}")
        else:
            if not args.mid:
                p.error("accept requires a mid")
            api.accept_chat_invitation(args.mid)
            print(f"joined {args.mid}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

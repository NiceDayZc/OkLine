#!/usr/bin/env python3
"""backup_chat — save the recent messages of a chat to a JSON file.

python examples/backup_chat.py C1234...chatmid -n 200 -o chat.json
"""

from __future__ import annotations

import argparse
import json

from _common import add_auth_args, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("chat_mid", help="the chat/group/user mid")
    p.add_argument("-n", "--count", type=int, default=200, help="how many messages")
    p.add_argument("-o", "--output", help="output file (default <mid>.json)")
    args = p.parse_args()
    api = load(args)
    try:
        out = args.output or f"{args.chat_mid}.json"
        messages: list = []
        # page backwards until we have `count` (or run out)
        recent = api.get_recent_messages(args.chat_mid, min(args.count, 200)) or []
        messages.extend(recent)
        while len(messages) < args.count and messages:
            oldest = messages[-1]
            end_id, end_time = (
                oldest.get("id"),
                int(oldest.get("deliveredTime") or oldest.get("createdTime") or 0),
            )
            page = (
                api.get_previous_messages(
                    args.chat_mid, end_id, end_time, count=min(args.count - len(messages), 200)
                )
                or []
            )
            if not page:
                break
            messages.extend(page)
        messages = messages[: args.count]
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(messages, fh, ensure_ascii=False, indent=2)
        print(f"saved {len(messages)} messages -> {out}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

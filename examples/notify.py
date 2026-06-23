#!/usr/bin/env python3
"""notify — watch incoming messages and print alert lines.

    python examples/notify.py
    python examples/notify.py --keyword urgent --group-only
"""
from __future__ import annotations

import argparse

from okline import Bot

from _common import add_auth_args, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("--keyword", help="only alert on messages containing this text")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--group-only", action="store_true", help="only group/room messages")
    g.add_argument("--dm-only", action="store_true", help="only direct messages")
    args = p.parse_args()

    api = load(args)
    try:
        bot = Bot(api)

        @bot.on_message
        def handle(ctx) -> None:
            if args.group_only and not ctx.is_group:
                return
            if args.dm_only and ctx.is_group:
                return
            text = ctx.text or ""
            if args.keyword and args.keyword not in text:
                return
            kind = "group" if ctx.is_group else "dm"
            print(f"[{kind}] {ctx.sender}: {text or '<non-text>'}")

        print("watching for messages; Ctrl-C to stop")
        bot.run()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        api.close()


if __name__ == "__main__":
    main()

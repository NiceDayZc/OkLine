#!/usr/bin/env python3
"""autoreply — a keyword auto-reply bot driven by simple rules.

python examples/autoreply.py --rule "hello=hi there!" --rule "bye=see ya"
python examples/autoreply.py --ignore-case --rule "PING=pong"
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, load

from okline import Bot


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument(
        "--rule",
        action="append",
        default=[],
        metavar="KEYWORD=REPLY",
        help="reply with REPLY when KEYWORD appears (repeatable)",
    )
    p.add_argument(
        "--ignore-case", action="store_true", help="match keywords case-insensitively"
    )
    args = p.parse_args()

    rules: dict[str, str] = {}
    for raw in args.rule:
        if "=" not in raw:
            p.error(f"bad --rule {raw!r}; expected KEYWORD=REPLY")
        key, reply = raw.split("=", 1)
        key = key.strip()
        if args.ignore_case:
            key = key.lower()
        rules[key] = reply
    if not rules:
        p.error("need at least one --rule KEYWORD=REPLY")

    api = load(args)
    try:
        bot = Bot(api)

        @bot.on_message
        def handle(ctx) -> None:
            if not ctx.text:
                return
            haystack = ctx.text.lower() if args.ignore_case else ctx.text
            for key, reply in rules.items():
                if key in haystack:
                    print(f"match {key!r} from {ctx.sender}: replying")
                    ctx.reply(reply)
                    return

        print(f"auto-reply running with {len(rules)} rule(s); Ctrl-C to stop")
        bot.run()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        api.close()


if __name__ == "__main__":
    main()

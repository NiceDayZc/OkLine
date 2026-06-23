#!/usr/bin/env python3
"""watch — print incoming messages live (optionally echo them back).

python examples/watch.py            # just print incoming messages
python examples/watch.py --echo     # reply "you said: ..." (a tiny echo bot)
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, load

from okline import Bot


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("--echo", action="store_true", help="echo each message back")
    args = p.parse_args()
    api = load(args)
    bot = Bot(api)

    @bot.on_message
    def on_msg(ctx):
        where = "group" if ctx.is_group else "dm"
        print(f"[{where}] {ctx.sender}: {ctx.text!r}")
        if args.echo and ctx.text:
            ctx.reply(f"you said: {ctx.text}")

    print("watching for messages... (Ctrl-C to stop)")
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        api.close()


if __name__ == "__main__":
    main()

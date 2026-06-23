#!/usr/bin/env python3
"""broadcast — send the same text to several chats (rate-limited, with confirm).

    python examples/broadcast.py "Happy new year!" --to C111... C222... U333...

Use responsibly: only message chats/people who expect it. Not for spam.
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, load

from okline.ratelimit import RateLimiter


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("message", help="the text to send")
    p.add_argument("--to", nargs="+", required=True, help="one or more target mids")
    p.add_argument("--rate", type=float, default=3.0, help="messages per second")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = p.parse_args()
    api = load(args)
    api.transport.rate_limiter = RateLimiter(rate=args.rate, per=1.0)
    try:
        print(f"About to send to {len(args.to)} target(s):\n  {args.message!r}")
        if not args.yes and input("proceed? [y/N] ").strip().lower() != "y":
            print("cancelled")
            return
        ok = 0
        for mid in args.to:
            try:
                api.send_text(mid, args.message)
                ok += 1
                print(f"  sent -> {mid}")
            except Exception as exc:
                print(f"  FAIL -> {mid}: {exc}")
        print(f"\n{ok}/{len(args.to)} delivered")
    finally:
        api.close()


if __name__ == "__main__":
    main()

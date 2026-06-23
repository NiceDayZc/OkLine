#!/usr/bin/env python3
"""send — quick-send text, a sticker, or a location to a chat.

    python examples/send.py C123 --text "hi"
    python examples/send.py U123 --sticker 11537 52002734
    python examples/send.py C123 --location 35.6586 139.7454 --title "Tokyo Tower"
"""
from __future__ import annotations

import argparse

from _common import add_auth_args, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("to", help="target chat/group/user mid")
    p.add_argument("--text", help="text message to send")
    p.add_argument("--sticker", nargs=2, metavar=("PKG", "STK"),
                   help="package id and sticker id")
    p.add_argument("--location", nargs=2, type=float, metavar=("LAT", "LON"),
                   help="latitude and longitude")
    p.add_argument("--title", default="", help="location title (with --location)")
    args = p.parse_args()
    if not (args.text or args.sticker or args.location):
        p.error("provide one of --text / --sticker / --location")
    api = load(args)
    try:
        if args.text:
            res = api.send_text(args.to, args.text)
        elif args.sticker:
            res = api.send_sticker(args.to, args.sticker[0], args.sticker[1])
        else:
            lat, lon = args.location
            res = api.send_location(args.to, lat, lon, title=args.title)
        print("sent; message id:", res.get("id") if isinstance(res, dict) else res)
    finally:
        api.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""send_media — send an image or file to a chat.

    python examples/send_media.py C123...chat --image photo.jpg
    python examples/send_media.py U123...friend --file report.pdf

Note: media send is the V1 (non-E2EE) flow — works for chats that allow plain
mode (most groups). Letter-Sealed DMs are not yet supported for media.
"""
from __future__ import annotations

import argparse

from _common import add_auth_args, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("to", help="target chat/group/user mid")
    p.add_argument("--image", help="path to an image")
    p.add_argument("--file", help="path to a file")
    args = p.parse_args()
    if not (args.image or args.file):
        p.error("provide --image or --file")
    api = load(args)
    try:
        if args.image:
            res = api.send_image(args.to, args.image)
        else:
            res = api.send_file(args.to, args.file)
        print("sent; message id:", res.get("id") if isinstance(res, dict) else res)
    finally:
        api.close()


if __name__ == "__main__":
    main()

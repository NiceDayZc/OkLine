#!/usr/bin/env python3
"""unsend — recall (unsend) one of your own messages.

python examples/unsend.py <message_id>
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("message_id", help="id of your message to unsend")
    args = p.parse_args()
    api = load(args)
    try:
        api.unsend_message(args.message_id)
        print("ok")
    finally:
        api.close()


if __name__ == "__main__":
    main()

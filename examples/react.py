#!/usr/bin/env python3
"""react — react to (or un-react) a message.

python examples/react.py <message_id> --reaction LOVE
python examples/react.py <message_id> --remove
"""

from __future__ import annotations

import argparse

from _common import add_auth_args, load

from okline.enums import PredefinedReactionType


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("message_id", help="id of the message to react to")
    p.add_argument(
        "--reaction",
        default="NICE",
        choices=[r.name for r in PredefinedReactionType],
        help="reaction name (default NICE)",
    )
    p.add_argument(
        "--remove", action="store_true", help="cancel your reaction instead of adding one"
    )
    args = p.parse_args()
    api = load(args)
    try:
        if args.remove:
            if hasattr(api, "cancel_reaction"):
                api.cancel_reaction(args.message_id)
            else:
                api.react(args.message_id, int(PredefinedReactionType.NICE))
        else:
            api.react(args.message_id, int(PredefinedReactionType[args.reaction]))
        print("ok")
    finally:
        api.close()


if __name__ == "__main__":
    main()

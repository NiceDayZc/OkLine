#!/usr/bin/env python3
"""login — scan a QR code to log in, and save the session for reuse.

Run this once; the other tools then reuse the saved session (no re-scan).

    python examples/login.py                 # -> tokens.json
    python examples/login.py -o my.json      # save somewhere else
"""

from __future__ import annotations

import argparse
import sys

from _common import interactive_login


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "-o",
        "--output",
        default="tokens.json",
        help="where to save the session (default tokens.json)",
    )
    args = p.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # QR blocks / Thai on Windows
    except Exception:
        pass
    api = interactive_login(args.output)
    try:
        me = api.get_profile()
        print(f"\nLogged in as {me.get('displayName')}  ({me.get('mid')})")
        print(f"E2EE keys ready: {'yes' if api.e2ee.is_ready() else 'no'}")
        print(f"\nTry it:  python examples/whoami.py --tokens-file {args.output}")
    finally:
        api.close()


if __name__ == "__main__":
    main()

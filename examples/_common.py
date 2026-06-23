"""Shared helpers for the OkLine example mini-tools.

Each tool loads a session the same way (``--tokens-file`` / ``--token`` / env /
the default ``tokens.json``) so you can run them like::

    python examples/whoami.py
    python examples/find_contact.py "Some Name"
"""

from __future__ import annotations

import argparse
import os
import sys

from okline import OkLine


def add_auth_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--tokens-file", default="tokens.json",
                        help="session JSON from `okline qr-login --save` (default tokens.json)")
    parser.add_argument("--token", help="access token (overrides the file)")
    return parser


def load(args: argparse.Namespace) -> OkLine:
    """Build an authenticated OkLine, or exit with a helpful message."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Thai / emoji output on Windows
    except Exception:
        pass
    if getattr(args, "token", None):
        return OkLine(access_token=args.token)
    path = getattr(args, "tokens_file", "tokens.json")
    if not os.path.exists(path):
        for alt in ("tokens.json", os.path.expanduser("~/tokens.json")):
            if os.path.exists(alt):
                path = alt
                break
    if not os.path.exists(path):
        sys.exit(f"No session file. Create one with:\n"
                 f"    python -m okline qr-login --save {path}")
    return OkLine.from_tokens_file(path)


def contact_name(wrapper: dict) -> str:
    """Best display name from a getContactsV2 wrapper or bare Contact."""
    c = wrapper.get("contact", wrapper) if isinstance(wrapper, dict) else {}
    return c.get("displayNameOverridden") or c.get("displayName") or ""


def all_contacts(api: OkLine) -> dict:
    """Return ``{mid: Contact}`` for every contact (fetched in chunks)."""
    ids = api.get_all_contact_ids() or []
    out: dict = {}
    for i in range(0, len(ids), 100):
        res = api.get_contacts(ids[i:i + 100])
        out.update((res.get("contacts", {}) or {}) if isinstance(res, dict) else {})
    return out

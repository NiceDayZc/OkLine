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
                        help="session JSON (default tokens.json); created on first run")
    parser.add_argument("--token", help="access token (overrides the file)")
    parser.add_argument("--login", action="store_true",
                        help="force a fresh QR login even if a session exists")
    return parser


def _qr_login_and_save(path: str) -> OkLine:
    """Interactive QR login (scan with the LINE app), then save the session."""
    from okline.qrterm import print_qr
    api = OkLine()
    print("No saved session yet — scan this QR with the LINE app "
          "(Settings > Add friends > QR code):\n")
    api.qr_login(
        on_qr=lambda url: print_qr(url),
        on_pin=lambda pin: print(f"\n>>> Confirm this PIN in the app:  {pin}\n"),
    )
    api.save_tokens(path)
    print(f"\nLogged in — session saved to {path} (reused automatically next time).")
    return api


def load(args: argparse.Namespace) -> OkLine:
    """Build an authenticated OkLine — logging in by QR on first use.

    Resolution order: ``--token`` → an existing session file → otherwise a fresh
    QR login (saved to the file so subsequent runs are instant).  Pass ``--login``
    to force a new QR login.  A restored session also brings back E2EE keys.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Thai / emoji output on Windows
    except Exception:
        pass
    if getattr(args, "token", None):
        return OkLine(access_token=args.token)
    path = getattr(args, "tokens_file", "tokens.json")
    if not getattr(args, "login", False):
        if not os.path.exists(path):
            for alt in ("tokens.json", os.path.expanduser("~/tokens.json")):
                if os.path.exists(alt):
                    path = alt
                    break
        if os.path.exists(path):
            return OkLine.from_tokens_file(path)
    return _qr_login_and_save(path)


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

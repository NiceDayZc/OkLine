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


def render_qr(url: str) -> None:
    """Draw the login QR in the terminal, falling back to the raw URL."""
    try:
        from okline.qrterm import print_qr
        print_qr(url)
    except ModuleNotFoundError:
        print(url)
        print("\n(install 'qrcode' for an inline QR:  pip install qrcode  — or paste\n"
              " the URL above into any QR generator and scan that with the LINE app)")


def interactive_login(path: str = "tokens.json") -> OkLine:
    """Scan a QR with the LINE app to log in, then save the session to ``path``.

    The saved file includes your E2EE keys, so later runs (and the other example
    tools) reuse this session automatically — no re-scan.
    """
    api = OkLine()
    print("Scan this QR with the LINE app "
          "(Settings > Add friends > QR code):\n")
    api.qr_login(
        on_qr=render_qr,
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
    print("No saved session yet —")
    return interactive_login(path)


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

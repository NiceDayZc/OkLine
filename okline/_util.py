"""Small internal utilities shared across modules."""

from __future__ import annotations

import sys
from typing import Any


def reconfigure_stdout_utf8(stream: Any = None) -> None:
    """Best-effort switch a text stream to UTF-8 so box glyphs / Thai / emoji
    print on any OS code page.  A no-op if the stream cannot be reconfigured."""
    stream = sys.stdout if stream is None else stream
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8")
    except Exception:
        pass

"""Session persistence — save / load credentials so you log in once.

>>> from okline import OkLine
>>> api = OkLine()
>>> api.auth.qr_login(on_qr=print)        # first time: scan the QR   # doctest: +SKIP
>>> api.save_tokens("session.json")        # remember the tokens       # doctest: +SKIP

>>> api = OkLine.from_tokens_file("session.json")   # next time: instant  # doctest: +SKIP
>>> api.get_profile()                                                     # doctest: +SKIP

When loaded from a file, OkLine **auto-saves** the file whenever the access
token is refreshed, so the session stays valid across runs.

> ⚠️ The session file contains live credentials — keep it private (it is matched
> by the project ``.gitignore``).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any


def _coerce_float(value: Any) -> float | None:
    """Coerce a persisted schedule value (number or numeric string) to float.

    The extension stores tokenV3IssueResult fields as strings on the wire
    (``Number()`` at the use site); accept both shapes.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Session:
    """Persisted credentials."""

    access_token: str | None = None
    refresh_token: str | None = None
    certificate: str | None = None
    mid: str | None = None
    region_code: str | None = None
    #: Exported E2EE keychain (``E2EEManager.export_keys()``) so Letter Sealing
    #: works across sessions without a fresh QR login.  Private-key material —
    #: keep the file secret.
    e2ee: dict[str, Any] | None = None
    #: Proactive token-renewal schedule (the extension's tT class arms a
    #: ``setTimeout(renewToken)`` on every token issuance): when the token was
    #: issued (epoch seconds) and how long it stays fresh, so
    #: ``OkLine(..., auto_refresh_schedule=True)`` can re-arm the renewal
    #: timer right after ``from_tokens_file``.  ``refresh_api_retry_policy``
    #: is the server's ``refreshApiRetryPolicy`` block for 10202 retries.
    token_issue_time_epoch_sec: float | None = None
    duration_until_refresh_sec: float | None = None
    refresh_api_retry_policy: dict[str, Any] | None = None

    # JSON uses the camelCase keys the rest of the ecosystem expects.
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "accessToken": self.access_token,
            "refreshToken": self.refresh_token,
            "certificate": self.certificate,
            "mid": self.mid,
            "regionCode": self.region_code,
        }
        if self.e2ee:
            d["e2ee"] = self.e2ee
        if self.token_issue_time_epoch_sec is not None:
            d["tokenIssueTimeEpochSec"] = self.token_issue_time_epoch_sec
        if self.duration_until_refresh_sec is not None:
            d["durationUntilRefreshInSec"] = self.duration_until_refresh_sec
        if self.refresh_api_retry_policy:
            d["refreshApiRetryPolicy"] = self.refresh_api_retry_policy
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Session:
        d = d or {}
        return cls(
            access_token=d.get("accessToken") or d.get("access_token"),
            refresh_token=d.get("refreshToken") or d.get("refresh_token"),
            certificate=d.get("certificate"),
            mid=d.get("mid"),
            region_code=d.get("regionCode") or d.get("region_code"),
            e2ee=d.get("e2ee"),
            # renewal schedule — absent in pre-v2.9 files (stays None)
            token_issue_time_epoch_sec=_coerce_float(
                d.get("tokenIssueTimeEpochSec") or d.get("token_issue_time_epoch_sec")
            ),
            duration_until_refresh_sec=_coerce_float(
                d.get("durationUntilRefreshInSec") or d.get("duration_until_refresh_sec")
            ),
            refresh_api_retry_policy=(
                d.get("refreshApiRetryPolicy") or d.get("refresh_api_retry_policy")
                if isinstance(
                    d.get("refreshApiRetryPolicy") or d.get("refresh_api_retry_policy"), dict
                )
                else None
            ),
        )

    def save(self, path: str) -> None:
        """Persist atomically: write to a temp file in the target directory,
        then ``os.replace`` it into place.

        Concurrent saves — the background renewal timer and a user thread
        both calling ``save_tokens`` — can then never interleave their
        ``json.dump`` writes into a corrupted half-written file (the replace
        is atomic; a reader sees either the old or the new file, never a
        mix)."""
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp_path = tempfile.mkstemp(prefix=".okline-session-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    @classmethod
    def load(cls, path: str) -> Session:
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    @classmethod
    def from_tokens(cls, tokens) -> Session:
        # The renewal-schedule fields are read via getattr so this keeps
        # working whether or not the Tokens dataclass carries them.
        return cls(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            certificate=tokens.certificate,
            mid=tokens.mid,
            token_issue_time_epoch_sec=getattr(tokens, "token_issue_time_epoch_sec", None),
            duration_until_refresh_sec=getattr(tokens, "duration_until_refresh_sec", None),
            refresh_api_retry_policy=getattr(tokens, "refresh_api_retry_policy", None),
        )

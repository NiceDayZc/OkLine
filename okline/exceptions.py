"""Exception hierarchy for the LINE Chrome gateway client.

The gateway (``line-chrome-gw.line-apps.com``) returns Thrift application
exceptions encoded as JSON.  A failed Thrift call comes back with an HTTP error
status and a body shaped roughly like::

    {"error": {"code": 8, "message": "...", "metadata": {...}}}

or, for the talk auth layer, with an ``X-Line-...`` header carrying the code.
We normalise all of those into :class:`LineApiError` subclasses.
"""

from __future__ import annotations

from typing import Any


class LineError(Exception):
    """Base class for every error raised by this library."""


class LineConfigError(LineError):
    """Raised when the client is mis-configured (missing token, bad host...)."""


class LineTransportError(LineError):
    """Network / HTTP transport failure (timeout, connection reset, 5xx...)."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class LineApiError(LineError):
    """A Thrift application exception returned by the LINE backend.

    Attributes
    ----------
    code:
        The numeric Thrift error code (see :class:`okline.enums.ErrorCode`).
    reason:
        Human readable reason string (if any).
    metadata:
        Arbitrary error metadata returned by the server.
    path:
        The endpoint path that produced the error.
    raw:
        The decoded JSON error body, untouched.
    """

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        reason: str | None = None,
        metadata: Any = None,
        path: str | None = None,
        status: int | None = None,
        raw: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.metadata = metadata
        self.path = path
        self.status = status
        self.raw = raw

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        bits = [super().__str__()]
        if self.code is not None:
            bits.append(f"code={self.code}")
        if self.path:
            bits.append(f"path={self.path}")
        return " ".join(bits)


class LineAuthError(LineApiError):
    """Authentication / token problem.  Usually means the access token expired
    or the device certificate was revoked and a re-login is required."""


class LineMustUpgradeError(LineApiError):
    """The server requires a newer client version (``REQUEST_MUST_UPGRADE``)."""


class LineLoginRequired(LineAuthError):
    """No usable credentials are available; call one of the login flows first."""


# Maps the well-known talk error codes onto specific exception classes so that
# callers can ``except LineAuthError`` rather than string-matching.
_AUTH_CODES = {
    0,
    1,
    8,
}  # ILLEGAL_ARGUMENT/AUTHENTICATION_FAILED/NOT_AUTHORIZED_DEVICE families

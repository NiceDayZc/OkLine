"""Shared pytest fixtures and fakes for the OkLine test-suite.

All tests are **offline** — no real network and no Node.js required.  We fake
two things:

* the HTTP layer, via :class:`FakeSession` (a drop-in for ``requests.Session``)
* the LTSM Node bridge, via :class:`FakeBridge` (X-Hmac + curve-key ops)

Use the high-level helpers/fixtures below to build a client wired to canned
responses, then assert on what it sent (``api.transport.session.last``) or on
the recorded exchanges (``api.history``).
"""

from __future__ import annotations

import base64
import json
from typing import Any, Callable

import pytest

from okline import OkLine
from okline.transport import LineConfig, Tokens, Transport

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------
USER_MID = "u" + "a" * 32
USER_MID2 = "u" + "b" * 32
GROUP_MID = "c" + "1" * 32
ROOM_MID = "r" + "2" * 32

SAMPLE_PROFILE = {
    "mid": USER_MID,
    "userid": "okline",
    "displayName": "Tester",
    "regionCode": "TH",
    "statusMessage": "hi",
}
SAMPLE_CONTACT = {
    "mid": USER_MID2,
    "displayName": "Friend",
    "type": 0,
    "status": 1,
    "relation": 0,
}


# ---------------------------------------------------------------------------
# HTTP fakes
# ---------------------------------------------------------------------------
class FakeResp:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = headers or {"content-type": "application/json"}
        self.content = self.text.encode("utf-8")

    def json(self) -> Any:
        return json.loads(self.text)


class FakeSession:
    """A ``requests.Session`` replacement driven by a ``responder`` callback.

    ``responder(method, url, kwargs) -> FakeResp``.  The most recent call is
    stored on ``.last`` for assertions.
    """

    def __init__(self, responder: Callable[[str, str, dict], FakeResp]) -> None:
        self.responder = responder
        self.last: dict | None = None
        self.calls: list[dict] = []
        self.proxies: dict[str, str] = {}

    def request(self, method: str, url: str, **kw: Any) -> FakeResp:
        self.last = dict(method=method, url=url, **kw)
        self.calls.append(self.last)
        return self.responder(method, url, kw)


def enveloped(
    data: Any, *, message: str = "OK", status: int = 200, extra: dict | None = None
) -> FakeResp:
    """Wrap ``data`` in LINE's ``{"message":"OK","data":...}`` envelope."""
    body = {"message": message, "data": data}
    if extra:
        body.update(extra)
    return FakeResp(status, body)


def route(table: dict[str, Any], default: Any = None):
    """Build a responder from a ``{endpoint_suffix: data_or_FakeResp}`` table.

    Keys are matched as URL suffixes (e.g. ``"getProfile"``).  Values that are
    not already a :class:`FakeResp` are wrapped in an OK envelope.
    """

    def responder(method: str, url: str, kw: dict) -> FakeResp:
        for suffix, value in table.items():
            if url.endswith(suffix):
                return value if isinstance(value, FakeResp) else enveloped(value)
        if isinstance(default, FakeResp):
            return default
        return enveloped(default if default is not None else {})

    return responder


# ---------------------------------------------------------------------------
# LTSM bridge fake (no Node.js needed)
# ---------------------------------------------------------------------------
class FakeBridge:
    """Deterministic stand-in for :class:`okline.LtsmBridge`."""

    def __init__(self) -> None:
        self.signed: list[tuple] = []
        self._key = 0

    def sign(self, access_token: str, path: str, body: str = "") -> str:
        self.signed.append((access_token, path, body))
        raw = f"{access_token}|{path}|{body}".encode()
        return base64.b64encode(raw[:32].ljust(32, b"\0")).decode("ascii")

    def curvekey_generate(self) -> int:
        self._key += 1
        return self._key

    def e2ee_public_key(self, key_id: int) -> str:
        return base64.b64encode(bytes([key_id]) * 32).decode("ascii")

    def e2ee_create_channel(self, key_id: int, server_pubkey_b64: str) -> int:
        return 1000 + key_id

    def e2ee_unwrap_keychain(self, channel_id: int, enc_keychain_b64: str) -> list:
        return [1, 2]

    def e2ee_get_key_id(self, handle: int) -> int:
        return 5000000 + int(handle)

    def e2ee_export_key(self, handle: int) -> str:
        return base64.b64encode(f"exported:{handle}".encode()).decode("ascii")

    def e2ee_load_key(self, exported_b64: str) -> int:
        raw = base64.b64decode(exported_b64).decode()
        return int(raw.split(":", 1)[1])  # round-trips e2ee_export_key

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Builders / fixtures
# ---------------------------------------------------------------------------
def build_api(
    responder: Callable | None = None,
    *,
    access_token: str | None = "TKN",
    bridge: Any | None = None,
    enable_hmac: bool = False,
    record: bool = True,
    **client_kw: Any,
) -> OkLine:
    """Build an :class:`OkLine` wired to a fake session (and optional bridge)."""
    responder = responder or (lambda m, u, kw: enveloped({}))
    cfg = LineConfig(enable_hmac=enable_hmac)
    transport = Transport(
        cfg, Tokens(access_token=access_token), session=FakeSession(responder), signer=bridge
    )
    return OkLine(transport=transport, record=record, **client_kw)


@pytest.fixture
def make_api():
    """Factory fixture: ``make_api(responder, **kw) -> OkLine``."""
    created: list[OkLine] = []

    def _factory(responder=None, **kw) -> OkLine:
        api = build_api(responder, **kw)
        created.append(api)
        return api

    yield _factory
    for api in created:
        try:
            api.close()
        except Exception:
            pass


@pytest.fixture
def fake_bridge() -> FakeBridge:
    return FakeBridge()


@pytest.fixture
def api(make_api):
    """A ready client that returns an empty OK envelope for everything."""
    return make_api(lambda m, u, kw: enveloped({}))


@pytest.fixture
def last_request():
    """Helper to decode the JSON body of the last request a client sent."""

    def _decode(api: OkLine) -> Any:
        data = api.transport.session.last["data"]  # type: ignore[index]
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")
        return json.loads(data) if data else None

    return _decode

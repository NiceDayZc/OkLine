"""Tests for :mod:`okline.hmac_signer` — the real LTSM Node/WASM bridge.

These exercise the *actual* ``LtsmBridge`` (not the ``FakeBridge`` fake), so
they require Node.js plus the ``ltsm/`` artifacts.  The whole module is skipped
when those are unavailable, keeping the rest of the suite fully offline.

Covered surface:

* ``sign()`` reproduces a known ``X-Hmac`` vector exactly.
* ``curvekey_generate()`` returns an integer handle.
* ``e2ee_public_key()`` returns a base64 Curve25519 public key (32 raw bytes).
* ``HmacSigner`` is the backwards-compatible alias for ``LtsmBridge``.
"""

from __future__ import annotations

import base64

import pytest

from okline import HmacSigner

# A single real WASM bridge is expensive to launch and the curve-key handle
# lives inside that one process, so we skip the module entirely when Node or
# the bridge artifacts are missing rather than failing.
if not HmacSigner.is_available():
    pytest.skip("Node/bridge unavailable", allow_module_level=True)

from okline import LtsmBridge

# The canonical vector: empty access token, the createSession path and a "[{}]"
# body must always hash to this exact X-Hmac.  This pins the key-derivation +
# HMAC implementation inside ltsm.wasm.
KNOWN_PATH = "/api/talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/createSession"
KNOWN_BODY = "[{}]"
KNOWN_HMAC = "xc7hTRfwaauLuMpoXQRt2DDZE+nu+8e4auOw1F/UQZo="


@pytest.fixture
def bridge():
    """A live LTSM bridge, torn down (Node process killed) after each test."""
    b = LtsmBridge()
    try:
        yield b
    finally:
        b.close()


# ---------------------------------------------------------------------------
# X-Hmac signing
# ---------------------------------------------------------------------------
def test_sign_matches_known_vector(bridge):
    """The empty-token createSession vector reproduces the canonical X-Hmac."""
    sig = bridge.sign("", KNOWN_PATH, KNOWN_BODY)
    assert sig == KNOWN_HMAC


def test_sign_returns_base64_str(bridge):
    """A signature is a non-empty base64 string (32-byte HMAC-SHA256 -> 44 chars)."""
    sig = bridge.sign("", KNOWN_PATH, KNOWN_BODY)
    assert isinstance(sig, str) and sig
    raw = base64.b64decode(sig)  # must be valid base64
    assert len(raw) == 32


def test_sign_is_deterministic(bridge):
    """Signing the same inputs twice yields an identical signature."""
    a = bridge.sign("", KNOWN_PATH, KNOWN_BODY)
    b = bridge.sign("", KNOWN_PATH, KNOWN_BODY)
    assert a == b == KNOWN_HMAC


def test_sign_varies_with_body(bridge):
    """A different body produces a different signature (body is signed)."""
    base = bridge.sign("", KNOWN_PATH, KNOWN_BODY)
    other = bridge.sign("", KNOWN_PATH, "[{},{}]")
    assert other != base


def test_sign_varies_with_path(bridge):
    """A different path produces a different signature (path is signed)."""
    base = bridge.sign("", KNOWN_PATH, KNOWN_BODY)
    other = bridge.sign("", KNOWN_PATH + "X", KNOWN_BODY)
    assert other != base


def test_sign_varies_with_access_token(bridge):
    """The access token is mixed into the key, so it changes the signature."""
    base = bridge.sign("", KNOWN_PATH, KNOWN_BODY)
    other = bridge.sign("some-token", KNOWN_PATH, KNOWN_BODY)
    assert other != base


def test_sign_default_empty_body(bridge):
    """Omitting ``body`` defaults to an empty string (bodyless request)."""
    explicit = bridge.sign("", KNOWN_PATH, "")
    implied = bridge.sign("", KNOWN_PATH)
    assert explicit == implied


# ---------------------------------------------------------------------------
# Curve25519 / E2EE key handles
# ---------------------------------------------------------------------------
def test_curvekey_generate_returns_int(bridge):
    """Generating a curve keypair returns an integer WASM handle id."""
    key_id = bridge.curvekey_generate()
    assert isinstance(key_id, int)
    assert not isinstance(key_id, bool)


def test_e2ee_public_key_is_32_bytes(bridge):
    """The public key for a generated handle base64-decodes to 32 raw bytes."""
    key_id = bridge.curvekey_generate()
    pub_b64 = bridge.e2ee_public_key(key_id)
    assert isinstance(pub_b64, str) and pub_b64
    raw = base64.b64decode(pub_b64)
    assert len(raw) == 32


def test_distinct_curve_handles(bridge):
    """Two generated keypairs are distinct: different ids and public keys."""
    id1 = bridge.curvekey_generate()
    id2 = bridge.curvekey_generate()
    assert id1 != id2
    assert bridge.e2ee_public_key(id1) != bridge.e2ee_public_key(id2)


# ---------------------------------------------------------------------------
# Aliasing / lifecycle
# ---------------------------------------------------------------------------
def test_hmacsigner_is_ltsmbridge_alias():
    """``HmacSigner`` is the legacy name for ``LtsmBridge`` (same class)."""
    assert HmacSigner is LtsmBridge


def test_close_is_idempotent(bridge):
    """Closing an already-used bridge twice does not raise."""
    bridge.sign("", KNOWN_PATH, KNOWN_BODY)
    bridge.close()
    bridge.close()  # second close must be a no-op


def test_is_available_returns_bool():
    """``is_available`` reports a plain boolean for capability gating."""
    assert isinstance(LtsmBridge.is_available(), bool)

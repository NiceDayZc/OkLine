"""Offline unit tests for :mod:`okline.crypto`.

These tests exercise the RSA password-login helpers entirely in-process —
no network and no Node.js.  Where a real RSA operation is needed we generate
a throwaway key with the ``cryptography`` package, encrypt with the client's
helper, then decrypt with the private half to prove the byte layout and
PKCS#1 v1.5 padding round-trip correctly.
"""

from __future__ import annotations

import re

import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from okline.crypto import (
    RSAKeyInfo,
    build_login_plaintext,
    gen_uuid_hex,
    rsa_encrypt_credentials,
    sha256,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _gen_rsa_keyinfo(session_key: str = "S3SS10N", public_exponent: int = 65537):
    """Generate a fresh RSA key and a matching :class:`RSAKeyInfo`.

    Returns ``(private_key, key_info)``.  ``nvalue``/``evalue`` are lowercase
    hex strings, exactly as the LINE server returns them from getRSAKeyInfo.
    """
    private_key = rsa.generate_private_key(public_exponent=public_exponent, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    info = RSAKeyInfo(
        keynm="testkey:1",
        nvalue=format(numbers.n, "x"),
        evalue=format(numbers.e, "x"),
        sessionKey=session_key,
    )
    return private_key, info


# ---------------------------------------------------------------------------
# build_login_plaintext
# ---------------------------------------------------------------------------
class TestBuildLoginPlaintext:
    """The cleartext blob layout: chr(len)+value for each of 3 fields."""

    def test_returns_bytes(self):
        """Output is UTF-8 encoded bytes, not str."""
        out = build_login_plaintext("sk", "user@example.com", "pw")
        assert isinstance(out, bytes)

    def test_exact_byte_layout(self):
        """Blob = len(sk)+sk + len(id)+id + len(pw)+pw, each len a 1-char prefix."""
        session_key, identifier, password = "abc", "joe", "secret"
        out = build_login_plaintext(session_key, identifier, password)
        expected = (
            chr(len(session_key))
            + session_key
            + chr(len(identifier))
            + identifier
            + chr(len(password))
            + password
        ).encode("utf-8")
        assert out == expected

    def test_length_prefixes_are_field_lengths(self):
        """Each segment is introduced by a byte equal to its char-length."""
        session_key, identifier, password = "ab", "cde", "fghi"
        out = build_login_plaintext(session_key, identifier, password)
        # ASCII-only inputs => 1 byte per char, so we can index directly.
        assert out[0] == len(session_key)
        assert out[1 : 1 + len(session_key)] == session_key.encode()
        off = 1 + len(session_key)
        assert out[off] == len(identifier)
        assert out[off + 1 : off + 1 + len(identifier)] == identifier.encode()
        off += 1 + len(identifier)
        assert out[off] == len(password)
        assert out[off + 1 :] == password.encode()

    def test_empty_fields(self):
        """Empty strings produce a zero-length prefix (NUL byte) each."""
        out = build_login_plaintext("", "", "")
        assert out == b"\x00\x00\x00"

    def test_length_prefix_counts_code_units_not_utf8_bytes(self):
        """The prefix is String.fromCharCode(s.length): char count, not bytes.

        A multibyte char ("e" with acute accent) is length 1 in the prefix but
        encodes to 2 UTF-8 bytes in the body — mirroring the JS reference.
        """
        identifier = "é"  # 'é', len 1, 2 UTF-8 bytes
        out = build_login_plaintext("", identifier, "")
        # session prefix(0) | id prefix(1) | id bytes(2) | pw prefix(0)
        assert out == b"\x00" + b"\x01" + identifier.encode("utf-8") + b"\x00"


# ---------------------------------------------------------------------------
# rsa_encrypt_credentials
# ---------------------------------------------------------------------------
class TestRsaEncryptCredentials:
    """Round-trip the ciphertext against a freshly generated RSA key."""

    def test_roundtrip_decrypts_to_plaintext(self):
        """Encrypting then decrypting (PKCS1v15) recovers the login plaintext."""
        private_key, info = _gen_rsa_keyinfo(session_key="sesskey")
        identifier, password = "user@example.com", "hunter2"

        hexct = rsa_encrypt_credentials(info, identifier, password)
        recovered = private_key.decrypt(bytes.fromhex(hexct), padding.PKCS1v15())

        assert recovered == build_login_plaintext("sesskey", identifier, password)

    def test_output_is_lowercase_hex(self):
        """Ciphertext is returned as a lowercase hex string (no 0x, no upper)."""
        _, info = _gen_rsa_keyinfo()
        hexct = rsa_encrypt_credentials(info, "a@b.c", "pw")
        assert re.fullmatch(r"[0-9a-f]+", hexct)

    def test_ciphertext_length_matches_modulus(self):
        """A 2048-bit modulus yields a 256-byte (512 hex char) ciphertext."""
        _, info = _gen_rsa_keyinfo()
        hexct = rsa_encrypt_credentials(info, "a@b.c", "pw")
        assert len(hexct) == 512  # 256 bytes * 2 hex chars

    def test_pkcs1v15_is_randomized(self):
        """PKCS#1 v1.5 padding randomises output: same input, different cipher."""
        _, info = _gen_rsa_keyinfo()
        ct1 = rsa_encrypt_credentials(info, "a@b.c", "pw")
        ct2 = rsa_encrypt_credentials(info, "a@b.c", "pw")
        assert ct1 != ct2

    def test_roundtrip_with_unicode_password(self):
        """Multibyte credentials survive the UTF-8 + RSA round-trip."""
        private_key, info = _gen_rsa_keyinfo(session_key="k")
        identifier, password = "üser", "päss"

        hexct = rsa_encrypt_credentials(info, identifier, password)
        recovered = private_key.decrypt(bytes.fromhex(hexct), padding.PKCS1v15())

        assert recovered == build_login_plaintext("k", identifier, password)

    def test_uses_advertised_public_exponent(self):
        """Encryption honours evalue even for a non-default exponent (e=3)."""
        private_key, info = _gen_rsa_keyinfo(session_key="sk", public_exponent=3)
        assert int(info.evalue, 16) == 3

        hexct = rsa_encrypt_credentials(info, "x@y.z", "pw")
        recovered = private_key.decrypt(bytes.fromhex(hexct), padding.PKCS1v15())
        assert recovered == build_login_plaintext("sk", "x@y.z", "pw")


# ---------------------------------------------------------------------------
# RSAKeyInfo
# ---------------------------------------------------------------------------
class TestRSAKeyInfo:
    """Construction from a getRSAKeyInfo response dict."""

    def test_from_response_maps_fields(self):
        """All four fields are copied verbatim from the response dict."""
        data = {
            "keynm": "rsa:42",
            "nvalue": "abcdef",
            "evalue": "10001",
            "sessionKey": "sk-123",
        }
        info = RSAKeyInfo.from_response(data)
        assert info.keynm == "rsa:42"
        assert info.nvalue == "abcdef"
        assert info.evalue == "10001"
        assert info.sessionKey == "sk-123"

    def test_from_response_missing_key_raises(self):
        """A response missing a required field raises KeyError (no silent default)."""
        data = {"keynm": "k", "nvalue": "n", "evalue": "e"}  # no sessionKey
        with pytest.raises(KeyError):
            RSAKeyInfo.from_response(data)

    def test_direct_construction(self):
        """The dataclass can be built directly with positional/keyword args."""
        info = RSAKeyInfo(keynm="k", nvalue="n", evalue="e", sessionKey="s")
        assert (info.keynm, info.nvalue, info.evalue, info.sessionKey) == ("k", "n", "e", "s")

    def test_from_response_feeds_encrypt(self):
        """An RSAKeyInfo from a realistic response can drive encryption."""
        private_key, base = _gen_rsa_keyinfo(session_key="ssk")
        data = {
            "keynm": base.keynm,
            "nvalue": base.nvalue,
            "evalue": base.evalue,
            "sessionKey": base.sessionKey,
        }
        info = RSAKeyInfo.from_response(data)

        hexct = rsa_encrypt_credentials(info, "id@host", "pw")
        recovered = private_key.decrypt(bytes.fromhex(hexct), padding.PKCS1v15())
        assert recovered == build_login_plaintext("ssk", "id@host", "pw")


# ---------------------------------------------------------------------------
# gen_uuid_hex
# ---------------------------------------------------------------------------
class TestGenUuidHex:
    """The dash-less UUID4 hex id used as a UUID-without-dashes."""

    def test_format_is_32_lowercase_hex(self):
        """Exactly 32 lowercase hex chars, no dashes."""
        uid = gen_uuid_hex()
        assert re.fullmatch(r"[0-9a-f]{32}", uid)

    def test_values_are_unique(self):
        """Successive calls produce distinct ids (random source)."""
        ids = {gen_uuid_hex() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# sha256
# ---------------------------------------------------------------------------
class TestSha256:
    """Thin wrapper over hashlib.sha256 returning raw digest bytes."""

    def test_known_vector(self):
        """Matches the canonical SHA-256 digest of the empty string."""
        empty = bytes.fromhex(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
        assert sha256(b"") == empty

    def test_returns_32_raw_bytes(self):
        """Output is the 32-byte raw digest (not hex)."""
        digest = sha256(b"hello")
        assert isinstance(digest, bytes)
        assert len(digest) == 32

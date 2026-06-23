"""Cryptographic helpers used by the LINE login flows.

The only mandatory primitive for password login is RSA: the extension encrypts
``chr(len(sessionKey)) + sessionKey + chr(len(email)) + email +
chr(len(password)) + password`` with the server's RSA public key using
**PKCS#1 v1.5** padding and sends the result as a lowercase hex string.  This
mirrors ``static/js/main.js`` exactly::

    yT = e => String.fromCharCode(e.length)
    o  = [yT(a),a, yT(e),e, yT(t),t].join("")        // a=sessionKey e=email t=password
    mT = (o,{n,e}) => bytesToHex( setRsaPublicKey(BigInt(n,16),BigInt(e,16))
                                    .encrypt(utf8(o), "RSAES-PKCS1-V1_5") )
    request = { identifier: keynm, password: mT(o, {n: nvalue, e: evalue}) }
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

try:
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "RSA login requires the 'cryptography' package: pip install cryptography"
    ) from exc


@dataclass
class RSAKeyInfo:
    """Result of ``Talk.TalkService.getRSAKeyInfo``."""

    keynm: str  # key name -> goes into LoginRequest.identifier
    nvalue: str  # RSA modulus, hex
    evalue: str  # RSA public exponent, hex
    sessionKey: str  # per-login session key, prefixes the plaintext

    @classmethod
    def from_response(cls, data: dict) -> RSAKeyInfo:
        return cls(
            keynm=data["keynm"],
            nvalue=data["nvalue"],
            evalue=data["evalue"],
            sessionKey=data["sessionKey"],
        )


def _len_prefix(s: str) -> str:
    """``String.fromCharCode(s.length)`` — a single code-unit length prefix."""
    return chr(len(s))


def build_login_plaintext(session_key: str, identifier: str, password: str) -> bytes:
    """Assemble and UTF-8 encode the cleartext blob fed to RSA."""
    blob = (
        _len_prefix(session_key)
        + session_key
        + _len_prefix(identifier)
        + identifier
        + _len_prefix(password)
        + password
    )
    return blob.encode("utf-8")


def rsa_encrypt_credentials(key: RSAKeyInfo, identifier: str, password: str) -> str:
    """Return the hex ciphertext for ``LoginRequest.password``.

    ``identifier`` is the e-mail address (or phone) used to log in.
    """
    plaintext = build_login_plaintext(key.sessionKey, identifier, password)
    n = int(key.nvalue, 16)
    e = int(key.evalue, 16)
    public_key = rsa.RSAPublicNumbers(e, n).public_key()
    ciphertext = public_key.encrypt(plaintext, padding.PKCS1v15())
    return ciphertext.hex()


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def gen_uuid_hex() -> str:
    """A random 32-char hex id, matching the extension's UUID-without-dashes."""
    import uuid

    return uuid.uuid4().hex

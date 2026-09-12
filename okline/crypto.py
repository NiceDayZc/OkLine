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

import base64
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


# -- E2EE email-login `secret` (LoginType.ID_CREDENTIAL_WITH_E2EE) ---------
#
# Extracted from the extension's E2EE login flow (main.js @~2127845, the ``C``
# callback in the e-mail login hook)::
#
#     r.current = curveKeyGenerate()
#     a         = e2eeKeyGetPublicKey(r.current)      # raw curve25519 bytes
#     o         = vT()                               # random 6-digit string
#     s         = SHA-256(o)                          # 32-byte AES key
#     secret    = RR(blockEncrypt(a, s))              # base64
#
#     blockEncrypt(e, t) = for each 16-byte block of the public key:
#         AES-CBC(block, key=t, iv=Uint8Array(16) zeros) -> first 16 bytes
#
# The same zero IV is reused for every block (this is *not* chained CBC — each
# block is encrypted independently, and only the block-sized prefix of the
# PKCS#7-padded WebCrypto ciphertext is kept).  The 6-digit code ``o`` doubles
# as the PIN shown to the user during device confirmation.


def generate_e2ee_login_code() -> str:
    """A random 6-digit code string (the extension's ``vT()``).

    ``vT`` draws a uniform uint32, rejects the top of the range to avoid
    modulo bias, and returns ``String(n % 10**6).padStart(6, "0")`` — i.e. a
    zero-padded 6-digit decimal string.  :func:`secrets.randbelow` gives the
    same distribution without the rejection step.
    """
    import secrets

    return f"{secrets.randbelow(10**6):06d}"


def _aes_cbc_block(block: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-CBC-encrypt one block (pad-or-truncate to 16) and keep 16 bytes."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(block) < 16:  # short tail block: PKCS#7-pad it, like WebCrypto
        pad = 16 - len(block)
        block = block + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    out = enc.update(block[:16]) + enc.finalize()
    return out[:16]


def encrypt_e2ee_login_secret(public_key: bytes, code: str) -> str:
    """Build the ``secret`` field of an E2EE e-mail ``loginV2`` request.

    ``public_key`` are the *raw* Curve25519 public-key bytes (the bridge's
    base64 value decoded); ``code`` is the 6-digit code the user will be shown.
    Returns base64 of the per-16-byte-block AES-CBC encryption of the public
    key under ``SHA-256(code)`` with a zero IV.
    """
    key = hashlib.sha256(code.encode("utf-8")).digest()
    iv = bytes(16)
    out = bytearray()
    for offset in range(0, len(public_key), 16):
        out += _aes_cbc_block(public_key[offset : offset + 16], key, iv)
    return base64.b64encode(bytes(out)).decode("ascii")


def decrypt_e2ee_login_secret(secret_b64: str, code: str) -> bytes:
    """Invert :func:`encrypt_e2ee_login_secret` (each block uses the zero IV).

    Mostly useful for tests / verifying what was sent.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = hashlib.sha256(code.encode("utf-8")).digest()
    iv = bytes(16)
    data = base64.b64decode(secret_b64)
    out = bytearray()
    for offset in range(0, len(data) - 15, 16):
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        out += dec.update(data[offset : offset + 16]) + dec.finalize()
    return bytes(out)


def gen_uuid_hex() -> str:
    """A random 32-char hex id, matching the extension's UUID-without-dashes."""
    import uuid

    return uuid.uuid4().hex

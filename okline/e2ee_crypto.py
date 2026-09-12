"""E2EE (Letter Sealing) message framing — the pure-Python half.

The actual encryption/decryption and key handling happen inside LINE's WASM
module (driven via the Node bridge).  This module only does the *framing* around
it, extracted verbatim from the extension bundle:

* **plaintext**  = ``JSON.stringify({text, location, REPLACE})`` UTF-8  (``gL``/``wL``),
                      plus ``{keyMaterial, fileName}`` when the message carries
                      ``contentMetadata.ENC_KM`` (sealed-media, V2 flow)
* **chunks (V2)** = ``[b64(ct[0:16]), b64(ct[28:]), b64(ct[16:28]),
                       b64(keyId4BE_sender), b64(keyId4BE_receiver)]``  (``vL``)
* **decrypt**    = reconstruct ``ct = c[0] + c[2] + c[1]``  (``hL``)
* **message**    = ``{...msg, contentMetadata:{e2eeVersion:"2"}, chunks}`` with
                      ``text``/``location`` removed and — a deliberate
                      live-verified deviation from ``EL`` — ``from`` removed too
                      (the gateway 500s on ``from``/``text:null``)

Sealed **media** additionally uses the blob encryption in this module
(:func:`derive_file_keys` / :func:`encrypt_blob` / :func:`decrypt_blob`),
extracted from the bundle's ``_L``/``CL``/``TL``.

All of this is round-trip unit-tested; it is independent of the WASM crypto.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from typing import Any


def _b64e(b: bytes) -> str:
    return base64.b64encode(bytes(b)).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s)


def key_id_to_bytes(n: int, length: int = 4) -> bytes:
    """``LR`` — big-endian fixed-length encoding of a key id."""
    return (int(n) & ((1 << (8 * length)) - 1)).to_bytes(length, "big")


def key_id_from_bytes(b: bytes) -> int:
    """``IR`` — big-endian bytes -> int."""
    return int.from_bytes(bytes(b), "big")


def serialize_plaintext(message: dict[str, Any]) -> bytes:
    """``wL(gL(message))`` — the bytes that get encrypted.

    JSON of ``{text, location, REPLACE}`` (omitting absent fields, like the JS
    ``JSON.stringify`` drops ``undefined``).  When the message carries
    ``contentMetadata.ENC_KM`` (sealed media, V2 flow) the key material is
    sealed **into** the ciphertext as ``{keyMaterial, fileName}`` (``gL``'s
    ``Object.assign(r, {keyMaterial, fileName})`` branch).
    """
    obj: dict[str, Any] = {}
    if message.get("text") is not None:
        obj["text"] = message["text"]
    if message.get("location") is not None:
        obj["location"] = message["location"]
    meta = message.get("contentMetadata") or {}
    replace = meta.get("REPLACE")
    if replace:
        # MR: JSON.parse on strings, non-strings pass through; a parse failure
        # returns undefined (the key is dropped) — never keep the raw string.
        try:
            obj["REPLACE"] = json.loads(replace) if isinstance(replace, str) else replace
        except (ValueError, TypeError):
            pass
    if meta.get("ENC_KM"):
        obj["keyMaterial"] = meta["ENC_KM"]
        if meta.get("FILE_NAME") is not None:
            obj["fileName"] = meta["FILE_NAME"]
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _escape_controls(text: str) -> str:
    """``xL`` — escape C0 (``\\u0000``-``\\u001f``) and C1-ish (``\\u0080``-``\\u00ff``)
    characters to ``\\u00XX`` JSON escapes, *preserving* the characters (they
    survive ``JSON.parse``), instead of deleting them."""

    def sub(m: re.Match[str]) -> str:
        return "\\u00" + format(ord(m.group(0)), "02x")

    return re.sub("[\x00-\x1f\x80-\xff]", sub, text)


def deserialize_plaintext(data: bytes) -> dict[str, Any]:
    """``bL`` — decrypted bytes back into ``{text, location, ...}``."""
    text = bytes(data).decode("utf-8", "replace")
    try:
        return json.loads(_escape_controls(text))
    except ValueError:
        # deliberate leniency (the extension throws here): surface the raw
        # text rather than failing the whole decrypt
        return {"text": text}


def build_chunks(ciphertext: bytes, sender_key_id: int, receiver_key_id: int) -> list[str]:
    """``vL`` — split the V2 ciphertext into the 5 base64 chunks."""
    e = bytes(ciphertext)
    return [
        _b64e(e[0:16]),  # salt / header
        _b64e(e[28:]),  # body
        _b64e(e[16:28]),  # tag
        _b64e(key_id_to_bytes(sender_key_id)),
        _b64e(key_id_to_bytes(receiver_key_id)),
    ]


def parse_chunks(chunks: list[str]) -> tuple[bytes, int | None, int | None]:
    """``hL`` (+ key ids) — rebuild ``(ciphertext, sender_key_id, receiver_key_id)``
    for **V2** messages (salt/tag swapped: ``c[0] + c[2] + c[1]``).

    Like ``dL``/``pL``, the key ids are only read when **all five** chunks are
    present; a short (3-4 chunk) message yields ``None`` key ids instead of a
    silent 0 (missing keys then fail loudly as "unknown key" downstream).
    """
    ct = _b64d(chunks[0]) + _b64d(chunks[2]) + _b64d(chunks[1])
    sid: int | None = None
    rid: int | None = None
    if len(chunks) >= 5:
        sid = key_id_from_bytes(_b64d(chunks[3]))
        rid = key_id_from_bytes(_b64d(chunks[4]))
    return ct, sid, rid


def build_chunks_v1(ciphertext: bytes, sender_key_id: int, receiver_key_id: int) -> list[str]:
    """``mL`` — split the **V1** ciphertext: ``[salt(8), body, tag(16), sid, rid]``."""
    e = bytes(ciphertext)
    return [
        _b64e(e[0:8]),  # salt
        _b64e(e[8:-16]),  # body
        _b64e(e[-16:]),  # tag
        _b64e(key_id_to_bytes(sender_key_id)),
        _b64e(key_id_to_bytes(receiver_key_id)),
    ]


def parse_chunks_v1(chunks: list[str]) -> tuple[bytes, int | None, int | None]:
    """``fL`` (+ key ids) — rebuild ``(ciphertext, sender_key_id, receiver_key_id)``
    for **V1** messages.  Unlike V2, the chunks are concatenated *in order*
    (``c[0] + c[1] + c[2]`` = salt + body + tag)."""
    ct = _b64d(chunks[0]) + _b64d(chunks[1]) + _b64d(chunks[2])
    sid: int | None = None
    rid: int | None = None
    if len(chunks) >= 5:
        sid = key_id_from_bytes(_b64d(chunks[3]))
        rid = key_id_from_bytes(_b64d(chunks[4]))
    return ct, sid, rid


def message_e2ee_version(message: dict[str, Any]) -> int:
    """The Letter-Sealing version of a received message (1 or 2; default 2)."""
    meta = message.get("contentMetadata") or {}
    try:
        return int(meta.get("e2eeVersion") or 2)
    except (ValueError, TypeError):
        return 2


def build_e2ee_message(
    message: dict[str, Any], chunks: list[str], version: int = 2
) -> dict[str, Any]:
    """``EL`` — turn a plain message + chunks into the sealed Message struct.

    Sets ``contentMetadata.e2eeVersion`` and drops ``text``/``location`` (the
    real ``EL`` sets them to ``undefined`` so ``JSON.stringify`` drops the
    keys).  When the plaintext carried sealed-media key material
    (``contentMetadata.ENC_KM``), ``ENC_KM``/``FILE_NAME`` are deleted from
    ``contentMetadata`` too — they now live inside the ciphertext (``EL``'s
    ``i=Boolean(r?.ENC_KM)`` branch).

    **Deliberate deviation (live-verified):** the real ``EL`` does *not* touch
    ``from`` — but sending ``from`` through this gateway 500s
    (``UNKNOWN_ERROR`` 99999; the server populates ``from`` from the auth
    token), so we delete it here on purpose.  Do not "fix" this to match the
    bundle.
    """
    meta = dict(message.get("contentMetadata") or {})
    has_enc_km = bool(meta.get("ENC_KM"))
    meta["e2eeVersion"] = str(version)
    meta.pop("REPLACE", None)  # REPLACE is now inside the ciphertext
    if has_enc_km:
        meta.pop("ENC_KM", None)  # key material is now inside the ciphertext
        meta.pop("FILE_NAME", None)
    out = dict(message)
    out["contentMetadata"] = meta
    out["chunks"] = chunks
    out.pop("text", None)
    out.pop("location", None)
    out.pop("from", None)  # deliberate deviation — see docstring
    return out


def is_e2ee_message(message: dict[str, Any]) -> bool:
    """True if a received message is Letter-Sealed (has E2EE chunks)."""
    chunks = message.get("chunks")
    if not isinstance(chunks, list) or len(chunks) < 3:
        return False
    meta = message.get("contentMetadata") or {}
    return bool(meta.get("e2eeVersion")) or len(chunks) >= 3


# -- sealed media (V2 flow) --------------------------------------------------
# Extracted from the bundle's _L (HKDF key derivation), CL (AES-CTR encrypt) and
# TL (HMAC tag).  In the V2 media flow the file bytes themselves are
# end-to-end encrypted under key material generated client-side (ENC_KM, NL)
# and sealed *into* the message ciphertext as {keyMaterial, fileName} (gL).

ENC_KM_KEY_BYTES = 32  # NL: RR(crypto.getRandomValues(new Uint8Array(32)))
_FILE_ENCRYPTION_INFO = b"FileEncryption"
_BLOB_TAG_BYTES = 32  # HMAC-SHA256


def generate_enc_km() -> str:
    """``NL`` — generate ``contentMetadata.ENC_KM``: base64 of 32 random bytes.

    Pass the result (via the media builders' ``enc_km=`` parameter) on every
    sealed media send; it is the key material the blob is encrypted with and
    the value the peer reads back out of the decrypted plaintext.
    """
    return base64.b64encode(secrets.token_bytes(ENC_KM_KEY_BYTES)).decode("ascii")


def derive_file_keys(key_material: bytes) -> tuple[bytes, bytes, bytes]:
    """``_L`` — HKDF-SHA256 over the ENC_KM key material -> ``(enc_key, mac_key,
    nonce)``.

    ``salt`` is empty, ``info`` is ``b"FileEncryption"`` and the output is 608
    bits = 76 bytes: AES-CTR key (32) + HMAC-SHA256 key (32) + nonce (12).
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    # HKDF salt=None == the all-zero HashLen salt, which is what WebCrypto's
    # empty-ArrayBuffer salt means (RFC 5869 "not provided" case).
    okm = HKDF(
        algorithm=hashes.SHA256(),
        length=76,  # 608 bits
        salt=None,
        info=_FILE_ENCRYPTION_INFO,
    ).derive(key_material)
    return okm[:32], okm[32:64], okm[64:76]


def encrypt_blob(key_material: bytes, data: bytes) -> bytes:
    """``CL``/``TL`` — encrypt a sealed-media file blob -> ``ciphertext || tag``.

    AES-CTR under the HKDF-derived ``enc_key`` with the counter block seeded
    ``nonce || \\x00\\x00\\x00\\x00`` (initial counter value length 32 bits),
    followed by an HMAC-SHA256 tag (``mac_key``) over the ciphertext.  The
    extension's chunked-upload variant (``SL``: per-128-KiB chunk hashes, tag
    over the hash list) is not needed for the simple single-blob path.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    enc_key, mac_key, nonce = derive_file_keys(key_material)
    # WebCrypto {counter: [...nonce,0,0,0,0], length: 32}: only the low 32 bits
    # of the block increment, which equals a whole-block counter for any blob
    # under 2**32 blocks (64 GiB).
    encryptor = Cipher(algorithms.AES(enc_key), modes.CTR(nonce + b"\x00\x00\x00\x00"))
    ct = encryptor.encryptor()
    ciphertext = ct.update(data) + ct.finalize()
    tag = hmac.new(mac_key, ciphertext, hashlib.sha256).digest()
    return ciphertext + tag


def decrypt_blob(key_material: bytes, blob: bytes) -> bytes:
    """Invert :func:`encrypt_blob` — verifies the trailing HMAC tag first."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(blob) < _BLOB_TAG_BYTES:
        raise ValueError("blob too short to carry an HMAC tag")
    ciphertext, tag = blob[:-_BLOB_TAG_BYTES], blob[-_BLOB_TAG_BYTES:]
    enc_key, mac_key, nonce = derive_file_keys(key_material)
    expected = hmac.new(mac_key, ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise ValueError("sealed-media blob HMAC verification failed")
    decryptor = Cipher(algorithms.AES(enc_key), modes.CTR(nonce + b"\x00\x00\x00\x00"))
    dc = decryptor.decryptor()
    return dc.update(ciphertext) + dc.finalize()

"""E2EE (Letter Sealing) message framing — the pure-Python half.

The actual encryption/decryption and key handling happen inside LINE's WASM
module (driven via the Node bridge).  This module only does the *framing* around
it, extracted verbatim from the extension bundle:

* **plaintext**  = ``JSON.stringify({text, location, REPLACE})`` UTF-8  (``gL``/``wL``)
* **chunks (V2)** = ``[b64(ct[0:16]), b64(ct[28:]), b64(ct[16:28]),
                       b64(keyId4BE_sender), b64(keyId4BE_receiver)]``  (``vL``)
* **decrypt**    = reconstruct ``ct = c[0] + c[2] + c[1]``  (``hL``)
* **message**    = ``{...msg, contentMetadata:{e2eeVersion:"2"}, chunks}`` with
                      ``text``/``location``/``from`` removed  (``EL``)

All of this is round-trip unit-tested; it is independent of the crypto.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple


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


def serialize_plaintext(message: Dict[str, Any]) -> bytes:
    """``wL(gL(message))`` — the bytes that get encrypted.

    JSON of ``{text, location, REPLACE}`` (omitting absent fields, like the JS
    ``JSON.stringify`` drops ``undefined``).
    """
    obj: Dict[str, Any] = {}
    if message.get("text") is not None:
        obj["text"] = message["text"]
    if message.get("location") is not None:
        obj["location"] = message["location"]
    meta = message.get("contentMetadata") or {}
    replace = meta.get("REPLACE")
    if replace:
        try:
            obj["REPLACE"] = json.loads(replace) if isinstance(replace, str) else replace
        except (ValueError, TypeError):
            obj["REPLACE"] = replace
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def deserialize_plaintext(data: bytes) -> Dict[str, Any]:
    """``bL`` — decrypted bytes back into ``{text, location, ...}``."""
    text = bytes(data).decode("utf-8", "replace")
    # xL sanitiser: strip C0 control chars that break JSON.parse
    text = "".join(ch for ch in text if ch >= " " or ch in "\t\n\r")
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def build_chunks(ciphertext: bytes, sender_key_id: int,
                 receiver_key_id: int) -> List[str]:
    """``vL`` — split the V2 ciphertext into the 5 base64 chunks."""
    e = bytes(ciphertext)
    return [
        _b64e(e[0:16]),                       # salt / header
        _b64e(e[28:]),                        # body
        _b64e(e[16:28]),                      # tag
        _b64e(key_id_to_bytes(sender_key_id)),
        _b64e(key_id_to_bytes(receiver_key_id)),
    ]


def parse_chunks(chunks: List[str]) -> Tuple[bytes, Optional[int], Optional[int]]:
    """``hL`` (+ key ids) — rebuild ``(ciphertext, sender_key_id, receiver_key_id)``."""
    ct = _b64d(chunks[0]) + _b64d(chunks[2]) + _b64d(chunks[1])
    sid = key_id_from_bytes(_b64d(chunks[3])) if len(chunks) > 3 else None
    rid = key_id_from_bytes(_b64d(chunks[4])) if len(chunks) > 4 else None
    return ct, sid, rid


def build_e2ee_message(message: Dict[str, Any], chunks: List[str],
                       version: int = 2) -> Dict[str, Any]:
    """``EL`` — turn a plain message + chunks into the sealed Message struct.

    The real ``EL`` sets ``text``/``location`` to ``undefined`` (so ``JSON.stringify``
    *drops* the keys) and never carries a ``from`` field — the server populates it.
    Sending ``"text": null`` / ``"from": …`` instead makes the gateway reject the
    message (``UNKNOWN_ERROR`` 99999), so we delete those keys outright.
    """
    meta = dict(message.get("contentMetadata") or {})
    meta["e2eeVersion"] = str(version)
    meta.pop("REPLACE", None)            # REPLACE is now inside the ciphertext
    out = dict(message)
    out["contentMetadata"] = meta
    out["chunks"] = chunks
    out.pop("text", None)
    out.pop("location", None)
    out.pop("from", None)
    return out


def is_e2ee_message(message: Dict[str, Any]) -> bool:
    """True if a received message is Letter-Sealed (has E2EE chunks)."""
    chunks = message.get("chunks")
    if not isinstance(chunks, list) or len(chunks) < 3:
        return False
    meta = message.get("contentMetadata") or {}
    return bool(meta.get("e2eeVersion")) or len(chunks) >= 3

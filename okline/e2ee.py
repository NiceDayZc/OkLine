"""E2EE (Letter Sealing) for 1:1 messages — EXPERIMENTAL.

Ties together the WASM bridge (key handles + encrypt/decrypt) and the framing in
:mod:`okline.e2ee_crypto`.

Scope / how it works
--------------------
The E2EE private keys live in the keychain that is unwrapped **during QR login**
(``qrCodeLoginV2`` -> ``metaData.encryptedKeyChain``).  So:

* call ``api.auth.qr_login(...)`` then use E2EE **in the same process** — the
  manager captures the unwrapped key handles automatically (see
  :meth:`E2EEManager.load_from_login`);
* cross-session reuse (from a saved token) needs LINE's secure-storage layer and
  is not implemented yet.

Only **user (1:1)** messages are handled here; group Letter Sealing uses shared
group keys (not yet wired).  This is new and needs live verification — treat it
as experimental.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, Optional, Tuple

from . import e2ee_crypto as fr
from .enums import ContentType

log = logging.getLogger("okline.e2ee")


class E2EEManager:
    def __init__(self, api: Any) -> None:
        self.api = api
        self.my_mid: Optional[str] = getattr(api.tokens, "mid", None)
        # our unwrapped E2EE keys: keyId -> wasm handle
        self.my_keys: Dict[int, int] = {}
        self.latest_key_id: Optional[int] = None
        # peer mid -> (channel, my_key_id, peer_key_id)
        self._peer_channels: Dict[str, Tuple[int, int, int]] = {}
        self._seq = 0

    @property
    def _bridge(self):
        return self.api.transport.bridge

    def is_ready(self) -> bool:
        return bool(self.my_keys and self.latest_key_id is not None)

    # -- key loading ---------------------------------------------------------
    def load_from_login(self, curve_key_id: int, metadata: Dict[str, Any]) -> bool:
        """Capture our E2EE keys from a ``qrCodeLoginV2`` ``metaData`` block.

        ``metadata`` = ``{keyId, publicKey, encryptedKeyChain}``; ``curve_key_id``
        is the handle returned by ``bridge.curvekey_generate()`` during the QR
        flow.  Returns True on success.
        """
        try:
            channel = self._bridge.e2ee_create_channel(curve_key_id, metadata["publicKey"])
            handles = self._bridge.e2ee_unwrap_keychain(channel, metadata["encryptedKeyChain"])
        except Exception as exc:  # noqa: BLE001
            log.warning("E2EE keychain unwrap failed: %s", exc)
            return False
        if not isinstance(handles, list):
            return False
        self.my_keys.clear()
        for h in handles:
            try:
                kid = int(self._bridge.e2ee_get_key_id(h))
                self.my_keys[kid] = int(h)
            except Exception:  # noqa: BLE001
                continue
        if self.my_keys:
            self.latest_key_id = max(self.my_keys)
        if not self.my_mid:
            self.my_mid = getattr(self.api.tokens, "mid", None)
        log.info("E2EE ready: %d key(s), latest=%s", len(self.my_keys), self.latest_key_id)
        return self.is_ready()

    # -- channels ------------------------------------------------------------
    def _negotiate_peer(self, peer_mid: str) -> Tuple[str, int]:
        """Return the peer's (public_key_b64, key_id) via negotiate/getE2EEPublicKey."""
        neg = self.api.negotiate_e2ee_public_key(peer_mid)
        pk = neg.get("publicKey") if isinstance(neg, dict) else None
        if isinstance(pk, dict) and pk.get("keyData") and pk.get("keyId") is not None:
            return pk["keyData"], int(pk["keyId"])
        # fall back to getE2EEPublicKey
        gk = self.api.get_e2ee_public_key(peer_mid, 1, 0)
        if isinstance(gk, dict) and gk.get("keyData") is not None:
            return gk["keyData"], int(gk.get("keyId", 0))
        raise RuntimeError(f"could not negotiate E2EE key for {peer_mid}")

    def _channel_for_send(self, peer_mid: str) -> Tuple[int, int, int]:
        if peer_mid in self._peer_channels:
            return self._peer_channels[peer_mid]
        if not self.is_ready():
            raise RuntimeError("E2EE not initialised — log in with qr_login first")
        my_kid = self.latest_key_id
        my_handle = self.my_keys[my_kid]
        peer_pub, peer_kid = self._negotiate_peer(peer_mid)
        channel = self._bridge.e2ee_create_channel_with_pubkey(my_handle, peer_pub)
        self._peer_channels[peer_mid] = (channel, my_kid, peer_kid)
        return self._peer_channels[peer_mid]

    def _channel_for_receive(self, sender_mid: str, sender_key_id: int,
                             receiver_key_id: int) -> int:
        my_handle = self.my_keys.get(receiver_key_id)
        if my_handle is None:
            # fall back to whatever key we have
            my_handle = self.my_keys.get(self.latest_key_id)
        if my_handle is None:
            raise RuntimeError("no local E2EE key to decrypt with")
        sender_pub = self.api.get_e2ee_public_key(sender_mid, 1, sender_key_id)
        pub_b64 = sender_pub.get("keyData") if isinstance(sender_pub, dict) else None
        if not pub_b64:
            raise RuntimeError(f"no public key for sender {sender_mid}")
        return self._bridge.e2ee_create_channel_with_pubkey(my_handle, pub_b64)

    # -- encrypt / decrypt ---------------------------------------------------
    def encrypt(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt a 1:1 message dict -> sealed Message (with ``chunks``)."""
        to = message["to"]
        frm = message.get("from") or self.my_mid
        channel, my_kid, peer_kid = self._channel_for_send(to)
        plaintext = fr.serialize_plaintext(message)
        ct_b64 = self._bridge.e2ee_encrypt_v2(
            channel, to=to, frm=frm, sender_key_id=my_kid, receiver_key_id=peer_kid,
            content_type=int(message.get("contentType", 0)),
            sequence_number=self._next_seq(),
            plaintext_b64=base64.b64encode(plaintext).decode("ascii"))
        ciphertext = base64.b64decode(ct_b64)
        chunks = fr.build_chunks(ciphertext, my_kid, peer_kid)
        # EL() drops text/location/from — the gateway 500s if `from`/`text:null`
        # are present (the server populates `from` from the auth token).
        return fr.build_e2ee_message(message, chunks, 2)

    def decrypt(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Decrypt a received sealed 1:1 message -> plain message dict."""
        chunks = message.get("chunks") or []
        ciphertext, sender_key_id, receiver_key_id = fr.parse_chunks(chunks)
        sender = message.get("from")
        to = message.get("to")
        channel = self._channel_for_receive(sender, sender_key_id or 0,
                                            receiver_key_id or 0)
        pt_b64 = self._bridge.e2ee_decrypt_v2(
            channel, to=to, frm=sender, sender_key_id=sender_key_id or 0,
            receiver_key_id=receiver_key_id or 0,
            content_type=int(message.get("contentType", 0)),
            ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"))
        plain = fr.deserialize_plaintext(base64.b64decode(pt_b64))
        out = dict(message)
        out["text"] = plain.get("text")
        if plain.get("location") is not None:
            out["location"] = plain["location"]
        out["_decrypted"] = True
        return out

    def _next_seq(self) -> int:
        s = self._seq
        self._seq += 1
        return s

    # -- self-test -----------------------------------------------------------
    def roundtrip(self, to: str, text: str) -> str:
        """Encrypt a message to ``to`` then decrypt it back with the *same* send
        channel (the symmetric ECDH secret), proving encrypt+framing+decrypt are
        mutually consistent without needing a second party.  Returns the recovered
        text.  Raises on crypto/framing mismatch."""
        msg = {"to": to, "toType": 0, "contentType": 0, "text": text,
               "contentMetadata": {}}
        sealed = self.encrypt(msg)
        channel, my_kid, peer_kid = self._channel_for_send(to)
        ct, sid, rid = fr.parse_chunks(sealed["chunks"])
        pt_b64 = self._bridge.e2ee_decrypt_v2(
            channel, to=to, frm=self.my_mid, sender_key_id=sid or my_kid,
            receiver_key_id=rid or peer_kid, content_type=0,
            ciphertext_b64=base64.b64encode(ct).decode("ascii"))
        plain = fr.deserialize_plaintext(base64.b64decode(pt_b64))
        return plain.get("text", "")

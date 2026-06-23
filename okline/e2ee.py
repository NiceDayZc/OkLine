"""E2EE (Letter Sealing) — 1:1 **and** group.

Ties together the WASM bridge (key handles + encrypt/decrypt) and the framing in
:mod:`okline.e2ee_crypto`.  Live-verified: encrypted **send** (V2) and **decrypt**
(V1 and V2) for **1:1**; **group** decrypt + send-when-a-group-key-exists reuse the
same crypto with a group shared key.  :meth:`E2EEManager.encrypt` /
:meth:`~E2EEManager.decrypt` route on the target (group vs user) automatically.

Scope / how it works
--------------------
The E2EE private keys live in the keychain that is unwrapped **during QR login**
(``qrCodeLoginV2`` -> ``metaData.encryptedKeyChain``).  So:

* call ``api.auth.qr_login(...)`` then use E2EE in the same process (the manager
  captures the unwrapped handles, see :meth:`E2EEManager.load_from_login`); **or**
* **persist + reload across sessions** — :meth:`E2EEManager.export_keys` serializes
  the keychain (saved by ``save_tokens``) and :meth:`~E2EEManager.load_from_export`
  restores it (by ``from_tokens_file``), so E2EE works without a fresh QR scan.

Group shared keys are fetched (``getLastE2EEGroupSharedKey``) and unwrapped via the
WASM at runtime, then cached.  Creating a brand-new group key for the very first
encrypted message in a group (``registerE2EEGroupKey``) is not implemented.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from . import e2ee_crypto as fr

log = logging.getLogger("okline.e2ee")


class E2EEManager:
    def __init__(self, api: Any) -> None:
        self.api = api
        self.my_mid: str | None = getattr(api.tokens, "mid", None)
        # our unwrapped E2EE keys: keyId -> wasm handle
        self.my_keys: dict[int, int] = {}
        self.latest_key_id: int | None = None
        # peer mid -> (channel, my_key_id, peer_key_id)
        self._peer_channels: dict[str, tuple[int, int, int]] = {}
        # (group mid, group key id) -> unwrapped group-shared-key handle
        self._group_keys: dict[tuple[str, int], int] = {}
        self._seq = 0

    @property
    def _bridge(self):
        return self.api.transport.bridge

    def is_ready(self) -> bool:
        return bool(self.my_keys and self.latest_key_id is not None)

    # -- key loading ---------------------------------------------------------
    def load_from_login(self, curve_key_id: int, metadata: dict[str, Any]) -> bool:
        """Capture our E2EE keys from a ``qrCodeLoginV2`` ``metaData`` block.

        ``metadata`` = ``{keyId, publicKey, encryptedKeyChain}``; ``curve_key_id``
        is the handle returned by ``bridge.curvekey_generate()`` during the QR
        flow.  Returns True on success.
        """
        try:
            channel = self._bridge.e2ee_create_channel(curve_key_id, metadata["publicKey"])
            handles = self._bridge.e2ee_unwrap_keychain(channel, metadata["encryptedKeyChain"])
        except Exception as exc:
            log.warning("E2EE keychain unwrap failed: %s", exc)
            return False
        if not isinstance(handles, list):
            return False
        self.my_keys.clear()
        for h in handles:
            try:
                kid = int(self._bridge.e2ee_get_key_id(h))
                self.my_keys[kid] = int(h)
            except Exception:
                continue
        if self.my_keys:
            self.latest_key_id = max(self.my_keys)
        if not self.my_mid:
            self.my_mid = getattr(self.api.tokens, "mid", None)
        log.info("E2EE ready: %d key(s), latest=%s", len(self.my_keys), self.latest_key_id)
        return self.is_ready()

    # -- cross-session persistence ------------------------------------------
    def export_keys(self) -> dict[str, Any]:
        """Serialize the unwrapped keychain so it survives the process.

        Mirrors the extension's ``exportedKeyMap`` (``E2EEKey.exportKey()`` per
        keyId).  Persist the result (e.g. in the session file) and feed it to
        :meth:`load_from_export` next run to use E2EE **without a fresh QR login**.

        ⚠️ The blobs are private-key material — store them as carefully as the
        access token.
        """
        if not self.is_ready():
            return {}
        keys: dict[str, str] = {}
        for kid, handle in self.my_keys.items():
            try:
                keys[str(kid)] = self._bridge.e2ee_export_key(handle)
            except Exception as exc:
                log.warning("E2EE export of key %s failed: %s", kid, exc)
        return {"mid": self.my_mid, "latestKeyId": self.latest_key_id, "keys": keys}

    def load_from_export(self, data: dict[str, Any]) -> bool:
        """Rebuild the keychain from :meth:`export_keys` output (no QR needed)."""
        keys = (data or {}).get("keys") or {}
        if not keys:
            return False
        self.my_keys.clear()
        for kid_s, blob in keys.items():
            try:
                self.my_keys[int(kid_s)] = int(self._bridge.e2ee_load_key(blob))
            except Exception as exc:
                log.warning("E2EE load of key %s failed: %s", kid_s, exc)
        if not self.my_keys:
            return False
        latest = data.get("latestKeyId")
        self.latest_key_id = (
            int(latest)
            if latest is not None and int(latest) in self.my_keys
            else max(self.my_keys)
        )
        if not self.my_mid:
            self.my_mid = data.get("mid") or getattr(self.api.tokens, "mid", None)
        log.info("E2EE restored: %d key(s), latest=%s", len(self.my_keys), self.latest_key_id)
        return self.is_ready()

    # -- channels ------------------------------------------------------------
    def _negotiate_peer(self, peer_mid: str) -> tuple[str, int]:
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

    def _channel_for_send(self, peer_mid: str) -> tuple[int, int, int]:
        if peer_mid in self._peer_channels:
            return self._peer_channels[peer_mid]
        if not self.is_ready() or self.latest_key_id is None:
            raise RuntimeError("E2EE not initialised — log in with qr_login first")
        my_kid = self.latest_key_id
        my_handle = self.my_keys[my_kid]
        peer_pub, peer_kid = self._negotiate_peer(peer_mid)
        channel = self._bridge.e2ee_create_channel_with_pubkey(my_handle, peer_pub)
        self._peer_channels[peer_mid] = (channel, my_kid, peer_kid)
        return self._peer_channels[peer_mid]

    def _channel_for_receive(
        self, sender_mid: str, sender_key_id: int, receiver_key_id: int
    ) -> int:
        my_handle = self.my_keys.get(receiver_key_id)
        if my_handle is None:
            # fall back to whatever key we have
            my_handle = self.my_keys.get(self.latest_key_id or 0)
        if my_handle is None:
            raise RuntimeError("no local E2EE key to decrypt with")
        sender_pub = self.api.get_e2ee_public_key(sender_mid, 1, sender_key_id)
        pub_b64 = sender_pub.get("keyData") if isinstance(sender_pub, dict) else None
        if not pub_b64:
            raise RuntimeError(f"no public key for sender {sender_mid}")
        return self._bridge.e2ee_create_channel_with_pubkey(my_handle, pub_b64)

    # -- encrypt / decrypt (routers) -----------------------------------------
    @staticmethod
    def _is_group(message: dict[str, Any]) -> bool:
        """A group/room/square target (vs a 1:1 user)."""
        if int(message.get("toType", 0) or 0) in (1, 2, 4):  # ROOM, GROUP, SQUARE_CHAT
            return True
        return (message.get("to") or "")[:1].lower() in ("c", "r", "s")

    def encrypt(self, message: dict[str, Any]) -> dict[str, Any]:
        """Encrypt a message dict -> sealed Message (with ``chunks``).

        Routes to **group** Letter Sealing for group/room targets, else **1:1**.
        """
        return (self._encrypt_group if self._is_group(message) else self._encrypt_user)(
            message
        )

    def decrypt(self, message: dict[str, Any]) -> dict[str, Any]:
        """Decrypt a received sealed message -> plain message dict.

        Handles **V1** and **V2** framing (dispatched on
        ``contentMetadata.e2eeVersion``) for both **1:1** and **group** messages.
        """
        return (self._decrypt_group if self._is_group(message) else self._decrypt_user)(
            message
        )

    def _finish_decrypt(self, message: dict[str, Any], pt_b64: str) -> dict[str, Any]:
        plain = fr.deserialize_plaintext(base64.b64decode(pt_b64))
        out = dict(message)
        out["text"] = plain.get("text")
        if plain.get("location") is not None:
            out["location"] = plain["location"]
        out["_decrypted"] = True
        return out

    # -- 1:1 -----------------------------------------------------------------
    def _encrypt_user(self, message: dict[str, Any]) -> dict[str, Any]:
        to = message["to"]
        frm = message.get("from") or self.my_mid
        channel, my_kid, peer_kid = self._channel_for_send(to)
        plaintext = fr.serialize_plaintext(message)
        ct_b64 = self._bridge.e2ee_encrypt_v2(
            channel,
            to=to,
            frm=frm,
            sender_key_id=my_kid,
            receiver_key_id=peer_kid,
            content_type=int(message.get("contentType", 0)),
            sequence_number=self._next_seq(),
            plaintext_b64=base64.b64encode(plaintext).decode("ascii"),
        )
        chunks = fr.build_chunks(base64.b64decode(ct_b64), my_kid, peer_kid)
        # EL() drops text/location/from — the gateway 500s if `from`/`text:null`
        # are present (the server populates `from` from the auth token).
        return fr.build_e2ee_message(message, chunks, 2)

    def _decrypt_user(self, message: dict[str, Any]) -> dict[str, Any]:
        chunks = message.get("chunks") or []
        version = fr.message_e2ee_version(message)
        sender, to = message.get("from") or "", message.get("to") or ""
        parse = fr.parse_chunks_v1 if version == 1 else fr.parse_chunks
        ciphertext, sender_key_id, receiver_key_id = parse(chunks)
        channel = self._channel_for_receive(sender, sender_key_id or 0, receiver_key_id or 0)
        ct_b64 = base64.b64encode(ciphertext).decode("ascii")
        if version == 1:
            pt_b64 = self._bridge.e2ee_decrypt_v1(channel, ciphertext_b64=ct_b64)
        else:
            pt_b64 = self._bridge.e2ee_decrypt_v2(
                channel,
                to=to,
                frm=sender,
                sender_key_id=sender_key_id or 0,
                receiver_key_id=receiver_key_id or 0,
                content_type=int(message.get("contentType", 0)),
                ciphertext_b64=ct_b64,
            )
        return self._finish_decrypt(message, pt_b64)

    # -- group ---------------------------------------------------------------
    def _user_pub(self, mid: str, key_id: int) -> str:
        """The Curve25519 public key (base64) of ``mid`` for ``key_id``."""
        pk = self.api.get_e2ee_public_key(mid, 1, int(key_id))
        data = pk.get("keyData") if isinstance(pk, dict) else None
        if not data:
            raise RuntimeError(f"no E2EE public key for {mid} keyId={key_id}")
        return data

    def _group_key_handle(
        self, group_mid: str, group_key_id: int | None = None
    ) -> tuple[int, int]:
        """Fetch + unwrap a group shared key -> ``(handle, group_key_id)`` (cached).

        ``group_key_id=None`` resolves the **latest** key for the group.  Unwrap =
        ECDH(my key, group creator's public key) then ``unwrap_group_shared_key``.
        """
        if group_key_id is not None and (group_mid, int(group_key_id)) in self._group_keys:
            return self._group_keys[(group_mid, int(group_key_id))], int(group_key_id)
        if group_key_id is None:
            gsk = self.api.get_last_e2ee_group_shared_key(group_mid)
        else:
            gsk = self.api.get_e2ee_group_shared_key(group_mid, int(group_key_id))
        if not isinstance(gsk, dict) or not gsk.get("encryptedSharedKey"):
            raise RuntimeError(
                f"no E2EE group shared key for {group_mid} "
                "(first-message key creation is not implemented)"
            )
        gkid = int(gsk.get("groupKeyId", group_key_id or 0))
        if (group_mid, gkid) in self._group_keys:
            return self._group_keys[(group_mid, gkid)], gkid
        recv_kid = int(gsk.get("receiverKeyId") or self.latest_key_id or 0)
        my_handle = self.my_keys.get(recv_kid) or self.my_keys.get(self.latest_key_id or 0)
        if my_handle is None:
            raise RuntimeError("no local E2EE key to unwrap the group key")
        unwrap_channel = self._bridge.e2ee_create_channel_with_pubkey(
            my_handle, self._user_pub(gsk["creator"], gsk["creatorKeyId"])
        )
        handle = int(
            self._bridge.e2ee_unwrap_group_shared_key(
                unwrap_channel, enc_shared_key_b64=gsk["encryptedSharedKey"]
            )
        )
        self._group_keys[(group_mid, gkid)] = handle
        return handle, gkid

    def _encrypt_group(self, message: dict[str, Any]) -> dict[str, Any]:
        group_mid = message["to"]
        gk_handle, gkid = self._group_key_handle(group_mid, None)  # latest key
        my_kid = self.latest_key_id
        if my_kid is None:
            raise RuntimeError("E2EE not initialised — log in with qr_login first")
        my_pub = self._bridge.e2ee_public_key_for_handle(self.my_keys[my_kid])
        channel = self._bridge.e2ee_create_channel_with_pubkey(gk_handle, my_pub)
        plaintext = fr.serialize_plaintext(message)
        ct_b64 = self._bridge.e2ee_encrypt_v2(
            channel,
            to=group_mid,
            frm=self.my_mid,
            sender_key_id=my_kid,
            receiver_key_id=gkid,
            content_type=int(message.get("contentType", 0)),
            sequence_number=self._next_seq(),
            plaintext_b64=base64.b64encode(plaintext).decode("ascii"),
        )
        chunks = fr.build_chunks(base64.b64decode(ct_b64), my_kid, gkid)
        return fr.build_e2ee_message(message, chunks, 2)

    def _decrypt_group(self, message: dict[str, Any]) -> dict[str, Any]:
        chunks = message.get("chunks") or []
        version = fr.message_e2ee_version(message)
        parse = fr.parse_chunks_v1 if version == 1 else fr.parse_chunks
        ciphertext, sender_key_id, group_key_id = parse(chunks)
        group_mid, sender = message.get("to") or "", message.get("from") or ""
        gk_handle, _ = self._group_key_handle(group_mid, group_key_id or None)
        channel = self._bridge.e2ee_create_channel_with_pubkey(
            gk_handle, self._user_pub(sender, sender_key_id or 0)
        )
        ct_b64 = base64.b64encode(ciphertext).decode("ascii")
        if version == 1:
            pt_b64 = self._bridge.e2ee_decrypt_v1(channel, ciphertext_b64=ct_b64)
        else:
            pt_b64 = self._bridge.e2ee_decrypt_v2(
                channel,
                to=group_mid,
                frm=sender,
                sender_key_id=sender_key_id or 0,
                receiver_key_id=group_key_id or 0,
                content_type=int(message.get("contentType", 0)),
                ciphertext_b64=ct_b64,
            )
        return self._finish_decrypt(message, pt_b64)

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
        msg = {"to": to, "toType": 0, "contentType": 0, "text": text, "contentMetadata": {}}
        sealed = self.encrypt(msg)
        channel, my_kid, peer_kid = self._channel_for_send(to)
        ct, sid, rid = fr.parse_chunks(sealed["chunks"])
        pt_b64 = self._bridge.e2ee_decrypt_v2(
            channel,
            to=to,
            frm=self.my_mid,
            sender_key_id=sid or my_kid,
            receiver_key_id=rid or peer_kid,
            content_type=0,
            ciphertext_b64=base64.b64encode(ct).decode("ascii"),
        )
        plain = fr.deserialize_plaintext(base64.b64decode(pt_b64))
        return plain.get("text", "")

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
WASM at runtime, then cached.  When a group has **no** key yet, the very first
encrypted message creates one (:meth:`E2EEManager.register_group_key`, the
extension's ``registerGroupKey``: generate a curve key, wrap it once per member
public key, upload via ``registerE2EEGroupKey``, unwrap locally).

Sends go out with the peer's negotiated ``specVersion`` (V1 or V2 framing), and
:meth:`E2EEManager.send_with_retry` adds the extension's E2EE send-error retry
semantics (codes 84/86/87/88/90 -> re-negotiate + retry, up to 3 transparent
retries like the extension's wrapper; 99 -> re-register the group key + retry;
122 -> reset the negotiation and re-throw).

Deliberate live-verified deviations from the bundle (do not "fix"):
``build_e2ee_message`` deletes ``from`` (the gateway 500s otherwise); the
server-side ``function.e2ee`` config check is not replicated (needs live
settings) — sealing is gated locally on the sealable content-type whitelist,
already-sealed detection and the negotiated ``allowedTypes``/media flow instead.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from . import e2ee_crypto as fr
from .enums import ContentType, ErrorCode
from .exceptions import LineApiError

log = logging.getLogger("okline.e2ee")

# sL/lL in the bundle: the content types that may be Letter-Sealed.
SEALABLE_CONTENT_TYPES = frozenset(
    int(t)
    for t in (
        ContentType.NONE,
        ContentType.LOCATION,
        ContentType.IMAGE,
        ContentType.VIDEO,
        ContentType.FILE,
        ContentType.AUDIO,
    )
)
# FP in the bundle: media content types, which additionally require the
# negotiated media flow to be V2 before sealing.
MEDIA_CONTENT_TYPES = frozenset(
    int(t) for t in (ContentType.IMAGE, ContentType.VIDEO, ContentType.FILE, ContentType.AUDIO)
)
# The extension's send-retry switch: reset the cached E2EE info and retry once.
# (E2EE_SENDER_NOT_ALLOWED=89 is NOT here — the extension disables E2EE on it.)
E2EE_RETRY_CODES = frozenset(
    int(c)
    for c in (
        ErrorCode.E2EE_UPDATE_RECEIVER_KEY,  # 84
        ErrorCode.E2EE_INVALID_VERSION,  # 86
        ErrorCode.E2EE_SENDER_DISABLED,  # 87
        ErrorCode.E2EE_RECEIVER_DISABLED,  # 88
        ErrorCode.E2EE_RECEIVER_NOT_ALLOWED,  # 90
    )
)


class E2EEManager:
    def __init__(self, api: Any) -> None:
        self.api = api
        self.my_mid: str | None = getattr(api.tokens, "mid", None)
        # our unwrapped E2EE keys: keyId -> wasm handle
        self.my_keys: dict[int, int] = {}
        # keyId -> exported blob (the extension's exportedKeyMap). The WASM
        # cannot re-export an imported key, so blobs loaded from a session
        # file are served from this cache by export_keys().
        self._export_blobs: dict[int, str] = {}
        self.latest_key_id: int | None = None
        # peer mid -> (channel, my_key_id, peer_key_id)
        self._peer_channels: dict[str, tuple[int, int, int]] = {}
        # peer mid -> {specVersion, allowedTypes} from negotiateE2EEPublicKey
        self._peer_negotiation: dict[str, dict[str, Any]] = {}
        # group mid -> {specVersion, allowedTypes} from the group shared key
        self._group_negotiation: dict[str, dict[str, Any]] = {}
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
        self._export_blobs.clear()  # fresh unwrapped keys — re-export from scratch
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
            blob = self._export_blobs.get(kid)
            if blob is None:
                try:
                    blob = self._bridge.e2ee_export_key(handle)
                except Exception as exc:
                    # Live-verified: a key *loaded* back from an export cannot be
                    # re-exported by the WASM ("illegal operation") — but its
                    # original blob is already known, so this only happens for
                    # keys that somehow bypassed the cache.
                    log.warning("E2EE export of key %s failed: %s", kid, exc)
                    continue
                self._export_blobs[kid] = blob
            keys[str(kid)] = blob
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
                # remember the blob — the WASM refuses to re-export an imported
                # key, so future export_keys() must serve it from this cache
                # (the extension's exportedKeyMap holds the blobs the same way)
                self._export_blobs[int(kid_s)] = blob
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
        """Return the peer's (public_key_b64, key_id) via negotiate/getE2EEPublicKey.

        Also caches the negotiation's ``specVersion`` (default 2, ``Nd``) and
        ``allowedTypes`` for :meth:`encrypt`'s gating and the V1/V2 send choice.
        """
        neg = self.api.negotiate_e2ee_public_key(peer_mid)
        if isinstance(neg, dict):
            spec = neg.get("specVersion")
            allowed = neg.get("allowedTypes")
            self._peer_negotiation[peer_mid] = {
                "specVersion": int(spec) if spec else 2,
                "allowedTypes": (
                    [int(a) for a in allowed] if isinstance(allowed, list) else []
                ),
            }
            pk = neg.get("publicKey")
            if isinstance(pk, dict) and pk.get("keyData") and pk.get("keyId") is not None:
                return pk["keyData"], int(pk["keyId"])
        # fall back to getE2EEPublicKey (no negotiation info beyond defaults)
        self._peer_negotiation.setdefault(peer_mid, {"specVersion": 2, "allowedTypes": []})
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

    def _channel_for_own(self, peer_mid: str, sender_key_id: int, receiver_key_id: int) -> int:
        """Channel for our **own** sealed 1:1 messages read back from history.

        We sealed them with ECDH(our key ``sender_key_id``, the *peer's* public
        key), so the ECDH counterparty is the original **recipient** (``to``),
        not the sender — that is us.  X25519 secrets are symmetric, so
        ECDH(our key, peer pub) recovers the very same send-side channel.
        """
        my_handle = self.my_keys.get(sender_key_id)
        if my_handle is None:
            # fall back to whatever key we have
            my_handle = self.my_keys.get(self.latest_key_id or 0)
        if my_handle is None:
            raise RuntimeError("no local E2EE key to decrypt with")
        return self._bridge.e2ee_create_channel_with_pubkey(
            my_handle, self._user_pub(peer_mid, receiver_key_id or 0)
        )

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

        Gating (the extension's ``encryptMessage``): messages that already carry
        chunks are returned untouched (never double-sealed, ``iL``), and only
        content types in the sealable whitelist (NONE/TEXT, LOCATION, IMAGE,
        VIDEO, FILE, AUDIO — ``sL``) are sealed.  Media types additionally
        require the negotiated media flow to be V2 (best-effort: if the flow
        cannot be looked up, sealing proceeds), and if a cached negotiation
        carries ``allowedTypes`` that exclude the content type, the message is
        returned unsealed.  The extension's server-side ``function.e2ee`` config
        check is deliberately not replicated (it needs live settings).
        """
        if message.get("chunks"):
            return message  # iL: already sealed
        content_type = int(message.get("contentType", 0) or 0)
        if content_type not in SEALABLE_CONTENT_TYPES:
            log.debug("E2EE: contentType %d not sealable; leaving plain", content_type)
            return message
        to = message.get("to") or ""
        is_group = self._is_group(message)
        negotiation = (self._group_negotiation if is_group else self._peer_negotiation).get(
            to
        ) or {}
        allowed = negotiation.get("allowedTypes") or []
        if allowed and content_type not in allowed:
            log.debug("E2EE: contentType %d not in negotiated allowedTypes", content_type)
            return message
        if content_type in MEDIA_CONTENT_TYPES and not self._media_flow_is_v2(
            to, content_type
        ):
            log.debug("E2EE: media flow is not V2 for contentType %d", content_type)
            return message
        return (self._encrypt_group if is_group else self._encrypt_user)(message)

    def _media_flow_is_v2(self, chat_mid: str, content_type: int) -> bool:
        """``checkAndGetMediaMessageFlow`` gate: media sealing requires flow == V2.

        Best-effort — the flow lookup needs the live ``determineMediaMessageFlow``
        thrift call; when it is unavailable (offline / error) sealing proceeds
        rather than silently downgrading the send to plain.
        """
        from .enums import E2EEMediaFlow

        try:
            flow = self.api.determine_media_message_flow(chat_mid)
        except Exception as exc:  # offline / endpoint unavailable
            log.debug("media flow lookup unavailable (%s); assuming V2", exc)
            return True
        if not isinstance(flow, dict):
            return True
        flow_map = flow.get("flowMap") or {}
        value = flow_map.get(str(content_type), flow_map.get(content_type))
        if value is None:
            return True
        return int(value) == int(E2EEMediaFlow.V2)

    def decrypt(self, message: dict[str, Any]) -> dict[str, Any]:
        """Decrypt a received sealed message -> plain message dict.

        Handles **V1** and **V2** framing (dispatched on
        ``contentMetadata.e2eeVersion``) for both **1:1** and **group** messages
        — including your **own** sealed messages read back from history.
        """
        return (self._decrypt_group if self._is_group(message) else self._decrypt_user)(
            message
        )

    # -- sealed media ----------------------------------------------------------
    @staticmethod
    def generate_enc_km() -> str:
        """``NL`` — fresh ``contentMetadata.ENC_KM`` for a sealed media send.

        Base64 of 32 random bytes; pass it to the media message builders'
        ``enc_km=`` parameter.  The file blob itself is encrypted under it with
        :func:`okline.e2ee_crypto.encrypt_blob` (HKDF ``FileEncryption``
        AES-CTR+HMAC).
        """
        return fr.generate_enc_km()

    def download_sealed_media(
        self, message: dict[str, Any], *, info: bool = False
    ) -> bytes | tuple[bytes, Any]:
        """Download and decrypt the media blob of a sealed (E2EE) message.

        The extension's ``$P``/``GD`` flow as one call: decrypt the message
        (restores ``ENC_KM`` into ``contentMetadata``), fetch the object from
        ``/r/talk/<SID>/<OID>`` with the ``X-Talk-Meta`` header, then decrypt
        the blob with the HKDF ``FileEncryption`` keys derived from
        ``ENC_KM``.  Returns the plaintext bytes (``info=True`` adds the
        ``object_info.obs`` dict — name, mime, size, ...).

        Raises :class:`~okline.exceptions.LineApiError` when the message is
        not sealed media (no ``ENC_KM`` after decryption).
        """
        d = self.decrypt(message)
        meta = d.get("contentMetadata") or {}
        enc_km = meta.get("ENC_KM")
        oid = meta.get("OID")
        if not enc_km or not oid:
            raise LineApiError(
                "message has no ENC_KM/OID after decryption — not sealed media",
                path="download_sealed_media",
                raw={"contentType": d.get("contentType"), "hasENC_KM": bool(enc_km)},
            )
        sid = meta.get("SID", "m")
        msg_id = str(d.get("id") or message.get("id") or "")
        obs_info: Any = None
        if info:
            obs_info = self.api.obs.object_info(
                f"/r/talk/{sid}/{oid}", message_id=msg_id or None
            )
        blob = self.api.obs.download_object("talk", sid, oid, message_id=msg_id or None)
        km = base64.b64decode(enc_km + "=" * (-len(enc_km) % 4))
        plain = fr.decrypt_blob(km, blob)
        return (plain, obs_info) if info else plain

    def _finish_decrypt(self, message: dict[str, Any], pt_b64: str) -> dict[str, Any]:
        """``yL`` — merge the decrypted plaintext back into the message.

        Restores ``text``, ``location`` and — into ``contentMetadata`` —
        ``REPLACE`` (unsend/unpick UI) plus the sealed-media ``ENC_KM`` /
        ``FILE_NAME`` (from the plaintext's ``keyMaterial``/``fileName``), and
        clears the sealed ``chunks``.
        """
        plain = fr.deserialize_plaintext(base64.b64decode(pt_b64))
        out = dict(message)
        out["chunks"] = []
        if plain.get("text"):
            out["text"] = plain["text"]
        if plain.get("location"):
            out["location"] = plain["location"]
        meta = dict(out.get("contentMetadata") or {})
        if plain.get("REPLACE"):
            meta["REPLACE"] = plain["REPLACE"]
        if plain.get("keyMaterial"):
            meta["ENC_KM"] = plain["keyMaterial"]
        if plain.get("fileName"):
            meta["FILE_NAME"] = plain["fileName"]
        out["contentMetadata"] = meta
        out["_decrypted"] = True
        return out

    # -- 1:1 -----------------------------------------------------------------
    def _encrypt_user(self, message: dict[str, Any]) -> dict[str, Any]:
        to = message["to"]
        frm = message.get("from") or self.my_mid
        channel, my_kid, peer_kid = self._channel_for_send(to)
        plaintext = fr.serialize_plaintext(message)
        spec_version = int((self._peer_negotiation.get(to) or {}).get("specVersion") or 2)
        pt_b64 = base64.b64encode(plaintext).decode("ascii")
        if spec_version == 1:
            # peer negotiated spec 1: V1 framing (mL), no AAD fields
            ct_b64 = self._bridge.e2ee_encrypt_v1(channel, plaintext_b64=pt_b64)
            chunks = fr.build_chunks_v1(base64.b64decode(ct_b64), my_kid, peer_kid)
            return fr.build_e2ee_message(message, chunks, 1)
        if spec_version != 2:
            raise RuntimeError(f"unsupported E2EE specVersion {spec_version} for {to}")
        ct_b64 = self._bridge.e2ee_encrypt_v2(
            channel,
            to=to,
            frm=frm,
            sender_key_id=my_kid,
            receiver_key_id=peer_kid,
            content_type=int(message.get("contentType", 0)),
            sequence_number=self._next_seq(),
            plaintext_b64=pt_b64,
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
        if sender and to and sender == self.my_mid:
            # our own message read back from history: the ECDH counterparty is
            # the original *recipient* (to), not the sender (us)
            channel = self._channel_for_own(to, sender_key_id or 0, receiver_key_id or 0)
        else:
            channel = self._channel_for_receive(
                sender, sender_key_id or 0, receiver_key_id or 0
            )
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

        ``group_key_id=None`` resolves the **latest** key for the group; when the
        group has none yet (``NOT_FOUND``), one is created via
        :meth:`register_group_key` (the extension's ``renewLatestGroupKey``
        fallback).  Unwrap = ECDH(my key, group creator's public key) then
        ``unwrap_group_shared_key``.
        """
        if group_key_id is not None and (group_mid, int(group_key_id)) in self._group_keys:
            return self._group_keys[(group_mid, int(group_key_id))], int(group_key_id)
        if group_key_id is None:
            try:
                gsk = self.api.get_last_e2ee_group_shared_key(group_mid)
            except LineApiError as exc:
                if exc.code == int(ErrorCode.NOT_FOUND):
                    return self.register_group_key(group_mid)
                raise
        else:
            gsk = self.api.get_e2ee_group_shared_key(group_mid, int(group_key_id))
        if not isinstance(gsk, dict) or not gsk.get("encryptedSharedKey"):
            raise RuntimeError(f"no E2EE group shared key for {group_mid}")
        gkid = int(gsk.get("groupKeyId", group_key_id or 0))
        if (group_mid, gkid) in self._group_keys:
            return self._group_keys[(group_mid, gkid)], gkid
        return self._unwrap_group_key(group_mid, gsk)

    def _unwrap_group_key(self, group_mid: str, gsk: dict[str, Any]) -> tuple[int, int]:
        """Unwrap one ``getE2EEGroupSharedKey``-shaped struct -> cached handle.

        The struct carries the group's ``allowedTypes``/``specVersion`` (used for
        encrypt gating and the V1/V2 send choice), which we cache alongside.
        """
        gkid = int(gsk.get("groupKeyId", 0))
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
        self._group_negotiation[group_mid] = {
            "specVersion": int(gsk.get("specVersion") or 2),
            "allowedTypes": (
                [int(a) for a in gsk["allowedTypes"]]
                if isinstance(gsk.get("allowedTypes"), list)
                else []
            ),
        }
        return handle, gkid

    def register_group_key(self, group_mid: str) -> tuple[int, int]:
        """Create + register a group shared key (the extension's ``registerGroupKey``).

        Generate a curve key, fetch the current member public keys
        (``getLastE2EEPublicKeys``), wrap the new key once per member
        (``e2eeChannelWrapGroupSharedKey``), upload via
        ``registerE2EEGroupKey(version=1, chatMid, memberMids[], keyIds[],
        wrappedKeys[])`` and finally unwrap the returned key locally (cached).
        Returns ``(group_key_handle, group_key_id)``.
        """
        if not self.is_ready() or self.latest_key_id is None:
            raise RuntimeError("E2EE not initialised — log in with qr_login first")
        shared_key_handle = int(self._bridge.curvekey_generate())
        members = self.api.get_last_e2ee_public_keys(group_mid)
        if not isinstance(members, dict) or not members:
            raise RuntimeError(f"no members with E2EE public keys for {group_mid}")
        my_handle = self.my_keys[self.latest_key_id]
        member_mids: list[str] = []
        key_ids: list[int] = []
        wrapped_keys: list[str] = []
        for mid, pk in members.items():
            pub = pk.get("keyData") if isinstance(pk, dict) else None
            if not pub:
                continue
            channel = self._bridge.e2ee_create_channel_with_pubkey(my_handle, pub)
            wrapped_keys.append(
                self._bridge.e2ee_wrap_group_shared_key(channel, key_handle=shared_key_handle)
            )
            member_mids.append(mid)
            key_ids.append(int(pk.get("keyId", 0)))
        if not member_mids:
            raise RuntimeError(f"no member public key data for {group_mid}")
        result = self.api.register_e2ee_group_key(
            group_mid, member_mids, key_ids, wrapped_keys, version=1
        )
        if not isinstance(result, dict) or not result.get("encryptedSharedKey"):
            raise RuntimeError(f"registerE2EEGroupKey returned no key for {group_mid}")
        handle, gkid = self._unwrap_group_key(group_mid, result)
        log.info(
            "E2EE group key %d registered for %s (%d member(s))",
            gkid,
            group_mid,
            len(member_mids),
        )
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
        spec_version = int(
            (self._group_negotiation.get(group_mid) or {}).get("specVersion") or 2
        )
        pt_b64 = base64.b64encode(plaintext).decode("ascii")
        if spec_version == 1:
            ct_b64 = self._bridge.e2ee_encrypt_v1(channel, plaintext_b64=pt_b64)
            chunks = fr.build_chunks_v1(base64.b64decode(ct_b64), my_kid, gkid)
            return fr.build_e2ee_message(message, chunks, 1)
        if spec_version != 2:
            raise RuntimeError(f"unsupported E2EE specVersion {spec_version} for {group_mid}")
        ct_b64 = self._bridge.e2ee_encrypt_v2(
            channel,
            to=group_mid,
            frm=self.my_mid,
            sender_key_id=my_kid,
            receiver_key_id=gkid,
            content_type=int(message.get("contentType", 0)),
            sequence_number=self._next_seq(),
            plaintext_b64=pt_b64,
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

    # -- send-error retry semantics ------------------------------------------
    def reset_negotiation(self, chat_mid: str) -> None:
        """``resetE2eeInfo`` — drop all cached E2EE state for one chat.

        Clears the cached peer channel + negotiation info (so the next send
        re-negotiates keys) and any cached group shared keys for the chat.
        """
        self._peer_channels.pop(chat_mid, None)
        self._peer_negotiation.pop(chat_mid, None)
        self._group_negotiation.pop(chat_mid, None)
        for gkid in [k for k in self._group_keys if k[0] == chat_mid]:
            del self._group_keys[gkid]

    def send_with_retry(self, message: dict[str, Any]) -> Any:
        """Seal + send a message with the extension's E2EE error-retry semantics.

        On ``LineApiError`` codes 84 (E2EE_UPDATE_RECEIVER_KEY), 86
        (E2EE_INVALID_VERSION), 87/88 (sender/receiver E2EE_DISABLED) and 90
        (E2EE_RECEIVER_NOT_ALLOWED): drop the cached negotiation/channels
        (:meth:`reset_negotiation`) and retry the sealed send — up to **three**
        transparent retries (four attempts), matching the extension's ``sB``
        wrapper (``if (n >= 3) throw``); the keys are re-negotiated on each
        fresh send.  On 99 (E2EE_RECREATE_GROUP_KEY): reset, re-register the
        group key (:meth:`register_group_key`) for group targets, and retry
        within the same budget.  On 122 (REFRESH_MEDIA_FLOW): reset the cached
        negotiation and **re-raise immediately without retrying** — the
        extension's ``resetE2eeInfo`` + rethrow, so the next send re-negotiates
        the (now-obsolete) media flow instead of reusing it.  Any other error —
        or a retry that still fails after the budget — propagates to the caller.

        :meth:`okline.services.messaging.MessagingMixin.send_message` routes
        its sealed sends (``encrypt=True`` and the code-82 fallback) through
        this method; callers can also use ``api.e2ee.send_with_retry(message)``
        directly.
        """
        to = message.get("to") or ""
        attempts = 4  # the original send + up to 3 transparent retries (sB)
        for attempt in range(attempts):
            try:
                return self._send_sealed(self.encrypt(message))
            except LineApiError as exc:
                code = exc.code
                if code == int(ErrorCode.REFRESH_MEDIA_FLOW):
                    # resetE2eeInfo + rethrow — never retried here
                    self.reset_negotiation(to)
                    raise
                if code == int(ErrorCode.E2EE_RECREATE_GROUP_KEY):
                    self.reset_negotiation(to)
                    if self._is_group(message):
                        self.register_group_key(to)
                elif code in E2EE_RETRY_CODES:
                    self.reset_negotiation(to)
                else:
                    raise
                if attempt == attempts - 1:
                    raise  # retry budget exhausted — surface the last error
        raise LineApiError("send_with_retry: exhausted attempts")  # pragma: no cover

    def _send_sealed(self, message: dict[str, Any]) -> Any:
        """Send a (possibly sealed) message without re-entering this retry loop.

        A sealed message (``chunks`` present) is a plain pass-through for
        :meth:`api.send_message`.  An *unsealed* one — encrypt gating declined
        to seal it (e.g. contentType outside the negotiated ``allowedTypes``) —
        is sent straight through the transport so a code-82 rejection
        propagates to the caller instead of bouncing back into
        :meth:`send_with_retry` via messaging's 82-fallback forever.
        """
        if message.get("chunks"):
            return self.api.send_message(message)
        return self.api.transport.call(
            "Talk.TalkService.sendMessage", [self.api.next_req_seq(), message]
        )

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

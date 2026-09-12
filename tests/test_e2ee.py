"""Tests for the E2EE framing (pure-Python) and the manager send/decrypt paths.

The actual crypto runs in the WASM bridge (live-only; see test_hmac_bridge.py);
here we test the framing round-trips, the sealed-media framing/blob encryption,
the registerGroupKey + V1-send + retry paths of :class:`okline.e2ee.E2EEManager`
(using the offline FakeBridge), and that send_message seals + retries on a
code-82 rejection using a fake E2EE manager.
"""

from __future__ import annotations

import base64
import json

import pytest
from conftest import GROUP_MID, USER_MID, USER_MID2, FakeBridge, FakeResp, build_api, enveloped

from okline import e2ee_crypto as fr
from okline.enums import ContentType, ErrorCode
from okline.exceptions import LineApiError


# --- framing ---------------------------------------------------------------
def test_key_id_byte_roundtrip():
    for n in (0, 1, 255, 256, 70000, 5312832, 0xFFFFFFFF):
        b = fr.key_id_to_bytes(n)
        assert len(b) == 4 and fr.key_id_from_bytes(b) == n


def test_chunks_roundtrip():
    ct = bytes(range(60))  # any ciphertext >= 28 bytes
    chunks = fr.build_chunks(ct, sender_key_id=11, receiver_key_id=22)
    assert len(chunks) == 5
    # the wire order is [head16, body, tag12, sidBE, ridBE]
    assert base64.b64decode(chunks[0]) == ct[0:16]
    assert base64.b64decode(chunks[2]) == ct[16:28]
    assert base64.b64decode(chunks[1]) == ct[28:]
    ct2, sid, rid = fr.parse_chunks(chunks)
    assert ct2 == ct and sid == 11 and rid == 22


def test_chunks_v1_roundtrip():
    ct = bytes(range(60))  # salt(8) + body(36) + tag(16)
    chunks = fr.build_chunks_v1(ct, sender_key_id=11, receiver_key_id=22)
    assert len(chunks) == 5
    # V1 wire order is [salt8, body, tag16, sidBE, ridBE] — NOT swapped
    assert base64.b64decode(chunks[0]) == ct[0:8]
    assert base64.b64decode(chunks[1]) == ct[8:-16]
    assert base64.b64decode(chunks[2]) == ct[-16:]
    ct2, sid, rid = fr.parse_chunks_v1(chunks)
    assert ct2 == ct and sid == 11 and rid == 22  # concatenated in order


def test_message_e2ee_version():
    assert fr.message_e2ee_version({"contentMetadata": {"e2eeVersion": "1"}}) == 1
    assert fr.message_e2ee_version({"contentMetadata": {"e2eeVersion": "2"}}) == 2
    assert fr.message_e2ee_version({"contentMetadata": {}}) == 2  # default
    assert fr.message_e2ee_version({}) == 2


# --- cross-session key persistence -----------------------------------------
def test_session_persists_e2ee_keychain(tmp_path):
    from okline.session import Session

    exp = {"mid": "Ume", "latestKeyId": 5312832, "keys": {"5312832": "QkxPQg=="}}
    p = str(tmp_path / "sess.json")
    Session(access_token="T", mid="Ume", e2ee=exp).save(p)
    assert Session.load(p).e2ee == exp
    # a session with no E2EE omits the key from the file entirely
    p2 = str(tmp_path / "plain.json")
    Session(access_token="T").save(p2)
    assert "e2ee" not in json.loads(open(p2, encoding="utf-8").read())


def test_e2ee_manager_export_load_roundtrip():
    from conftest import FakeBridge

    api = build_api(bridge=FakeBridge())
    mgr = api.e2ee
    mgr.my_mid, mgr.my_keys, mgr.latest_key_id = "Ume", {5312832: 10, 5312833: 11}, 5312833
    exp = mgr.export_keys()
    assert exp["mid"] == "Ume" and exp["latestKeyId"] == 5312833
    assert set(exp["keys"]) == {"5312832", "5312833"}

    api2 = build_api(bridge=FakeBridge())  # fresh process / no QR login
    assert api2.e2ee.load_from_export(exp) is True
    assert api2.e2ee.my_keys == {5312832: 10, 5312833: 11}
    assert api2.e2ee.latest_key_id == 5312833 and api2.e2ee.my_mid == "Ume"
    api.close()
    api2.close()


def test_e2ee_export_empty_when_not_ready():
    from conftest import FakeBridge

    api = build_api(bridge=FakeBridge())
    assert api.e2ee.export_keys() == {}  # no keys loaded
    assert api.e2ee.load_from_export({"keys": {}}) is False
    api.close()


# --- group vs 1:1 routing --------------------------------------------------
def test_e2ee_is_group_routing():
    g = __import__("okline.e2ee", fromlist=["E2EEManager"]).E2EEManager._is_group
    assert g({"to": "C" + "a" * 32, "toType": 2})  # group by toType
    assert g({"to": "Cabc", "toType": 0})  # group by prefix (upper)
    assert g({"to": "rabc"})  # room (legacy lower)
    assert not g({"to": "U" + "a" * 32, "toType": 0})  # 1:1 user
    assert not g({"to": "Uabc"})


def test_plaintext_roundtrip():
    msg = {"text": "สวัสดี 👋", "contentType": 0, "contentMetadata": {}}
    pt = fr.serialize_plaintext(msg)
    assert json.loads(pt.decode()) == {"text": "สวัสดี 👋"}
    back = fr.deserialize_plaintext(pt)
    assert back["text"] == "สวัสดี 👋"


def test_build_e2ee_message():
    msg = {
        "to": USER_MID,
        "text": "hi",
        "location": {"x": 1},
        "from": "Ume",
        "contentType": 0,
        "contentMetadata": {"REPLACE": "x"},
        "toType": 0,
    }
    sealed = fr.build_e2ee_message(msg, ["a", "b", "c", "d", "e"], 2)
    # EL() drops text/location/from entirely (not text:null) — sending them 500s
    assert "text" not in sealed
    assert "location" not in sealed
    assert "from" not in sealed
    assert sealed["chunks"] == ["a", "b", "c", "d", "e"]
    assert sealed["contentMetadata"]["e2eeVersion"] == "2"
    assert "REPLACE" not in sealed["contentMetadata"]  # moved into ciphertext


def test_is_e2ee_message():
    assert fr.is_e2ee_message(
        {"chunks": ["a", "b", "c", "d", "e"], "contentMetadata": {"e2eeVersion": "2"}}
    )
    assert not fr.is_e2ee_message({"text": "plain", "contentMetadata": {}})


# --- auto-encrypt retry on code 82 -----------------------------------------
class _FakeE2EE:
    """Minimal stand-in for E2EEManager."""

    def __init__(self, api=None):
        self.encrypt_calls = 0
        self.retry_calls = 0
        self.api = api

    def is_ready(self):
        return True

    def encrypt(self, message):
        self.encrypt_calls += 1
        sealed = dict(message)
        sealed["chunks"] = ["c1", "c2", "c3", "c4", "c5"]
        sealed["text"] = None
        return sealed

    def send_with_retry(self, message):
        self.retry_calls += 1
        assert self.api is not None
        return self.api.send_message(self.encrypt(message))


def test_send_message_seals_and_retries_on_code_82(make_api):
    state = {"n": 0}
    err_body = {
        "code": 10051,
        "message": "RESPONSE_ERROR",
        "data": {"code": 82, "reason": "can not send using plain mode"},
    }

    def responder(method, url, kw):
        if url.endswith("sendMessage"):
            body = json.loads(kw["data"])
            sealed = bool(body[1].get("chunks"))
            state["n"] += 1
            if not sealed:
                return FakeResp(400, err_body)  # plain rejected
            return enveloped({"id": "1", "chunks": body[1]["chunks"]})
        return enveloped({})

    api = make_api(responder)
    api.e2ee = _FakeE2EE(api)  # pretend E2EE is ready
    res = api.send_text(USER_MID2, "secret")
    assert api.e2ee.encrypt_calls == 1  # sealed once
    assert api.e2ee.retry_calls == 1  # via send_with_retry (82 fallback)
    assert state["n"] == 2  # plain attempt + sealed retry
    assert isinstance(res, dict) and res.get("id") == "1"


def test_send_message_encrypt_true_seals_upfront(make_api):
    seen = {}

    def responder(method, url, kw):
        if url.endswith("sendMessage"):
            seen["body"] = json.loads(kw["data"])
            return enveloped({"id": "9"})
        return enveloped({})

    api = make_api(responder)
    api.e2ee = _FakeE2EE(api)
    api.send_message(
        {"to": USER_MID2, "text": "hi", "contentType": 0, "contentMetadata": {}}, encrypt=True
    )
    assert seen["body"][1].get("chunks")  # sealed before sending
    assert api.e2ee.encrypt_calls == 1
    assert api.e2ee.retry_calls == 1  # routed through send_with_retry


def test_decrypt_message_passthrough_for_plain(make_api):
    api = make_api()
    plain = {"text": "hello", "contentMetadata": {}}
    assert api.decrypt_message(plain) is plain  # not sealed -> unchanged


# --- 1:1 decrypt channel: peer vs own messages -------------------------------
class _DecryptBridge(FakeBridge):
    """FakeBridge plus the channel/decrypt calls the 1:1 decrypt path uses."""

    def __init__(self):
        super().__init__()
        self.channels: list[tuple[int, str]] = []  # (my_handle, peer_pub)

    def e2ee_create_channel_with_pubkey(self, my_handle, peer_pub_b64) -> int:
        self.channels.append((int(my_handle), peer_pub_b64))
        return 7000 + len(self.channels)

    def e2ee_decrypt_v2(self, channel, **kw) -> str:
        return base64.b64encode(json.dumps({"text": "recovered"}).encode()).decode()


def _decrypt_api(make_api, pubs: dict[str, str]):
    """A logged-in api whose ``getE2EEPublicKey`` serves ``pubs`` per mid."""

    def responder(method, url, kw):
        if url.endswith("getE2EEPublicKey"):
            mid, _ver, kid = json.loads(kw["data"])
            return enveloped({"keyData": pubs[mid], "keyId": kid})
        return enveloped({})

    api = make_api(responder, bridge=_DecryptBridge())
    mgr = api.e2ee
    mgr.my_mid, mgr.my_keys, mgr.latest_key_id = "Ume", {5312832: 11}, 5312832
    return api


def _sealed(frm: str, to: str, sender_kid: int, receiver_kid: int) -> dict:
    return {
        "to": to,
        "from": frm,
        "toType": 0,
        "contentType": 0,
        "contentMetadata": {"e2eeVersion": "2"},
        "chunks": fr.build_chunks(bytes(range(60)), sender_kid, receiver_kid),
    }


def test_decrypt_own_message_uses_recipient_pubkey(make_api):
    """Our own sealed messages read back from history must be keyed against the
    *recipient's* public key (the send-side ECDH counterparty), not our own
    (issue #2: own messages showed ``[encrypted]`` in chat logs)."""
    my_pub = base64.b64encode(b"M" * 32).decode()
    peer_pub = base64.b64encode(b"P" * 32).decode()
    api = _decrypt_api(make_api, {"Ume": my_pub, USER_MID2: peer_pub})
    out = api.decrypt_message(_sealed("Ume", USER_MID2, 5312832, 42))
    assert out["_decrypted"] and out["text"] == "recovered"
    # channel = ECDH(our key handle 11, the PEER's public key)
    assert api.transport.bridge.channels == [(11, peer_pub)]


def test_decrypt_peer_message_uses_sender_pubkey(make_api):
    """Messages from the peer stay keyed against the sender's public key."""
    my_pub = base64.b64encode(b"M" * 32).decode()
    peer_pub = base64.b64encode(b"P" * 32).decode()
    api = _decrypt_api(make_api, {"Ume": my_pub, USER_MID2: peer_pub})
    out = api.decrypt_message(_sealed(USER_MID2, "Ume", 42, 5312832))
    assert out["_decrypted"] and out["text"] == "recovered"
    # channel = ECDH(our key handle 11, the SENDER's public key)
    assert api.transport.bridge.channels == [(11, peer_pub)]


# --- sealed-media framing (ENC_KM / FILE_NAME, gL/EL parity) -----------------
def test_serialize_plaintext_media_key_material():
    msg = {
        "text": "hi",
        "contentType": 1,
        "contentMetadata": {"ENC_KM": "AAECAw==", "FILE_NAME": "pic.jpg"},
    }
    obj = json.loads(fr.serialize_plaintext(msg))
    assert obj["keyMaterial"] == "AAECAw=="  # gL: sealed INTO the plaintext
    assert obj["fileName"] == "pic.jpg"


def test_serialize_plaintext_without_enc_km_omits_media_fields():
    obj = json.loads(fr.serialize_plaintext({"text": "x", "contentMetadata": {}}))
    assert "keyMaterial" not in obj and "fileName" not in obj


def test_serialize_plaintext_replace_parse_error_dropped():
    # MR returns undefined on JSON.parse failure -> the key is dropped entirely
    obj = json.loads(
        fr.serialize_plaintext({"text": "x", "contentMetadata": {"REPLACE": "{bad"}})
    )
    assert "REPLACE" not in obj


def test_build_e2ee_message_deletes_enc_km_and_file_name():
    msg = {
        "to": USER_MID,
        "contentType": 1,
        "text": "hi",
        "contentMetadata": {"ENC_KM": "AAECAw==", "FILE_NAME": "pic.jpg"},
    }
    sealed = fr.build_e2ee_message(msg, ["a", "b", "c", "d", "e"], 2)
    cm = sealed["contentMetadata"]
    assert "ENC_KM" not in cm  # EL deletes ENC_KM/FILE_NAME after sealing
    assert "FILE_NAME" not in cm
    assert cm["e2eeVersion"] == "2"


def test_generate_enc_km():
    a = fr.generate_enc_km()
    assert len(base64.b64decode(a)) == 32  # NL: base64(32 random bytes)
    assert a != fr.generate_enc_km()


# --- sealed-media blob encryption (HKDF FileEncryption AES-CTR+HMAC) ---------
def test_derive_file_keys_shape():
    enc, mac, nonce = fr.derive_file_keys(bytes(range(32)))
    assert (len(enc), len(mac), len(nonce)) == (32, 32, 12)  # 608 bits total


def test_blob_encrypt_roundtrip():
    km = bytes(range(32))
    data = b"the quick brown fox jumps over the lazy dog" * 10
    blob = fr.encrypt_blob(km, data)
    assert len(blob) == len(data) + 32  # ciphertext || HMAC-SHA256 tag
    assert blob[: len(data)] != data  # actually encrypted
    assert fr.decrypt_blob(km, blob) == data


def test_blob_encrypt_fixed_vector():
    # Vector derived from THIS implementation and cross-checked against the
    # extracted bundle algorithm (_L/CL/TL: HKDF-SHA256 salt=empty
    # info=b"FileEncryption" 608 bits; AES-CTR counter=nonce||4 zero bytes with
    # a 32-bit increment; trailing HMAC-SHA256 tag).  The HKDF step was also
    # verified independently against a manual RFC 5869 implementation (empty
    # salt and HashLen zeros are equivalent under HMAC key padding, which is
    # what WebCrypto's empty-ArrayBuffer salt means).
    blob = fr.encrypt_blob(bytes(range(32)), b"okline")
    assert base64.b64encode(blob).decode() == (
        "OgtqusWMqZLZ+9pICPfDisnDS5CoOqmDISRWpW794GeFPdNFmWI="
    )


def test_blob_decrypt_tamper_raises():
    km = bytes(32)
    blob = bytearray(fr.encrypt_blob(km, b"payload"))
    blob[0] ^= 1
    with pytest.raises(ValueError):
        fr.decrypt_blob(km, bytes(blob))


def test_blob_decrypt_short_blob_raises():
    with pytest.raises(ValueError):
        fr.decrypt_blob(bytes(32), b"too short")


# --- xL control-char sanitizer (escape, not strip) ---------------------------
def test_deserialize_plaintext_escapes_and_preserves_controls():
    # C0 controls and U+0080-U+00FF are escaped to \u00XX (xL) so the parsed
    # VALUES are preserved; \x7f (DEL) is outside the bundle's escape set.
    # (The U+0080/U+00FF chars are proper 2-byte UTF-8, like TextDecoder yields.)
    raw = '{"text": "a\x01b\x1fc\x80d\xffd"}'.encode()
    assert fr.deserialize_plaintext(raw) == {"text": "a\x01b\x1fc\x80d\xffd"}


def test_deserialize_plaintext_preserves_tab_newline():
    raw = b'{"text": "a\tb\nc"}'
    assert fr.deserialize_plaintext(raw) == {"text": "a\tb\nc"}


def test_deserialize_plaintext_fallback_keeps_raw_text():
    out = fr.deserialize_plaintext(b"not json \x01")
    assert out == {"text": "not json \x01"}


# --- chunk-count strictness (dL/pL parity) -----------------------------------
def test_parse_chunks_short_yields_none_key_ids():
    ct = bytes(range(60))
    # key ids are only read when ALL 5 chunks are defined — never defaulted to 0
    ct2, sid, rid = fr.parse_chunks(fr.build_chunks(ct, 11, 22)[:4])
    assert ct2 == ct and sid is None and rid is None
    ct3, sid3, rid3 = fr.parse_chunks_v1(fr.build_chunks_v1(ct, 11, 22)[:4])
    assert ct3 == ct and sid3 is None and rid3 is None


# --- manager: encrypt gating --------------------------------------------------
def _mgr(api, my_mid="Ume"):
    mgr = api.e2ee
    mgr.my_mid, mgr.my_keys, mgr.latest_key_id = my_mid, {5312832: 11}, 5312832
    return mgr


def _pub(tag: str) -> str:
    return base64.b64encode(tag.encode() * 8).decode()


def test_encrypt_skips_already_sealed(make_api):
    api = make_api(bridge=FakeBridge())
    mgr = _mgr(api)
    sealed = {"to": USER_MID2, "chunks": ["a", "b", "c"], "contentMetadata": {}}
    assert mgr.encrypt(dict(sealed)) == sealed  # iL: never double-seal


def test_encrypt_skips_unsealable_content_type(make_api):
    api = make_api(bridge=FakeBridge())
    mgr = _mgr(api)
    msg = {
        "to": USER_MID2,
        "toType": 0,
        "contentType": int(ContentType.STICKER),
        "text": "x",
        "contentMetadata": {},
    }
    assert mgr.encrypt(msg) == msg  # not in the sL whitelist -> left plain


def test_encrypt_skips_excluded_allowed_type(make_api):
    api = make_api(bridge=FakeBridge())
    mgr = _mgr(api)
    mgr._peer_negotiation[USER_MID2] = {"specVersion": 2, "allowedTypes": [0]}
    msg = {
        "to": USER_MID2,
        "toType": 0,
        "contentType": int(ContentType.LOCATION),
        "location": {"latitude": 1, "longitude": 2},
        "contentMetadata": {},
    }
    assert mgr.encrypt(msg) == msg  # LOCATION not in negotiated allowedTypes


def test_encrypt_media_requires_v2_flow(make_api):
    def responder(method, url, kw):
        if url.endswith("determineMediaMessageFlow"):
            return enveloped({"flowMap": {"1": 1}})  # IMAGE -> V1
        return enveloped({})

    api = make_api(responder, bridge=FakeBridge())
    mgr = _mgr(api)
    msg = {
        "to": USER_MID2,
        "toType": 0,
        "contentType": int(ContentType.IMAGE),
        "contentMetadata": {"ENC_KM": "AAECAw=="},
    }
    assert mgr.encrypt(msg) == msg  # media flow V1 -> no sealing


def test_encrypt_media_seals_on_v2_flow(make_api):
    def responder(method, url, kw):
        if url.endswith("determineMediaMessageFlow"):
            return enveloped({"flowMap": {str(int(ContentType.IMAGE)): 2}})
        if url.endswith("negotiateE2EEPublicKey"):
            return enveloped(
                {"publicKey": {"keyData": _pub("P"), "keyId": 42}, "specVersion": 2}
            )
        return enveloped({})

    api = make_api(responder, bridge=FakeBridge())
    mgr = _mgr(api)
    sealed = mgr.encrypt(
        {
            "to": USER_MID2,
            "toType": 0,
            "contentType": int(ContentType.IMAGE),
            "contentMetadata": {"ENC_KM": "AAECAw==", "FILE_NAME": "p.jpg"},
        }
    )
    assert sealed["chunks"]
    assert sealed["contentMetadata"]["e2eeVersion"] == "2"
    assert "ENC_KM" not in sealed["contentMetadata"]


def test_encrypt_media_best_effort_when_flow_unavailable(make_api):
    def responder(method, url, kw):
        if url.endswith("determineMediaMessageFlow"):
            return FakeResp(500, {"message": "boom"})
        if url.endswith("negotiateE2EEPublicKey"):
            return enveloped(
                {"publicKey": {"keyData": _pub("P"), "keyId": 42}, "specVersion": 2}
            )
        return enveloped({})

    api = make_api(responder, bridge=FakeBridge())
    mgr = _mgr(api)
    sealed = mgr.encrypt(
        {
            "to": USER_MID2,
            "toType": 0,
            "contentType": int(ContentType.AUDIO),
            "contentMetadata": {},
        }
    )
    assert sealed["chunks"]  # lookup failed -> proceed with sealing


# --- manager: V1/V2 send by negotiated specVersion ---------------------------
def _send_api(make_api, spec_version):
    def responder(method, url, kw):
        if url.endswith("negotiateE2EEPublicKey"):
            return enveloped(
                {
                    "publicKey": {"keyData": _pub("P"), "keyId": 42},
                    "specVersion": spec_version,
                    "allowedTypes": [0, 1],
                }
            )
        return enveloped({})

    api = make_api(responder, bridge=FakeBridge())
    return api, _mgr(api)


def test_encrypt_user_v1_when_negotiated_spec_version_1(make_api):
    api, mgr = _send_api(make_api, spec_version=1)
    text = "a v1 message that is comfortably long enough for chunking"
    sealed = mgr.encrypt(
        {
            "to": USER_MID2,
            "toType": 0,
            "contentType": 0,
            "text": text,
            "contentMetadata": {},
        }
    )
    assert sealed["contentMetadata"]["e2eeVersion"] == "1"
    # mL framing: [salt8, body, tag16, sidBE, ridBE]; FakeBridge echoes the
    # plaintext as "ciphertext" so parsing it back must yield the plaintext
    ct, sid, rid = fr.parse_chunks_v1(sealed["chunks"])
    assert sid == 5312832 and rid == 42
    assert json.loads(ct.decode()) == {"text": text}
    api.close()


def test_encrypt_user_v2_default_when_spec_version_absent(make_api):
    api, mgr = _send_api(make_api, spec_version=None)
    text = "a v2 message that is comfortably long enough for chunking"
    sealed = mgr.encrypt(
        {
            "to": USER_MID2,
            "toType": 0,
            "contentType": 0,
            "text": text,
            "contentMetadata": {},
        }
    )
    assert sealed["contentMetadata"]["e2eeVersion"] == "2"
    ct, sid, rid = fr.parse_chunks(sealed["chunks"])
    assert sid == 5312832 and rid == 42
    assert json.loads(ct.decode()) == {"text": text}
    api.close()


# --- manager: decrypt finish (yL parity) --------------------------------------
def test_finish_decrypt_restores_replace_and_media_metadata():
    api = build_api(bridge=FakeBridge())
    mgr = api.e2ee
    sealed = {
        "to": USER_MID2,
        "chunks": ["a", "b", "c", "d", "e"],
        "contentMetadata": {"e2eeVersion": "2"},
    }
    pt = json.dumps(
        {
            "text": "hi",
            "REPLACE": {"seq": 3},
            "location": {"latitude": 1},
            "keyMaterial": "AAECAw==",
            "fileName": "pic.jpg",
        }
    ).encode()
    out = mgr._finish_decrypt(sealed, base64.b64encode(pt).decode())
    assert out["text"] == "hi"
    assert out["location"] == {"latitude": 1}
    assert out["chunks"] == []  # yL clears the sealed chunks
    cm = out["contentMetadata"]
    assert cm["REPLACE"] == {"seq": 3}
    assert cm["ENC_KM"] == "AAECAw=="
    assert cm["FILE_NAME"] == "pic.jpg"
    api.close()


# --- manager: registerGroupKey ------------------------------------------------
GSK_STRUCT = {
    "keyVersion": 1,
    "groupKeyId": 777,
    "creator": "Ume",
    "creatorKeyId": 5312832,
    "receiver": "Ume",
    "receiverKeyId": 5312832,
    "encryptedSharedKey": "AAECAw==",
    "allowedTypes": [0, 1],
    "specVersion": 2,
}


def _group_api(make_api, *, last_key_error=None, seen=None):
    def responder(method, url, kw):
        if url.endswith("getLastE2EEPublicKeys"):
            return enveloped(
                {
                    "Ume": {"keyData": _pub("M"), "keyId": 5312832},
                    USER_MID2: {"keyData": _pub("P"), "keyId": 42},
                }
            )
        if url.endswith("registerE2EEGroupKey"):
            seen["register"] = json.loads(kw["data"])
            return enveloped(GSK_STRUCT)
        if url.endswith("getLastE2EEGroupSharedKey"):
            if last_key_error is not None:
                return last_key_error
            return enveloped(GSK_STRUCT)
        if url.endswith("getE2EEPublicKey"):
            _mid, _ver, kid = json.loads(kw["data"])
            return enveloped({"keyData": _pub("M"), "keyId": kid})
        return enveloped({})

    api = make_api(responder, bridge=FakeBridge())
    return api, _mgr(api)


def test_register_group_key_full_flow(make_api):
    seen: dict = {}
    api, mgr = _group_api(make_api, seen=seen)
    _handle, gkid = mgr.register_group_key(GROUP_MID)
    # wire order: [version=1, chatMid, memberMids, keyIds, wrappedKeys]
    args = seen["register"]
    assert args[0] == 1
    assert args[1] == GROUP_MID
    assert set(args[2]) == {"Ume", USER_MID2}
    assert args[3] == [5312832, 42]  # keyIds follow the member order
    assert len(args[4]) == 2  # one wrapped key per member
    # register response unwrapped + cached
    assert gkid == 777
    assert (GROUP_MID, 777) in mgr._group_keys
    assert mgr._group_negotiation[GROUP_MID]["specVersion"] == 2
    api.close()


def test_group_key_not_found_triggers_register(make_api):
    seen: dict = {}
    err = FakeResp(
        400,
        {
            "code": 10051,
            "message": "RESPONSE_ERROR",
            "data": {"code": int(ErrorCode.NOT_FOUND), "reason": "not found"},
        },
    )
    api, mgr = _group_api(make_api, last_key_error=err, seen=seen)
    _handle, gkid = mgr._group_key_handle(GROUP_MID, None)
    assert "register" in seen  # renewLatestGroupKey's NOT_FOUND fallback
    assert gkid == 777 and (GROUP_MID, 777) in mgr._group_keys
    api.close()


def test_group_encrypt_uses_registered_key(make_api):
    seen: dict = {}
    api, mgr = _group_api(make_api, seen=seen)
    msg = {
        "to": GROUP_MID,
        "toType": 2,
        "contentType": 0,
        "text": "first encrypted message in this group is long enough",
        "contentMetadata": {},
    }
    sealed = mgr.encrypt(msg)
    assert sealed["chunks"] and sealed["contentMetadata"]["e2eeVersion"] == "2"
    ct, _sid, rid = fr.parse_chunks(sealed["chunks"])
    assert rid == 777  # receiver key id = the registered group key id
    assert json.loads(ct.decode())["text"] == msg["text"]
    api.close()


# --- manager: send_with_retry (codes 84/86/87/88/90 + 99) ---------------------
def _retry_api(make_api, *, first_code, seen):
    def responder(method, url, kw):
        if url.endswith("negotiateE2EEPublicKey"):
            seen["negotiations"] = seen.get("negotiations", 0) + 1
            return enveloped(
                {"publicKey": {"keyData": _pub("P"), "keyId": 42}, "specVersion": 2}
            )
        if url.endswith("sendMessage"):
            seen["sends"] = seen.get("sends", 0) + 1
            body = json.loads(kw["data"])
            if seen["sends"] == 1 and first_code is not None:
                return FakeResp(
                    400,
                    {
                        "code": 10051,
                        "message": "RESPONSE_ERROR",
                        "data": {"code": first_code, "reason": "e2ee"},
                    },
                )
            return enveloped({"id": "7", "chunks": body[1].get("chunks")})
        if url.endswith("getLastE2EEPublicKeys"):
            return enveloped({"Ume": {"keyData": _pub("M"), "keyId": 5312832}})
        if url.endswith("registerE2EEGroupKey"):
            seen["register"] = json.loads(kw["data"])
            return enveloped(GSK_STRUCT)
        if url.endswith("getLastE2EEGroupSharedKey"):
            return enveloped(GSK_STRUCT)
        if url.endswith("getE2EEPublicKey"):
            _mid, _ver, kid = json.loads(kw["data"])
            return enveloped({"keyData": _pub("M"), "keyId": kid})
        return enveloped({})

    api = make_api(responder, bridge=FakeBridge())
    return api, _mgr(api)


def test_send_with_retry_on_code_84(make_api):
    seen: dict = {}
    api, mgr = _retry_api(make_api, first_code=84, seen=seen)
    res = mgr.send_with_retry(
        {
            "to": USER_MID2,
            "toType": 0,
            "contentType": 0,
            "text": "please retry this sealed send",
            "contentMetadata": {},
        }
    )
    assert res.get("id") == "7"
    assert seen["sends"] == 2  # reset + retry once
    assert seen["negotiations"] == 2  # cached channel was dropped
    api.close()


def test_send_with_retry_on_code_86(make_api):
    seen: dict = {}
    api, mgr = _retry_api(make_api, first_code=86, seen=seen)
    res = mgr.send_with_retry(
        {
            "to": USER_MID2,
            "toType": 0,
            "contentType": 0,
            "text": "please retry this sealed send",
            "contentMetadata": {},
        }
    )
    assert res.get("id") == "7" and seen["sends"] == 2
    api.close()


def test_send_with_retry_code_99_group_reregisters(make_api):
    seen: dict = {}
    api, mgr = _retry_api(make_api, first_code=99, seen=seen)
    res = mgr.send_with_retry(
        {
            "to": GROUP_MID,
            "toType": 2,
            "contentType": 0,
            "text": "group message that must be retried after reregister",
            "contentMetadata": {},
        }
    )
    assert res.get("id") == "7"
    assert seen["sends"] == 2
    assert "register" in seen  # E2EE_RECREATE_GROUP_KEY -> registerGroupKey
    api.close()


def test_send_with_retry_propagates_other_errors(make_api):
    seen: dict = {}
    api, mgr = _retry_api(make_api, first_code=5, seen=seen)
    with pytest.raises(LineApiError):
        mgr.send_with_retry(
            {
                "to": USER_MID2,
                "toType": 0,
                "contentType": 0,
                "text": "this error is not retryable",
                "contentMetadata": {},
            }
        )
    assert seen["sends"] == 1
    api.close()


def test_send_with_retry_code_122_resets_and_reraises(make_api):
    """REFRESH_MEDIA_FLOW (122): the cached negotiation is dropped
    (resetE2eeInfo) and the error re-raised immediately — no retry, so the
    next send re-negotiates the media flow instead of reusing the stale one."""
    seen: dict = {}
    api, mgr = _retry_api(make_api, first_code=122, seen=seen)
    with pytest.raises(LineApiError) as ei:
        mgr.send_with_retry(
            {
                "to": USER_MID2,
                "toType": 0,
                "contentType": 0,
                "text": "refresh the media flow",
                "contentMetadata": {},
            }
        )
    assert ei.value.code == 122
    assert seen["sends"] == 1  # reset + rethrow — never retried here
    # the cached peer channel/negotiation was dropped
    assert USER_MID2 not in mgr._peer_channels
    assert USER_MID2 not in mgr._peer_negotiation
    api.close()


def test_send_with_retry_budget_is_three_retries(make_api):
    """The extension's sB wrapper retries up to 3 times (4 attempts) before
    surfacing the error — a code-84 that keeps failing three times and then
    succeeding is recovered transparently."""
    seen: dict = {}

    def fail_thrice(method, url, kw):
        if url.endswith("negotiateE2EEPublicKey"):
            return enveloped(
                {"publicKey": {"keyData": _pub("P"), "keyId": 42}, "specVersion": 2}
            )
        if url.endswith("sendMessage"):
            seen["sends"] = seen.get("sends", 0) + 1
            if seen["sends"] <= 3:
                return FakeResp(
                    400,
                    {
                        "code": 10051,
                        "message": "RESPONSE_ERROR",
                        "data": {"code": 84, "reason": "e2ee"},
                    },
                )
            return enveloped({"id": "7"})
        if url.endswith("getLastE2EEPublicKeys"):
            return enveloped({"Ume": {"keyData": _pub("M"), "keyId": 5312832}})
        return enveloped({})

    api = make_api(fail_thrice, bridge=FakeBridge())
    mgr = _mgr(api)
    res = mgr.send_with_retry(
        {
            "to": USER_MID2,
            "toType": 0,
            "contentType": 0,
            "text": "third time lucky",
            "contentMetadata": {},
        }
    )
    assert res.get("id") == "7"
    assert seen["sends"] == 4  # original + 3 transparent retries
    api.close()


def test_send_with_retry_propagates_after_budget_exhausted(make_api):
    seen: dict = {}

    def always_fail(method, url, kw):
        if url.endswith("sendMessage"):
            seen["sends"] = seen.get("sends", 0) + 1
            return FakeResp(
                400,
                {
                    "code": 10051,
                    "message": "RESPONSE_ERROR",
                    "data": {"code": 84, "reason": "e2ee"},
                },
            )
        return enveloped({"publicKey": {"keyData": _pub("P"), "keyId": 42}, "specVersion": 2})

    api = make_api(always_fail, bridge=FakeBridge())
    mgr = _mgr(api)
    with pytest.raises(LineApiError):
        mgr.send_with_retry(
            {
                "to": USER_MID2,
                "toType": 0,
                "contentType": 0,
                "text": "failing forever means the error surfaces",
                "contentMetadata": {},
            }
        )
    assert seen["sends"] == 4  # original + the 3-retry budget (sB's r>=3 throw)
    api.close()


# --- send_message <-> send_with_retry wiring ---------------------------------
def test_send_message_encrypt_true_retries_on_code_84(make_api):
    """``send_message(..., encrypt=True)`` routes through send_with_retry, so a
    code-84 rejection of the sealed send is recovered (reset + retry once)."""
    seen: dict = {}
    api, _unused_mgr = _retry_api(make_api, first_code=84, seen=seen)
    res = api.send_message(
        {
            "to": USER_MID2,
            "toType": 0,
            "contentType": 0,
            "text": "sealed via send_message(encrypt=True)",
            "contentMetadata": {},
        },
        encrypt=True,
    )
    assert res.get("id") == "7"
    assert seen["sends"] == 2  # sealed send + retry after reset
    assert seen["negotiations"] == 2  # cached channel was dropped
    api.close()


def test_send_with_retry_no_loop_when_gating_declines(make_api):
    """When encrypt gating declines to seal (allowedTypes excludes the content
    type) the plain send's code-82 rejection must surface instead of bouncing
    between send_message's 82-fallback and send_with_retry forever."""
    seen: dict = {}
    msg = {
        "to": USER_MID2,
        "toType": 0,
        "contentType": 0,
        "text": "cannot be sealed for this peer",
        "contentMetadata": {},
    }

    def responder(method, url, kw):
        if url.endswith("negotiateE2EEPublicKey"):
            # allowedTypes excludes contentType 0 (NONE) -> gating declines
            return enveloped(
                {
                    "publicKey": {"keyData": _pub("P"), "keyId": 42},
                    "specVersion": 2,
                    "allowedTypes": [1],
                }
            )
        if url.endswith("sendMessage"):
            seen["sends"] = seen.get("sends", 0) + 1
            body = json.loads(kw["data"])
            assert not body[1].get("chunks")  # gating declined -> never sealed
            return FakeResp(
                400,
                {
                    "code": 10051,
                    "message": "RESPONSE_ERROR",
                    "data": {"code": 82, "reason": "can not send using plain mode"},
                },
            )
        return enveloped({})

    api = make_api(responder, bridge=FakeBridge())
    mgr = _mgr(api)
    # Prime the cached negotiation: the first encrypt seals (allowedTypes not
    # cached yet) and caches allowedTypes=[1]; from then on contentType 0 is
    # outside the negotiated allowedTypes and encrypt() leaves messages plain.
    assert mgr.encrypt(dict(msg)).get("chunks")
    with pytest.raises(LineApiError) as ei:
        api.send_message(dict(msg), encrypt=True)
    assert ei.value.code == 82  # surfaces; no RecursionError
    assert seen["sends"] == 1  # exactly one plain attempt
    api.close()


# --- download_sealed_media (the extension's $P/GD flow as one call) ---------
class _ObsBytesResp(FakeResp):
    """FakeResp tolerating a raw-bytes body (OBS downloads)."""

    def __init__(self, status: int, body):
        if isinstance(body, (bytes, bytearray)):
            self.status_code = status
            self.content = bytes(body)
            self.text = ""
            self.headers = {"content-type": "application/octet-stream"}
        else:
            super().__init__(status, body)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


class _MediaDecryptBridge(_DecryptBridge):
    """Decrypts to a plaintext carrying sealed-media key material."""

    def e2ee_decrypt_v2(self, channel, **kw) -> str:
        return base64.b64encode(
            json.dumps({"keyMaterial": ENC_KM, "fileName": "pic.jpg"}).encode()
        ).decode()


ENC_KM = base64.b64encode(b"K" * 32).decode()
_KM = b"K" * 32
_PLAIN = b"\xff\xd8\xff" + b"JPEGDATA" * 40
_BLOB = fr.encrypt_blob(_KM, _PLAIN)


def _media_api(make_api):
    peer_pub = base64.b64encode(b"P" * 32).decode()

    def responder(method, url, kw):
        if url.endswith("getE2EEPublicKey"):
            _mid, _ver, kid = json.loads(kw["data"])
            return enveloped({"keyData": peer_pub, "keyId": kid})
        if "/r/talk/emi/OID1" in url:
            if url.endswith("object_info.obs"):
                return FakeResp(200, {"size": len(_BLOB), "mime": "image/jpeg"})
            return _ObsBytesResp(200, _BLOB)
        return enveloped({})

    api = make_api(responder, bridge=_MediaDecryptBridge())
    mgr = api.e2ee
    mgr.my_mid, mgr.my_keys, mgr.latest_key_id = "Ume", {5312832: 11}, 5312832
    api.transport.tokens.encrypted_access_tokens["2"] = "ENC1"  # OBS auth cache
    return api


def _sealed_media() -> dict:
    msg = _sealed(USER_MID2, "Ume", 42, 5312832)
    msg["contentType"] = 1
    msg["id"] = "MSG-9"
    msg["contentMetadata"].update({"SID": "emi", "OID": "OID1"})
    return msg


def test_download_sealed_media_roundtrip(make_api):
    api = _media_api(make_api)
    out = api.e2ee.download_sealed_media(_sealed_media())
    assert out == _PLAIN


def test_download_sealed_media_with_info(make_api):
    api = _media_api(make_api)
    plain, info = api.e2ee.download_sealed_media(_sealed_media(), info=True)
    assert plain == _PLAIN
    assert info == {"size": len(_BLOB), "mime": "image/jpeg"}
    # the object_info call carried the X-Talk-Meta built from the message id

    calls = [c for c in api.transport.session.calls if "object_info.obs" in c["url"]]
    assert calls and "X-Talk-Meta" in calls[0]["headers"]


def test_download_sealed_media_sends_talk_meta_and_sid_oid(make_api):
    api = _media_api(make_api)
    api.e2ee.download_sealed_media(_sealed_media())
    dl = [c for c in api.transport.session.calls if "/r/talk/emi/OID1" in c["url"]]
    assert dl and "X-Talk-Meta" in dl[0]["headers"]


def test_download_sealed_media_rejects_non_media(make_api):
    api = _media_api(make_api)
    msg = _sealed(USER_MID2, "Ume", 42, 5312832)  # text message, no SID/OID
    with pytest.raises(LineApiError) as ei:
        api.e2ee.download_sealed_media(msg)
    assert "ENC_KM/OID" in str(ei.value)


# --- export cache: imported keys cannot be re-exported by the WASM -----------
class _NoReExportBridge(FakeBridge):
    """Live behaviour: exporting an *imported* key raises 'illegal operation'."""

    def __init__(self):
        super().__init__()
        self.exported: set[int] = set()
        self.imported: set[int] = set()

    def e2ee_load_key(self, exported_b64: str) -> int:
        h = super().e2ee_load_key(exported_b64)
        self.imported.add(h)
        return h

    def e2ee_export_key(self, handle: int) -> str:
        if handle in self.imported:
            raise Exception("Failed to export secure key: illegal operation")
        self.exported.add(handle)
        return super().e2ee_export_key(handle)


def test_loaded_key_reexports_from_cache(make_api):
    """load_from_export must remember the blobs: the WASM refuses to re-export
    an imported key (live-verified — this broke cross-session E2EE reload)."""
    api = make_api(None, bridge=_NoReExportBridge())
    mgr = api.e2ee
    blob = base64.b64encode(b"exported:99").decode()  # FakeBridge export format
    assert mgr.load_from_export({"keys": {"42": blob}, "latestKeyId": 42})
    assert mgr.my_keys == {42: 99}
    # re-export serves the ORIGINAL blob from the cache — no bridge call
    out = mgr.export_keys()
    assert out["keys"] == {"42": blob}
    # and the bridge was never asked to export (nothing in its exported set)
    assert api.transport.bridge.exported == set()


def test_fresh_login_keys_reexport_through_bridge(make_api):
    """Freshly unwrapped keys export through the bridge as before."""
    api = make_api(None, bridge=_NoReExportBridge())
    mgr = api.e2ee
    mgr.my_keys, mgr.latest_key_id = {7: 7}, 7
    out = mgr.export_keys()
    assert set(out["keys"]) == {"7"}
    # ...and the produced blob is cached, so a second export is also stable
    again = mgr.export_keys()
    assert again == out

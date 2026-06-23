"""Tests for the E2EE framing (pure-Python) and the auto-encrypt retry.

The actual crypto runs in the WASM bridge (live-only); here we test the framing
round-trips and that send_message seals + retries on a code-82 rejection using a
fake E2EE manager.
"""

from __future__ import annotations

import base64
import json

from conftest import USER_MID, USER_MID2, FakeResp, build_api, enveloped

from okline import e2ee_crypto as fr


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

    def __init__(self):
        self.encrypt_calls = 0

    def is_ready(self):
        return True

    def encrypt(self, message):
        self.encrypt_calls += 1
        sealed = dict(message)
        sealed["chunks"] = ["c1", "c2", "c3", "c4", "c5"]
        sealed["text"] = None
        return sealed


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
    api.e2ee = _FakeE2EE()  # pretend E2EE is ready
    res = api.send_text(USER_MID2, "secret")
    assert api.e2ee.encrypt_calls == 1  # sealed once
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
    api.e2ee = _FakeE2EE()
    api.send_message(
        {"to": USER_MID2, "text": "hi", "contentType": 0, "contentMetadata": {}}, encrypt=True
    )
    assert seen["body"][1].get("chunks")  # sealed before sending
    assert api.e2ee.encrypt_calls == 1


def test_decrypt_message_passthrough_for_plain(make_api):
    api = make_api()
    plain = {"text": "hello", "contentMetadata": {}}
    assert api.decrypt_message(plain) is plain  # not sealed -> unchanged

"""Tests for the E2EE framing (pure-Python) and the auto-encrypt retry.

The actual crypto runs in the WASM bridge (live-only); here we test the framing
round-trips and that send_message seals + retries on a code-82 rejection using a
fake E2EE manager.
"""

from __future__ import annotations

import base64
import json

from conftest import USER_MID, USER_MID2, build_api, enveloped
from conftest import FakeResp

from okline import e2ee_crypto as fr


# --- framing ---------------------------------------------------------------
def test_key_id_byte_roundtrip():
    for n in (0, 1, 255, 256, 70000, 5312832, 0xFFFFFFFF):
        b = fr.key_id_to_bytes(n)
        assert len(b) == 4 and fr.key_id_from_bytes(b) == n


def test_chunks_roundtrip():
    ct = bytes(range(60))                      # any ciphertext >= 28 bytes
    chunks = fr.build_chunks(ct, sender_key_id=11, receiver_key_id=22)
    assert len(chunks) == 5
    # the wire order is [head16, body, tag12, sidBE, ridBE]
    assert base64.b64decode(chunks[0]) == ct[0:16]
    assert base64.b64decode(chunks[2]) == ct[16:28]
    assert base64.b64decode(chunks[1]) == ct[28:]
    ct2, sid, rid = fr.parse_chunks(chunks)
    assert ct2 == ct and sid == 11 and rid == 22


def test_plaintext_roundtrip():
    msg = {"text": "สวัสดี 👋", "contentType": 0, "contentMetadata": {}}
    pt = fr.serialize_plaintext(msg)
    assert json.loads(pt.decode()) == {"text": "สวัสดี 👋"}
    back = fr.deserialize_plaintext(pt)
    assert back["text"] == "สวัสดี 👋"


def test_build_e2ee_message():
    msg = {"to": USER_MID, "text": "hi", "location": {"x": 1}, "from": "Ume",
           "contentType": 0, "contentMetadata": {"REPLACE": "x"}, "toType": 0}
    sealed = fr.build_e2ee_message(msg, ["a", "b", "c", "d", "e"], 2)
    # EL() drops text/location/from entirely (not text:null) — sending them 500s
    assert "text" not in sealed
    assert "location" not in sealed
    assert "from" not in sealed
    assert sealed["chunks"] == ["a", "b", "c", "d", "e"]
    assert sealed["contentMetadata"]["e2eeVersion"] == "2"
    assert "REPLACE" not in sealed["contentMetadata"]      # moved into ciphertext


def test_is_e2ee_message():
    assert fr.is_e2ee_message({"chunks": ["a", "b", "c", "d", "e"],
                               "contentMetadata": {"e2eeVersion": "2"}})
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
    err_body = {"code": 10051, "message": "RESPONSE_ERROR",
                "data": {"code": 82, "reason": "can not send using plain mode"}}

    def responder(method, url, kw):
        if url.endswith("sendMessage"):
            body = json.loads(kw["data"])
            sealed = bool(body[1].get("chunks"))
            state["n"] += 1
            if not sealed:
                return FakeResp(400, err_body)       # plain rejected
            return enveloped({"id": "1", "chunks": body[1]["chunks"]})
        return enveloped({})

    api = make_api(responder)
    api.e2ee = _FakeE2EE()                            # pretend E2EE is ready
    res = api.send_text(USER_MID2, "secret")
    assert api.e2ee.encrypt_calls == 1               # sealed once
    assert state["n"] == 2                            # plain attempt + sealed retry
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
    api.send_message({"to": USER_MID2, "text": "hi", "contentType": 0,
                      "contentMetadata": {}}, encrypt=True)
    assert seen["body"][1].get("chunks")             # sealed before sending
    assert api.e2ee.encrypt_calls == 1


def test_decrypt_message_passthrough_for_plain(make_api):
    api = make_api()
    plain = {"text": "hello", "contentMetadata": {}}
    assert api.decrypt_message(plain) is plain       # not sealed -> unchanged

"""Offline tests for the special (non-Thrift) endpoints: SSE query params /
revision tracking, LF1/JQ long-poll constants, ``/api/lan/notice``, the OBS
auth branches (FD headerMapper), the X-Talk-Meta thrift blob, the
``.obs`` metadata endpoints and the timeline/pageinfo REST helpers.

Everything runs against :class:`conftest.FakeSession` — no network, no Node.
"""

from __future__ import annotations

import base64
import json

import pytest
from conftest import FakeResp, FakeSession, enveloped

from okline.exceptions import LineApiError
from okline.obs import ObsClient, build_talk_meta
from okline.operations import Operation, OperationReceiver
from okline.transport import APP_VERSION, LineConfig, Tokens, Transport

OBS_BASE = "https://obs.line-apps.com"
LEGY_BASE = "https://legy-jp.line-apps.com"
GW_BASE = "https://line-chrome-gw.line-apps.com"
APP_HDR = "CHROMEOS\t3.7.2\tChrome_OS\t"

# The acquireEncryptedAccessToken blob whose rows[1][0] is the token.
ENC_BLOB = "hdr\x1eENC1\x1ftail"


class FakeObsResp(FakeResp):
    """FakeResp + raise_for_status (raw OBS downloads call it)."""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


class FakeSSEResp:
    """A minimal streamed ``text/event-stream`` response."""

    def __init__(self, lines: list[str], status: int = 200) -> None:
        self.status_code = status
        self._lines = lines
        self.headers = {"content-type": "text/event-stream"}
        self.closed = False

    def iter_lines(self, decode_unicode: bool = False):
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


def make_transport(responder, **cfg_kw) -> Transport:
    cfg = LineConfig(enable_hmac=False, **cfg_kw)
    return Transport(cfg, Tokens(access_token="TKN"), session=FakeSession(responder))


def calls_of(session: FakeSession, fragment: str) -> list[dict]:
    return [c for c in session.calls if fragment in c["url"]]


# ---------------------------------------------------------------------------
# SSE: query params, minimal headers, revision tracking
# ---------------------------------------------------------------------------
SSE_LINES = [
    "event: ping",
    "data: {}",
    "",
    'data: {"operations": [{"revision": 2, "type": 25}]}',
    "",
    'data: {"operations": [{"revision": 3, "type": 25}, {"revision": 2, "type": 25}]}',
    "",
    "event: fullSync",
    'data: {"reasons": [], "nextRevision": 7}',
    "",
]


def _sse_responder(sse_resp=None):
    def responder(method, url, kw):
        if url.endswith("getLastOpRevision"):
            return enveloped(12345)
        if url.endswith("/api/operation/receive"):
            return sse_resp or FakeSSEResp(SSE_LINES)
        return enveloped({})

    return responder


def test_sse_query_params_and_minimal_headers():
    t = make_transport(_sse_responder())
    rx = OperationReceiver(t)
    params = rx._sse_query(None)
    assert params["version"] == APP_VERSION == "3.7.2"
    assert params["localRev"] == 12345  # seeded via getLastOpRevision
    assert params["language"] == "en_US"
    assert params["lastPartialFullSyncs"] == "{}"  # JSON.stringify({})
    assert "fullSyncRequestReason" not in params
    assert "legyHost" not in params
    # the seeding call actually happened, exactly once
    assert len(calls_of(t.session, "getLastOpRevision")) == 1

    # full stream: minimal header set + auth only
    events = list(rx.stream(reconnect=False))
    assert events
    sse_call = calls_of(t.session, "/api/operation/receive")[0]
    h = sse_call["headers"]
    assert h["accept"] == "text/event-stream"
    assert h["cache-control"] == "no-cache"
    assert h["X-Line-Access"] == "TKN"
    # documented deviation: header auth (no cookies in Python) but nothing else
    for absent in ("X-LAL", "X-Line-Chrome-Version", "X-Line-Application"):
        assert absent not in h, f"{absent} should not be on the SSE request"
    assert sse_call["params"]["localRev"] == 12345


def test_sse_preseeded_local_rev_skips_get_last_op_revision():
    t = make_transport(_sse_responder())
    rx = OperationReceiver(t, local_rev=555)
    assert rx._sse_query(None)["localRev"] == 555
    assert not calls_of(t.session, "getLastOpRevision")


def test_sse_query_extras():
    t = make_transport(lambda m, u, kw: enveloped(1), locale="ja-JP")
    t.config.legy_host = "legy-backup.line-apps.com"  # duck-typed config field
    rx = OperationReceiver(t, local_rev=9)
    params = rx._sse_query("INITIAL")
    assert params["language"] == "ja_JP"
    assert params["fullSyncRequestReason"] == "INITIAL"
    assert params["legyHost"] == "legy-backup.line-apps.com"


def test_iter_operations_tracks_and_dedups_revisions():
    t = make_transport(_sse_responder())
    rx = OperationReceiver(t, local_rev=1)  # pre-seeded cursor
    ops = list(rx.iter_operations(reconnect=False))
    # revision 2 is re-delivered after revision 3 -> dropped as stale
    assert [op.revision for op in ops] == [2, 3]
    # fullSync's nextRevision advanced the cursor past everything
    assert rx.local_rev == 7
    assert rx.last_partial_full_syncs == {}  # reset after the open


def test_sse_reconnect_resends_tracked_local_rev():
    # first stream ends after one op; the reconnect must carry its revision
    lines = ['data: {"operations": [{"revision": 42, "type": 25}]}', ""]
    t = make_transport(_sse_responder(FakeSSEResp(lines)))
    rx = OperationReceiver(t, local_rev=1)
    ops = list(rx.iter_operations(reconnect=False))
    assert [op.revision for op in ops] == [42]
    assert rx._sse_query(None)["localRev"] == 42


def test_partial_full_sync_updates_cursor():
    t = make_transport(
        _sse_responder(
            FakeSSEResp(
                [
                    "event: partialFullSync",
                    'data: {"targetCategories": {}, "nextRevision": 99}',
                    "",
                ]
            )
        )
    )
    rx = OperationReceiver(t, local_rev=1)
    assert list(rx.iter_operations(reconnect=False)) == []
    assert rx.local_rev == 99


# ---------------------------------------------------------------------------
# long-poll (LF1/JQ): PIN-verification constants
# ---------------------------------------------------------------------------
def test_long_poll_lst_defaults():
    t = make_transport(lambda m, u, kw: enveloped({"result": {}}))
    rx = OperationReceiver(t)
    rx.long_poll("123456", endpoint="LF1")
    h = t.session.last["headers"]
    assert h["X-Line-Session-ID"] == "123456"
    assert h["X-LST"] == "110000"  # checkPinCodeVerifiedForEmailWithE2EE
    rx.long_poll("123456", endpoint="JQ")
    assert t.session.last["headers"]["X-LST"] == "180000"  # checkPinCodeVerifiedForEmail
    # explicit timeout still wins
    rx.long_poll("123456", endpoint="LF1", timeout_ms=60000)
    assert t.session.last["headers"]["X-LST"] == "60000"


def test_long_poll_returns_decoded_body():
    t = make_transport(lambda m, u, kw: enveloped({"result": {"verifier": "V"}}))
    rx = OperationReceiver(t)
    assert rx.long_poll("PIN") == {"result": {"verifier": "V"}}


# ---------------------------------------------------------------------------
# /api/lan/notice
# ---------------------------------------------------------------------------
def test_lan_notice_params_and_paging_shape():
    def responder(method, url, kw):
        assert url == GW_BASE + "/api/lan/notice"
        if kw["params"].get("nextSeq") == 5:
            return enveloped({"documents": [{"id": "b"}]})
        return enveloped({"documents": [{"id": "a"}], "nextSeq": 5})

    t = make_transport(responder)
    rx = OperationReceiver(t)
    page = rx.lan_notice("en", "JP")
    assert page == {"documents": [{"id": "a"}], "nextSeq": 5}
    assert t.session.last["params"] == {"lang": "en", "country": "JP", "includeBody": True}
    # follow the cursor
    assert rx.lan_notice("en", "JP", 5) == {"documents": [{"id": "b"}]}
    assert t.session.last["params"]["nextSeq"] == 5


# ---------------------------------------------------------------------------
# X-Talk-Meta: exact thrift bytes (the bundle's lB)
# ---------------------------------------------------------------------------
def test_build_talk_meta_exact_bytes():
    # documented sequence: writeByte(11), writeI16(4), writeI32(len),
    # bytes(messageId), writeByte(15), writeI16(27), writeByte(12),
    # writeI32(0), writeByte(0) -> base64(json({"message": base64(bytes)}))
    mid = b"MSG-123"
    thrift = (
        b"\x0b"
        + (4).to_bytes(2, "big")
        + len(mid).to_bytes(4, "big")
        + mid
        + b"\x0f"
        + (27).to_bytes(2, "big")
        + b"\x0c"
        + (0).to_bytes(4, "big")
        + b"\x00"
    )
    expected = base64.b64encode(b'{"message":"' + base64.b64encode(thrift) + b'"}').decode(
        "ascii"
    )
    assert build_talk_meta("MSG-123") == expected
    # and the value round-trips to the same struct
    outer = json.loads(base64.b64decode(build_talk_meta("MSG-123")))
    assert list(outer) == ["message"]
    assert base64.b64decode(outer["message"]) == thrift


# ---------------------------------------------------------------------------
# OBS auth branches (the extension's FD headerMapper)
# ---------------------------------------------------------------------------
def test_obs_private_download_uses_encrypted_token():
    def responder(method, url, kw):
        if url.endswith("acquireEncryptedAccessToken"):
            return enveloped(ENC_BLOB)
        if "/r/talk/m/msg1" in url:
            return FakeObsResp(200, "RAWBYTES")
        return enveloped({})

    t = make_transport(responder)
    obs = ObsClient(t)
    data = obs.download_object("talk", "m", "msg1", message_id="msg1")
    assert data == b"RAWBYTES"
    h = calls_of(t.session, "/r/talk/m/msg1")[0]["headers"]
    assert h["X-Line-Access"] == "ENC1"  # encrypted, not the raw token
    assert h["X-Line-Application"] == APP_HDR
    assert h["X-Talk-Meta"] == build_talk_meta("msg1")
    assert "X-Line-ChannelToken" not in h
    # the encrypted token was cached -> no second acquire
    assert len(calls_of(t.session, "acquireEncryptedAccessToken")) == 1
    assert t.tokens.encrypted_access_tokens["2"] == "ENC1"
    obs.download_object("talk", "m", "msg1")
    assert len(calls_of(t.session, "acquireEncryptedAccessToken")) == 1


def test_obs_myhome_download_uses_channel_token():
    def responder(method, url, kw):
        if url.endswith("issueChannelToken"):
            return enveloped({"channelAccessToken": "CHTOK"})
        if "/r/myhome/" in url:
            return FakeObsResp(200, "COVER")
        return enveloped({})

    t = make_transport(responder)
    obs = ObsClient(t)
    assert obs.download_object("myhome", "c", "reqid-1") == b"COVER"
    h = calls_of(t.session, "/r/myhome/c/reqid-1")[0]["headers"]
    assert h["X-Line-ChannelToken"] == "CHTOK"
    assert "X-Line-Access" not in h  # no (raw or encrypted) access token
    assert "X-Line-Application" not in h
    assert t.tokens.channel_access_token == "CHTOK"


def test_obs_public_download_sends_no_auth():
    t = make_transport(lambda m, u, kw: FakeObsResp(200, "PUBLIC"))
    obs = ObsClient(t)
    assert obs.download_object("talk", "m", "pub1", public=True) == b"PUBLIC"
    h = t.session.last["headers"]
    assert "X-Line-Access" not in h
    assert "X-Line-ChannelToken" not in h
    assert "X-Line-Application" not in h
    # and no token endpoints were hit at all
    assert not calls_of(t.session, "acquireEncryptedAccessToken")
    assert not calls_of(t.session, "issueChannelToken")


def test_obs_download_cdn_and_host_overrides():
    t = make_transport(lambda m, u, kw: FakeObsResp(200, "X"))
    obs = ObsClient(t)
    obs.download_object("talk", "m", "o1", public=True, cdn="cdn_profile")
    assert t.session.last["url"] == "https://profile.line-scdn.net/r/talk/m/o1"
    obs.download_object("talk", "m", "o2", public=True, cdn="cdn_obs")
    assert t.session.last["url"] == "https://obs.line-scdn.net/r/talk/m/o2"
    obs.download_object("talk", "m", "o3", public=True, host="https://example.com")
    assert t.session.last["url"] == "https://example.com/r/talk/m/o3"
    with pytest.raises(ValueError):
        obs.download_object("talk", "m", "o4", public=True, cdn="cdn_nope")


def test_upload_message_object_defaults_to_encrypted_token():
    def responder(method, url, kw):
        if url.endswith("acquireEncryptedAccessToken"):
            return enveloped(ENC_BLOB)
        if "/r/talk/m/" in url:
            return FakeResp(200, {"ok": True})
        return enveloped({})

    t = make_transport(responder)
    obs = ObsClient(t)
    res = obs.upload_message_object("m1", b"data", name="a.jpg", obs_type="image")
    assert res == {"ok": True}
    h = calls_of(t.session, "/r/talk/m/m1")[0]["headers"]
    assert h["X-Line-Access"] == "ENC1"
    assert h["X-Line-Application"] == APP_HDR
    # caller-supplied token still wins
    obs.upload_message_object("m2", b"data", name="a.jpg", obs_type="image", enc_token="CUST")
    h2 = calls_of(t.session, "/r/talk/m/m2")[0]["headers"]
    assert h2["X-Line-Access"] == "CUST"
    assert h2["X-Line-Application"] == APP_HDR


def test_upload_object_myhome_uses_channel_token():
    def responder(method, url, kw):
        if url.endswith("issueChannelToken"):
            return enveloped({"channelAccessToken": "CHTOK"})
        if "/r/myhome/" in url:
            return FakeResp(200, {"ok": True})
        return enveloped({})

    t = make_transport(responder)
    obs = ObsClient(t)
    obs.upload_object(
        "myhome",
        "c",
        "reqid-9",
        b"cover",
        obs_params={"ver": "2.0", "name": "c.jpg", "type": "image"},
    )
    h = t.session.last["headers"]
    assert h["X-Line-ChannelToken"] == "CHTOK"
    assert "X-Line-Access" not in h
    assert "X-Obs-Params" in h


# ---------------------------------------------------------------------------
# OBS token-acquisition failures are loud (never a silent unauthenticated
# private request — it would 401/403 server-side with no local indication)
# ---------------------------------------------------------------------------
def test_obs_unparseable_encrypted_token_raises():
    def responder(method, url, kw):
        if url.endswith("acquireEncryptedAccessToken"):
            return enveloped("garbage-no-delimiters")  # rows[1][0] missing
        if url.endswith(".obs"):
            return FakeResp(200, {"size": 1})
        return enveloped({})

    t = make_transport(responder)
    obs = ObsClient(t)
    with pytest.raises(LineApiError) as ei:
        obs.resource_info("/r/talk/m/msg1")
    assert "acquireEncryptedAccessToken" in str(ei.value)
    # the private request was never sent (it would have been unauthenticated)
    assert not calls_of(t.session, "/r/talk/m/msg1/info.obs")


def test_obs_channel_token_failure_raises():
    def responder(method, url, kw):
        if url.endswith("issueChannelToken"):
            return enveloped({"unrelated": True})  # no channelAccessToken/token
        if "/r/myhome/" in url:
            return FakeObsResp(200, "COVER")
        return enveloped({})

    t = make_transport(responder)
    obs = ObsClient(t)
    with pytest.raises(LineApiError) as ei:
        obs.download_object("myhome", "c", "reqid-1")
    assert "issueChannelToken" in str(ei.value)
    assert not calls_of(t.session, "/r/myhome/c/reqid-1")


def test_obs_meta_endpoints_surface_http_errors():
    # every raw-OBS path 403s (the .obs metadata endpoints and the /r/ upload);
    # only the gateway token acquisition succeeds
    t = make_transport(
        lambda m, u, kw: (
            FakeResp(403, {"error": "forbidden"})
            if u.startswith(OBS_BASE + "/r/")
            else enveloped(ENC_BLOB)
        )
    )
    obs = ObsClient(t)
    with pytest.raises(LineApiError) as ei:
        obs.object_info("/r/talk/m/msg1")
    assert ei.value.status == 403
    with pytest.raises(LineApiError):
        obs.resource_info("/r/talk/m/msg1")
    with pytest.raises(LineApiError):
        obs.playback_info("/r/talk/v/msg1")
    with pytest.raises(LineApiError):
        obs.upload_object(
            "talk",
            "m",
            "o1",
            b"data",
            obs_params={"ver": "2.0", "name": "a.jpg", "type": "image"},
        )


# ---------------------------------------------------------------------------
# .obs metadata endpoints: object_info / info.obs / playback.obs
# ---------------------------------------------------------------------------
def _obs_meta_transport():
    def responder(method, url, kw):
        if url.endswith("acquireEncryptedAccessToken"):
            return enveloped(ENC_BLOB)
        if url.endswith(".obs"):
            return FakeResp(200, {"size": 12})
        return enveloped({})

    return make_transport(responder)


def test_object_info_url_and_auth():
    t = _obs_meta_transport()
    obs = ObsClient(t)
    obs.object_info("/r/talk/m/msg1", talk_meta="RAW-META")
    call = calls_of(t.session, "/r/talk/m/msg1/object_info.obs")[0]
    assert call["url"] == OBS_BASE + "/r/talk/m/msg1/object_info.obs"
    assert call["headers"]["X-Talk-Meta"] == "RAW-META"
    assert call["headers"]["X-Line-Access"] == "ENC1"


def test_resource_info_url_and_auth():
    t = _obs_meta_transport()
    obs = ObsClient(t)
    assert obs.resource_info("/r/talk/m/msg1") == {"size": 12}
    call = calls_of(t.session, "/r/talk/m/msg1/info.obs")[0]
    assert call["url"] == OBS_BASE + "/r/talk/m/msg1/info.obs"
    assert call["headers"]["X-Line-Access"] == "ENC1"
    assert call["headers"]["X-Line-Application"] == APP_HDR


def test_playback_info_params_and_talk_meta():
    t = _obs_meta_transport()
    obs = ObsClient(t)
    obs.playback_info("/r/talk/v/msg1", message_id="msg1")
    call = calls_of(t.session, "/r/talk/v/msg1/playback.obs")[0]
    assert call["url"] == OBS_BASE + "/r/talk/v/msg1/playback.obs"
    assert call["params"] == {
        "modelName": "CHROMEOS",
        "networkType": "WiFi",
        "lang": "en",
    }
    assert call["headers"]["X-Talk-Meta"] == build_talk_meta("msg1")
    assert call["headers"]["X-Line-Access"] == "ENC1"


def test_playback_info_lang_from_locale():
    t = _obs_meta_transport()
    t.config.locale = "zh-CN"
    obs = ObsClient(t)
    obs.playback_info("/r/talk/v/msg1")
    assert t.session.last["params"]["lang"] == "zh-Hans"
    # explicit lang overrides the locale mapping
    obs.playback_info("/r/talk/v/msg1", lang="ko")
    assert t.session.last["params"]["lang"] == "ko"


# ---------------------------------------------------------------------------
# timeline / pageinfo REST helpers
# ---------------------------------------------------------------------------
def test_timeline_home_id():
    t = make_transport(lambda m, u, kw: enveloped({"homeId": "H1"}))
    obs = ObsClient(t)
    assert obs.timeline_home_id("uX") == {"homeId": "H1"}
    call = calls_of(t.session, "/api/timeline/homeId")[0]
    assert call["params"] == {"eMid": "uX"}


def test_timeline_get_cover():
    t = make_transport(lambda m, u, kw: enveloped({"coverObsInfo": {}}))
    obs = ObsClient(t)
    obs.timeline_get_cover("uTarget", "uMe", "JP")
    call = calls_of(t.session, "/api/timeline/getCover")[0]
    assert call["params"] == {"targetMid": "uTarget", "myMid": "uMe", "myRegion": "JP"}


def test_timeline_update_cover_posts_body():
    t = make_transport(lambda m, u, kw: enveloped({"coverObsInfo": {}}))
    obs = ObsClient(t)
    obs.timeline_update_cover("uMe", "JP", "reqid-1")
    call = calls_of(t.session, "/api/timeline/updateCover")[0]
    assert call["method"] == "POST"
    assert json.loads(call["data"]) == {
        "myMid": "uMe",
        "myRegion": "JP",
        "coverObjectId": "reqid-1",
        "storyShare": False,
    }


def test_page_info():
    def responder(method, url, kw):
        assert url == LEGY_BASE + "/sc/api/v2/pageinfo/get"
        return FakeResp(200, {"result": {"title": "T", "summary": "S", "image_source": "I"}})

    t = make_transport(responder)
    obs = ObsClient(t)
    assert obs.page_info("https://example.com/x") == {
        "title": "T",
        "summary": "S",
        "image_source": "I",
    }
    call = t.session.last
    assert call["params"] == {"url": "https://example.com/x", "caller": "LINE_CHROME"}
    assert call["headers"]["Accept-Language"] == "en-US"
    obs.page_info("https://example.com/x", accept_language="ja-JP")
    assert t.session.last["headers"]["Accept-Language"] == "ja-JP"


# ---------------------------------------------------------------------------
# Operation dataclass passthrough (param3 is used by sync consumers)
# ---------------------------------------------------------------------------
def test_operation_from_dict_params():
    op = Operation.from_dict({"revision": 1, "type": 5, "param3": "0"})
    assert op.revision == 1 and op.param3 == "0"


def test_object_info_builds_talk_meta_from_message_id():
    """``message_id=`` builds X-Talk-Meta (E2EE media needs it on object_info
    too — live-tested on /r/talk/emi/<OID>/object_info.obs)."""
    t = _obs_meta_transport()
    obs = ObsClient(t)
    obs.object_info("/r/talk/emi/OID1", message_id="MSG-9")
    call = calls_of(t.session, "/r/talk/emi/OID1/object_info.obs")[0]
    assert call["headers"]["X-Talk-Meta"] == build_talk_meta("MSG-9")

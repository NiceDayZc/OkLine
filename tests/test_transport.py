"""Offline tests for :mod:`okline.transport`.

These exercise the low-level request engine that every Thrift service shares:

* the exact standard header set (``base_headers``), including the X-LAL /
  Accept-Language pairing and the X-Line-Application descriptor,
* URL building in :meth:`Transport.post_json`,
* the LINE ``{"message":"OK","data":...}`` envelope unwrap (and the bare
  ``{"data":...}`` variant),
* error mapping: non-OK envelope -> ``LineApiError``, HTTP 401 ->
  ``LineAuthError``, ``REQUEST_MUST_UPGRADE`` -> ``LineMustUpgradeError``,
* the ``_safe_json`` helper,
* recording integration (``api.history`` / ``api.last`` grow per call),
* ``LineLoginRequired`` when ``require_auth`` is set but no token is held.

Everything runs against the in-memory :class:`FakeSession` from
``tests/conftest.py`` — no real network and no Node.js bridge.
"""

from __future__ import annotations

import json

import pytest
from conftest import FakeResp, FakeSession, build_api, enveloped, route

from okline.exceptions import (
    LineApiError,
    LineAuthError,
    LineLoginRequired,
    LineMustUpgradeError,
)
from okline.transport import (
    _LAL_MAP,
    DEFAULT_APPLICATION_HEADER,
    DEFAULT_USER_AGENT,
    LineConfig,
    Tokens,
    Transport,
)

# A real Thrift endpoint key used throughout for ``call``-based tests.
PROFILE = "Talk.TalkService.getProfile"
PROFILE_PATH = "/api/talk/thrift/Talk/TalkService/getProfile"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def make_transport(responder=None, *, access_token="TKN", **cfg_kw) -> Transport:
    """A bare :class:`Transport` wired to a fake session (no OkLine wrapper)."""
    responder = responder or (lambda m, u, kw: enveloped({}))
    cfg = LineConfig(**cfg_kw)
    return Transport(cfg, Tokens(access_token=access_token), session=FakeSession(responder))


# ---------------------------------------------------------------------------
# base_headers
# ---------------------------------------------------------------------------
class TestBaseHeaders:
    """The standard header set must match the real extension byte-for-byte."""

    def test_static_headers_have_exact_values(self):
        t = make_transport()
        h = t.base_headers()
        assert h["content-type"] == "application/json"
        assert h["accept"] == "application/json, text/plain, */*"
        assert h["X-Line-Application"] == DEFAULT_APPLICATION_HEADER
        assert h["X-Line-Application"] == "CHROMEOS\t3.7.2\tChrome_OS\t"
        assert h["X-Line-Chrome-Version"] == "3.7.2"
        assert h["User-Agent"] == DEFAULT_USER_AGENT

    def test_locale_drives_accept_language_and_xlal(self):
        """X-LAL is the underscore form of Accept-Language (the bundle's Up map)."""
        t = make_transport(locale="ja-JP")
        h = t.base_headers()
        assert h["Accept-Language"] == "ja-JP"
        assert h["X-LAL"] == "ja_JP"
        # default locale en-US -> en_US
        assert make_transport().base_headers()["X-LAL"] == "en_US"

    def test_unknown_locale_falls_back_to_en_us(self):
        t = make_transport(locale="xx-YY")
        h = t.base_headers()
        assert h["Accept-Language"] == "xx-YY"  # echoed verbatim
        assert h["X-LAL"] == "en_US"  # but X-LAL falls back

    def test_lal_map_is_consistent_for_every_known_locale(self):
        for locale, lal in _LAL_MAP.items():
            assert make_transport(locale=locale).base_headers()["X-LAL"] == lal

    def test_access_token_header_present_when_held(self):
        h = make_transport(access_token="SECRET").base_headers()
        assert h["X-Line-Access"] == "SECRET"

    def test_access_token_omitted_when_with_access_false(self):
        h = make_transport(access_token="SECRET").base_headers(with_access=False)
        assert "X-Line-Access" not in h

    def test_access_token_omitted_when_no_token(self):
        h = make_transport(access_token=None).base_headers()
        assert "X-Line-Access" not in h

    def test_channel_token_header_present_when_held(self):
        t = make_transport()
        t.tokens.channel_access_token = "CHAN"
        assert t.base_headers()["X-Line-ChannelToken"] == "CHAN"
        # absent by default
        assert "X-Line-ChannelToken" not in make_transport().base_headers()


# ---------------------------------------------------------------------------
# post_json / call: URL building and request shape
# ---------------------------------------------------------------------------
class TestUrlBuilding:
    """``post_json`` builds ``<gateway_base><path>`` and POSTs the JSON body."""

    def test_call_targets_gateway_plus_thrift_path(self):
        t = make_transport()
        t.call(PROFILE, [0])
        last = t.session.last
        assert last["method"] == "POST"
        assert last["url"] == "https://line-chrome-gw.line-apps.com" + PROFILE_PATH

    def test_post_json_uses_configured_gateway_base(self):
        t = make_transport(gateway_base="https://example.test")
        t.post_json("/api/foo", [1, 2])
        assert t.session.last["url"] == "https://example.test/api/foo"

    def test_post_json_honours_explicit_base_override(self):
        t = make_transport()
        t.post_json("/api/foo", [], base="https://other.test")
        assert t.session.last["url"] == "https://other.test/api/foo"

    def test_body_is_compact_positional_json_array(self):
        t = make_transport()
        t.call(PROFILE, [0, {"mid": "u123"}])
        sent = t.session.last["data"]
        if isinstance(sent, (bytes, bytearray)):
            sent = sent.decode("utf-8")
        # compact separators, no spaces
        assert sent == '[0,{"mid":"u123"}]'
        assert json.loads(sent) == [0, {"mid": "u123"}]

    def test_body_keeps_non_ascii_unescaped(self):
        """``ensure_ascii=False`` keeps multibyte text readable on the wire."""
        t = make_transport()
        t.post_json("/api/foo", ["こんにちは"])
        sent = t.session.last["data"]
        if isinstance(sent, (bytes, bytearray)):
            sent = sent.decode("utf-8")
        assert "こんにちは" in sent


# ---------------------------------------------------------------------------
# Envelope unwrapping
# ---------------------------------------------------------------------------
class TestEnvelopeUnwrap:
    """The gateway wraps results as ``{"message":"OK","data":<result>}``."""

    def test_ok_envelope_returns_inner_data(self):
        t = make_transport(route({PROFILE_PATH: {"mid": "u1", "displayName": "Z"}}))
        result = t.call(PROFILE, [0])
        assert result == {"mid": "u1", "displayName": "Z"}

    def test_ok_message_is_case_insensitive(self):
        t = make_transport(lambda m, u, kw: enveloped({"x": 1}, message="ok"))
        assert t.call(PROFILE, [0]) == {"x": 1}

    def test_ok_envelope_without_data_returns_whole_payload(self):
        """An OK envelope that lacks a ``data`` key yields the dict itself."""
        t = make_transport(lambda m, u, kw: FakeResp(200, {"message": "OK"}))
        assert t.call(PROFILE, [0]) == {"message": "OK"}

    def test_bare_data_wrapper_is_unwrapped(self):
        """A ``{"data": ...}`` body with no ``message`` is still unwrapped."""
        t = make_transport(lambda m, u, kw: FakeResp(200, {"data": [1, 2, 3]}))
        assert t.call(PROFILE, [0]) == [1, 2, 3]

    def test_plain_json_without_envelope_passes_through(self):
        t = make_transport(lambda m, u, kw: FakeResp(200, [9, 8, 7]))
        assert t.call(PROFILE, [0]) == [9, 8, 7]

    def test_data_can_be_falsy_and_is_preserved(self):
        t = make_transport(lambda m, u, kw: enveloped(0))
        assert t.call(PROFILE, [0]) == 0
        t2 = make_transport(lambda m, u, kw: enveloped([]))
        assert t2.call(PROFILE, [0]) == []


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------
class TestErrorMapping:
    """HTTP status + body + headers map onto the exception hierarchy."""

    def test_non_ok_envelope_raises_api_error_despite_200(self):
        """A non-OK message at HTTP 200 is still an application error."""
        body = {
            "message": "FAILED",
            "data": None,
            "error": {"code": 20, "message": "bad request"},
        }
        t = make_transport(lambda m, u, kw: FakeResp(200, body))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        err = ei.value
        assert err.code == 20
        assert err.reason == "bad request"
        assert err.status == 200
        assert err.path == PROFILE_PATH

    def test_non_ok_envelope_uses_message_when_no_error_block(self):
        t = make_transport(lambda m, u, kw: FakeResp(200, {"message": "NOPE"}))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        # message bubbles into the reason when no structured error is present
        assert "NOPE" in str(ei.value) or ei.value.reason == "NOPE"

    def test_http_401_raises_auth_error(self):
        body = {"error": {"code": 8, "message": "token expired"}}
        t = make_transport(lambda m, u, kw: FakeResp(401, body))
        with pytest.raises(LineAuthError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.status == 401
        assert ei.value.code == 8

    def test_http_403_raises_auth_error(self):
        t = make_transport(lambda m, u, kw: FakeResp(403, {"error": {"message": "forbidden"}}))
        with pytest.raises(LineAuthError):
            t.call(PROFILE, [0])

    def test_auth_code_zero_raises_auth_error_even_on_generic_status(self):
        """Talk auth codes {0,1,8} classify as auth errors regardless of status."""
        body = {"error": {"code": 0, "message": "ILLEGAL_ARGUMENT"}}
        t = make_transport(lambda m, u, kw: FakeResp(400, body))
        with pytest.raises(LineAuthError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == 0

    def test_generic_http_error_raises_plain_api_error(self):
        """A non-auth, non-upgrade error is a plain LineApiError (not a subclass)."""
        body = {"error": {"code": 42, "message": "boom"}}
        t = make_transport(
            lambda m, u, kw: FakeResp(500, body, headers={"content-type": "application/json"})
        )
        # 500 retries (max_retries default 2) then surfaces the final 500 body.
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        assert type(ei.value) is LineApiError
        assert ei.value.code == 42
        assert ei.value.status == 500

    def test_error_code_from_response_header(self):
        """When the body has no code, ``x-line-resp-code`` supplies it."""
        resp = FakeResp(
            400,
            {"message": "fail"},
            headers={"content-type": "application/json", "x-line-resp-code": "8"},
        )
        t = make_transport(lambda m, u, kw: resp)
        with pytest.raises(LineAuthError) as ei:  # code 8 -> auth
            t.call(PROFILE, [0])
        assert ei.value.code == 8


class TestMustUpgrade:
    """``REQUEST_MUST_UPGRADE`` / code 86 must classify as a must-upgrade error."""

    def test_upgrade_reason_string_classifies_as_must_upgrade(self):
        body = {"error": {"code": 99, "message": "REQUEST_MUST_UPGRADE"}}
        t = make_transport(lambda m, u, kw: FakeResp(426, body))
        with pytest.raises(LineMustUpgradeError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.reason == "REQUEST_MUST_UPGRADE"

    def test_upgrade_substring_anywhere_in_reason(self):
        body = {"error": {"message": "client must upgrade now"}}
        t = make_transport(lambda m, u, kw: FakeResp(400, body))
        with pytest.raises(LineMustUpgradeError):
            t.call(PROFILE, [0])

    def test_error_code_86_classifies_as_must_upgrade(self):
        body = {"error": {"code": 86, "message": "outdated"}}
        t = make_transport(lambda m, u, kw: FakeResp(400, body))
        with pytest.raises(LineMustUpgradeError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == 86

    def test_must_upgrade_takes_precedence_over_auth_status(self):
        """An UPGRADE reason wins even when the status would imply auth."""
        body = {"error": {"code": 86, "message": "MUST_UPGRADE"}}
        t = make_transport(lambda m, u, kw: FakeResp(401, body))
        with pytest.raises(LineMustUpgradeError):
            t.call(PROFILE, [0])


# ---------------------------------------------------------------------------
# _safe_json
# ---------------------------------------------------------------------------
class TestSafeJson:
    """``_safe_json`` returns parsed JSON or the raw text on failure."""

    def test_parses_valid_json_object(self):
        assert Transport._safe_json('{"a": 1}') == {"a": 1}

    def test_parses_valid_json_array(self):
        assert Transport._safe_json("[1, 2, 3]") == [1, 2, 3]

    def test_parses_json_scalars(self):
        assert Transport._safe_json("true") is True
        assert Transport._safe_json("42") == 42

    def test_returns_raw_text_on_invalid_json(self):
        assert Transport._safe_json("not json at all") == "not json at all"

    def test_returns_empty_string_unchanged(self):
        assert Transport._safe_json("") == ""


# ---------------------------------------------------------------------------
# Recording integration
# ---------------------------------------------------------------------------
class TestRecording:
    """A recording OkLine grows ``history`` / updates ``last`` per call."""

    def test_history_grows_per_successful_call(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}))
        assert api.history == []
        api.call(PROFILE, 0)
        assert len(api.history) == 1
        api.call(PROFILE, 0)
        assert len(api.history) == 2

    def test_last_reflects_most_recent_exchange(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}))
        api.call(PROFILE, 0)
        ex = api.last
        assert ex is not None
        assert ex.endpoint == PROFILE
        assert ex.method == "POST"
        assert ex.path == PROFILE_PATH
        assert ex.status == 200
        assert ex.ok is True
        assert ex.response_body == {"mid": "u1"}

    def test_request_body_is_recorded_as_positional_args(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}))
        api.call(PROFILE, 0, {"k": "v"})
        assert api.last.request_body == [0, {"k": "v"}]

    def test_failed_call_is_recorded_with_error(self, make_api):
        body = {"error": {"code": 8, "message": "expired"}}
        api = make_api(lambda m, u, kw: FakeResp(401, body))
        with pytest.raises(LineAuthError):
            api.call(PROFILE, 0)
        # the exchange is still recorded, flagged not-ok with the error string
        assert len(api.history) == 1
        ex = api.last
        assert ex.ok is False
        assert ex.error is not None
        assert ex.status == 401

    def test_no_recorder_means_empty_history(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}), record=False)
        api.call(PROFILE, 0)
        assert api.history == []
        assert api.last is None

    def test_seq_numbers_increase(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}))
        api.call(PROFILE, 0)
        api.call(PROFILE, 0)
        seqs = [ex.seq for ex in api.history]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # all distinct

    def test_secrets_redacted_in_recorded_request_headers(self, make_api):
        """The access token must be masked in the recorded transcript by default."""
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}), access_token="TOPSECRET")
        api.call(PROFILE, 0)
        # raw header on the wire still carries the real token...
        assert api.transport.session.last["headers"]["X-Line-Access"] == "TOPSECRET"
        # ...but the dumped transcript redacts it
        assert "TOPSECRET" not in api.dump()
        assert "<redacted>" in api.dump()


# ---------------------------------------------------------------------------
# require_auth / login required
# ---------------------------------------------------------------------------
class TestLoginRequired:
    """``require_auth`` without a token short-circuits before any HTTP call."""

    def test_login_required_when_no_token(self):
        t = make_transport(access_token=None)
        with pytest.raises(LineLoginRequired) as ei:
            t.post_json("/api/foo", [], require_auth=True)
        assert ei.value.path == "/api/foo"
        # nothing was sent
        assert t.session.last is None

    def test_login_required_is_an_auth_error_subclass(self):
        assert issubclass(LineLoginRequired, LineAuthError)

    def test_no_token_allowed_when_require_auth_false(self):
        """Unauthenticated endpoints (require_auth=False) still go out."""
        t = make_transport(
            access_token=None,
        )
        t.post_json("/api/foo", [1], require_auth=False)
        assert t.session.last is not None
        # no access header attached when unauthenticated
        assert "X-Line-Access" not in t.session.last["headers"]

    def test_call_via_okline_raises_login_required(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}), access_token=None)
        with pytest.raises(LineLoginRequired):
            api.call(PROFILE, 0)


# ---------------------------------------------------------------------------
# build_api smoke test (ensures the conftest wiring is what these tests assume)
# ---------------------------------------------------------------------------
def test_build_api_returns_recording_client_by_default():
    api = build_api(route({PROFILE_PATH: {"mid": "u1"}}))
    try:
        assert api.recorder is not None
        assert api.call(PROFILE, 0) == {"mid": "u1"}
        assert len(api.history) == 1
    finally:
        api.close()

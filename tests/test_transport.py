"""Offline tests for :mod:`okline.transport`.

These exercise the low-level request engine that every Thrift service shares:

* the standard header set (``base_headers``) and its scoping rules — X-LAL
  only on gateway bases, ``X-Line-ChannelToken`` only on ``/api/timeline/``
  gateway paths, ``X-Line-Application`` never on gateway requests (opt-in
  only, the extension sends it solely on private OBS fetches), content-type
  only when a JSON body is sent,
* URL building in :meth:`Transport.post_json`,
* the LINE ``{"message":"OK","data":...}`` envelope unwrap — strictly "OK",
  and non-enveloped 2xx bodies rejected on gateway bases (kept lenient for
  non-gateway OBS/legy bases),
* error mapping: non-OK envelope -> ``LineApiError``, inner codes {1,7,8} /
  HTTP 401/403 -> ``LineAuthError``, outer 10006 -> ``LineMustUpgradeError``
  (86 is E2EE_INVALID_VERSION, a plain error), inner 119 -> renew-and-retry,
  outer 99999 / inner 115 -> retried within the ``max_retries`` budget,
  outer 10052 surfacing the nested ``statusCode`` / ``rejectionReason``,
* the ``_safe_json`` helper,
* recording integration (``api.history`` / ``api.last`` grow per call),
* ``LineLoginRequired`` when ``require_auth`` is set but no token is held.

Everything runs against the in-memory :class:`FakeSession` /
:class:`FakeBridge` from ``tests/conftest.py`` — no real network and no
Node.js bridge.
"""

from __future__ import annotations

import json

import pytest
from conftest import FakeBridge, FakeResp, FakeSession, build_api, enveloped, route

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

# A non-gateway base (OBS) used to test the lenient non-gateway unwrap.
OBS_URL = "https://obs.line-apps.com"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def make_transport(responder=None, *, access_token="TKN", **cfg_kw) -> Transport:
    """A bare :class:`Transport` wired to a fake session (no OkLine wrapper).

    A :class:`FakeBridge` signer is injected so X-Hmac signing works without
    Node.js (``enable_hmac`` stays on, matching production behaviour).
    """
    responder = responder or (lambda m, u, kw: enveloped({}))
    cfg = LineConfig(**cfg_kw)
    return Transport(
        cfg,
        Tokens(access_token=access_token),
        session=FakeSession(responder),
        signer=FakeBridge(),
    )


def talk_exc(
    inner_code: int,
    reason: str,
    *,
    outer_code: int = 10051,
    status: int = 400,
    **extra: object,
) -> FakeResp:
    """A gateway error body wrapping a nested TalkException."""
    body = {
        "code": outer_code,
        "message": "RESPONSE_ERROR",
        "data": {"name": "TalkException", "code": inner_code, "reason": reason, **extra},
    }
    return FakeResp(status, body)


# ---------------------------------------------------------------------------
# base_headers
# ---------------------------------------------------------------------------
class TestBaseHeaders:
    """The standard header set must match the real extension's axios clients."""

    def test_static_headers_have_exact_values(self):
        t = make_transport()
        h = t.base_headers()
        assert h["accept"] == "application/json, text/plain, */*"
        assert h["X-Line-Chrome-Version"] == "3.7.2"
        assert h["Accept-Language"] == "en-US"
        assert h["X-LAL"] == "en_US"
        assert h["User-Agent"] == DEFAULT_USER_AGENT

    def test_no_content_type_in_base_set(self):
        """Axios attaches content-type only when a body is sent — it is not
        part of the base header set (post_json adds it for its JSON body)."""
        assert "content-type" not in make_transport().base_headers()

    def test_no_line_application_on_gateway_requests(self):
        """The extension never sends X-Line-Application on the gateway — it is
        exclusive to private OBS resource fetches, hence opt-in here."""
        t = make_transport()
        assert "X-Line-Application" not in t.base_headers()
        assert "X-Line-Application" not in t.base_headers(path=PROFILE_PATH)

    def test_line_application_is_opt_in(self):
        h = make_transport().base_headers(application=True)
        assert h["X-Line-Application"] == DEFAULT_APPLICATION_HEADER
        assert h["X-Line-Application"] == "CHROMEOS\t3.7.2\tChrome_OS\t"

    def test_xlal_only_on_gateway_base(self):
        """The OBS client gets Accept-Language alone; X-LAL is gateway-only."""
        t = make_transport()
        assert "X-LAL" in t.base_headers()  # base=None -> gateway
        h_obs = t.base_headers(base=OBS_URL)
        assert "X-LAL" not in h_obs
        assert h_obs["Accept-Language"] == "en-US"

    def test_legy_host_header_only_when_configured(self):
        """``LineConfig(legy_host=...)`` -> X-Legy-Host on gateway requests only
        (the extension sets it as a gateway-client default, SD()/zU())."""
        t = make_transport(legy_host="legy-backup.line-apps.com")
        assert t.base_headers()["X-Legy-Host"] == "legy-backup.line-apps.com"
        # non-gateway bases (OBS/legy) never carry it
        assert "X-Legy-Host" not in t.base_headers(base=OBS_URL)
        # and it is absent entirely when not configured
        assert "X-Legy-Host" not in make_transport().base_headers()

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

    def test_channel_token_only_on_timeline_gateway_paths(self):
        """The extension's gateway headerMapper attaches X-Line-ChannelToken
        to /api/timeline/* URLs only — never to thrift calls or OBS."""
        t = make_transport()
        t.tokens.channel_access_token = "CHAN"
        assert t.base_headers(path="/api/timeline/home")["X-Line-ChannelToken"] == "CHAN"
        assert "X-Line-ChannelToken" not in t.base_headers(path=PROFILE_PATH)
        assert "X-Line-ChannelToken" not in t.base_headers()  # no path context
        # non-gateway (OBS /r/myhome/ handling is obs.py's job, not the base set)
        assert "X-Line-ChannelToken" not in t.base_headers(
            path="/api/timeline/home", base=OBS_URL
        )

    def test_channel_token_absent_when_not_held(self):
        t = make_transport()
        assert "X-Line-ChannelToken" not in t.base_headers(path="/api/timeline/home")


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


class TestHeaderScopingOnRequests:
    """Header scoping as actually sent by post_json / get."""

    def test_post_json_sends_content_type_with_json_body(self):
        t = make_transport()
        t.call(PROFILE, [0])
        assert t.session.last["headers"]["content-type"] == "application/json"

    def test_post_json_gateway_headers_match_extension(self):
        """Gateway POST: X-LAL yes, X-Line-Application no, X-Hmac yes."""
        t = make_transport()
        t.call(PROFILE, [0])
        h = t.session.last["headers"]
        assert h["X-LAL"] == "en_US"
        assert h["X-Line-Chrome-Version"] == "3.7.2"
        assert h["X-Line-Access"] == "TKN"
        assert "X-Line-Application" not in h
        assert h["X-Hmac"]  # signed (FakeBridge)

    def test_post_json_non_gateway_base_has_no_xlal(self):
        t = make_transport()
        t.post_json("/r/talk/m/oid", [], base=OBS_URL)
        h = t.session.last["headers"]
        assert "X-LAL" not in h
        assert h["Accept-Language"] == "en-US"

    def test_post_json_channel_token_on_timeline_path(self):
        t = make_transport()
        t.tokens.channel_access_token = "CHAN"
        t.post_json("/api/timeline/updateCover", {"myMid": "u1"})
        assert t.session.last["headers"]["X-Line-ChannelToken"] == "CHAN"

    def test_post_json_no_channel_token_on_thrift_path(self):
        t = make_transport()
        t.tokens.channel_access_token = "CHAN"
        t.call(PROFILE, [0])
        assert "X-Line-ChannelToken" not in t.session.last["headers"]

    def test_get_sends_no_content_type(self):
        """Bodyless GETs carry no content-type (axios adds none)."""
        t = make_transport()
        t.get(PROFILE_PATH)
        assert "content-type" not in t.session.last["headers"]


# ---------------------------------------------------------------------------
# Envelope unwrapping
# ---------------------------------------------------------------------------
class TestEnvelopeUnwrap:
    """The gateway wraps results as ``{"message":"OK","data":<result>}``."""

    def test_ok_envelope_returns_inner_data(self):
        t = make_transport(route({PROFILE_PATH: {"mid": "u1", "displayName": "Z"}}))
        result = t.call(PROFILE, [0])
        assert result == {"mid": "u1", "displayName": "Z"}

    def test_ok_check_is_case_sensitive(self):
        """The extension compares strictly against "OK" — "ok"/"Ok" are errors."""
        t = make_transport(lambda m, u, kw: enveloped({"x": 1}, message="ok"))
        with pytest.raises(LineApiError):
            t.call(PROFILE, [0])
        t2 = make_transport(lambda m, u, kw: enveloped({"x": 1}, message="Ok"))
        with pytest.raises(LineApiError):
            t2.call(PROFILE, [0])

    def test_ok_envelope_without_data_returns_whole_payload(self):
        """An OK envelope that lacks a ``data`` key yields the dict itself."""
        t = make_transport(lambda m, u, kw: FakeResp(200, {"message": "OK"}))
        assert t.call(PROFILE, [0]) == {"message": "OK"}

    def test_data_can_be_falsy_and_is_preserved(self):
        t = make_transport(lambda m, u, kw: enveloped(0))
        assert t.call(PROFILE, [0]) == 0
        t2 = make_transport(lambda m, u, kw: enveloped([]))
        assert t2.call(PROFILE, [0]) == []

    # -- non-gateway bases keep the lenient unwrap --------------------------
    def test_bare_data_wrapper_is_unwrapped_on_non_gateway_base(self):
        """OBS/legy return raw payloads; a ``{"data": ...}`` body with no
        ``message`` is still unwrapped there."""
        t = make_transport(lambda m, u, kw: FakeResp(200, {"data": [1, 2, 3]}))
        assert t.post_json("/r/talk/m/oid", [], base=OBS_URL) == [1, 2, 3]

    def test_plain_json_without_envelope_passes_through_on_non_gateway_base(self):
        t = make_transport(lambda m, u, kw: FakeResp(200, [9, 8, 7]))
        assert t.post_json("/r/talk/m/oid", [], base=OBS_URL) == [9, 8, 7]

    # -- gateway bases reject anything that is not an OK envelope -----------
    def test_non_enveloped_2xx_dict_is_an_error_on_gateway(self):
        t = make_transport(lambda m, u, kw: FakeResp(200, {"mid": "u1"}))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        assert "OK envelope" in str(ei.value)

    def test_non_enveloped_2xx_list_is_an_error_on_gateway(self):
        t = make_transport(lambda m, u, kw: FakeResp(200, [9, 8, 7]))
        with pytest.raises(LineApiError):
            t.call(PROFILE, [0])

    def test_non_enveloped_2xx_surfaces_extractable_error_fields(self):
        """A gateway 2xx error body without a message key still surfaces any
        code/reason that can be extracted from it."""
        body = {"error": {"code": 42, "message": "boom"}}
        t = make_transport(lambda m, u, kw: FakeResp(200, body))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == 42
        assert ei.value.reason == "boom"


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------
class TestErrorMapping:
    """HTTP status + body map onto the exception hierarchy."""

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

    @pytest.mark.parametrize("code", [1, 7, 8])
    def test_auth_codes_raise_auth_error_even_on_generic_status(self, code):
        """Talk auth codes {1,7,8} classify as auth errors regardless of status
        (AUTHENTICATION_FAILED / NOT_AVAILABLE_USER / NOT_AUTHORIZED_DEVICE)."""
        t = make_transport(lambda m, u, kw: talk_exc(code, "auth problem"))
        with pytest.raises(LineAuthError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == code

    def test_illegal_argument_zero_is_plain_api_error(self):
        """ILLEGAL_ARGUMENT (0) is an ordinary request error — the extension
        never treats it as auth."""
        body = {"error": {"code": 0, "message": "ILLEGAL_ARGUMENT"}}
        t = make_transport(lambda m, u, kw: FakeResp(400, body))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        assert type(ei.value) is LineApiError
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

    def test_inner_talk_exception_code_and_reason_surface(self):
        """A wrapped TalkException surfaces its inner code/reason, not 10051."""
        t = make_transport(lambda m, u, kw: talk_exc(82, "can not send using plain mode"))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == 82
        assert ei.value.reason == "can not send using plain mode"

    def test_no_invented_resp_code_header_fallback(self):
        """The x-line-resp-code header fallback is NOT extension behaviour —
        a body without a code must not borrow one from a response header."""
        resp = FakeResp(
            400,
            {"message": "fail"},
            headers={"content-type": "application/json", "x-line-resp-code": "8"},
        )
        t = make_transport(lambda m, u, kw: resp)
        with pytest.raises(LineApiError) as ei:
            # HTTP 400 + no extractable code -> plain API error, not auth
            t.call(PROFILE, [0])
        assert type(ei.value) is LineApiError
        assert ei.value.code is None


class TestMustUpgrade:
    """The ONLY upgrade trigger is outer envelope code 10006
    (REQUEST_MUST_UPGRADE), or an UPGRADE-flavoured reason."""

    def test_outer_code_10006_classifies_as_must_upgrade(self):
        body = {"code": 10006, "message": "REQUEST_MUST_UPGRADE"}
        t = make_transport(lambda m, u, kw: FakeResp(400, body))
        with pytest.raises(LineMustUpgradeError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == 10006
        assert ei.value.reason == "REQUEST_MUST_UPGRADE"

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

    def test_must_upgrade_takes_precedence_over_auth_status(self):
        """Outer 10006 wins even when the HTTP status would imply auth."""
        body = {"code": 10006, "message": "REQUEST_MUST_UPGRADE"}
        t = make_transport(lambda m, u, kw: FakeResp(401, body))
        with pytest.raises(LineMustUpgradeError):
            t.call(PROFILE, [0])

    def test_code_86_is_e2ee_invalid_version_not_upgrade(self):
        """86 is E2EE_INVALID_VERSION — a plain API error, never an upgrade."""
        t = make_transport(lambda m, u, kw: talk_exc(86, "E2EE_INVALID_VERSION"))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        assert type(ei.value) is LineApiError
        assert ei.value.code == 86


class TestResponseHttpError:
    """Outer code 10052 (RESPONSE_HTTP_ERROR): the meaningful status is the
    nested data.statusCode; rejectionReason lands in metadata."""

    def test_10052_surfaces_nested_status_code_and_rejection_reason(self):
        body = {
            "code": 10052,
            "message": "RESPONSE_HTTP_ERROR",
            "data": {
                "statusCode": 410,
                "reason": "pin code timeout",
                "rejectionReason": "PIN_CODE_TIMEOUT",
            },
        }
        t = make_transport(lambda m, u, kw: FakeResp(400, body))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        err = ei.value
        assert err.code == 410
        assert err.status == 410
        assert err.reason == "pin code timeout"
        assert err.metadata == {"rejectionReason": "PIN_CODE_TIMEOUT"}

    def test_10052_without_nested_status_keeps_outer_code(self):
        body = {
            "code": 10052,
            "message": "RESPONSE_HTTP_ERROR",
            "data": {"reason": "upstream rejected"},
        }
        t = make_transport(lambda m, u, kw: FakeResp(502, body))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == 10052
        assert ei.value.status == 502
        assert ei.value.reason == "upstream rejected"


# ---------------------------------------------------------------------------
# MUST_REFRESH_V3_TOKEN (inner 119): renew and retry
# ---------------------------------------------------------------------------
class TestMustRefreshV3Token:
    """Inner TalkException 119 renews the token via the refresh hook and
    replays the request once (the extension's renewToken + retry path)."""

    @staticmethod
    def _hook(t: Transport, calls: list):
        def _refresh() -> bool:
            calls.append(t.tokens.access_token)
            t.tokens.access_token = "TKN2"  # renewed
            return True

        return _refresh

    def test_119_renews_and_retries_once(self):
        calls: list = []
        responses = [
            talk_exc(119, "MUST_REFRESH_V3_TOKEN", status=400),
            enveloped({"ok": True}),
        ]

        def responder(m, u, kw):
            return responses.pop(0)

        t = make_transport(responder)
        t._refresh_hook = self._hook(t, calls)
        assert t.call(PROFILE, [0]) == {"ok": True}
        assert calls == ["TKN"]  # hook invoked exactly once
        # the retried request carries the renewed token
        assert t.session.calls[-1]["headers"]["X-Line-Access"] == "TKN2"
        assert len(t.session.calls) == 2

    def test_119_without_hook_raises_auth_error(self):
        t = make_transport(lambda m, u, kw: talk_exc(119, "MUST_REFRESH_V3_TOKEN"))
        with pytest.raises(LineAuthError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == 119

    def test_119_with_failing_hook_raises_auth_error(self):
        t = make_transport(lambda m, u, kw: talk_exc(119, "MUST_REFRESH_V3_TOKEN"))
        t._refresh_hook = lambda: False
        with pytest.raises(LineAuthError):
            t.call(PROFILE, [0])

    def test_119_is_not_retried_more_than_once(self):
        """If the renewed request still answers 119, the error surfaces."""
        calls: list = []
        t = make_transport(lambda m, u, kw: talk_exc(119, "MUST_REFRESH_V3_TOKEN"))
        t._refresh_hook = self._hook(t, calls)
        with pytest.raises(LineAuthError):
            t.call(PROFILE, [0])
        assert calls == ["TKN"]  # renewed once, not again
        assert len(t.session.calls) == 2

    def test_http_401_refresh_still_works(self):
        """The HTTP-401 renew-and-retry stays as a defensive layer (the
        extension's gateway answers token expiry via 119 instead)."""
        calls: list = []
        responses = [
            FakeResp(401, {"error": {"code": 8, "message": "expired"}}),
            enveloped({"ok": True}),
        ]

        def responder(m, u, kw):
            return responses.pop(0)

        t = make_transport(responder)
        t._refresh_hook = self._hook(t, calls)
        assert t.call(PROFILE, [0]) == {"ok": True}
        assert calls == ["TKN"]
        assert t.session.calls[-1]["headers"]["X-Line-Access"] == "TKN2"


# ---------------------------------------------------------------------------
# Retryable application errors: outer 99999 / inner 115
# ---------------------------------------------------------------------------
class TestRetryableErrors:
    """Outer envelope 99999 (UNKNOWN_ERROR) and inner 115 (SHOULD_RETRY) are
    retried within the same ``max_retries`` budget as 5xx."""

    def test_outer_99999_is_retried_then_succeeds(self):
        responses = [
            FakeResp(200, {"code": 99999, "message": "UNKNOWN_ERROR"}),
            FakeResp(200, {"code": 99999, "message": "UNKNOWN_ERROR"}),
            enveloped({"ok": True}),
        ]

        def responder(m, u, kw):
            return responses.pop(0)

        t = make_transport(responder)
        assert t.call(PROFILE, [0]) == {"ok": True}
        assert len(t.session.calls) == 3  # max_retries=2 -> 3 attempts

    def test_outer_99999_surfaces_after_budget_exhausted(self):
        t = make_transport(
            lambda m, u, kw: FakeResp(200, {"code": 99999, "message": "UNKNOWN_ERROR"})
        )
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == 99999
        assert len(t.session.calls) == 3  # max_retries=2 -> 3 attempts

    def test_inner_115_is_retried_then_succeeds(self):
        responses = [talk_exc(115, "SHOULD_RETRY"), enveloped({"ok": True})]

        def responder(m, u, kw):
            return responses.pop(0)

        t = make_transport(responder)
        assert t.call(PROFILE, [0]) == {"ok": True}
        assert len(t.session.calls) == 2

    def test_retry_budget_respects_max_retries_config(self):
        t = make_transport(
            lambda m, u, kw: FakeResp(200, {"code": 99999, "message": "UNKNOWN_ERROR"}),
            max_retries=0,
        )
        with pytest.raises(LineApiError):
            t.call(PROFILE, [0])
        assert len(t.session.calls) == 1

    def test_non_retryable_error_is_sent_once(self):
        t = make_transport(lambda m, u, kw: talk_exc(42, "nope"))
        with pytest.raises(LineApiError):
            t.call(PROFILE, [0])
        assert len(t.session.calls) == 1

    def test_retried_success_records_only_final_exchange(self, make_api):
        """Intermediate retryable attempts are not recorded (matching the 5xx
        retry behaviour) — only the final exchange lands in history."""
        responses = [
            FakeResp(200, {"code": 99999, "message": "UNKNOWN_ERROR"}),
            enveloped({"mid": "u1"}),
        ]

        def responder(m, u, kw):
            return responses.pop(0)

        api = make_api(responder)
        try:
            assert api.call(PROFILE, 0) == {"mid": "u1"}
            assert len(api.history) == 1
            assert api.last is not None
            assert api.last.ok is True
        finally:
            api.close()


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

"""Offline tests for :mod:`okline.auth` driven through :class:`OkLine`.

Everything here is fully offline:

* the HTTP layer is faked via the shared ``conftest`` helpers
  (``build_api`` / ``route`` / ``enveloped`` / ``FakeResp``);
* the LTSM Node bridge is faked via ``FakeBridge`` so QR login works
  without Node.js or a real WASM bridge.

We generate a throwaway RSA keypair so the ``email_login`` password field is a
*real* PKCS#1 v1.5 ciphertext we can decrypt and verify, rather than a mock.
"""

from __future__ import annotations

import binascii

import pytest
from conftest import FakeBridge, FakeResp, build_api, route
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from okline.auth import AuthFlows, LoginResult, _append_secret
from okline.exceptions import LineApiError, LineAuthError


# ---------------------------------------------------------------------------
# RSA key fixture: a real keypair whose public half feeds getRSAKeyInfo and
# whose private half lets us decrypt + verify the LoginRequest.password blob.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def rsa_key():
    """A small (but valid) RSA keypair plus its getRSAKeyInfo dict form."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = priv.public_key().public_numbers()
    info = {
        "keynm": "testkey-01",
        "nvalue": format(numbers.n, "x"),
        "evalue": format(numbers.e, "x"),
        "sessionKey": "SESS123",
    }
    return priv, info


def _decrypt_password(priv, hex_password: str) -> bytes:
    """Recover the cleartext credential blob from the hex ciphertext."""
    ciphertext = binascii.unhexlify(hex_password)
    return priv.decrypt(ciphertext, padding.PKCS1v15())


# ===========================================================================
# email_login
# ===========================================================================
def test_email_login_builds_login_request_and_adopts_tokens(rsa_key, last_request):
    """email_login: RSA flow -> SUCCESS, correct LoginRequest body + token adoption."""
    priv, info = rsa_key
    success = {
        "type": 1,  # LoginResultType.SUCCESS
        "certificate": "CERT-XYZ",
        "tokenV3IssueResult": {
            "accessToken": "ACCESS-NEW",
            "refreshToken": "REFRESH-NEW",
        },
    }
    responder = route({"getRSAKeyInfo": info, "loginV2": success})
    # Start with no token so we can prove login adopts a fresh one.
    api = build_api(responder, access_token=None)

    result = api.auth.email_login("me@example.com", "hunter2")

    # --- result + adopted credentials -------------------------------------
    assert result.success is True
    assert result.access_token == "ACCESS-NEW"
    assert result.refresh_token == "REFRESH-NEW"
    assert result.certificate == "CERT-XYZ"
    assert api.transport.tokens.access_token == "ACCESS-NEW"
    assert api.transport.tokens.refresh_token == "REFRESH-NEW"
    assert api.transport.tokens.certificate == "CERT-XYZ"

    # --- the LoginRequest body we actually transmitted --------------------
    body = last_request(api)  # [ {LoginRequest...} ]
    assert isinstance(body, list) and len(body) == 1
    req = body[0]
    assert req["type"] == 2  # ID_CREDENTIAL_WITH_E2EE (default)
    assert req["identityProvider"] == 1  # IdentityProvider.LINE
    assert req["identifier"] == info["keynm"]
    assert req["e2eeVersion"] == 1
    assert req["keepLoggedIn"] is True

    # password must be lowercase hex...
    pw = req["password"]
    assert isinstance(pw, str)
    int(pw, 16)  # parses as hex -> no ValueError
    assert pw == pw.lower()

    # ...and decrypt to chr(len)+sessionKey + chr(len)+email + chr(len)+pwd
    cleartext = _decrypt_password(priv, pw).decode("utf-8")
    expected = (
        chr(len(info["sessionKey"]))
        + info["sessionKey"]
        + chr(len("me@example.com"))
        + "me@example.com"
        + chr(len("hunter2"))
        + "hunter2"
    )
    assert cleartext == expected


def test_email_login_without_e2ee_uses_plain_credential_type(rsa_key, last_request):
    """with_e2ee=False switches the LoginRequest type to ID_CREDENTIAL (0)."""
    _priv, info = rsa_key
    success = {"type": 1, "tokenV3IssueResult": {"accessToken": "A"}}
    responder = route({"getRSAKeyInfo": info, "loginV2": success})
    api = build_api(responder, access_token=None)

    api.auth.email_login("u@x.io", "pw", with_e2ee=False)

    req = last_request(api)[0]
    assert req["type"] == 0  # LoginType.ID_CREDENTIAL


def test_email_login_targets_the_right_endpoints(rsa_key):
    """email_login hits getRSAKeyInfo first, then loginV2 (last URL)."""
    _priv, info = rsa_key
    success = {"type": 1, "tokenV3IssueResult": {"accessToken": "A"}}
    responder = route({"getRSAKeyInfo": info, "loginV2": success})
    api = build_api(responder, access_token=None)

    api.auth.email_login("u@x.io", "pw")

    urls = [c["url"] for c in api.transport.session.calls]
    assert urls[0].endswith("/Talk/TalkService/getRSAKeyInfo")
    assert urls[-1].endswith("/Talk/AuthService/loginV2")


def test_email_login_non_success_does_not_adopt_tokens(rsa_key):
    """A non-SUCCESS loginV2 (e.g. device confirm) leaves tokens untouched."""
    _priv, info = rsa_key
    # REQUIRE_DEVICE_CONFIRM (3): has a pinCode, no tokens to adopt.
    challenge = {"type": 3, "pinCode": "1234"}
    responder = route({"getRSAKeyInfo": info, "loginV2": challenge})
    api = build_api(responder, access_token="OLD-TOKEN")

    result = api.auth.email_login("u@x.io", "pw")

    assert result.success is False
    assert result.pin_code == "1234"
    assert result.access_token is None
    # original token preserved (nothing adopted)
    assert api.transport.tokens.access_token == "OLD-TOKEN"


# ===========================================================================
# qr_login
# ===========================================================================
def _qr_responder(*, verify_status=400):
    """A canned responder for the full secondary-device QR flow.

    ``verifyCertificate`` is forced to ``400 NOT_CERTIFICATED`` so the flow
    falls through to the PIN sub-flow (first-login path).
    """
    verify = FakeResp(verify_status, {"error": {"code": 43, "message": "NOT_CERTIFICATED"}})
    return route(
        {
            "createSession": {"authSessionId": "SESSION-1"},
            "createQrCode": {
                "callbackUrl": "https://line.me/R/au?t=abc",
                "longPollingIntervalSec": 1,
                "longPollingMaxCount": 2,
            },
            "checkQrCodeVerified": {"ok": True},
            "verifyCertificate": verify,
            "createPinCode": {"pinCode": "778899"},
            "checkPinCodeVerified": {"ok": True},
            "qrCodeLoginV2": {
                "type": 1,
                "certificate": "QR-CERT",
                "tokenV3IssueResult": {
                    "accessToken": "QR-ACCESS",
                    "refreshToken": "QR-REFRESH",
                },
                "mid": "u" + "9" * 32,
            },
        }
    )


def test_qr_login_full_flow(last_request):
    """qr_login: drives session->qr->pin->tokens, embeds secret in the QR URL."""
    api = build_api(_qr_responder(), access_token=None, bridge=FakeBridge())

    seen = {}
    qr_login_kw = {
        "on_qr": lambda url: seen.setdefault("qr", url),
        "on_pin": lambda pin: seen.setdefault("pin", pin),
        "wait_seconds": 0.01,  # keep the long-poll budget tiny
    }
    result = api.auth.qr_login(**qr_login_kw)

    # --- on_qr received a URL carrying the e2ee secret --------------------
    assert "qr" in seen
    qr_url = seen["qr"]
    assert "secret=" in qr_url
    assert "e2eeVersion=1" in qr_url
    # the original query param survived the rewrite
    assert "t=abc" in qr_url

    # --- on_pin was called with the server PIN ----------------------------
    assert seen.get("pin") == "778899"

    # --- tokens issued + adopted ------------------------------------------
    assert result.access_token == "QR-ACCESS"
    assert result.refresh_token == "QR-REFRESH"
    assert result.certificate == "QR-CERT"
    assert api.transport.tokens.access_token == "QR-ACCESS"
    assert api.transport.tokens.refresh_token == "QR-REFRESH"


def test_qr_login_visits_pin_endpoints_when_not_certificated():
    """When verifyCertificate fails, the PIN endpoints are exercised."""
    api = build_api(_qr_responder(), access_token=None, bridge=FakeBridge())

    api.auth.qr_login(on_qr=lambda u: None, on_pin=lambda p: None, wait_seconds=0.01)

    urls = "\n".join(c["url"] for c in api.transport.session.calls)
    assert "createPinCode" in urls
    assert "checkPinCodeVerified" in urls
    assert urls.rstrip().endswith("qrCodeLoginV2")


def test_qr_login_skips_pin_when_certificate_verifies():
    """A returning device (verifyCertificate OK) skips the PIN sub-flow."""
    responder = _qr_responder(verify_status=200)  # verify now returns OK
    api = build_api(responder, access_token=None, bridge=FakeBridge())

    pins = []
    result = api.auth.qr_login(
        on_qr=lambda u: None,
        on_pin=lambda p: pins.append(p),
        certificate="EXISTING-CERT",
        wait_seconds=0.01,
    )

    assert pins == []  # on_pin never fired
    urls = "\n".join(c["url"] for c in api.transport.session.calls)
    assert "createPinCode" not in urls
    assert result.access_token == "QR-ACCESS"


def test_qr_login_uses_the_session_bridge_for_curve_keys():
    """The Curve25519 keypair comes from the shared LTSM bridge."""
    bridge = FakeBridge()
    api = build_api(_qr_responder(), access_token=None, bridge=bridge)

    captured = {}
    api.auth.qr_login(
        on_qr=lambda u: captured.setdefault("u", u), on_pin=lambda p: None, wait_seconds=0.01
    )

    # FakeBridge.e2ee_public_key(1) -> b64 of bytes([1]) * 32. The QR URL
    # carries it as a (URL-encoded) ``secret`` query parameter.
    import base64
    from urllib.parse import parse_qs, urlsplit

    expected_secret = base64.b64encode(bytes([1]) * 32).decode("ascii")
    query = parse_qs(urlsplit(captured["u"]).query)
    assert query.get("secret") == [expected_secret]  # parse_qs URL-decodes it
    assert query.get("e2eeVersion") == ["1"]


# ===========================================================================
# _append_secret
# ===========================================================================
def test_append_secret_adds_secret_and_e2ee_version():
    """_append_secret keeps existing params and appends secret + e2eeVersion."""
    out = _append_secret("https://line.me/R/au?foo=bar", "PUB+KEY/b64==")

    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(out)
    q = parse_qs(parts.query)
    assert q["foo"] == ["bar"]  # original preserved
    assert q["secret"] == ["PUB+KEY/b64=="]
    assert q["e2eeVersion"] == ["1"]
    assert parts.scheme == "https"
    assert parts.netloc == "line.me"


def test_append_secret_on_url_without_query():
    """A URL with no query string still gets both params appended."""
    out = _append_secret("https://line.me/R/au", "ABC")

    assert "secret=ABC" in out
    assert "e2eeVersion=1" in out
    assert out.startswith("https://line.me/R/au?")


def test_append_secret_overwrites_existing_secret():
    """A pre-existing secret/e2eeVersion is replaced, not duplicated."""
    out = _append_secret("https://line.me/R/au?secret=OLD&e2eeVersion=9", "NEW")

    from urllib.parse import parse_qs, urlsplit

    q = parse_qs(urlsplit(out).query)
    assert q["secret"] == ["NEW"]
    assert q["e2eeVersion"] == ["1"]


# ===========================================================================
# LoginResult.parse
# ===========================================================================
def test_login_result_parse_full_token_payload():
    """parse() pulls tokens out of tokenV3IssueResult and flags success."""
    data = {
        "type": 1,
        "certificate": "C",
        "mid": "uMID",
        "tokenV3IssueResult": {"accessToken": "AT", "refreshToken": "RT"},
    }
    res = LoginResult.parse(data)

    assert res.type == 1
    assert res.success is True
    assert res.access_token == "AT"
    assert res.refresh_token == "RT"
    assert res.certificate == "C"
    assert res.mid == "uMID"
    assert res.raw is data


def test_login_result_parse_falls_back_to_auth_token():
    """When there's no tokenV3IssueResult, parse() reads legacy authToken."""
    res = LoginResult.parse({"type": 1, "authToken": "LEGACY"})

    assert res.access_token == "LEGACY"
    assert res.refresh_token is None


def test_login_result_parse_defaults_type_to_success():
    """A payload with no 'type' defaults to SUCCESS (1)."""
    res = LoginResult.parse({})

    assert res.type == 1
    assert res.success is True


def test_login_result_parse_non_success_type():
    """A non-1 type is reported and 'success' is False."""
    res = LoginResult.parse({"type": 3, "pinCode": "0000", "verifier": "V"})

    assert res.type == 3
    assert res.success is False
    assert res.pin_code == "0000"
    assert res.verifier == "V"


# ===========================================================================
# refresh_access_token
# ===========================================================================
def test_refresh_access_token_updates_tokens(last_request):
    """refresh_access_token swaps in the new access (and refresh) token."""
    data = {"tokenV3IssueResult": {"accessToken": "FRESH-AT", "refreshToken": "FRESH-RT"}}
    responder = route({"tokenRefresh": data})
    api = build_api(responder, access_token="STALE")
    api.transport.tokens.refresh_token = "OLD-RT"

    out = api.auth.refresh_access_token()

    assert out == "FRESH-AT"
    assert api.transport.tokens.access_token == "FRESH-AT"
    assert api.transport.tokens.refresh_token == "FRESH-RT"

    # we sent the held refresh token to /api/auth/tokenRefresh
    body = last_request(api)
    assert body == {"refreshToken": "OLD-RT"}
    assert api.transport.session.last["url"].endswith("/api/auth/tokenRefresh")


def test_refresh_access_token_accepts_explicit_token():
    """An explicit refresh token overrides the stored one."""
    data = {"accessToken": "FROM-EXPLICIT"}  # flat shape, no tokenV3IssueResult
    responder = route({"tokenRefresh": data})
    api = build_api(responder, access_token=None)

    out = api.auth.refresh_access_token("PASSED-RT")

    assert out == "FROM-EXPLICIT"
    assert api.transport.tokens.access_token == "FROM-EXPLICIT"


def test_refresh_access_token_without_token_raises():
    """No refresh token anywhere -> LineAuthError before any HTTP call."""
    api = build_api(route({}), access_token="X")
    # no refresh token stored, none passed
    with pytest.raises(LineAuthError):
        api.auth.refresh_access_token()


def test_refresh_access_token_no_access_in_response_raises():
    """A response with no access token surfaces a LineAuthError."""
    responder = route({"tokenRefresh": {"somethingElse": True}})
    api = build_api(responder, access_token="X")

    with pytest.raises(LineAuthError):
        api.auth.refresh_access_token("RT")


# ===========================================================================
# _poll  (long-poll retry helper)
# ===========================================================================
def test_poll_retries_on_410_then_succeeds():
    """_poll keeps retrying while the server returns 410, then returns ok."""
    flows = AuthFlows(build_api(route({})).transport)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise LineApiError("poll window elapsed", status=410)
        return "DONE"

    assert flows._poll(flaky, max_count=5) == "DONE"
    assert calls["n"] == 3  # two 410s, then success


def test_poll_gives_up_after_max_count_and_reraises():
    """If every attempt times out (410), the last error is re-raised."""
    flows = AuthFlows(build_api(route({})).transport)

    def always_timeout():
        raise LineApiError("still waiting", status=410)

    with pytest.raises(LineApiError) as ei:
        flows._poll(always_timeout, max_count=3)
    assert ei.value.status == 410


def test_poll_propagates_non_retryable_errors_immediately():
    """A non-408/410 LineApiError is raised straight through (no retry)."""
    flows = AuthFlows(build_api(route({})).transport)

    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise LineApiError("forbidden", status=403)

    with pytest.raises(LineApiError) as ei:
        flows._poll(boom, max_count=5)
    assert ei.value.status == 403
    assert calls["n"] == 1  # raised on first attempt, no retry

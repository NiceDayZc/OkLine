"""Offline tests for :mod:`okline.auth` driven through :class:`OkLine`.

Everything here is fully offline:

* the HTTP layer is faked via the shared ``conftest`` helpers
  (``build_api`` / ``route`` / ``enveloped`` / ``FakeResp``);
* the LTSM Node bridge is faked via ``FakeBridge`` so QR + E2EE e-mail login
  work without Node.js or a real WASM bridge.

We generate a throwaway RSA keypair so the ``email_login`` password field is a
*real* PKCS#1 v1.5 ciphertext we can decrypt and verify, rather than a mock —
and likewise re-derive the E2EE ``secret`` from the recorded 6-digit code.
"""

from __future__ import annotations

import base64
import binascii
import json

import pytest
from conftest import FakeBridge, FakeResp, build_api, enveloped, route
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from okline.auth import AuthFlows, LoginResult, _append_secret
from okline.crypto import (
    decrypt_e2ee_login_secret,
    encrypt_e2ee_login_secret,
    generate_e2ee_login_code,
)
from okline.enums import LoginType
from okline.exceptions import LineApiError, LineAuthError


# ---------------------------------------------------------------------------
# RSA key fixture: a real keypair whose public half feeds getRSAKeyInfo and
# whose private half lets us decrypt + verify the LoginRequest.password blob.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def rsa_key():
    """A small (but valid) RSA keypair plus its getRSAKeyInfo dict form."""
    from cryptography.hazmat.primitives.asymmetric import rsa

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
    from cryptography.hazmat.primitives.asymmetric import padding

    ciphertext = binascii.unhexlify(hex_password)
    return priv.decrypt(ciphertext, padding.PKCS1v15())


def _bodies(api, suffix: str) -> list:
    """The decoded JSON bodies of every POST sent to ``suffix``, in order."""
    out = []
    for c in api.transport.session.calls:
        if c["url"].endswith(suffix) and c.get("data") is not None:
            raw = c["data"]
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            out.append(json.loads(raw))
    return out


def _calls_to(api, fragment: str) -> list[dict]:
    return [c for c in api.transport.session.calls if fragment in c["url"]]


# ===========================================================================
# email_login — request construction + E2EE secret
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
    api = build_api(responder, access_token=None, bridge=FakeBridge())

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


def test_email_login_e2ee_secret_is_decryptable_with_the_recorded_code(rsa_key, last_request):
    """The E2EE tier sends a real `secret`: the per-block AES-CBC encryption
    of the bridge curve public key under SHA-256(6-digit code), zero IV."""
    _priv, info = rsa_key
    success = {"type": 1, "tokenV3IssueResult": {"accessToken": "A"}}
    responder = route({"getRSAKeyInfo": info, "loginV2": success})
    bridge = FakeBridge()
    api = build_api(responder, access_token=None, bridge=bridge)

    api.auth.email_login("me@example.com", "pw")

    req = last_request(api)[0]
    secret = req["secret"]
    assert secret  # no longer the empty string the audit flagged

    # the 6-digit code (the PIN the user confirms) is recorded on the flows
    e2ee = api.auth.last_email_e2ee
    assert e2ee is not None
    code = e2ee["code"]
    assert isinstance(code, str) and len(code) == 6 and code.isdigit()
    assert e2ee["curve_key_id"] == bridge._key

    # secret is valid base64 decrypting (with the code) to the bridge pubkey:
    # FakeBridge.e2ee_public_key(1) -> b64(bytes([1]) * 32)
    public_key = bytes([1]) * 32
    assert decrypt_e2ee_login_secret(secret, code) == public_key

    # a wrong code must NOT reproduce the public key
    wrong = "000000" if code != "000000" else "000001"
    assert decrypt_e2ee_login_secret(secret, wrong) != public_key


def test_email_login_without_e2ee_uses_plain_credential_type(rsa_key, last_request):
    """with_e2ee=False switches the LoginRequest type to ID_CREDENTIAL (0) and
    keeps `secret` empty (no bridge needed on this path)."""
    _priv, info = rsa_key
    success = {"type": 1, "tokenV3IssueResult": {"accessToken": "A"}}
    responder = route({"getRSAKeyInfo": info, "loginV2": success})
    api = build_api(responder, access_token=None)  # no bridge!

    api.auth.email_login("u@x.io", "pw", with_e2ee=False)

    req = last_request(api)[0]
    assert req["type"] == 0  # LoginType.ID_CREDENTIAL
    assert req["secret"] == ""


def test_email_login_targets_the_right_endpoints(rsa_key):
    """email_login hits getRSAKeyInfo first, then loginV2 (last URL)."""
    _priv, info = rsa_key
    success = {"type": 1, "tokenV3IssueResult": {"accessToken": "A"}}
    responder = route({"getRSAKeyInfo": info, "loginV2": success})
    api = build_api(responder, access_token=None, bridge=FakeBridge())

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
    api = build_api(responder, access_token="OLD-TOKEN", bridge=FakeBridge())

    result = api.auth.email_login("u@x.io", "pw")

    assert result.success is False
    assert result.pin_code == "1234"
    assert result.access_token is None
    # original token preserved (nothing adopted)
    assert api.transport.tokens.access_token == "OLD-TOKEN"


# ===========================================================================
# email_login — REQUIRE_DEVICE_CONFIRM continuations (JQ / LF1)
# ===========================================================================
def _jq_responder(info, challenge, success):
    """Responder driving the non-E2EE device-confirm sequence:
    loginV2(type-3) -> GET long-polling/JQ -> loginV2(SUCCESS)."""
    state = {"login": 0}

    def responder(method, url, kw):
        if url.endswith("getRSAKeyInfo"):
            return enveloped(info)
        if url.endswith("Talk/AuthService/loginV2"):
            state["login"] += 1
            return enveloped(challenge if state["login"] == 1 else success)
        if url.endswith("long-polling/JQ"):
            return enveloped({"result": {"verifier": "CONFIRMED-V"}})
        return enveloped({})

    return responder


def test_email_login_confirm_device_jq_flow(rsa_key):
    """Non-E2EE continuation: JQ long-poll (X-LST=180000, session id = the
    type-3 verifier) then loginV2(type=QRCODE, verifier)."""
    _priv, info = rsa_key
    challenge = {"type": 3, "pinCode": "123456", "verifier": "VER-1"}
    success = {
        "type": 1,
        "certificate": "CERT-9",
        "tokenV3IssueResult": {"accessToken": "A9", "refreshToken": "R9"},
    }
    api = build_api(_jq_responder(info, challenge, success), access_token=None)

    pins: list[str] = []
    result = api.auth.email_login(
        "u@x.io",
        "pw",
        with_e2ee=False,
        confirm_device=True,
        on_pin=pins.append,
        wait_seconds=0.01,
    )

    # the server pinCode was displayed
    assert pins == ["123456"]

    # --- the JQ long-poll: GET with the right headers ---------------------
    jq = _calls_to(api, "long-polling/JQ")
    assert len(jq) == 1
    assert jq[0]["method"] == "GET"
    # Bundle-exact: X-Line-Session-ID carries the loginV2 *verifier* (the
    # pin is display-only) — main.js @2119316 binds the header to the poll
    # helper's second argument, which the caller fills with loginResult.verifier.
    assert jq[0]["headers"]["X-Line-Session-ID"] == "VER-1"
    assert jq[0]["headers"]["X-LST"] == "180000"

    # --- final relogin: type=QRCODE(1) + the confirmed verifier ------------
    logins = _bodies(api, "Talk/AuthService/loginV2")
    assert len(logins) == 2
    final = logins[1][0]
    assert final["type"] == int(LoginType.QRCODE)
    assert final["verifier"] == "CONFIRMED-V"
    assert final["identifier"] == ""
    assert final["password"] == ""
    assert final["certificate"] == ""

    # --- SUCCESS result adopted + certificate remembered per email ---------
    assert result.success is True
    assert result.access_token == "A9"
    assert api.transport.tokens.access_token == "A9"
    assert api.auth.email_certificates["u@x.io"] == "CERT-9"


def test_email_login_confirm_device_jq_pin_timeout(rsa_key):
    """A 410 from the JQ poll is terminal PIN_CODE_TIMEOUT (the extension's
    shared error handler never retries it for the device-confirm polls) —
    raise immediately instead of re-polling the expired PIN."""
    _priv, info = rsa_key
    challenge = {"type": 3, "pinCode": "123456", "verifier": "VER-1"}

    def responder(method, url, kw):
        if url.endswith("getRSAKeyInfo"):
            return enveloped(info)
        if url.endswith("loginV2"):
            return enveloped(challenge)
        if url.endswith("long-polling/JQ"):
            return FakeResp(410, {"message": "POLL_TIMEOUT"})
        return enveloped({})

    api = build_api(responder, access_token=None)
    with pytest.raises(LineAuthError) as ei:
        api.auth.email_login(
            "u@x.io", "pw", with_e2ee=False, confirm_device=True, wait_seconds=0.01
        )
    assert "PIN code timeout" in str(ei.value)
    assert ei.value.status == 410
    # terminal — the expired PIN is not polled again
    assert len(_calls_to(api, "long-polling/JQ")) == 1


def test_email_login_confirm_device_jq_nonjson_body_retries(rsa_key):
    """A 200 whose body is not valid JSON is treated like any other poll
    anomaly (retry), not an uncaught decode error."""
    _priv, info = rsa_key
    challenge = {"type": 3, "pinCode": "123456", "verifier": "VER-1"}
    success = {
        "type": 1,
        "certificate": "CERT-9",
        "tokenV3IssueResult": {"accessToken": "A9", "refreshToken": "R9"},
    }
    state = {"login": 0, "jq": 0}

    def responder(method, url, kw):
        if url.endswith("getRSAKeyInfo"):
            return enveloped(info)
        if url.endswith("loginV2"):
            state["login"] += 1
            return enveloped(challenge if state["login"] == 1 else success)
        if url.endswith("long-polling/JQ"):
            state["jq"] += 1
            if state["jq"] == 1:
                return FakeResp(200, "")  # empty keep-alive-ish body
            return enveloped({"result": {"verifier": "CONFIRMED-V"}})
        return enveloped({})

    api = build_api(responder, access_token=None)
    result = api.auth.email_login(
        # wait_seconds must span >1 JQ window (180s) so a retry attempt exists
        "u@x.io",
        "pw",
        with_e2ee=False,
        confirm_device=True,
        wait_seconds=360,
    )
    assert result.success is True
    assert len(_calls_to(api, "long-polling/JQ")) == 2  # retried past the bad body


def _lf1_responder(info, success):
    """Responder driving the E2EE device-confirm sequence:
    loginV2(type-3, E2EE) -> GET long-polling/LF1 -> confirmE2EELogin ->
    loginV2(type=QRCODE, verifier) -> SUCCESS."""
    state = {"login": 0}

    def responder(method, url, kw):
        if url.endswith("getRSAKeyInfo"):
            return enveloped(info)
        if url.endswith("Talk/AuthService/loginV2"):
            state["login"] += 1
            if state["login"] == 1:
                return enveloped({"type": 3, "verifier": "VER-1"})
            return enveloped(success)
        if url.endswith("long-polling/LF1"):
            return enveloped(
                {
                    "result": {
                        "metadata": {
                            "publicKey": "PRIMARY-PUB-B64",
                            "encryptedKeyChain": "PRIMARY-KC-B64",
                        }
                    }
                }
            )
        if url.endswith("Talk/AuthService/confirmE2EELogin"):
            return enveloped("VER-2")
        return enveloped({})

    return responder


def test_email_login_confirm_device_lf1_flow(rsa_key):
    """E2EE continuation: LF1 long-poll (X-LST=110000, session id = verifier),
    channel + hash key chain, confirmE2EELogin, QRCODE relogin."""
    _priv, info = rsa_key
    success = {
        "type": 1,
        "certificate": "CERT-E",
        "tokenV3IssueResult": {"accessToken": "AE", "refreshToken": "RE"},
    }
    api = build_api(_lf1_responder(info, success), access_token=None, bridge=FakeBridge())

    pins: list[str] = []
    result = api.auth.email_login(
        "u@x.io",
        "pw",
        confirm_device=True,  # with_e2ee=True (default)
        on_pin=pins.append,
        wait_seconds=0.01,
    )

    # --- the first loginV2 was the E2EE tier with a real secret ------------
    logins = _bodies(api, "Talk/AuthService/loginV2")
    assert len(logins) == 2
    assert logins[0][0]["type"] == 2
    assert logins[0][0]["secret"]

    # the displayed PIN is the locally generated 6-digit code
    e2ee = api.auth.last_email_e2ee
    assert e2ee is not None
    code = e2ee["code"]
    assert pins == [code]

    # --- the LF1 long-poll --------------------------------------------------
    lf1 = _calls_to(api, "long-polling/LF1")
    assert len(lf1) == 1
    assert lf1[0]["method"] == "GET"
    assert lf1[0]["headers"]["X-Line-Session-ID"] == "VER-1"
    assert lf1[0]["headers"]["X-LST"] == "110000"

    # --- confirmE2EELogin(verifier, b64(hashKeyChain)) ---------------------
    confirms = _bodies(api, "Talk/AuthService/confirmE2EELogin")
    assert len(confirms) == 1
    # FakeBridge: e2ee_create_channel(1, "PRIMARY-PUB-B64") -> 1001, and the
    # fake hash-key-chain op returns b64("hashchain:<channel>").
    expected_chain = base64.b64encode(b"hashchain:1001").decode("ascii")
    assert confirms[0] == ["VER-1", expected_chain]

    # unwrapped E2EE key handles recorded for a later E2EEManager
    assert e2ee["key_handles"] == [1, 2]

    # --- final relogin: type=QRCODE(1) + the confirmed verifier -------------
    final = logins[1][0]
    assert final["type"] == int(LoginType.QRCODE)
    assert final["verifier"] == "VER-2"

    assert result.success is True
    assert result.access_token == "AE"
    assert api.transport.tokens.access_token == "AE"
    assert api.auth.email_certificates["u@x.io"] == "CERT-E"


def test_email_login_confirm_device_lf1_requires_hash_key_chain_op(rsa_key):
    """A bridge without the hash-key-chain op fails with a clear error."""
    _priv, info = rsa_key
    success = {"type": 1, "tokenV3IssueResult": {"accessToken": "AE"}}

    class _NoHashChainBridge(FakeBridge):
        # the real LTSM bridge does not expose this op yet (deferred)
        e2ee_generate_hash_key_chain_to_confirm_e2ee = None  # type: ignore[assignment]

    api = build_api(
        _lf1_responder(info, success), access_token=None, bridge=_NoHashChainBridge()
    )

    with pytest.raises(LineAuthError, match="hash_key_chain"):
        api.auth.email_login("u@x.io", "pw", confirm_device=True, wait_seconds=0.01)


def test_email_login_without_confirm_device_keeps_old_behaviour(rsa_key):
    """confirm_device is opt-in: a type-3 result is returned untouched."""
    _priv, info = rsa_key
    challenge = {"type": 3, "pinCode": "1234", "verifier": "V"}
    responder = route({"getRSAKeyInfo": info, "loginV2": challenge})
    api = build_api(responder, access_token=None, bridge=FakeBridge())

    result = api.auth.email_login("u@x.io", "pw")

    assert result.type == 3
    # no long-poll endpoints were touched
    urls = [c["url"] for c in api.transport.session.calls]
    assert not any("long-polling" in u for u in urls)


# ===========================================================================
# email_login_ladder — the extension's S/A/C/_ strategy
# ===========================================================================
def _success_body(cert: str = "CERT-NEW") -> dict:
    return {
        "type": 1,
        "certificate": cert,
        "tokenV3IssueResult": {"accessToken": "A", "refreshToken": "R"},
    }


def test_ladder_uses_stored_certificate_first(rsa_key):
    """Tier 1 ("A"): a stored per-email certificate is tried with type=ID_CREDENTIAL."""
    _priv, info = rsa_key
    responder = route({"getRSAKeyInfo": info, "loginV2": _success_body()})
    api = build_api(responder, access_token=None)
    api.auth.email_certificates["u@x.io"] = "CERT-A"

    result = api.auth.email_login_ladder("u@x.io", "pw")

    assert result.success is True
    logins = _bodies(api, "Talk/AuthService/loginV2")
    assert len(logins) == 1
    req = logins[0][0]
    assert req["type"] == 0  # ID_CREDENTIAL with the stored certificate
    assert req["certificate"] == "CERT-A"
    # the fresh certificate replaced the stored one
    assert api.auth.email_certificates["u@x.io"] == "CERT-NEW"


def test_ladder_without_certificate_starts_at_e2ee_tier(rsa_key):
    """No stored certificate -> tier 2 ("C"): E2EE login with a real secret."""
    _priv, info = rsa_key
    responder = route({"getRSAKeyInfo": info, "loginV2": _success_body()})
    api = build_api(responder, access_token=None, bridge=FakeBridge())

    result = api.auth.email_login_ladder("u@x.io", "pw")

    assert result.success is True
    logins = _bodies(api, "Talk/AuthService/loginV2")
    assert len(logins) == 1
    req = logins[0][0]
    assert req["type"] == 2  # ID_CREDENTIAL_WITH_E2EE
    assert req["secret"]
    assert req["certificate"] == ""  # the E2EE tier never sends a certificate
    assert api.auth.email_certificates["u@x.io"] == "CERT-NEW"


def _fallback_responder(info, first_resp, success):
    state = {"login": 0}

    def responder(method, url, kw):
        if url.endswith("getRSAKeyInfo"):
            return enveloped(info)
        if url.endswith("Talk/AuthService/loginV2"):
            state["login"] += 1
            if state["login"] == 1:
                return first_resp
            return enveloped(success)
        return enveloped({})

    return responder


@pytest.mark.parametrize(
    "code",
    [89, 94, 97],  # E2EE_SENDER_NOT_ALLOWED / UPDATE_PRIMARY_DEVICE / NOT_SUPPORT
)
def test_ladder_falls_back_to_plain_on_e2ee_error_codes(rsa_key, code):
    """Tier 2 -> tier 3 ("_" plain login) on the bundle's fallback codes."""
    _priv, info = rsa_key
    first = FakeResp(400, {"error": {"code": code, "message": "E2EE not allowed"}})
    api = build_api(
        _fallback_responder(info, first, _success_body()),
        access_token=None,
        bridge=FakeBridge(),
    )

    result = api.auth.email_login_ladder("u@x.io", "pw")

    assert result.success is True
    logins = _bodies(api, "Talk/AuthService/loginV2")
    assert len(logins) == 2
    assert logins[0][0]["type"] == 2  # E2EE tier attempted first (with secret)
    assert logins[0][0]["secret"]
    assert logins[1][0]["type"] == 0  # plain fallback
    assert logins[1][0]["secret"] == ""


def test_ladder_does_not_fall_back_on_other_errors(rsa_key):
    """An unrelated loginV2 error propagates (no silent fallback)."""
    _priv, info = rsa_key
    first = FakeResp(400, {"error": {"code": 5, "message": "INVALID_IDENTITY_CREDENTIAL"}})
    api = build_api(
        _fallback_responder(info, first, _success_body()),
        access_token=None,
        bridge=FakeBridge(),
    )

    with pytest.raises(LineApiError):
        api.auth.email_login_ladder("u@x.io", "pw")


def test_ladder_certificate_require_device_confirm_goes_to_e2ee_tier(rsa_key):
    """Tier 1 type-3 ("A" -> "C"): the E2EE tier runs next, not the JQ poll."""
    _priv, info = rsa_key
    state = {"login": 0}

    def responder(method, url, kw):
        if url.endswith("getRSAKeyInfo"):
            return enveloped(info)
        if url.endswith("Talk/AuthService/loginV2"):
            state["login"] += 1
            if state["login"] == 1:
                return enveloped({"type": 3, "pinCode": "111222", "verifier": "V1"})
            return enveloped(_success_body())
        return enveloped({})

    api = build_api(responder, access_token=None, bridge=FakeBridge())
    api.auth.email_certificates["u@x.io"] = "CERT-A"

    result = api.auth.email_login_ladder("u@x.io", "pw")

    assert result.success is True
    logins = _bodies(api, "Talk/AuthService/loginV2")
    assert len(logins) == 2
    assert logins[0][0]["type"] == 0 and logins[0][0]["certificate"] == "CERT-A"
    assert logins[1][0]["type"] == 2 and logins[1][0]["secret"]
    # the E2EE tier succeeded, so no device-confirm poll happened
    urls = [c["url"] for c in api.transport.session.calls]
    assert not any("long-polling" in u for u in urls)


def test_ladder_certificate_my_key_not_available_goes_to_e2ee_tier(rsa_key):
    """Tier 1 error E2EE_MY_KEY_NOT_AVAILABLE ("A" -> "C") — a *string* error
    id in the bundle, matched wherever the transport surfaces it."""
    _priv, info = rsa_key
    first = FakeResp(400, {"error": {"code": 5, "message": "e2ee_my_key_not_available"}})
    api = build_api(
        _fallback_responder(info, first, _success_body()),
        access_token=None,
        bridge=FakeBridge(),
    )
    api.auth.email_certificates["u@x.io"] = "CERT-A"

    result = api.auth.email_login_ladder("u@x.io", "pw")

    assert result.success is True
    logins = _bodies(api, "Talk/AuthService/loginV2")
    assert len(logins) == 2
    assert logins[0][0]["type"] == 0
    assert logins[1][0]["type"] == 2


def test_ladder_e2ee_confirm_device_runs_lf1(rsa_key):
    """Full ladder: no cert -> E2EE tier type-3 -> LF1 confirm -> QRCODE."""
    _priv, info = rsa_key
    success = {
        "type": 1,
        "certificate": "CERT-L",
        "tokenV3IssueResult": {"accessToken": "AL", "refreshToken": "RL"},
    }
    state = {"login": 0}

    def responder(method, url, kw):
        if url.endswith("getRSAKeyInfo"):
            return enveloped(info)
        if url.endswith("Talk/AuthService/loginV2"):
            state["login"] += 1
            if state["login"] == 1:
                return enveloped({"type": 3, "verifier": "VER-1"})
            return enveloped(success)
        if url.endswith("long-polling/LF1"):
            return enveloped(
                {
                    "result": {
                        "metadata": {
                            "publicKey": "PRIMARY-PUB-B64",
                            "encryptedKeyChain": "PRIMARY-KC-B64",
                        }
                    }
                }
            )
        if url.endswith("Talk/AuthService/confirmE2EELogin"):
            return enveloped("VER-2")
        return enveloped({})

    api = build_api(responder, access_token=None, bridge=FakeBridge())

    result = api.auth.email_login_ladder("u@x.io", "pw", wait_seconds=0.01)

    assert result.success is True
    logins = _bodies(api, "Talk/AuthService/loginV2")
    assert [b[0]["type"] for b in logins] == [2, 1]  # E2EE tier, then QRCODE
    assert logins[1][0]["verifier"] == "VER-2"
    assert api.auth.email_certificates["u@x.io"] == "CERT-L"


# ===========================================================================
# crypto — the E2EE login secret scheme
# ===========================================================================
def test_generate_e2ee_login_code_is_six_digits():
    codes = {generate_e2ee_login_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert len(codes) > 100  # actually random, zero-padded


def test_e2ee_login_secret_round_trip():
    """encrypt -> decrypt reproduces the public key (2 blocks = 32 bytes)."""
    public_key = bytes(range(32))
    code = "424242"

    secret = encrypt_e2ee_login_secret(public_key, code)

    raw = base64.b64decode(secret, validate=True)
    assert len(raw) == 32  # one ciphertext block per 16-byte pubkey block
    assert decrypt_e2ee_login_secret(secret, code) == public_key


def test_e2ee_login_secret_matches_webcrypto_padded_cbc():
    """Each block equals the first 16 bytes of a PKCS#7-padded WebCrypto
    AES-CBC encryption under SHA-256(code) with a zero IV."""
    public_key = bytes(range(32))
    code = "999999"
    key = __import__("hashlib").sha256(code.encode()).digest()
    iv = bytes(16)

    # manual: pad each 16-byte block to 32 bytes (full PKCS#7 block), CBC
    # encrypt, keep the first 16 bytes — the extension's slice(0, 16).
    expected = b""
    for offset in (0, 16):
        block = public_key[offset : offset + 16] + bytes([16]) * 16
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        expected += (enc.update(block) + enc.finalize())[:16]

    assert base64.b64decode(encrypt_e2ee_login_secret(public_key, code)) == expected


def test_e2ee_login_secret_handles_short_tail_block():
    """A public key with a partial tail block pads it (like WebCrypto would)."""
    public_key = bytes(range(20))  # 16 + 4 bytes
    secret = encrypt_e2ee_login_secret(public_key, "123456")

    raw = base64.b64decode(secret)
    assert len(raw) == 32  # 2 blocks out

    # manual check of the padded 4-byte tail block
    key = __import__("hashlib").sha256(b"123456").digest()
    tail = public_key[16:] + bytes([12]) * 12  # pad to 16
    enc = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).encryptor()
    expected_tail = (enc.update(tail) + enc.finalize())[:16]
    assert raw[16:] == expected_tail


def test_e2ee_login_secret_reuses_zero_iv_per_block():
    """Two identical pubkey blocks produce identical ciphertext (same zero IV
    for every block — this is *not* chained CBC)."""
    public_key = bytes([7]) * 32
    raw = base64.b64decode(encrypt_e2ee_login_secret(public_key, "000001"))
    assert raw[:16] == raw[16:]


# ===========================================================================
# qr_login
# ===========================================================================
def _qr_responder(*, verify_status=400):
    """A canned responder for the full secondary-device QR flow.

    ``verifyCertificate`` is forced to fail (400 NOT_CERTIFICATED) so the flow
    falls through to the PIN sub-flow (first-login path); pass
    ``verify_status=200`` for the returning-device path.
    """
    if verify_status == 200:
        verify = FakeResp(200, {"message": "OK", "data": {}})
    else:
        verify = FakeResp(
            verify_status, {"error": {"code": 43, "message": "NOT_CERTIFICATED"}}
        )
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

    seen: dict = {}
    result = api.auth.qr_login(
        on_qr=lambda url: seen.setdefault("qr", url),
        on_pin=lambda pin: seen.setdefault("pin", pin),
        wait_seconds=0.01,  # keep the long-poll budget tiny
    )

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

    captured: dict = {}
    api.auth.qr_login(
        on_qr=lambda u: captured.setdefault("u", u), on_pin=lambda p: None, wait_seconds=0.01
    )

    # FakeBridge.e2ee_public_key(1) -> b64 of bytes([1]) * 32. The QR URL
    # carries it as a (URL-encoded) ``secret`` query parameter.
    from urllib.parse import parse_qs, urlsplit

    expected_secret = base64.b64encode(bytes([1]) * 32).decode("ascii")
    query = parse_qs(urlsplit(captured["u"]).query)
    assert query.get("secret") == [expected_secret]  # parse_qs URL-decodes it
    assert query.get("e2eeVersion") == ["1"]


def test_qr_login_pin_poll_uses_fixed_110000_x_lst():
    """The PIN poll's X-LST is the fixed 110000 ("rH=11e4"), independent of
    longPollingIntervalSec — only the QR-scan poll derives its X-LST."""
    api = build_api(_qr_responder(), access_token=None, bridge=FakeBridge())

    api.auth.qr_login(on_qr=lambda u: None, on_pin=lambda p: None, wait_seconds=0.01)

    scan = _calls_to(api, "checkQrCodeVerified")[0]
    pin = _calls_to(api, "checkPinCodeVerified")[0]
    assert scan["headers"]["X-LST"] == "1000"  # longPollingIntervalSec(1) * 1000
    assert pin["headers"]["X-LST"] == "110000"  # fixed rH=11e4


def test_service_qr_check_pin_code_verified_default_x_lst():
    """The AuthServiceMixin's PIN poll (a separate code path from
    AuthFlows.qr_check_pincode_verified) also defaults to the fixed X-LST
    110000 (``rH=11e4``); an explicit ``timeout_ms`` overrides it."""
    api = build_api(lambda m, u, kw: enveloped({}), access_token=None)

    api.qr_check_pin_code_verified("SESSION-1")
    call = _calls_to(api, "checkPinCodeVerified")[0]
    assert call["headers"]["X-LST"] == "110000"  # fixed rH=11e4
    assert call["headers"]["X-Line-Session-ID"] == "SESSION-1"

    api.qr_check_pin_code_verified("SESSION-1", timeout_ms=45000)
    assert _calls_to(api, "checkPinCodeVerified")[1]["headers"]["X-LST"] == "45000"


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
# token-refresh lifecycle — schedule parsing, retry policy, kickout
# (the extension's tT class, main.js @~1850300)
# ===========================================================================
_SCHEDULE_POLICY = {
    "initialDelayInMillis": "1",  # numeric strings, like the wire
    "maxDelayInMillis": "8",
    "multiplier": 2,
    "jitterRate": 0,
}


def _token_v3(access: str, refresh: str, issue: float, duration: float = 3600.0) -> dict:
    """A wire-shaped tokenV3IssueResult carrying the renewal schedule."""
    return {
        "accessToken": access,
        "refreshToken": refresh,
        "tokenIssueTimeEpochSec": str(int(issue)),
        "durationUntilRefreshInSec": str(int(duration)),
        "refreshApiRetryPolicy": dict(_SCHEDULE_POLICY),
    }


def _10202() -> FakeResp:
    return FakeResp(400, {"error": {"code": 10202, "message": "AUTH_RETRY_REQUIRED"}})


def _10201() -> FakeResp:
    return FakeResp(400, {"error": {"code": 10201, "message": "AUTH_INVALID_REQUEST"}})


def test_login_result_parse_extracts_refresh_schedule():
    """parse() pulls the renewal schedule + retry policy out of a
    tokenV3IssueResult (numeric strings coerced, like the bundle's Number())."""
    data = {
        "type": 1,
        "tokenV3IssueResult": _token_v3("AT", "RT", 1700000000, 3600),
    }
    res = LoginResult.parse(data)

    assert res.token_issue_time_epoch_sec == 1700000000.0
    assert res.duration_until_refresh_sec == 3600.0
    assert res.refresh_api_retry_policy == _SCHEDULE_POLICY
    assert res.has_refresh_schedule is True


def test_login_result_parse_without_schedule():
    """No schedule fields -> None / False (nothing to arm)."""
    res = LoginResult.parse({"type": 1, "tokenV3IssueResult": {"accessToken": "A"}})

    assert res.token_issue_time_epoch_sec is None
    assert res.duration_until_refresh_sec is None
    assert res.refresh_api_retry_policy is None
    assert res.has_refresh_schedule is False


def test_refresh_policy_parse_fills_defaults_and_rejects_garbage():
    """RefreshApiRetryPolicy.parse: sane defaults for missing/invalid fields,
    None when the server sent no (or a degenerate) policy."""
    from okline.auth import RefreshApiRetryPolicy

    assert RefreshApiRetryPolicy.parse(None) is None
    assert RefreshApiRetryPolicy.parse({}) is None
    assert RefreshApiRetryPolicy.parse("nope") is None

    # the extension's placeholder defaults ("" strings, 0 multiplier) fall
    # back to sane values instead of a degenerate loop
    empty = RefreshApiRetryPolicy.parse(
        {"initialDelayInMillis": "", "maxDelayInMillis": "", "multiplier": 0, "jitterRate": 0}
    )
    assert empty == RefreshApiRetryPolicy(
        initial_delay_ms=1000.0,
        max_delay_ms=30000.0,
        multiplier=2.0,
        jitter_rate=0.0,  # jitterRate 0 (no jitter) is legitimate
    )

    # partial server policy keeps the provided fields
    partial = RefreshApiRetryPolicy.parse({"initialDelayInMillis": 500, "multiplier": 3})
    assert partial is not None
    assert partial.initial_delay_ms == 500.0
    assert partial.multiplier == 3.0
    assert partial.max_delay_ms == 30000.0

    # degenerate policies (initial >= max) never retry -> simple behaviour
    assert (
        RefreshApiRetryPolicy.parse({"initialDelayInMillis": 5000, "maxDelayInMillis": 10})
        is None
    )


def test_email_login_records_token_schedule(rsa_key):
    """A successful email login arms the renewal schedule and fires the
    on_token_issued hook (the extension's setTokenV3IssueResult)."""
    _priv, info = rsa_key
    issue = 1700000000
    success = {
        "type": 1,
        "tokenV3IssueResult": _token_v3("A", "R", issue, 1800),
    }
    responder = route({"getRSAKeyInfo": info, "loginV2": success})
    api = build_api(responder, access_token=None, bridge=FakeBridge())

    hooked: list[dict] = []
    api.auth.on_token_issued = hooked.append
    result = api.auth.email_login("u@x.io", "pw")

    assert result.token_issue_time_epoch_sec == 1700000000.0
    assert api.auth.token_schedule == {
        "tokenIssueTimeEpochSec": 1700000000.0,
        "durationUntilRefreshInSec": 1800.0,
        "refreshApiRetryPolicy": dict(_SCHEDULE_POLICY),
    }
    assert hooked == [api.auth.token_schedule]


def test_refresh_records_schedule_from_response():
    """A tokenRefresh response carrying a schedule updates token_schedule and
    fires the hook (re-arming the proactive renewal)."""
    issue = 1700000000
    responder = route(
        {"tokenRefresh": {"tokenV3IssueResult": _token_v3("A2", "R2", issue, 900)}}
    )
    api = build_api(responder, access_token="A1")
    api.transport.tokens.refresh_token = "R1"
    hooked: list[dict] = []
    api.auth.on_token_issued = hooked.append

    assert api.auth.refresh_access_token() == "A2"

    assert api.auth.token_schedule == {
        "tokenIssueTimeEpochSec": float(issue),
        "durationUntilRefreshInSec": 900.0,
        "refreshApiRetryPolicy": dict(_SCHEDULE_POLICY),
    }
    assert hooked == [api.auth.token_schedule]


def test_refresh_retries_10202_with_backoff_then_succeeds():
    """AUTH_RETRY_REQUIRED(10202) is retried with the stored policy's backoff
    until a success arrives (tiny delays: 1ms * 2^n, no jitter)."""
    issue = 1700000000
    calls = {"n": 0}

    def responder(method, url, kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # first refresh: returns the policy that governs later retries
            return enveloped({"tokenV3IssueResult": _token_v3("A1", "R1", issue)})
        if calls["n"] == 2:
            return _10202()  # retryable
        return enveloped({"tokenV3IssueResult": _token_v3("A2", "R2", issue, 60)})

    api = build_api(responder, access_token="OLD")
    api.transport.tokens.refresh_token = "R0"

    assert api.auth.refresh_access_token() == "A1"  # arms the schedule+policy
    assert api.auth.refresh_access_token() == "A2"  # 10202 -> backoff -> success

    assert calls["n"] == 3
    assert api.transport.tokens.access_token == "A2"
    assert api.transport.tokens.refresh_token == "R2"
    # the retry sent the same (still-valid) refresh token
    bodies = _bodies(api, "tokenRefresh")
    assert bodies[1] == {"refreshToken": "R1"}


def test_refresh_backoff_is_exponential_jittered_and_bounded(monkeypatch):
    """The backoff sleeps grow initial*multiplier^n, each jittered by
    ±jitterRate, and stop once the delay reaches maxDelayInMillis."""
    import okline.auth as auth_mod

    sleeps: list[float] = []
    monkeypatch.setattr(auth_mod.time, "sleep", lambda s: sleeps.append(s))

    # policy: initial=100ms, max=1000ms, multiplier=2, jitter ±25%
    # attempt delays: 100, 200, 400, 800 -> after 800 the grown delay (1600)
    # exceeds max -> budget exhausted.  4 attempts, sleeps [100, 200, 400, 800]*j.
    api = build_api(lambda m, u, kw: _10202(), access_token="X")
    api.auth.token_schedule = {
        "tokenIssueTimeEpochSec": 1700000000.0,
        "durationUntilRefreshInSec": 3600.0,
        "refreshApiRetryPolicy": {
            "initialDelayInMillis": 100,
            "maxDelayInMillis": 1000,
            "multiplier": 2,
            "jitterRate": 0.25,
        },
    }

    with pytest.raises(LineAuthError, match="retry budget"):
        api.auth.refresh_access_token("RT")

    assert len(_calls_to(api, "tokenRefresh")) == 4
    assert sleeps, "backoff sleeps were recorded"
    for delay_ms, slept in zip([100.0, 200.0, 400.0, 800.0], sleeps):
        assert delay_ms * 0.75 / 1000.0 <= slept <= delay_ms * 1.25 / 1000.0
    # no 5th attempt: the grown delay (1600ms) >= max (1000ms)
    assert len(_calls_to(api, "tokenRefresh")) == 4


@pytest.mark.parametrize("with_policy", [True, False], ids=["policy", "no-policy"])
def test_refresh_10201_is_a_hard_kickout(with_policy):
    """AUTH_INVALID_REQUEST(10201) during renewal raises LineAuthError
    (re-login required) — with or without a stored retry policy."""
    api = build_api(route({"tokenRefresh": _10201()}), access_token="X")
    if with_policy:
        api.auth.token_schedule = {
            "tokenIssueTimeEpochSec": 1700000000.0,
            "durationUntilRefreshInSec": 3600.0,
            "refreshApiRetryPolicy": dict(_SCHEDULE_POLICY),
        }

    with pytest.raises(LineAuthError, match="AUTH_INVALID_REQUEST") as ei:
        api.auth.refresh_access_token("RT")

    assert "re-login" in str(ei.value)
    # terminal: exactly one attempt, no retries
    assert len(_calls_to(api, "tokenRefresh")) == 1


def test_refresh_without_policy_keeps_simple_behaviour():
    """No stored retry policy -> a single attempt; 10202 propagates as the
    plain LineApiError it always was (pre-v2.9 behaviour)."""
    api = build_api(route({"tokenRefresh": _10202()}), access_token="X")

    with pytest.raises(LineApiError) as ei:
        api.auth.refresh_access_token("RT")

    assert ei.value.code == 10202
    assert len(_calls_to(api, "tokenRefresh")) == 1


def test_refresh_success_without_schedule_leaves_schedule_unset():
    """A schedule-less success response arms nothing."""
    responder = route(
        {"tokenRefresh": {"tokenV3IssueResult": {"accessToken": "A", "refreshToken": "R"}}}
    )
    api = build_api(responder, access_token="X")
    hooked: list[dict] = []
    api.auth.on_token_issued = hooked.append

    assert api.auth.refresh_access_token("RT") == "A"

    assert api.auth.token_schedule is None
    assert hooked == []


def test_refresh_without_schedule_clears_stale_schedule(tmp_path):
    """A schedule-less refresh response must CLEAR a previously armed
    schedule (the extension's setTokenV3IssueResult clear step): the old
    token's retry policy is not reused, its fire time is not persisted, and
    from_tokens_file(auto_refresh_schedule=True) arms nothing."""
    issue = 1700000000
    calls = {"n": 0}

    def responder(method, url, kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return enveloped({"tokenV3IssueResult": _token_v3("A1", "R1", issue)})
        return enveloped({"tokenV3IssueResult": {"accessToken": "A2", "refreshToken": "R2"}})

    api = build_api(responder, access_token="A0")
    api.transport.tokens.refresh_token = "R0"
    hooked: list[dict] = []
    api.auth.on_token_issued = hooked.append

    api.auth.refresh_access_token()  # first refresh: arms the schedule
    assert api.auth.token_schedule is not None
    assert api.transport.tokens.token_issue_time_epoch_sec is not None
    assert hooked == [api.auth.token_schedule]

    hooked.clear()
    assert api.auth.refresh_access_token() == "A2"  # schedule-less response
    assert api.auth.token_schedule is None  # stale schedule cleared...
    assert api.transport.tokens.token_issue_time_epoch_sec is None
    assert api.transport.tokens.duration_until_refresh_sec is None
    assert api.transport.tokens.refresh_api_retry_policy is None
    assert hooked == []  # ...and nothing re-armed

    # nothing schedule-shaped is persisted -> from_tokens_file arms nothing
    p = tmp_path / "s.json"
    api.save_tokens(str(p))
    raw = json.loads(p.read_text())
    assert "tokenIssueTimeEpochSec" not in raw
    assert "durationUntilRefreshInSec" not in raw
    assert "refreshApiRetryPolicy" not in raw

    from okline import OkLine

    api2 = OkLine.from_tokens_file(str(p), auto_refresh_schedule=True)
    try:
        assert api2.auth.token_schedule is None  # no bygone timer re-armed
    finally:
        api2.close()


def test_refresh_policy_clamps_huge_server_delays():
    """Server-provided retry delays are clamped client-side: the backoff
    sleeps block the calling thread (a user request via the 401/119 hook, or
    the keepalive ping thread), so a huge maxDelayInMillis must not be able
    to park it for an unbounded time."""
    from okline.auth import REFRESH_RETRY_DELAY_CAP_MS, RefreshApiRetryPolicy

    capped = RefreshApiRetryPolicy.parse(
        {"initialDelayInMillis": 2000, "maxDelayInMillis": 3_600_000, "multiplier": 2}
    )
    assert capped is not None
    assert capped.initial_delay_ms == 2000.0
    assert capped.max_delay_ms == REFRESH_RETRY_DELAY_CAP_MS

    # both delays above the cap clamp to it -> degenerate (initial >= max)
    # -> no policy: the pre-v2.9 single-attempt behaviour applies
    assert (
        RefreshApiRetryPolicy.parse(
            {"initialDelayInMillis": 7_200_000, "maxDelayInMillis": 3_600_000}
        )
        is None
    )


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


def test_poll_scan_mode_retries_on_410_only():
    """The QR-scan retry policy (the extension's Ez retryCondition): a 410 is
    retried, a 408 is *not*."""
    flows = AuthFlows(build_api(route({})).transport)

    calls = {"n": 0}

    def flaky_410():
        calls["n"] += 1
        if calls["n"] < 3:
            raise LineApiError("poll window elapsed", status=410)
        return "SCANNED"

    assert flows._poll(flaky_410, max_count=5, retry_statuses=(410,)) == "SCANNED"
    assert calls["n"] == 3

    def always_408():
        raise LineApiError("request timeout", status=408)

    with pytest.raises(LineApiError) as ei:
        flows._poll(always_408, max_count=5, retry_statuses=(410,))
    assert ei.value.status == 408  # raised immediately — 408 is not retried


def test_poll_default_still_retries_408():
    """The default (PIN-poll) semantics keep retrying on 408 as before."""
    flows = AuthFlows(build_api(route({})).transport)

    calls = {"n": 0}

    def flaky_408():
        calls["n"] += 1
        if calls["n"] < 2:
            raise LineApiError("request timeout", status=408)
        return "OK"

    assert flows._poll(flaky_408, max_count=3) == "OK"
    assert calls["n"] == 2

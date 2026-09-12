"""Login / authentication flows for the LINE Chrome client.

Four flows are implemented, all faithful to ``static/js/main.js``:

1. **E-mail + password** (``email_login``) — the RSA flow::

       rsa = getRSAKeyInfo(LINE)
       # E2EE tier (with_e2ee=True, the extension's "C" callback):
       curveKey          = bridge.curvekey_generate()
       code              = random 6-digit string          # shown as the PIN
       secret            = b64(per-16-byte-block AES-CBC(pubkey,
                                             key=SHA-256(code), iv=zeros16))
       req = LoginRequest(type=ID_CREDENTIAL_WITH_E2EE, identityProvider=LINE,
                          identifier=rsa.keynm,
                          password=RSA(chr|sessionKey|chr|email|chr|password),
                          keepLoggedIn=True, systemName="Chrome",
                          secret=secret, e2eeVersion=1)
       res = loginV2(req)            -> LoginResult
       # res.type==SUCCESS -> tokenV3IssueResult{accessToken,refreshToken}
       # res.type==REQUIRE_DEVICE_CONFIRM -> confirm_device=True continues:
       #     GET /api/talk/long-polling/LF1  (X-Line-Session-ID=<verifier>,
       #         X-LST=110000) -> result.metadata{publicKey, encryptedKeyChain}
       #     bridge channel + hash-key-chain -> confirmE2EELogin(verifier, ...)
       #     -> loginV2(type=QRCODE, verifier=<new verifier>)
       # (non-E2EE: GET /api/talk/long-polling/JQ, X-LST=180000 ->
       #  result.verifier -> loginV2(type=QRCODE, verifier))

   ``email_login_ladder`` reproduces the extension's full three-tier
   strategy: stored-certificate login -> E2EE login -> plain login, with the
   specific fallback codes between tiers (the "S/A/C/_" callbacks).

2. **Secondary QR-code login** (``qr_login``) — the "scan to log in" flow::

       {authSessionId}                = createSession({})
       {callbackUrl,...}              = createQrCode({authSessionId})
       # render callbackUrl as a QR / open it on the phone
       checkQrCodeVerified({authSessionId})        # long-poll until scanned
           (retries on HTTP 410 only, longPollingMaxCount attempts)
       {pinCode}    = createPinCode({authSessionId})
       checkPinCodeVerified({authSessionId})       # long-poll until pin entered
           (fixed X-LST=110000 — "rH" in the bundle)
       verifyCertificate({authSessionId, certificate})
       res = qrCodeLoginV2({authSessionId, ...})   -> certificate + tokens

3. **Token refresh** (``refresh_access_token``) — ``/api/auth/tokenRefresh``.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import endpoints as ep
from .crypto import (
    RSAKeyInfo,
    encrypt_e2ee_login_secret,
    generate_e2ee_login_code,
    rsa_encrypt_credentials,
)
from .enums import ErrorCode, IdentityProvider, LoginResultType, LoginType
from .exceptions import LineApiError, LineAuthError, LineTransportError
from .transport import Transport

log = logging.getLogger("okline.auth")

# Long-poll X-LST constants (milliseconds), extracted from the bundle:
#   JQ  (non-E2EE e-mail device confirm)  X-LST: 18e4      main.js @2119390
#   LF1 (E2EE e-mail device confirm)      X-LST: rH=11e4   main.js @2119490
#   checkPinCodeVerified (QR PIN poll)    X-LST: rH=11e4   main.js @2119800
# (checkQrCodeVerified instead uses longPollingIntervalSec*1000.)
JQ_POLL_LST_MS = 180000
LF1_POLL_LST_MS = 110000
PIN_POLL_LST_MS = 110000

# TalkException codes that make the extension's E2EE login tier ("C") fall
# back to the plain ID_CREDENTIAL tier ("_").  main.js @~2128300.
_E2EE_LOGIN_FALLBACK_CODES = frozenset(
    {
        int(ErrorCode.E2EE_SENDER_NOT_ALLOWED),  # 89
        int(ErrorCode.E2EE_UPDATE_PRIMARY_DEVICE),  # 94
        int(ErrorCode.E2EE_PRIMARY_NOT_SUPPORT),  # 97
    }
)

# String error id that makes the certificate tier ("A") fall through to the
# E2EE tier ("C").  In the bundle this is an internal error id
# (Id.E2EE_MY_KEY_NOT_AVAILABLE = "e2ee_my_key_not_available").
_MY_KEY_NOT_AVAILABLE = "e2ee_my_key_not_available"


def _append_secret(callback_url: str, secret_b64: str) -> str:
    """Append ``?secret=<b64 curve25519 pubkey>&e2eeVersion=1`` to the QR URL,
    matching the extension's ``URL.searchParams.set`` encoding."""
    parts = urlsplit(callback_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["secret"] = secret_b64
    query["e2eeVersion"] = "1"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


@dataclass
class LoginResult:
    """Normalised result of ``loginV2`` / ``qrCodeLoginV2``."""

    type: int
    access_token: str | None = None
    refresh_token: str | None = None
    certificate: str | None = None
    mid: str | None = None
    pin_code: str | None = None
    verifier: str | None = None
    display_message: str | None = None
    raw: Any = None

    @classmethod
    def parse(cls, data: dict) -> LoginResult:
        tok = (data.get("tokenV3IssueResult") or {}) if isinstance(data, dict) else {}
        return cls(
            type=int(data.get("type", LoginResultType.SUCCESS)),
            access_token=tok.get("accessToken") or data.get("authToken"),
            refresh_token=tok.get("refreshToken"),
            certificate=data.get("certificate"),
            mid=data.get("mid"),
            pin_code=data.get("pinCode"),
            verifier=data.get("verifier"),
            display_message=data.get("displayMessage"),
            raw=data,
        )

    @property
    def success(self) -> bool:
        return self.type == LoginResultType.SUCCESS


class AuthFlows:
    """Stateless helpers operating on a :class:`Transport`."""

    def __init__(self, transport: Transport) -> None:
        self._t = transport
        self.last_e2ee_login: dict | None = None
        # E2EE material from the last e-mail login attempt: the curve-key
        # handle and the 6-digit code (the PIN the user must confirm).
        self.last_email_e2ee: dict | None = None
        # Per-e-mail login certificates.  The extension persists these in
        # localStorage under ``<prefix>_<email>`` (wT/bT, main.js @~1862400);
        # we keep a plain in-process dict keyed by e-mail (pragmatic choice —
        # there is no localStorage here; the global certificate on
        # ``transport.tokens`` remains the fallback).
        self.email_certificates: dict[str, str] = {}

    # -- shared --------------------------------------------------------------
    def get_rsa_key_info(self, provider: int = IdentityProvider.LINE) -> RSAKeyInfo:
        data = self._t.call(
            "Talk.TalkService.getRSAKeyInfo", [int(provider)], require_auth=False
        )
        return RSAKeyInfo.from_response(data)

    def _login_request(
        self,
        *,
        type_: int,
        identifier: str,
        password: str,
        keep_logged_in: bool,
        system_name: str | None,
        certificate: str,
        verifier: str = "",
        secret: str = "",
    ) -> dict:
        """The 12-field LoginRequest, matching the extension's base request
        (``h`` in the e-mail login hook, main.js @~2123700)."""
        return {
            "type": int(type_),
            "identityProvider": int(IdentityProvider.LINE),
            "identifier": identifier,
            "password": password,
            "keepLoggedIn": keep_logged_in,
            "accessLocation": "",
            "systemName": system_name or self._t.config.system_name,
            "certificate": certificate,
            "verifier": verifier,
            "secret": secret,
            "e2eeVersion": 1,
            "modelName": "",
        }

    def _login_v2(self, request: dict, *, email: str | None = None) -> LoginResult:
        """One ``loginV2`` round-trip (+ token/certificate adoption)."""
        data = self._t.call("Talk.AuthService.loginV2", [request], require_auth=False)
        result = LoginResult.parse(data)
        if result.success and result.access_token:
            self._adopt(result)
            if email:
                self._remember_certificate(email, result)
        return result

    # -- 1. e-mail login -----------------------------------------------------
    def email_login(
        self,
        email: str,
        password: str,
        *,
        keep_logged_in: bool = True,
        with_e2ee: bool = True,
        system_name: str | None = None,
        certificate: str | None = None,
        confirm_device: bool = False,
        on_pin: Callable[[str], None] | None = None,
        wait_seconds: float = 180.0,
    ) -> LoginResult:
        """E-mail + password login (``loginV2``).

        ``with_e2ee=True`` (the extension's "C" flow) generates a Curve25519
        keypair in the LTSM bridge and sends ``secret`` — the per-16-byte-block
        AES-CBC encryption of the curve public key under ``SHA-256(<6-digit
        code>)`` with a zero IV.  The 6-digit code is the PIN the user must
        confirm on their primary device; it is left on
        :attr:`last_email_e2ee` (with the curve-key handle) for display.

        A first-time login answers ``REQUIRE_DEVICE_CONFIRM`` (type 3).  Pass
        ``confirm_device=True`` to drive the confirmation to completion
        (opt-in: without it the type-3 result is returned as before):

        * E2EE path — long-poll ``GET /api/talk/long-polling/LF1``
          (``X-LST=110000``) until it returns ``result.metadata`` (the primary
          device's curve public key + encrypted key chain), build the E2EE
          channel, generate the hash key chain, ``confirmE2EELogin`` for a new
          verifier, then re-login with ``type=QRCODE`` + verifier.
        * non-E2EE path — long-poll ``GET /api/talk/long-polling/JQ``
          (``X-LST=180000``) until it returns ``result.verifier``, then
          re-login with ``type=QRCODE`` + verifier.

        ``on_pin`` receives the PIN to display (the server ``pinCode`` on the
        plain path, the locally generated 6-digit code on the E2EE path).
        ``wait_seconds`` bounds the long-poll budget.
        """
        rsa = self.get_rsa_key_info(IdentityProvider.LINE)
        enc = rsa_encrypt_credentials(rsa, email, password)

        curve_key_id: int | None = None
        code: str | None = None
        secret = ""
        if with_e2ee:
            curve_key_id, code, secret = self._build_e2ee_secret()
            self.last_email_e2ee = {"curve_key_id": curve_key_id, "code": code}

        request = self._login_request(
            type_=LoginType.ID_CREDENTIAL_WITH_E2EE if with_e2ee else LoginType.ID_CREDENTIAL,
            identifier=rsa.keynm,
            password=enc,
            keep_logged_in=keep_logged_in,
            system_name=system_name,
            certificate=certificate
            if certificate is not None
            else (self._t.tokens.certificate or ""),
            secret=secret,
        )
        result = self._login_v2(request, email=email)

        if (
            confirm_device
            and result.type == LoginResultType.REQUIRE_DEVICE_CONFIRM
            and not result.success
        ):
            if with_e2ee and curve_key_id is not None and code is not None:
                return self._confirm_device_lf1(
                    result,
                    curve_key_id,
                    code,
                    email=email,
                    keep_logged_in=keep_logged_in,
                    system_name=system_name,
                    on_pin=on_pin,
                    wait_seconds=wait_seconds,
                )
            return self._confirm_device_jq(
                result,
                email=email,
                keep_logged_in=keep_logged_in,
                system_name=system_name,
                on_pin=on_pin,
                wait_seconds=wait_seconds,
            )
        return result

    # -- 1b. e-mail login ladder (the extension's S/A/C/_ strategy) ----------
    def email_login_ladder(
        self,
        email: str,
        password: str,
        *,
        keep_logged_in: bool = True,
        system_name: str | None = None,
        confirm_device: bool = True,
        on_pin: Callable[[str], None] | None = None,
        wait_seconds: float = 180.0,
    ) -> LoginResult:
        """The extension's full e-mail login strategy (main.js @~2128300).

        Tier 1 ("A") — if we hold a certificate for this e-mail
        (:attr:`email_certificates`), try ``type=ID_CREDENTIAL`` with it.
        ``REQUIRE_DEVICE_CONFIRM`` or ``E2EE_MY_KEY_NOT_AVAILABLE`` falls
        through to tier 2.

        Tier 2 ("C") — E2EE login with the encrypted ``secret`` (see
        :meth:`email_login`).  ``E2EE_SENDER_NOT_ALLOWED``,
        ``E2EE_PRIMARY_NOT_SUPPORT`` or ``E2EE_UPDATE_PRIMARY_DEVICE`` falls
        back to tier 3.

        Tier 3 ("_") — plain ``type=ID_CREDENTIAL`` login.

        ``REQUIRE_DEVICE_CONFIRM`` continues via the LF1 (tier 2) or JQ
        (tier 3) device-confirm flow when ``confirm_device=True``.  On SUCCESS
        the returned certificate is remembered per e-mail, exactly like the
        extension's ``bT(email, certificate)``.
        """
        stored = self.email_certificates.get(email, "")
        if stored:
            # -- tier 1: certificate login -----------------------------------
            try:
                result = self._email_login_tier(
                    email,
                    password,
                    type_=LoginType.ID_CREDENTIAL,
                    certificate=stored,
                    keep_logged_in=keep_logged_in,
                    system_name=system_name,
                )
            except LineApiError as exc:
                if not self._is_my_key_not_available(exc):
                    raise
            else:
                if result.success or result.type != LoginResultType.REQUIRE_DEVICE_CONFIRM:
                    return result
                # REQUIRE_DEVICE_CONFIRM on the certificate tier -> E2EE tier.

        # -- tier 2: E2EE login ----------------------------------------------
        try:
            result = self._email_login_tier(
                email,
                password,
                type_=LoginType.ID_CREDENTIAL_WITH_E2EE,
                certificate="",
                keep_logged_in=keep_logged_in,
                system_name=system_name,
            )
        except LineApiError as exc:
            if exc.code not in _E2EE_LOGIN_FALLBACK_CODES:
                raise
            # -- tier 3: plain login -----------------------------------------
            return self._email_login_tier(
                email,
                password,
                type_=LoginType.ID_CREDENTIAL,
                certificate="",
                keep_logged_in=keep_logged_in,
                system_name=system_name,
                confirm_device=confirm_device,
                on_pin=on_pin,
                wait_seconds=wait_seconds,
            )

        if (
            confirm_device
            and result.type == LoginResultType.REQUIRE_DEVICE_CONFIRM
            and not result.success
        ):
            e2ee = self.last_email_e2ee or {}
            if e2ee.get("curve_key_id") is not None and e2ee.get("code") is not None:
                return self._confirm_device_lf1(
                    result,
                    int(e2ee["curve_key_id"]),
                    str(e2ee["code"]),
                    email=email,
                    keep_logged_in=keep_logged_in,
                    system_name=system_name,
                    on_pin=on_pin,
                    wait_seconds=wait_seconds,
                )
        return result

    def _email_login_tier(
        self,
        email: str,
        password: str,
        *,
        type_: int,
        certificate: str,
        keep_logged_in: bool,
        system_name: str | None,
        confirm_device: bool = False,
        on_pin: Callable[[str], None] | None = None,
        wait_seconds: float = 180.0,
    ) -> LoginResult:
        """One ladder tier: a single loginV2 attempt (no fallbacks)."""
        rsa = self.get_rsa_key_info(IdentityProvider.LINE)
        enc = rsa_encrypt_credentials(rsa, email, password)

        curve_key_id: int | None = None
        code: str | None = None
        secret = ""
        if type_ == LoginType.ID_CREDENTIAL_WITH_E2EE:
            curve_key_id, code, secret = self._build_e2ee_secret()
            self.last_email_e2ee = {"curve_key_id": curve_key_id, "code": code}

        request = self._login_request(
            type_=type_,
            identifier=rsa.keynm,
            password=enc,
            keep_logged_in=keep_logged_in,
            system_name=system_name,
            certificate=certificate,
            secret=secret,
        )
        result = self._login_v2(request, email=email)

        if (
            confirm_device
            and result.type == LoginResultType.REQUIRE_DEVICE_CONFIRM
            and not result.success
        ):
            if curve_key_id is not None and code is not None:
                return self._confirm_device_lf1(
                    result,
                    curve_key_id,
                    code,
                    email=email,
                    keep_logged_in=keep_logged_in,
                    system_name=system_name,
                    on_pin=on_pin,
                    wait_seconds=wait_seconds,
                )
            return self._confirm_device_jq(
                result,
                email=email,
                keep_logged_in=keep_logged_in,
                system_name=system_name,
                on_pin=on_pin,
                wait_seconds=wait_seconds,
            )
        return result

    def _build_e2ee_secret(self) -> tuple[int, str, str]:
        """Curve key + 6-digit code + the encrypted ``secret`` for the E2EE
        e-mail login (the extension's "C" callback, main.js @~2127845).

        Returns ``(curve_key_id, code, secret)``.  The public key comes from
        the LTSM bridge base64-encoded (the extension base64-decodes
        ``e2eeKeyGetPublicKey``'s raw bytes at the call site, main.js @3252386)
        and is encrypted block-wise with ``SHA-256(code)`` as the AES key.
        """
        bridge = self._t.bridge
        curve_key_id = bridge.curvekey_generate()
        public_key = base64.b64decode(bridge.e2ee_public_key(curve_key_id))
        code = generate_e2ee_login_code()
        secret = encrypt_e2ee_login_secret(public_key, code)
        return curve_key_id, code, secret

    # -- 1c. REQUIRE_DEVICE_CONFIRM continuations -----------------------------
    def _confirm_device_jq(
        self,
        result: LoginResult,
        *,
        email: str,
        keep_logged_in: bool,
        system_name: str | None,
        on_pin: Callable[[str], None] | None,
        wait_seconds: float,
    ) -> LoginResult:
        """Non-E2EE continuation (the extension's "b" callback):

        long-poll ``/api/talk/long-polling/JQ`` (X-LST=180000, no retry) until
        the confirmed device returns a fresh ``result.verifier``, then
        re-login with ``type=QRCODE`` + verifier.
        """
        if on_pin and result.pin_code:
            on_pin(result.pin_code)
        verifier = self._device_confirm_poll(
            "JQ",
            result.verifier or "",
            timeout_ms=JQ_POLL_LST_MS,
            wait_seconds=wait_seconds,
            pick=lambda r: r.get("verifier"),
        )
        return self._qr_verifier_relogin(
            verifier,
            email=email,
            keep_logged_in=keep_logged_in,
            system_name=system_name,
        )

    def _confirm_device_lf1(
        self,
        result: LoginResult,
        curve_key_id: int,
        code: str,
        *,
        email: str,
        keep_logged_in: bool,
        system_name: str | None,
        on_pin: Callable[[str], None] | None,
        wait_seconds: float,
    ) -> LoginResult:
        """E2EE continuation (the extension's "x"/"w" callbacks):

        long-poll ``/api/talk/long-polling/LF1`` (X-LST=110000) until the
        primary device returns ``result.metadata{publicKey, encryptedKeyChain}``,
        create the E2EE channel with our login curve key, generate the hash
        key chain, ``confirmE2EELogin(verifier, b64(hashKeyChain))`` for a new
        verifier, then re-login with ``type=QRCODE`` + verifier.
        """
        if on_pin:
            on_pin(code)  # the locally generated 6-digit code is the PIN
        metadata = self._device_confirm_poll(
            "LF1",
            result.verifier or "",
            timeout_ms=LF1_POLL_LST_MS,
            wait_seconds=wait_seconds,
            pick=lambda r: r.get("metadata"),
        )
        if not isinstance(metadata, dict) or not metadata.get("publicKey"):
            raise LineAuthError("LF1 device-confirm poll returned no metadata", raw=metadata)

        bridge = self._t.bridge
        channel = bridge.e2ee_create_channel(curve_key_id, metadata["publicKey"])
        generate = getattr(bridge, "e2ee_generate_hash_key_chain_to_confirm_e2ee", None)
        if generate is None:
            raise LineAuthError(
                "the LTSM bridge does not expose "
                "'e2ee_generate_hash_key_chain_to_confirm_e2ee' (sandbox op "
                "'e2eechannel_generate_hash_key_chain_to_confirm_e2ee'); "
                "E2EE device confirmation cannot complete"
            )
        hash_key_chain_b64 = generate(channel, metadata["encryptedKeyChain"])

        confirmed = self._t.call(
            "Talk.AuthService.confirmE2EELogin",
            [result.verifier, hash_key_chain_b64],
            require_auth=False,
        )
        new_verifier = confirmed.get("verifier") if isinstance(confirmed, dict) else confirmed
        if not new_verifier:
            raise LineAuthError("confirmE2EELogin returned no verifier", raw=confirmed)

        # Best-effort: unwrap our E2EE key chain so an E2EEManager can adopt
        # it later (the extension passes e2eeKeyIdList into its post-login
        # init).  Failure here must not fail the login itself.
        if self.last_email_e2ee is not None:
            try:
                handles = bridge.e2ee_unwrap_keychain(channel, metadata["encryptedKeyChain"])
                self.last_email_e2ee["key_handles"] = handles
            except Exception:  # pragma: no cover - bridge-dependent
                log.debug("e2ee keychain unwrap after login confirm failed", exc_info=True)

        return self._qr_verifier_relogin(
            new_verifier,
            email=email,
            keep_logged_in=keep_logged_in,
            system_name=system_name,
        )

    def _device_confirm_poll(
        self,
        endpoint: str,
        session_id: str,
        *,
        timeout_ms: int,
        wait_seconds: float,
        pick: Callable[[dict], Any],
    ) -> Any:
        """Long-poll ``GET /api/talk/long-polling/{JQ|LF1}`` for the confirmed
        device's answer.

        ``session_id`` is the ``verifier`` issued by the type-3 ``loginV2``
        (byte-extracted: the extension's curried poll helper binds
        ``X-Line-Session-ID`` to its *second* argument, which every caller
        fills with ``loginResult.verifier`` — the PIN is display-only;
        main.js @2119316/@2119578 + @2127600).  A 408 (poll window elapsed)
        is retried, bounded by ``wait_seconds``; a **410 is terminal** — the
        extension maps it to ``PIN_CODE_TIMEOUT`` ("PIN code timeout") for
        both the JQ and LF1 polls and never retries it, so neither do we.
        A 200 whose body is not valid JSON is treated like any other poll
        anomaly (retry) instead of aborting with a decode error.
        """
        path = "/" + ep.SPECIAL_ENDPOINTS[f"longpoll.{endpoint}"]
        attempts = max(1, int(wait_seconds / (timeout_ms / 1000.0)) + 1)
        last: str | None = None
        for _ in range(attempts):
            resp = self._t.get(
                path,
                require_auth=False,
                extra_headers={
                    "X-Line-Session-ID": session_id,
                    "X-LST": str(timeout_ms),
                },
                timeout=timeout_ms / 1000.0 + 15.0,
            )
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except ValueError:  # requests' JSONDecodeError subclasses this
                    last = "poll returned non-JSON body"
                    continue
                data = body.get("data", body) if isinstance(body, dict) else body
                inner = data.get("result") if isinstance(data, dict) else None
                if isinstance(inner, dict):
                    value = pick(inner)
                    if value:
                        return value
                last = "poll returned no result"
                continue
            if resp.status_code == 410:
                # Terminal PIN expiry (the extension's shared error handler
                # maps 410 -> Id.PIN_CODE_TIMEOUT for both polls) — raise
                # rather than silently re-polling an expired PIN.
                raise LineAuthError(
                    f"PIN code timeout on {endpoint} device-confirm poll",
                    status=410,
                    path=path,
                    raw=resp.text,
                )
            if resp.status_code == 408:  # poll window elapsed — keep waiting
                last = "HTTP 408"
                continue
            raise LineApiError(
                f"device-confirm poll failed: HTTP {resp.status_code}",
                status=resp.status_code,
                path=path,
                raw=resp.text,
            )
        raise LineAuthError(
            f"device confirmation timed out on {endpoint}" + (f" ({last})" if last else "")
        )

    def _qr_verifier_relogin(
        self,
        verifier: str,
        *,
        email: str,
        keep_logged_in: bool,
        system_name: str | None,
    ) -> LoginResult:
        """The final ``loginV2({type: QRCODE, verifier})`` of the e-mail
        device-confirm flows (the only place LoginType.QRCODE is used)."""
        request = self._login_request(
            type_=LoginType.QRCODE,
            identifier="",
            password="",
            keep_logged_in=keep_logged_in,
            system_name=system_name,
            certificate="",
            verifier=verifier,
        )
        return self._login_v2(request, email=email)

    @staticmethod
    def _is_my_key_not_available(exc: LineApiError) -> bool:
        """The certificate tier's fall-through signal (a *string* error id in
        the bundle, so match it wherever the layer happened to put it)."""
        haystack = " ".join(
            str(part) for part in (exc.reason, exc.metadata, exc.raw) if part is not None
        )
        return _MY_KEY_NOT_AVAILABLE in haystack

    def _remember_certificate(self, email: str, result: LoginResult) -> None:
        """Persist a successful login's certificate per e-mail (``bT``)."""
        if result.certificate:
            self.email_certificates[email] = result.certificate

    # -- 2. QR login ---------------------------------------------------------
    def qr_create_session(self) -> str:
        data = self._t.call(
            "LoginQrCode.SecondaryQrCodeLoginService.createSession", [{}], require_auth=False
        )
        if isinstance(data, dict):
            sid = data.get("authSessionId")
            if not sid:
                raise LineApiError(
                    f"createSession returned no authSessionId: {data!r}", raw=data
                )
            return sid
        return data

    def qr_create_qrcode(self, auth_session_id: str) -> dict:
        return self._t.call(
            "LoginQrCode.SecondaryQrCodeLoginService.createQrCode",
            [{"authSessionId": auth_session_id}],
            require_auth=False,
        )

    def qr_check_verified(self, auth_session_id: str, *, timeout_ms: int = 120000) -> Any:
        return self._t.call(
            "LoginQrCode.SecondaryQrCodeLoginPermitNoticeService.checkQrCodeVerified",
            [{"authSessionId": auth_session_id}],
            require_auth=False,
            extra_headers={"X-Line-Session-ID": auth_session_id, "X-LST": str(timeout_ms)},
        )

    def qr_create_pincode(self, auth_session_id: str) -> str | None:
        data = self._t.call(
            "LoginQrCode.SecondaryQrCodeLoginService.createPinCode",
            [{"authSessionId": auth_session_id}],
            require_auth=False,
        )
        return data.get("pinCode") if isinstance(data, dict) else data

    def qr_check_pincode_verified(
        self, auth_session_id: str, *, timeout_ms: int = PIN_POLL_LST_MS
    ) -> Any:
        # X-LST is the fixed 110000 ("rH=11e4"), NOT longPollingIntervalSec*1000
        # — only checkQrCodeVerified derives its timeout from the interval.
        return self._t.call(
            "LoginQrCode.SecondaryQrCodeLoginPermitNoticeService.checkPinCodeVerified",
            [{"authSessionId": auth_session_id}],
            require_auth=False,
            extra_headers={"X-Line-Session-ID": auth_session_id, "X-LST": str(timeout_ms)},
        )

    def qr_verify_certificate(self, auth_session_id: str, certificate: str = "") -> Any:
        return self._t.call(
            "LoginQrCode.SecondaryQrCodeLoginService.verifyCertificate",
            [{"authSessionId": auth_session_id, "certificate": certificate}],
            require_auth=False,
        )

    def qr_login_v2(
        self,
        auth_session_id: str,
        *,
        system_name: str = "CHROMEOS",
        model_name: str = "CHROME",
        auto_login: bool = False,
    ) -> LoginResult:
        req = {
            "systemName": system_name,
            "modelName": model_name,
            "autoLoginIsRequired": auto_login,
            "authSessionId": auth_session_id,
        }
        data = self._t.call(
            "LoginQrCode.SecondaryQrCodeLoginService.qrCodeLoginV2", [req], require_auth=False
        )
        result = LoginResult.parse(data)
        if result.access_token:
            self._adopt(result)
        return result

    def qr_login(
        self,
        *,
        on_qr: Callable[[str], None],
        on_pin: Callable[[str], None] | None = None,
        system_name: str | None = None,
        certificate: str | None = None,
        wait_seconds: float = 180.0,
    ) -> LoginResult:
        """Drive the full secondary-device QR login, faithfully to the client.

        ``on_qr(qr_url)`` receives the **full** URL to render as a QR (it
        already includes the required ``?secret=<curve25519 pubkey>&
        e2eeVersion=1`` that the LINE app expects — without it the phone shows
        an error after scanning).  ``on_pin(pin)`` receives the PIN to display.
        Both callbacks block while we long-poll for confirmation.
        """
        bridge = self._t.bridge  # shared LTSM WASM bridge (also signs X-Hmac)

        session = self.qr_create_session()
        qr = self.qr_create_qrcode(session)
        callback_url = (qr.get("callbackUrl") if isinstance(qr, dict) else qr) or ""
        interval = (qr.get("longPollingIntervalSec") if isinstance(qr, dict) else None) or 10
        server_max = (qr.get("longPollingMaxCount") if isinstance(qr, dict) else None) or 12
        # The extension long-polls the scan check exactly longPollingMaxCount
        # times (retryCount = longPollingMaxCount - 1, retrying on 410 ONLY).
        # Keep that as the floor and extend it when the caller asks for a
        # larger overall wait budget.
        attempts = max(int(server_max), int(wait_seconds / max(interval, 1)) + 1)

        # 1) generate the Curve25519 keypair *inside the WASM* and embed its
        #    public key as the QR ``secret`` (this is what was missing).
        curve_key_id = bridge.curvekey_generate()
        public_key_b64 = bridge.e2ee_public_key(curve_key_id)
        qr_url = _append_secret(callback_url, public_key_b64)
        on_qr(qr_url)

        # 2) wait until the phone scans + approves the QR (410-only retry).
        self._poll(
            lambda: self.qr_check_verified(session, timeout_ms=interval * 1000),
            attempts,
            retry_statuses=(410,),
        )

        # 3) returning device -> verifyCertificate; first login -> PIN flow.
        cert = certificate if certificate is not None else (self._t.tokens.certificate or "")
        need_pin = True
        try:
            self.qr_verify_certificate(session, cert)
            need_pin = False
        except LineApiError:
            need_pin = True
        if need_pin:
            pin = self.qr_create_pincode(session)
            if on_pin and pin is not None:
                on_pin(pin)
            self._poll(
                lambda: self.qr_check_pincode_verified(session),  # fixed 110000 X-LST
                attempts,
            )

        # 4) issue the tokens.
        result = self.qr_login_v2(session, system_name=system_name or "CHROMEOS")

        # 5) stash the E2EE login material (curve key handle + metaData) so an
        #    E2EEManager can unwrap our Letter-Sealing keys (same process only).
        meta = (result.raw or {}).get("metaData") if isinstance(result.raw, dict) else None
        if isinstance(meta, dict) and meta.get("publicKey") and meta.get("encryptedKeyChain"):
            self.last_e2ee_login = {"curve_key_id": curve_key_id, "metadata": meta}
        else:
            self.last_e2ee_login = None
        return result

    def _poll(
        self,
        call: Callable[[], Any],
        max_count: int,
        *,
        retry_statuses: tuple[int, ...] = (408, 410),
    ) -> Any:
        """Long-poll helper: retry ``call`` while the server signals a poll
        window timeout (HTTP status in ``retry_statuses`` — the scan poll
        retries on 410 only, like the extension's ``Ez`` retryCondition)
        until it succeeds or ``max_count`` attempts elapse."""
        last: Any = None
        for _ in range(max(1, max_count)):
            try:
                return call()
            except LineApiError as exc:
                if exc.status in retry_statuses:  # poll window elapsed
                    last = exc
                    continue
                raise
            except LineTransportError as exc:
                last = exc
                continue
        if last:
            raise last
        return None

    # -- 3. token refresh ----------------------------------------------------
    def refresh_access_token(self, refresh_token: str | None = None) -> str:
        rt = refresh_token or self._t.tokens.refresh_token
        if not rt:
            raise LineAuthError("no refresh token available")
        path = "/" + ep.SPECIAL_ENDPOINTS["auth.tokenRefresh"]
        data = self._t.post_json(path, {"refreshToken": rt}, require_auth=False)
        tok = data.get("tokenV3IssueResult", data) if isinstance(data, dict) else {}
        access = tok.get("accessToken") or data.get("accessToken")
        if not access:
            raise LineAuthError("token refresh returned no access token", raw=data)
        self._t.tokens.access_token = access
        if tok.get("refreshToken"):
            self._t.tokens.refresh_token = tok["refreshToken"]
        return access

    def logout(self) -> Any:
        """``Talk.AuthService.logoutV2``.

        Browser-specific note: after the (fire-and-forget) call the extension
        also removes the ``lct`` session cookie from the gateway origin
        (``chrome.cookies.remove``) and clears its local auth state (``CT``) —
        main.js @~1862400.  This client has no cookie jar; the equivalent is
        clearing ``transport.tokens`` (and dropping any recorder artifacts).
        """
        return self._t.call("Talk.AuthService.logoutV2", [])

    # -- internal ------------------------------------------------------------
    def _adopt(self, result: LoginResult) -> None:
        self._t.tokens.access_token = result.access_token
        if result.refresh_token:
            self._t.tokens.refresh_token = result.refresh_token
        if result.certificate:
            self._t.tokens.certificate = result.certificate
        if result.mid:
            self._t.tokens.mid = result.mid

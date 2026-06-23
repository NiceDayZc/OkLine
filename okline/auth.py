"""Login / authentication flows for the LINE Chrome client.

Three flows are implemented, all faithful to ``static/js/main.js``:

1. **E-mail + password** (``email_login``) — the classic RSA flow::

       rsa = getRSAKeyInfo(LINE)
       req = LoginRequest(type=ID_CREDENTIAL_WITH_E2EE, identityProvider=LINE,
                          identifier=rsa.keynm,
                          password=RSA(chr|sessionKey|chr|email|chr|password),
                          keepLoggedIn=True, systemName="Chrome", e2eeVersion=1)
       res = loginV2(req)            -> LoginResult
       # res.type==SUCCESS -> tokenV3IssueResult{accessToken,refreshToken}, certificate
       # res.type==REQUIRE_DEVICE_CONFIRM -> show pinCode, poll device confirm

2. **Secondary QR-code login** (``qr_login``) — the "scan to log in" flow::

       {authSessionId}                = createSession({})
       {callbackUrl,...}              = createQrCode({authSessionId})
       # render callbackUrl as a QR / open it on the phone
       checkQrCodeVerified({authSessionId})        # long-poll until scanned
       {pinCode}    = createPinCode({authSessionId})
       checkPinCodeVerified({authSessionId})       # long-poll until pin entered
       verifyCertificate({authSessionId, certificate})
       res = qrCodeLoginV2({authSessionId, ...})   -> certificate + tokens

3. **Token refresh** (``refresh_access_token``) — ``/api/auth/tokenRefresh``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import endpoints as ep
from .crypto import RSAKeyInfo, rsa_encrypt_credentials
from .enums import IdentityProvider, LoginResultType, LoginType
from .exceptions import LineApiError, LineAuthError, LineTransportError
from .transport import Transport

log = logging.getLogger("okline.auth")


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

    # -- shared --------------------------------------------------------------
    def get_rsa_key_info(self, provider: int = IdentityProvider.LINE) -> RSAKeyInfo:
        data = self._t.call(
            "Talk.TalkService.getRSAKeyInfo", [int(provider)], require_auth=False
        )
        return RSAKeyInfo.from_response(data)

    # -- 1. e-mail login -----------------------------------------------------
    def email_login(
        self,
        email: str,
        password: str,
        *,
        keep_logged_in: bool = True,
        with_e2ee: bool = True,
        system_name: str | None = None,
    ) -> LoginResult:
        rsa = self.get_rsa_key_info(IdentityProvider.LINE)
        enc = rsa_encrypt_credentials(rsa, email, password)
        login_request = {
            "type": int(
                LoginType.ID_CREDENTIAL_WITH_E2EE if with_e2ee else LoginType.ID_CREDENTIAL
            ),
            "identityProvider": int(IdentityProvider.LINE),
            "identifier": rsa.keynm,
            "password": enc,
            "keepLoggedIn": keep_logged_in,
            "accessLocation": "",
            "systemName": system_name or self._t.config.system_name,
            "certificate": self._t.tokens.certificate or "",
            "verifier": "",
            "secret": "",
            "e2eeVersion": 1,
            "modelName": "",
        }
        data = self._t.call("Talk.AuthService.loginV2", [login_request], require_auth=False)
        result = LoginResult.parse(data)
        if result.success and result.access_token:
            self._adopt(result)
        return result

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
        self, auth_session_id: str, *, timeout_ms: int = 120000
    ) -> Any:
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
        # give the user enough total time to scan / enter the PIN
        attempts = max(int(server_max), int(wait_seconds / max(interval, 1)) + 1)

        # 1) generate the Curve25519 keypair *inside the WASM* and embed its
        #    public key as the QR ``secret`` (this is what was missing).
        curve_key_id = bridge.curvekey_generate()
        public_key_b64 = bridge.e2ee_public_key(curve_key_id)
        qr_url = _append_secret(callback_url, public_key_b64)
        on_qr(qr_url)

        # 2) wait until the phone scans + approves the QR.
        self._poll(
            lambda: self.qr_check_verified(session, timeout_ms=interval * 1000), attempts
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
                lambda: self.qr_check_pincode_verified(session, timeout_ms=interval * 1000),
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

    def _poll(self, call: Callable[[], Any], max_count: int) -> Any:
        """Long-poll helper: retry ``call`` while the server times out (HTTP 410)
        until it succeeds or ``max_count`` attempts elapse."""
        last: Any = None
        for _ in range(max(1, max_count)):
            try:
                return call()
            except LineApiError as exc:
                if exc.status in (408, 410):  # poll window elapsed -> keep waiting
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

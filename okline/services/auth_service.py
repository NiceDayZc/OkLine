"""Raw 1:1 wrappers for the auth / QR-login / identity Thrift endpoints.

These are the low-level methods (one per endpoint); the orchestrated login
*flows* live in :mod:`okline.auth` (reachable via ``api.auth``).
"""

from __future__ import annotations

from typing import Any

from ..enums import EncryptedAccessTokenFeatureType, IdentityProvider
from ._base import ServiceMixin


class AuthServiceMixin(ServiceMixin):
    # -- Talk.AuthService ----------------------------------------------------
    def login_v2(self, login_request: dict) -> Any:
        """``loginV2(LoginRequest)`` -> LoginResult."""
        return self.transport.call(
            "Talk.AuthService.loginV2", [login_request], require_auth=False
        )

    def logout_v2(self) -> Any:
        return self.transport.call("Talk.AuthService.logoutV2", [])

    def confirm_e2ee_login(self, verifier: str, device_secret: str) -> Any:
        """``confirmE2EELogin(verifier, deviceSecret)`` -> new verifier token."""
        return self.transport.call(
            "Talk.AuthService.confirmE2EELogin", [verifier, device_secret], require_auth=False
        )

    # -- Talk.TalkService (identity / token) ---------------------------------
    def get_rsa_key_info(self, provider: int = int(IdentityProvider.LINE)) -> Any:
        """``getRSAKeyInfo(provider)`` -> ``{keynm, nvalue, evalue, sessionKey}``."""
        return self.transport.call(
            "Talk.TalkService.getRSAKeyInfo", [int(provider)], require_auth=False
        )

    def get_encrypted_identity_v3(self) -> Any:
        """``getEncryptedIdentityV3()`` ->
        ``{wrappedNonce, kdfParameter1, kdfParameter2}``."""
        return self.transport.call("Talk.TalkService.getEncryptedIdentityV3", [])

    def acquire_encrypted_access_token(
        self, feature: int = int(EncryptedAccessTokenFeatureType.OBS_GENERAL)
    ) -> Any:
        """``acquireEncryptedAccessToken(feature)`` -> delimited token blob.

        The raw result is ``<rec-sep>``/``<unit-sep>`` delimited; the encrypted
        token is ``split[1][0]``.  Use :meth:`get_encrypted_access_token` for the
        parsed value.
        """
        return self.transport.call(
            "Talk.TalkService.acquireEncryptedAccessToken", [int(feature)]
        )

    def get_encrypted_access_token(
        self, feature: int = int(EncryptedAccessTokenFeatureType.OBS_GENERAL)
    ) -> str | None:
        """Parse :meth:`acquire_encrypted_access_token` into the token string."""
        raw = self.acquire_encrypted_access_token(feature)
        if not isinstance(raw, str):
            return raw
        rows = [r.split("\x1f") for r in raw.split("\x1e") if r]
        token = rows[1][0] if len(rows) > 1 and rows[1] else None
        if token:
            self.transport.tokens.encrypted_access_tokens[str(int(feature))] = token
        return token

    # -- LoginQrCode.SecondaryQrCodeLoginService (raw) -----------------------
    def qr_create_session(self) -> Any:
        return self.transport.call(
            "LoginQrCode.SecondaryQrCodeLoginService.createSession", [{}], require_auth=False
        )

    def qr_create_qr_code(self, auth_session_id: str) -> Any:
        return self.transport.call(
            "LoginQrCode.SecondaryQrCodeLoginService.createQrCode",
            [{"authSessionId": auth_session_id}],
            require_auth=False,
        )

    def qr_verify_certificate(self, auth_session_id: str, certificate: str = "") -> Any:
        return self.transport.call(
            "LoginQrCode.SecondaryQrCodeLoginService.verifyCertificate",
            [{"authSessionId": auth_session_id, "certificate": certificate}],
            require_auth=False,
        )

    def qr_create_pin_code(self, auth_session_id: str) -> Any:
        return self.transport.call(
            "LoginQrCode.SecondaryQrCodeLoginService.createPinCode",
            [{"authSessionId": auth_session_id}],
            require_auth=False,
        )

    def qr_code_login_v2(
        self,
        auth_session_id: str,
        *,
        system_name: str = "CHROMEOS",
        model_name: str = "CHROME",
        auto_login_is_required: bool = False,
    ) -> Any:
        return self.transport.call(
            "LoginQrCode.SecondaryQrCodeLoginService.qrCodeLoginV2",
            [
                {
                    "systemName": system_name,
                    "modelName": model_name,
                    "autoLoginIsRequired": auto_login_is_required,
                    "authSessionId": auth_session_id,
                }
            ],
            require_auth=False,
        )

    def qr_check_qr_code_verified(self, auth_session_id: str, timeout_ms: int = 120000) -> Any:
        return self.transport.call(
            "LoginQrCode.SecondaryQrCodeLoginPermitNoticeService.checkQrCodeVerified",
            [{"authSessionId": auth_session_id}],
            require_auth=False,
            extra_headers={"X-Line-Session-ID": auth_session_id, "X-LST": str(timeout_ms)},
        )

    def qr_check_pin_code_verified(
        self, auth_session_id: str, timeout_ms: int = 120000
    ) -> Any:
        return self.transport.call(
            "LoginQrCode.SecondaryQrCodeLoginPermitNoticeService.checkPinCodeVerified",
            [{"authSessionId": auth_session_id}],
            require_auth=False,
            extra_headers={"X-Line-Session-ID": auth_session_id, "X-LST": str(timeout_ms)},
        )

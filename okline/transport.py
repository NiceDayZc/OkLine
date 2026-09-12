"""HTTP transport for the LINE Chrome gateway.

This module owns everything that is independent of any individual Thrift
method: the :class:`requests.Session`, the standard header set, token storage,
automatic token refresh and the JSON encode/decode + error mapping.

Wire format recap (confirmed from ``static/js/main.js``)::

    POST /api/talk/thrift/<Ns>/<Service>/<method> HTTP/1.1
    Host: line-chrome-gw.line-apps.com
    content-type: application/json            <-- only when a body is sent
    X-Line-Access: <access token>
    X-Line-Chrome-Version: 3.7.2
    X-LAL: en_US                              <-- gateway requests only
    X-Hmac: <ltsm signature>

    [ <arg0>, <arg1>, ... ]          <-- positional thrift args, named structs

Header scoping mirrors the extension's two axios clients: the gateway client
sets ``X-LAL`` and — for ``/api/timeline/*`` URLs only — ``X-Line-ChannelToken``;
the OBS client carries ``Accept-Language`` alone.  ``X-Line-Application`` is
never sent on gateway requests (the extension sets it exclusively on private
OBS resource fetches), so it is opt-in here via
``base_headers(..., application=True)``.  ``User-Agent`` is a synthetic CrOS
Chrome string — the extension never sets one (the browser supplies its own);
see :data:`DEFAULT_USER_AGENT`.

The success response is ``{"message": "OK", "data": <result>}`` — exactly
``"OK"``, like the extension's response interceptor.  On the gateway anything
else, including envelope-less 2xx bodies, is an error.  (Non-gateway bases —
OBS/legy — legitimately return raw payloads and keep the lenient unwrap.)
An application error carries a JSON body describing the Thrift exception.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple

_DEBUG = bool(os.environ.get("LINE_DEBUG"))

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "okline requires the 'requests' package: pip install requests"
    ) from exc

from . import endpoints as ep
from .exceptions import (
    _AUTH_CODES,
    LineApiError,
    LineAuthError,
    LineError,
    LineLoginRequired,
    LineMustUpgradeError,
    LineTransportError,
)

log = logging.getLogger("okline")

# Exact application descriptor the real extension sends.  The trailing tab is
# intentional (LINE parses it as APP_TYPE \t APP_VER \t OS_NAME \t OS_VER).
# NOTE: the extension sends this header only on *private OBS resource* fetches,
# never on gateway requests — hence the opt-in `application=True` flag on
# `base_headers`.
APP_NAME = "CHROMEOS"
APP_VERSION = "3.7.2"
OS_NAME = "Chrome_OS"
DEFAULT_APPLICATION_HEADER = f"{APP_NAME}\t{APP_VERSION}\t{OS_NAME}\t"

# Synthetic CrOS Chrome/124 user agent.  The extension never constructs a UA
# (the browser supplies its own, whatever Chrome the user runs), so there is no
# extension-extracted value to reproduce — this is an invented-but-plausible
# fingerprint, kept as the default for behaviour parity with earlier releases.
# Override it via ``LineConfig(user_agent=...)``.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Gateway envelope error codes — the extension's `qU` enum in main.js.
_CODE_REQUEST_MUST_UPGRADE = 10006  # qU.REQUEST_MUST_UPGRADE
_CODE_RESPONSE_HTTP_ERROR = 10052  # qU.RESPONSE_HTTP_ERROR
_CODE_UNKNOWN_ERROR = 99999  # qU.UNKNOWN_ERROR
# TalkException codes — the extension's `mU` enum in main.js.
_CODE_SHOULD_RETRY = 115  # mU.SHOULD_RETRY
_CODE_MUST_REFRESH_V3_TOKEN = 119  # mU.MUST_REFRESH_V3_TOKEN


@dataclass
class LineConfig:
    """Tunable connection parameters."""

    gateway_base: str = ep.GATEWAY_BASE
    obs_base: str = ep.OBS_BASE
    legy_base: str = ep.LEGY_BASE
    app_version: str = APP_VERSION
    application_header: str = DEFAULT_APPLICATION_HEADER
    chrome_version: str = APP_VERSION
    user_agent: str = DEFAULT_USER_AGENT
    system_name: str = "Chrome"  # "Chrome" or "Whale"
    locale: str = "en-US"  # Accept-Language / X-LAL
    timeout: float = 30.0
    long_poll_timeout: float = 180.0  # X-LST default is 180000 ms
    max_retries: int = 2  # transport-level retries on 5xx / retryable errors
    verify_tls: bool = True
    proxies: Mapping[str, str] | None = None
    enable_hmac: bool = True  # attach the required X-Hmac header
    node_path: str | None = None  # node executable for the HMAC bridge
    ltsm_origin: str | None = None  # extension origin for the LTSM token
    # Optional legy routing host (the extension's ``FR().legyHost``): when set,
    # gateway requests carry ``X-Legy-Host`` and the SSE connect adds the
    # ``legyHost`` query param (see operations.py).
    legy_host: str | None = None
    # `user_agent` above is a synthetic CrOS Chrome/124 string (the extension
    # never sets a UA — the browser does).  Override it to present as something
    # else; it is purely a config value, not an extension-extracted constant.


# Accept-Language -> X-LAL underscore form (from the bundle's Up map).
_LAL_MAP = {
    "en-US": "en_US",
    "ja-JP": "ja_JP",
    "ko-KR": "ko_KR",
    "zh-CN": "zh_CN",
    "zh-TW": "zh_TW",
    "th-TH": "th_TH",
    "tr-TR": "tr_TR",
    "ru-RU": "ru_RU",
    "id-ID": "id_ID",
    "es-419": "es_419",
    "es-ES": "es_ES",
}


@dataclass
class Tokens:
    """Credential material kept for the duration of a session."""

    access_token: str | None = None  # X-Line-Access
    refresh_token: str | None = None  # used by /api/auth/tokenRefresh
    channel_access_token: str | None = None  # X-Line-ChannelToken
    encrypted_access_tokens: dict[str, str] = field(default_factory=dict)
    mid: str | None = None
    certificate: str | None = None  # device certificate from login


class _ErrorInfo(NamedTuple):
    """Everything the error classifier needs from a decoded error body."""

    outer_code: int | None  # gateway envelope code (qU family: 10006/10052/...)
    inner_code: int | None  # TalkException code inside envelope.data (mU family)
    code: int | None  # code to surface: inner TalkException, else outer envelope
    reason: str | None
    metadata: Any
    status: int | None = None  # status override (10052's nested statusCode)


class _RetryableApiError(LineApiError):
    """Outer envelope 99999 (UNKNOWN_ERROR) or inner 115 (SHOULD_RETRY).

    Internal marker used by :meth:`Transport.post_json`, which retries these
    within the same ``max_retries`` budget it spends on 5xx before letting the
    error escape to the caller (it is a plain :class:`LineApiError` for them).
    """


class _MustRefreshTokenError(LineAuthError):
    """Inner TalkException 119 (MUST_REFRESH_V3_TOKEN).

    Internal marker: the gateway wants the V3 token renewed and the request
    replayed — the extension's ``renewToken()`` + retry path.  When no refresh
    hook is wired (or renewal fails) it escapes as a :class:`LineAuthError`.
    """


class Transport:
    """Low-level request engine shared by every service."""

    def __init__(
        self,
        config: LineConfig | None = None,
        tokens: Tokens | None = None,
        session: requests.Session | None = None,
        signer: Any | None = None,
    ) -> None:
        self.config = config or LineConfig()
        self.tokens = tokens or Tokens()
        self.session = session or requests.Session()
        if self.config.proxies:
            self.session.proxies.update(self.config.proxies)
        # Hook the caller can set to refresh credentials lazily; returns True
        # if new credentials were obtained and the request should be retried.
        self._refresh_hook: Callable[[], bool] | None = None
        # X-Hmac signer (lazily started Node bridge running ltsm.wasm).
        self._signer = signer
        self._signer_init = signer is not None
        # Optional recorder + per-exchange hooks (set by the client).
        self.recorder: Any | None = None
        self.hooks: list = []
        self._seq = 0
        # Optional token-bucket rate limiter (see okline.ratelimit).
        self.rate_limiter: Any | None = None

    # -- X-Hmac signing ------------------------------------------------------
    @property
    def signer(self):
        if not self._signer_init and self.config.enable_hmac:
            from .hmac_signer import LtsmBridge

            self._signer = LtsmBridge(
                node_path=self.config.node_path, origin=self.config.ltsm_origin
            )
            self._signer_init = True
        return self._signer

    @property
    def bridge(self):
        """The shared LTSM bridge (same object used for X-Hmac and E2EE).

        Unlike :pyattr:`signer`, this starts the bridge even when
        ``enable_hmac`` is False, because QR login needs the curve-key ops.
        """
        if self._signer is None:
            from .hmac_signer import LtsmBridge

            self._signer = LtsmBridge(
                node_path=self.config.node_path, origin=self.config.ltsm_origin
            )
            self._signer_init = True
        return self._signer

    def _sign(self, headers: dict, path: str, body: str) -> None:
        if not self.config.enable_hmac:
            return
        signer = self.signer
        if signer is None:
            return
        headers["X-Hmac"] = signer.sign(self.tokens.access_token or "", path, body)

    # -- header construction -------------------------------------------------
    def base_headers(
        self,
        *,
        with_access: bool = True,
        path: str | None = None,
        base: str | None = None,
        application: bool = False,
    ) -> dict[str, str]:
        """Build the standard header set for a request.

        Scoping mirrors the extension's axios clients (``VD`` factory, gateway
        ``zU`` / OBS ``ZD`` singletons in main.js):

        * ``X-LAL`` is set on gateway-base requests only; the OBS client gets
          ``Accept-Language`` alone.
        * ``X-Line-ChannelToken`` is attached only to gateway ``/api/timeline/``
          paths (the extension's gateway headerMapper), never to thrift calls.
        * ``X-Line-Application`` is *never* set on gateway requests — the
          extension sends it exclusively on private OBS resource fetches — so
          it is opt-in via ``application=True``.
        * ``content-type`` is not part of the base set: axios attaches it only
          when a JSON body is sent, so :meth:`post_json` adds it (and bodyless
          GETs / raw OBS uploads carry none).

        ``base`` is the target base URL (``None`` = the gateway); ``path`` is
        the request path (used for the channel-token scoping).
        """
        is_gateway = base is None or base == self.config.gateway_base
        h = {
            "accept": "application/json, text/plain, */*",
            "X-Line-Chrome-Version": self.config.chrome_version,
            "Accept-Language": self.config.locale,
            "User-Agent": self.config.user_agent,
        }
        if is_gateway:
            h["X-LAL"] = _LAL_MAP.get(self.config.locale, "en_US")
            # the extension sets X-Legy-Host as a gateway-client default when a
            # legyHost is configured (SD() -> zU().defaults.headers.common)
            if self.config.legy_host:
                h["X-Legy-Host"] = self.config.legy_host
        if application:
            h["X-Line-Application"] = self.config.application_header
        if with_access and self.tokens.access_token:
            h["X-Line-Access"] = self.tokens.access_token
        if (
            is_gateway
            and path is not None
            and path.startswith("/api/timeline/")
            and self.tokens.channel_access_token
        ):
            h["X-Line-ChannelToken"] = self.tokens.channel_access_token
        return h

    # -- the core Thrift-over-JSON call --------------------------------------
    def call(
        self,
        endpoint_key: str,
        args: list[Any],
        *,
        require_auth: bool = True,
        extra_headers: Mapping[str, str] | None = None,
        allow_refresh: bool = True,
    ) -> Any:
        """Invoke a Thrift method by its ``Namespace.Service.method`` key.

        ``args`` is the ordered list of positional Thrift arguments.  Returns
        the decoded JSON result, or raises a :class:`LineApiError` subclass.
        """
        path = ep.thrift_path(endpoint_key)
        return self.post_json(
            path,
            args,
            require_auth=require_auth,
            extra_headers=extra_headers,
            allow_refresh=allow_refresh,
            endpoint_key=endpoint_key,
        )

    def post_json(
        self,
        path: str,
        body: Any,
        *,
        require_auth: bool = True,
        extra_headers: Mapping[str, str] | None = None,
        allow_refresh: bool = True,
        endpoint_key: str | None = None,
        base: str | None = None,
    ) -> Any:
        if require_auth and not self.tokens.access_token:
            raise LineLoginRequired("no access token; run a login flow first", path=path)

        is_gateway = base is None or base == self.config.gateway_base
        url = (base or self.config.gateway_base) + path
        headers = self.base_headers(with_access=require_auth, path=path, base=base)
        # Axios attaches Content-Type only when there is a body — we always
        # send one here (the compact positional-args JSON array).
        headers["content-type"] = "application/json"
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        # X-Hmac must be computed over the exact (path, body) we transmit, and
        # before any caller-supplied header overrides.
        if is_gateway:
            self._sign(headers, path, data)
        if extra_headers:
            headers.update(extra_headers)

        t0 = time.monotonic()
        started = time.time()
        attempts = max(1, self.config.max_retries + 1)
        refreshed = False
        for attempt in range(attempts):
            resp = self._send("POST", url, headers=headers, data=data.encode("utf-8"))

            # Defensive layer (deliberate deviation, kept on purpose): the
            # extension's *gateway* client does not retry on HTTP 401 (only its
            # OBS client does; the talk gateway signals credential expiry via
            # TalkException 119 below).  We keep the 401 renew-and-retry as a
            # safety net for gateways that answer 401 instead.
            if (
                resp.status_code == 401
                and allow_refresh
                and not refreshed
                and self._refresh_hook
            ):
                refreshed = True
                if self._refresh_hook():
                    return self.post_json(
                        path,
                        body,
                        require_auth=require_auth,
                        extra_headers=extra_headers,
                        allow_refresh=False,
                        endpoint_key=endpoint_key,
                        base=base,
                    )
            try:
                result = self._decode(
                    resp, path=path, endpoint_key=endpoint_key, gateway=is_gateway
                )
            except _MustRefreshTokenError as exc:
                # TalkException 119 (MUST_REFRESH_V3_TOKEN): renew the token
                # and replay the request once — the extension's renewToken()
                # path (same hook the 401 layer uses).
                if (
                    allow_refresh
                    and not refreshed
                    and self._refresh_hook
                    and self._refresh_hook()
                ):
                    refreshed = True
                    return self.post_json(
                        path,
                        body,
                        require_auth=require_auth,
                        extra_headers=extra_headers,
                        allow_refresh=False,
                        endpoint_key=endpoint_key,
                        base=base,
                    )
                self._record_exchange(
                    "POST",
                    url,
                    path,
                    endpoint_key,
                    headers,
                    body,
                    resp,
                    None,
                    exc,
                    t0,
                    started,
                )
                raise
            except _RetryableApiError as exc:
                # Outer envelope 99999 (UNKNOWN_ERROR) or inner 115
                # (SHOULD_RETRY): retried within the same budget as 5xx.
                if attempt < attempts - 1:
                    continue
                self._record_exchange(
                    "POST",
                    url,
                    path,
                    endpoint_key,
                    headers,
                    body,
                    resp,
                    None,
                    exc,
                    t0,
                    started,
                )
                raise
            except LineError as exc:
                self._record_exchange(
                    "POST",
                    url,
                    path,
                    endpoint_key,
                    headers,
                    body,
                    resp,
                    None,
                    exc,
                    t0,
                    started,
                )
                raise
            self._record_exchange(
                "POST", url, path, endpoint_key, headers, body, resp, result, None, t0, started
            )
            return result
        raise LineTransportError(  # pragma: no cover - loop always exits above
            f"request to {url} failed: retries exhausted"
        )

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        require_auth: bool = True,
        stream: bool = False,
        extra_headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        base: str | None = None,
        sign: bool = True,
    ) -> requests.Response:
        is_gateway = base is None or base == self.config.gateway_base
        url = (base or self.config.gateway_base) + path
        # No content-type on bodyless GETs (axios adds none); the SSE deviation
        # (header auth instead of cookies) is documented in operations.py.
        headers = self.base_headers(with_access=require_auth, path=path, base=base)
        # axios signs GETs too; the signed path includes the query string, the
        # body is the empty string. Compute it once and reuse for signing+record.
        sig_path = path
        if params:
            from urllib.parse import urlencode

            sig_path = path + "?" + urlencode(params)
        if is_gateway and sign:
            self._sign(headers, sig_path, "")
        if extra_headers:
            headers.update(extra_headers)
        t0 = time.monotonic()
        started = time.time()
        resp = self._send(
            "GET", url, headers=headers, params=params, stream=stream, timeout=timeout
        )
        if not stream:  # never consume a streamed (SSE) body
            self._record_exchange(
                "GET",
                url,
                sig_path,
                None,
                headers,
                None,
                resp,
                None,
                None,
                t0,
                started,
                decode_text=True,
            )
        return resp

    # -- recording -----------------------------------------------------------
    def _record_exchange(
        self,
        method: str,
        url: str,
        path: str,
        endpoint_key: str | None,
        headers: Mapping[str, str],
        req_body: Any,
        resp: Any,
        result: Any,
        error: Exception | None,
        t0: float,
        started: float,
        *,
        decode_text: bool = False,
    ) -> None:
        if self.recorder is None and not self.hooks:
            return
        from .recorder import Exchange

        self._seq += 1
        status = resp.status_code if resp is not None else None
        resp_headers = dict(resp.headers) if resp is not None else {}
        resp_text = ""
        if resp is not None:
            try:
                resp_text = resp.text
            except Exception:
                resp_text = ""
        if result is None and resp_text and (error is not None or decode_text):
            result = self._safe_json(resp_text)
        ex = Exchange(
            seq=self._seq,
            method=method,
            url=url,
            path=path,
            endpoint=endpoint_key,
            request_headers=dict(headers),
            request_body=req_body,
            status=status,
            response_headers=resp_headers,
            response_body=result,
            response_text=resp_text,
            duration_ms=(time.monotonic() - t0) * 1000.0,
            ok=error is None and (status is None or status < 400),
            error=str(error) if error else None,
            started_at=started,
        )
        if self.recorder is not None:
            self.recorder.record(ex)
        for hook in self.hooks:
            try:
                hook(ex)
            except Exception:  # pragma: no cover - hooks must never break a call
                pass

    @staticmethod
    def _safe_json(text: str) -> Any:
        try:
            return json.loads(text)
        except ValueError:
            return text

    # -- internals -----------------------------------------------------------
    def _send(self, method: str, url: str, **kw: Any) -> requests.Response:
        kw.setdefault("timeout", self.config.timeout)
        kw.setdefault("verify", self.config.verify_tls)
        if self.rate_limiter is not None and not kw.get("stream"):
            self.rate_limiter.acquire()
        last_exc: Exception | None = None
        attempts = max(1, self.config.max_retries + 1)
        for attempt in range(attempts):
            try:
                log.debug("%s %s", method, url)
                resp = self.session.request(method, url, **kw)
                # Retry only on transient 5xx (never on a streamed response).
                if resp.status_code >= 500 and not kw.get("stream") and attempt < attempts - 1:
                    last_exc = LineTransportError(
                        f"server error {resp.status_code}", status=resp.status_code
                    )
                    continue
                return resp
            except requests.RequestException as exc:  # pragma: no cover - network
                last_exc = exc
                if attempt >= attempts - 1:
                    break
        raise LineTransportError(f"request to {url} failed: {last_exc}") from last_exc

    def _decode(
        self,
        resp: requests.Response,
        *,
        path: str,
        endpoint_key: str | None = None,
        gateway: bool = True,
    ) -> Any:
        text = resp.text
        ctype = resp.headers.get("content-type", "")
        payload: Any = None
        if text and ("json" in ctype or text[:1] in '[{"-0123456789tfn'):
            try:
                payload = json.loads(text)
            except ValueError:
                payload = text
        if _DEBUG:
            print(f"[okline] {resp.status_code} {path}\n  <- {text[:1000]}", file=sys.stderr)

        if 200 <= resp.status_code < 300:
            # The Chrome gateway wraps every result in an envelope:
            #   {"message": "OK", "data": <result>, ...}
            # The extension's success interceptor compares strictly against
            # "OK" (no case folding) and rejects everything else, including
            # envelope-less bodies — a non-"OK" message is an application
            # error *despite* HTTP 200.
            if isinstance(payload, dict) and "message" in payload:
                if payload["message"] == "OK":
                    return payload.get("data") if "data" in payload else payload
                # non-OK envelope -> fall through to the error path below
            elif not gateway:
                # Non-gateway bases (OBS/legy) legitimately return raw,
                # non-enveloped payloads — keep the lenient unwrap there only.
                return self._unwrap(payload)
            # else: gateway 2xx without an OK envelope -> error path below

        # --- error path (non-2xx, non-OK envelope, or non-enveloped 2xx) ----
        info = self._extract_error(payload)
        if info.reason:
            msg: str = info.reason
        elif 200 <= resp.status_code < 300:
            msg = f"gateway response without OK envelope for {path}"
        else:
            msg = f"HTTP {resp.status_code} for {path}"
        kwargs: dict[str, Any] = {
            "code": info.code,
            "reason": info.reason,
            "metadata": info.metadata,
            "path": path,
            "status": info.status if info.status is not None else resp.status_code,
            "raw": payload,
        }
        # Classification, following the extension's interceptor chain
        # (uM/pM/iM/oM helpers in main.js):
        if (
            info.outer_code == _CODE_REQUEST_MUST_UPGRADE
            or "UPGRADE" in (info.reason or "").upper()
        ):
            # The ONLY upgrade trigger is the outer envelope code 10006
            # (REQUEST_MUST_UPGRADE).  Note inner code 86 is
            # E2EE_INVALID_VERSION — an E2EE protocol error, NOT an upgrade.
            raise LineMustUpgradeError(msg, **kwargs)
        if info.inner_code == _CODE_MUST_REFRESH_V3_TOKEN:
            # 119 (MUST_REFRESH_V3_TOKEN): renew + replay (post_json handles
            # it); surfaces as LineAuthError when no refresh hook exists.
            raise _MustRefreshTokenError(msg, **kwargs)
        if resp.status_code in (401, 403) or info.code in _AUTH_CODES:
            # Auth/kickout set: inner codes 1 (AUTHENTICATION_FAILED),
            # 7 (NOT_AVAILABLE_USER), 8 (NOT_AUTHORIZED_DEVICE) — or the HTTP
            # status says so.  ILLEGAL_ARGUMENT (0) is NOT an auth error.
            raise LineAuthError(msg, **kwargs)
        if info.outer_code == _CODE_UNKNOWN_ERROR or info.inner_code == _CODE_SHOULD_RETRY:
            # Retryable per the extension's default retryCondition: outer
            # envelope 99999 (UNKNOWN_ERROR) or inner 115 (SHOULD_RETRY).
            raise _RetryableApiError(msg, **kwargs)
        raise LineApiError(msg, **kwargs)

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        """Lenient unwrap for non-gateway bases (OBS/legy) only: a payload
        whose keys are a subset of ``{data, status, message}`` yields its
        ``data``; anything else passes through untouched."""
        if isinstance(payload, dict) and set(payload) <= {"data", "status", "message"}:
            if "data" in payload:
                return payload["data"]
        return payload

    @staticmethod
    def _extract_error(payload: Any) -> _ErrorInfo:
        """Pull the outer envelope code, the nested TalkException code/reason
        and any metadata out of an error body.

        Shapes seen on the wire::

            {"code": 10051, "message": "RESPONSE_ERROR",
             "data": {"name": "TalkException", "code": 82,
                      "reason": "can not send using plain mode"}}

            {"message": "FAILED", "error": {"code": 20, "message": "bad request"}}

        When the outer envelope code is 10052 (RESPONSE_HTTP_ERROR) the
        meaningful status lives one level deeper — ``data.statusCode`` (e.g.
        410 = pin-code timeout) plus a ``rejectionReason`` used for alerting —
        and both are surfaced (the statusCode as code *and* status, the
        rejectionReason merged into metadata).
        """
        outer: int | None = None
        inner: int | None = None
        code: int | None = None
        reason: str | None = None
        meta: Any = None
        nested: Any = None
        status: int | None = None
        if isinstance(payload, dict):
            err = payload.get("error", payload)
            if isinstance(err, dict):
                code = err.get("code", err.get("statusCode"))
                reason = err.get("message") or err.get("reason") or err.get("debugMessage")
                meta = err.get("metadata") or err.get("parameterMap")
                # The gateway wraps the real Thrift exception in `data`
                # (e.g. {"code":10051,"message":"RESPONSE_ERROR",
                #        "data":{"name":"TalkException","code":82,
                #                "reason":"can not send using plain mode"}}).
                # Surface that inner code/reason — it is what actually matters.
                nested = err.get("data")
                if isinstance(nested, dict) and (
                    nested.get("code") is not None or nested.get("reason")
                ):
                    if isinstance(nested.get("code"), int):
                        inner = nested["code"]
                        code = inner
                    reason = (
                        nested.get("reason")
                        or nested.get("alertMessage")
                        or nested.get("message")
                        or reason
                    )
                    meta = nested.get("parameterMap") or nested.get("metadata") or meta
            # Outer gateway envelope code (the qU family: 10006
            # REQUEST_MUST_UPGRADE, 10052 RESPONSE_HTTP_ERROR, 99999
            # UNKNOWN_ERROR, ...); for the {"error": {...}} shape the error
            # block plays the envelope role.
            raw_outer = payload.get("code")
            if not isinstance(raw_outer, int) and isinstance(payload.get("error"), dict):
                raw_outer = payload["error"].get("code")
            if isinstance(raw_outer, int):
                outer = raw_outer
            if outer == _CODE_RESPONSE_HTTP_ERROR and isinstance(nested, dict):
                status_code = nested.get("statusCode")
                if isinstance(status_code, int):
                    code = status_code
                    status = status_code
                rejection = nested.get("rejectionReason")
                if rejection is not None:
                    meta = {
                        **(meta if isinstance(meta, dict) else {}),
                        "rejectionReason": rejection,
                    }
        return _ErrorInfo(outer, inner, code, reason, meta, status)

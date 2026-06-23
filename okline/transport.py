"""HTTP transport for the LINE Chrome gateway.

This module owns everything that is independent of any individual Thrift
method: the :class:`requests.Session`, the standard header set, token storage,
automatic token refresh on ``401`` and the JSON encode/decode + error mapping.

Wire format recap (confirmed from ``static/js/main.js``)::

    POST /api/talk/thrift/<Ns>/<Service>/<method> HTTP/1.1
    Host: line-chrome-gw.line-apps.com
    content-type: application/json
    X-Line-Access: <access token>
    X-Line-Application: CHROMEOS\t3.7.2\tChrome_OS\t
    X-Line-Chrome-Version: 3.7.2

    [ <arg0>, <arg1>, ... ]          <-- positional thrift args, named structs

The success response is the bare JSON value the Thrift method returns.  An
application error comes back with a non-2xx status and a JSON body describing
the Thrift exception.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

_DEBUG = bool(os.environ.get("LINE_DEBUG"))

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "okline requires the 'requests' package: pip install requests"
    ) from exc

from . import endpoints as ep
from .exceptions import (
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
APP_NAME = "CHROMEOS"
APP_VERSION = "3.7.2"
OS_NAME = "Chrome_OS"
DEFAULT_APPLICATION_HEADER = f"{APP_NAME}\t{APP_VERSION}\t{OS_NAME}\t"

# A realistic Chrome UA; the gateway does not strictly require it but some
# anti-abuse checks look at it.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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
    max_retries: int = 2  # transport-level retries on 5xx
    verify_tls: bool = True
    proxies: Mapping[str, str] | None = None
    enable_hmac: bool = True  # attach the required X-Hmac header
    node_path: str | None = None  # node executable for the HMAC bridge
    ltsm_origin: str | None = None  # extension origin for the LTSM token


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
    def base_headers(self, *, with_access: bool = True) -> dict[str, str]:
        h = {
            "content-type": "application/json",
            "accept": "application/json, text/plain, */*",
            "X-Line-Application": self.config.application_header,
            "X-Line-Chrome-Version": self.config.chrome_version,
            "Accept-Language": self.config.locale,
            "X-LAL": _LAL_MAP.get(self.config.locale, "en_US"),
            "User-Agent": self.config.user_agent,
        }
        if with_access and self.tokens.access_token:
            h["X-Line-Access"] = self.tokens.access_token
        if self.tokens.channel_access_token:
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
        headers = self.base_headers(with_access=require_auth)
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        # X-Hmac must be computed over the exact (path, body) we transmit, and
        # before any caller-supplied header overrides.
        if is_gateway:
            self._sign(headers, path, data)
        if extra_headers:
            headers.update(extra_headers)

        t0 = time.monotonic()
        started = time.time()
        resp = self._send("POST", url, headers=headers, data=data.encode("utf-8"))

        if resp.status_code == 401 and allow_refresh and self._refresh_hook:
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
            result = self._decode(resp, path=path, endpoint_key=endpoint_key)
        except LineError as exc:
            self._record_exchange(
                "POST", url, path, endpoint_key, headers, body, resp, None, exc, t0, started
            )
            raise
        self._record_exchange(
            "POST", url, path, endpoint_key, headers, body, resp, result, None, t0, started
        )
        return result

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
        headers = self.base_headers(with_access=require_auth)
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

    def _decode(self, resp: requests.Response, *, path: str, endpoint_key: str | None) -> Any:
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
            # A non-"OK" message is an application error *despite* HTTP 200
            # (the extension's response interceptor rejects those).
            if isinstance(payload, dict) and "message" in payload:
                message = payload.get("message")
                if isinstance(message, str) and message.upper() == "OK":
                    return payload.get("data") if "data" in payload else payload
                # non-OK envelope -> error
                code, reason, meta = self._extract_error(payload, resp)
                raise LineApiError(
                    reason or str(message) or "request failed",
                    code=code,
                    reason=reason or message,
                    metadata=meta,
                    path=path,
                    status=resp.status_code,
                    raw=payload,
                )
            return self._unwrap(payload)

        # --- error path -----------------------------------------------------
        code, reason, meta = self._extract_error(payload, resp)
        msg = reason or f"HTTP {resp.status_code} for {path}"
        kwargs = {
            "code": code,
            "reason": reason,
            "metadata": meta,
            "path": path,
            "status": resp.status_code,
            "raw": payload,
        }
        # Heuristic classification.
        upgrade = (reason or "").upper().find("UPGRADE") >= 0
        if upgrade or code == 86:
            raise LineMustUpgradeError(msg, **kwargs)
        if resp.status_code in (401, 403) or code in (0, 8, 1):
            raise LineAuthError(msg, **kwargs)
        raise LineApiError(msg, **kwargs)

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        """The gateway sometimes wraps the result in ``{"data": ...}`` (the
        axios interceptor in the extension unwraps ``data.data``)."""
        if isinstance(payload, dict) and set(payload) <= {"data", "status", "message"}:
            if "data" in payload:
                return payload["data"]
        return payload

    @staticmethod
    def _extract_error(
        payload: Any, resp: requests.Response
    ) -> tuple[int | None, str | None, Any]:
        code: int | None = None
        reason: str | None = None
        meta: Any = None
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
                inner = err.get("data")
                if isinstance(inner, dict) and (
                    inner.get("code") is not None or inner.get("reason")
                ):
                    code = inner.get("code", code)
                    reason = (
                        inner.get("reason")
                        or inner.get("alertMessage")
                        or inner.get("message")
                        or reason
                    )
                    meta = inner.get("parameterMap") or inner.get("metadata") or meta
        # Talk auth code can also arrive as a header.
        if code is None:
            hv = resp.headers.get("x-line-resp-code") or resp.headers.get(
                "X-Line-Response-Code"
            )
            if hv and hv.isdigit():
                code = int(hv)
        return code, reason, meta

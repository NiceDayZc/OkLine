"""OBS (Object Storage Service) — upload/download of message media & profiles.

Two routes, both observed in the bundle:

* Gateway helpers (simplest, used for profile pictures)::

      POST /api/obs/uploadProfile?mid=<mid>     body=<bytes>  content-type=<mime>
      POST /api/obs/copyForMessage              body=<copy params json>

* Raw OBS (used for chat media), against ``obs.line-apps.com``::

      POST /r/<service>/<sid>/<oid>             body=<bytes>
      headers: X-Obs-Params: <base64(json)>,  range: bytes <off>-<end>/<total>
      GET  /r/<service>/<sid>/<oid>            download
      GET  <path>/object_info.obs              headers: X-Talk-Meta

``X-Obs-Params`` is a base64 of a small JSON descriptor (name, type, ver, ...).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from . import endpoints as ep
from .transport import Transport


def encode_obs_params(params: Mapping[str, Any]) -> str:
    """Base64(JSON) — the ``X-Obs-Params`` header value."""
    raw = json.dumps(params, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


class ObsClient:
    """High-level wrapper over the OBS endpoints."""

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    # -- gateway helpers -----------------------------------------------------
    def upload_profile_image(
        self, mid: str, data: bytes, content_type: str = "image/jpeg"
    ) -> Any:
        """Upload a profile picture for ``mid`` via the gateway."""
        path = "/" + ep.SPECIAL_ENDPOINTS["obs.uploadProfile"] + f"?mid={_q(mid)}"
        headers = self._t.base_headers()
        headers["content-type"] = content_type
        resp = self._t._send(
            "POST", self._t.config.gateway_base + path, headers=headers, data=data
        )
        return _json(resp)

    def copy_for_message(self, params: Mapping[str, Any]) -> Any:
        """``/api/obs/copyForMessage`` — re-use an already uploaded object."""
        path = "/" + ep.SPECIAL_ENDPOINTS["obs.copyForMessage"]
        return self._t.post_json(path, params)

    # -- raw OBS -------------------------------------------------------------
    def upload_object(
        self,
        service: str,
        sid: str,
        oid: str,
        data: bytes,
        *,
        obs_params: Mapping[str, Any],
        offset: int | None = None,
        total: int | None = None,
    ) -> Any:
        """Upload bytes to ``/r/<service>/<sid>/<oid>`` on the OBS host."""
        url = f"{self._t.config.obs_base}/r/{service}/{sid}/{oid}"
        headers = self._t.base_headers()
        headers["X-Obs-Params"] = encode_obs_params(obs_params)
        headers.pop("content-type", None)
        if offset is not None and total is not None:
            headers["range"] = f"bytes {offset}-{total - 1}/{total}"
        resp = self._t._send("POST", url, headers=headers, data=data)
        return _json(resp)

    def download_object(
        self, service: str, sid: str, oid: str, *, talk_meta: str | None = None
    ) -> bytes:
        """Download the raw bytes of an OBS object."""
        url = f"{self._t.config.obs_base}/r/{service}/{sid}/{oid}"
        headers = self._t.base_headers()
        headers.pop("content-type", None)
        if talk_meta:
            headers["X-Talk-Meta"] = talk_meta
        resp = self._t._send("GET", url, headers=headers)
        resp.raise_for_status()
        return resp.content

    def upload_message_object(
        self,
        oid: str,
        data: bytes,
        *,
        name: str,
        obs_type: str,
        cat: str | None = None,
        enc_token: str | None = None,
        service: str = "talk",
        sid: str = "m",
    ) -> Any:
        """Upload media bytes for a (non-E2EE / V1) message.

        ``oid`` is the message id returned by ``sendMessage``; the upload goes to
        ``/r/talk/m/<oid>`` with ``X-Obs-Params`` describing the object.  OBS
        uses the *encrypted* access token (``acquireEncryptedAccessToken``), not
        the raw one, and is **not** X-Hmac signed.
        """
        params: dict = {"ver": "2.0", "name": name, "type": obs_type}
        if cat:
            params["cat"] = cat
        url = f"{self._t.config.obs_base}/r/{service}/{sid}/{oid}"
        headers = self._t.base_headers()
        headers.pop("content-type", None)
        headers["X-Obs-Params"] = encode_obs_params(params)
        if enc_token:  # OBS auth = encrypted access token
            headers["X-Line-Access"] = enc_token
        import time

        t0 = time.monotonic()
        started = time.time()
        resp = self._t._send("POST", url, headers=headers, data=data)
        err = None
        if resp.status_code >= 400:
            from .exceptions import LineApiError

            err = LineApiError(
                f"OBS upload failed: HTTP {resp.status_code}",
                status=resp.status_code,
                path=url,
                raw=resp.text,
            )
        # record the upload so it shows up in api.last / api.dump()
        self._t._record_exchange(
            "POST",
            url,
            f"/r/{service}/{sid}/{oid}",
            "OBS.uploadMessageObject",
            {**headers, "X-Obs-Params": headers.get("X-Obs-Params", "")},
            f"<{len(data)} bytes: {obs_type}>",
            resp,
            None,
            err,
            t0,
            started,
            decode_text=True,
        )
        if err:
            raise err
        return _json(resp)

    def object_info(self, path: str, *, talk_meta: str | None = None) -> Any:
        url = f"{self._t.config.obs_base}{path}/object_info.obs"
        headers = self._t.base_headers()
        if talk_meta:
            headers["X-Talk-Meta"] = talk_meta
        resp = self._t._send("GET", url, headers=headers)
        return _json(resp)


def _q(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


def _json(resp: Any) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text

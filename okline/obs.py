"""OBS (Object Storage Service) — upload/download of message media & profiles.

Two routes, both observed in the bundle:

* Gateway helpers (simplest, used for profile pictures)::

      POST /api/obs/uploadProfile?mid=<mid>     body=<bytes>  content-type=<mime>
      POST /api/obs/copyForMessage              body=<copy params json>

* Raw OBS (used for chat media), against ``obs.line-apps.com`` (or one of the
  CDN hosts — see :data:`okline.endpoints.OBS_HOSTS`)::

      POST /r/<service>/<sid>/<oid>             body=<bytes>
      headers: X-Obs-Params: <base64(json)>,  range: bytes <off>-<end>/<total>
      GET  /r/<service>/<sid>/<oid>            download
      GET  <path>/object_info.obs              headers: X-Talk-Meta (getObjectInfo)
      GET  <path>/info.obs                     resource info
      GET  <path>/playback.obs                 params modelName/networkType/lang

``X-Obs-Params`` is a base64 of a small JSON descriptor (name, type, ver, ...).

**OBS auth** mirrors the extension's ``FD`` headerMapper: raw-OBS URLs
containing ``/r/myhome/`` (timeline/VOOM covers) authenticate with
``X-Line-ChannelToken`` (the timeline channel token, lazily issued); every
other private raw-OBS request authenticates with ``X-Line-Access`` set to the
*encrypted* access token of feature ``OBS_GENERAL`` plus
``X-Line-Application: "CHROMEOS\\t3.7.2\\tChrome_OS\\t"``.  Public objects
(``public=True``) carry no auth header at all.  Pass ``talk_meta=`` /
``message_id=`` to attach the ``X-Talk-Meta`` header (E2EE media); it is built
by :func:`build_talk_meta` exactly like the bundle's ``lB``.
"""

from __future__ import annotations

import base64
import json
import struct
from collections.abc import Mapping
from typing import Any

from . import endpoints as ep
from .enums import EncryptedAccessTokenFeatureType
from .exceptions import LineApiError
from .transport import _LAL_MAP, Transport

# The timeline/myhome channel id (see okline.services.channel_shop).
_TIMELINE_CHANNEL_ID = "1341209850"

# The "myhome" URL segment that switches raw-OBS auth to the channel token
# (the bundle's ``/r/${Tp}/`` test with Tp="myhome").
_MYHOME_SEGMENT = "/r/myhome/"

# playback.obs lang param — the bundle's KD map (locale -> short form),
# defaulting to "en" like the extension.
_PLAYBACK_LANGS = {
    "ko_KR": "ko",
    "ja_JP": "ja",
    "en_US": "en",
    "zh_CN": "zh-Hans",
    "zh_TW": "zh-Hant",
}


def encode_obs_params(params: Mapping[str, Any]) -> str:
    """Base64(JSON) — the ``X-Obs-Params`` header value."""
    raw = json.dumps(params, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def build_talk_meta(message_id: str) -> str:
    """Build the ``X-Talk-Meta`` header value for a message id.

    Byte-exact port of the bundle's ``lB`` (main.js @2013806): a minimal
    Thrift-binary struct — a string field 4 carrying the messageId and an
    empty list-of-struct field 27 — wrapped as
    ``base64(json({"message": base64(<thrift bytes>)}))``.  E2EE media
    downloads from ``/r/talk/em`` require this header.
    """
    mid = message_id.encode("utf-8")
    buf = bytearray()
    buf += b"\x0b"  # writeByte(11)  — Thrift STRING field header
    buf += struct.pack(">h", 4)  # writeI16(4)    — field id 4 (messageId)
    buf += struct.pack(">i", len(mid))  # writeI32(len)
    buf += mid  # writeBinary(messageId)
    buf += b"\x0f"  # writeByte(15)  — Thrift LIST
    buf += struct.pack(">h", 27)  # writeI16(27)   — field id 27
    buf += b"\x0c"  # writeByte(12)  — element type STRUCT
    buf += struct.pack(">i", 0)  # writeI32(0)    — empty list
    buf += b"\x00"  # writeByte(0)   — STOP
    blob = base64.b64encode(bytes(buf)).decode("ascii")
    return base64.b64encode(
        json.dumps({"message": blob}, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _parse_encrypted_token(raw: Any) -> str | None:
    """Split the ``acquireEncryptedAccessToken`` blob into the token string
    (``<rec-sep>``/``<unit-sep>`` delimited; the token is ``rows[1][0]``)."""
    if not isinstance(raw, str):
        return None
    rows = [r.split("\x1f") for r in raw.split("\x1e") if r]
    if len(rows) > 1 and rows[1]:
        return rows[1][0]
    return None


class ObsClient:
    """High-level wrapper over the OBS endpoints."""

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    # -- token plumbing (the extension's rT() accessors) ----------------------
    def _encrypted_token(self) -> str:
        """The encrypted access token for OBS_GENERAL, acquired lazily and
        cached on ``tokens.encrypted_access_tokens`` (the same cache the
        ``get_encrypted_access_token`` service method fills).

        Raises :class:`LineApiError` when the token cannot be acquired or
        parsed — a private raw-OBS request without it is guaranteed to fail
        server-side, so the failure is made loud at the source instead of
        silently sending an unauthenticated request."""
        key = str(int(EncryptedAccessTokenFeatureType.OBS_GENERAL))
        tok = self._t.tokens.encrypted_access_tokens.get(key)
        if tok:
            return tok
        raw = self._t.call(
            "Talk.TalkService.acquireEncryptedAccessToken",
            [int(EncryptedAccessTokenFeatureType.OBS_GENERAL)],
        )
        tok = _parse_encrypted_token(raw)
        if not tok:
            raise LineApiError(
                "acquireEncryptedAccessToken returned no parseable token "
                f"for feature OBS_GENERAL: {raw!r}",
                path="Talk.TalkService.acquireEncryptedAccessToken",
                raw=raw,
            )
        self._t.tokens.encrypted_access_tokens[key] = tok
        return tok

    def _channel_token(self) -> str:
        """The timeline channel access token, issued lazily and cached on
        ``tokens.channel_access_token`` (as the channel-token service does).

        Raises :class:`LineApiError` when ``issueChannelToken`` returns no
        usable token — see :meth:`_encrypted_token` for why."""
        tok: str | None = self._t.tokens.channel_access_token
        if tok:
            return tok
        data = self._t.call("Talk.ChannelService.issueChannelToken", [_TIMELINE_CHANNEL_ID])
        if isinstance(data, dict):
            tok = data.get("channelAccessToken") or data.get("token")
        if not tok:
            raise LineApiError(
                f"issueChannelToken returned no channelAccessToken: {data!r}",
                path="Talk.ChannelService.issueChannelToken",
                raw=data,
            )
        self._t.tokens.channel_access_token = tok
        return tok

    def _obs_auth(self, path: str) -> dict[str, str]:
        """The extension's ``FD`` headerMapper: ``/r/myhome/`` URLs take the
        channel access token; other private raw-OBS requests take the
        encrypted access token (OBS_GENERAL) plus X-Line-Application."""
        headers: dict[str, str] = {}
        if _MYHOME_SEGMENT in path:
            headers["X-Line-ChannelToken"] = self._channel_token()
        else:
            headers["X-Line-Access"] = self._encrypted_token()
            headers["X-Line-Application"] = self._t.config.application_header
        return headers

    def _obs_base(self, *, cdn: str | None = None, host: str | None = None) -> str:
        if host:
            return host
        if cdn:
            if cdn not in ep.OBS_HOSTS:
                raise ValueError(f"unknown cdn host {cdn!r} (try {sorted(ep.OBS_HOSTS)})")
            return ep.OBS_HOSTS[cdn]
        return self._t.config.obs_base

    def _talk_meta_header(
        self, talk_meta: str | None, message_id: str | None
    ) -> dict[str, str]:
        if message_id:
            return {"X-Talk-Meta": build_talk_meta(message_id)}
        if talk_meta:
            return {"X-Talk-Meta": talk_meta}
        return {}

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
        """Upload bytes to ``/r/<service>/<sid>/<oid>`` on the OBS host
        (the extension's ``qD`` — always private, so always FD auth)."""
        path = f"/r/{service}/{sid}/{oid}"
        url = self._t.config.obs_base + path
        headers = self._t.base_headers(with_access=False, base=self._t.config.obs_base)
        headers.update(self._obs_auth(path))
        headers["X-Obs-Params"] = encode_obs_params(obs_params)
        headers.pop("content-type", None)
        if offset is not None and total is not None:
            headers["range"] = f"bytes {offset}-{total - 1}/{total}"
        resp = self._t._send("POST", url, headers=headers, data=data)
        _raise_for_status(resp, url)
        return _json(resp)

    def download_object(
        self,
        service: str,
        sid: str,
        oid: str,
        *,
        talk_meta: str | None = None,
        message_id: str | None = None,
        public: bool = False,
        cdn: str | None = None,
        host: str | None = None,
    ) -> bytes:
        """Download the raw bytes of an OBS object (the extension's ``zD``).

        ``public=True`` sends no auth header (public objects); otherwise the
        FD auth scheme applies (channel token for ``/r/myhome/``, encrypted
        access token elsewhere).  ``message_id`` builds the ``X-Talk-Meta``
        header (E2EE media — required for ``/r/talk/em`` paths);
        ``talk_meta`` passes a pre-built value through.  ``cdn`` selects one
        of :data:`okline.endpoints.OBS_HOSTS` (``"cdn_obs"``,
        ``"cdn_profile"``, ...); ``host`` overrides the base URL outright.
        """
        path = f"/r/{service}/{sid}/{oid}"
        base = self._obs_base(cdn=cdn, host=host)
        url = base + path
        headers = self._t.base_headers(with_access=False, base=base)
        if not public:
            headers.update(self._obs_auth(path))
        headers.pop("content-type", None)
        headers.update(self._talk_meta_header(talk_meta, message_id))
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
        uses the *encrypted* access token (``acquireEncryptedAccessToken``,
        feature OBS_GENERAL) — acquired lazily and cached unless the caller
        supplies ``enc_token`` — and is **not** X-Hmac signed.
        """
        params: dict = {"ver": "2.0", "name": name, "type": obs_type}
        if cat:
            params["cat"] = cat
        path = f"/r/{service}/{sid}/{oid}"
        url = self._t.config.obs_base + path
        headers = self._t.base_headers(with_access=False, base=self._t.config.obs_base)
        headers.pop("content-type", None)
        headers["X-Obs-Params"] = encode_obs_params(params)
        if enc_token:  # caller-supplied encrypted OBS token overrides the lazy path
            headers["X-Line-Access"] = enc_token
            if service != "myhome":
                headers["X-Line-Application"] = self._t.config.application_header
        else:
            headers.update(self._obs_auth(path))
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
            path,
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

    # -- .obs metadata endpoints (the bundle's GD / FF / playback helpers) ----
    def object_info(
        self, path: str, *, talk_meta: str | None = None, message_id: str | None = None
    ) -> Any:
        """``GET <path>/object_info.obs`` — the extension's ``GD``
        (getObjectInfo); FD auth, optional pre-built ``X-Talk-Meta``
        (E2EE media needs it — pass ``message_id`` and it is built)."""
        url = self._t.config.obs_base + path + "/object_info.obs"
        headers = self._t.base_headers(with_access=False, base=self._t.config.obs_base)
        headers.update(self._obs_auth(path))
        headers.update(self._talk_meta_header(talk_meta, message_id))
        resp = self._t._send("GET", url, headers=headers)
        _raise_for_status(resp, url)
        return _json(resp)

    def resource_info(self, path: str) -> Any:
        """``GET <path>/info.obs`` — the extension's resource-info helper
        (FD auth; used to check an object before downloading)."""
        url = self._t.config.obs_base + path + "/info.obs"
        headers = self._t.base_headers(with_access=False, base=self._t.config.obs_base)
        headers.update(self._obs_auth(path))
        resp = self._t._send("GET", url, headers=headers)
        _raise_for_status(resp, url)
        return _json(resp)

    def playback_info(
        self,
        path: str,
        *,
        talk_meta: str | None = None,
        message_id: str | None = None,
        lang: str | None = None,
    ) -> Any:
        """``GET <path>/playback.obs`` — video playback info (FD auth) with
        the bundle's fixed query params ``modelName="CHROMEOS"`` and
        ``networkType="WiFi"`` plus ``lang`` (short form: ``en``, ``ja``,
        ``ko``, ``zh-Hans``, ``zh-Hant``; defaults from the configured
        locale).  Optional ``X-Talk-Meta`` (built from ``message_id``)."""
        url = self._t.config.obs_base + path + "/playback.obs"
        headers = self._t.base_headers(with_access=False, base=self._t.config.obs_base)
        headers.update(self._obs_auth(path))
        headers.update(self._talk_meta_header(talk_meta, message_id))
        if lang is None:
            lal = _LAL_MAP.get(self._t.config.locale, "en_US")
            lang = _PLAYBACK_LANGS.get(lal, "en")
        params = {"modelName": "CHROMEOS", "networkType": "WiFi", "lang": lang}
        resp = self._t._send("GET", url, headers=headers, params=params)
        _raise_for_status(resp, url)
        return _json(resp)

    # -- special gateway / legy REST helpers (timeline & pageinfo) ------------
    def _gw_get(self, path: str, params: Mapping[str, Any]) -> Any:
        resp = self._t.get(path, params=dict(params))
        return self._t._decode(resp, path=path)

    def timeline_home_id(self, e_mid: str) -> Any:
        """``GET /api/timeline/homeId`` — the timeline (VOOM) home id of a
        user (the gateway attaches the timeline channel token)."""
        return self._gw_get("/" + ep.SPECIAL_ENDPOINTS["timeline.home"], {"eMid": e_mid})

    def timeline_get_cover(self, target_mid: str, my_mid: str, my_region: str) -> Any:
        """``GET /api/timeline/getCover`` with ``{targetMid, myMid, myRegion}``."""
        return self._gw_get(
            "/" + ep.SPECIAL_ENDPOINTS["timeline.getCover"],
            {"targetMid": target_mid, "myMid": my_mid, "myRegion": my_region},
        )

    def timeline_update_cover(
        self, my_mid: str, my_region: str, cover_object_id: str, *, story_share: bool = False
    ) -> Any:
        """``POST /api/timeline/updateCover`` with ``{myMid, myRegion,
        coverObjectId, storyShare}`` — register a cover uploaded to
        ``/r/myhome/c/<oid>`` (see :meth:`upload_object`)."""
        return self._t.post_json(
            "/" + ep.SPECIAL_ENDPOINTS["timeline.updateCover"],
            {
                "myMid": my_mid,
                "myRegion": my_region,
                "coverObjectId": cover_object_id,
                "storyShare": story_share,
            },
        )

    def page_info(self, url: str, *, accept_language: str | None = None) -> Any:
        """``GET <legy>/sc/api/v2/pageinfo/get`` — link-preview metadata
        (``params {url, caller: "LINE_CHROME"}``, ``Accept-Language`` header).
        Returns the decoded ``{"result": {title, summary, image_source}}``
        payload (non-gateway base, so no OK-envelope unwrap)."""
        lang = accept_language or self._t.config.locale
        path = "/" + ep.SPECIAL_ENDPOINTS["legy.pageinfo"]
        resp = self._t.get(
            path,
            params={"url": url, "caller": "LINE_CHROME"},
            extra_headers={"Accept-Language": lang},
            base=self._t.config.legy_base,
        )
        data = self._t._decode(resp, path=path, gateway=False)
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            return data["result"]
        return data


def _q(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


def _json(resp: Any) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _raise_for_status(resp: Any, url: str) -> None:
    """Surface an HTTP error status from a raw-OBS request as a local
    :class:`LineApiError` (with the body attached) instead of returning the
    error payload as if it were the result."""
    if resp.status_code >= 400:
        raise LineApiError(
            f"OBS request failed: HTTP {resp.status_code}",
            status=resp.status_code,
            path=url,
            raw=getattr(resp, "text", ""),
        )

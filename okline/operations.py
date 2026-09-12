"""Receiving incoming operations (new messages, invitations, ...).

The Chrome client has two mechanisms:

* **SSE** — ``GET /api/operation/receive?version=3.7.2&localRev=...&language=...``
  returns a ``text/event-stream``.  Named events (``ping``,
  ``connInfoRevision``, ``reconnect``, ``talkException``, ``fullSync``,
  ``partialFullSync``) carry control info; the default/unnamed ``message``
  events carry batches of :class:`Operation` JSON.  This is the modern path
  the extension uses.  The query params are the resume cursor: ``localRev``
  is seeded from ``getLastOpRevision`` before the first open, updated from
  every received operation (and from ``fullSync``/``partialFullSync``
  control events' ``nextRevision``) and re-sent on every reconnect —
  exactly the extension's ``sT`` transport (main.js).  Other params:
  ``version``, ``language`` (the underscore X-LAL form of the configured
  locale), ``lastPartialFullSyncs`` (JSON), ``fullSyncRequestReason`` (only
  on the connect that requests a full sync) and ``legyHost`` when
  configured.

  *Documented deviation*: the extension opens the stream with an
  EventSource, which cannot set headers — it authenticates via cookies
  (``withCredentials``).  Python has no session cookie, so we send header
  auth instead (``X-Line-Access`` + the ``X-Hmac`` signature), reduced to
  the minimal header set (``accept: text/event-stream``, ``cache-control``
  and the browser-supplied ``User-Agent`` / ``Accept-Language``) plus that
  auth — the custom headers a real EventSource cannot carry
  (``X-LAL``, ``X-Line-Chrome-Version``) are dropped.
* **Long-poll** — ``GET /api/talk/long-polling/LF1`` (and ``/JQ``) with the
  ``X-Line-Session-ID`` header and an ``X-LST`` timeout (ms).  In the
  extension these are *login PIN-verification* long-polls, not an
  operation-receive fallback: ``JQ`` backs ``checkPinCodeVerifiedForEmail``
  (X-LST 180000) and ``LF1`` backs
  ``checkPinCodeVerifiedForEmailWithE2EE`` (X-LST 110000) during the
  device-confirm phase of an e-mail login (the auth flows call them via
  :meth:`long_poll`).  The method stays a generic utility here.

Both are exposed here; :meth:`OperationReceiver.stream` is the high-level
iterator most callers want.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from . import endpoints as ep
from .transport import _LAL_MAP, APP_VERSION, Transport

log = logging.getLogger("okline.ops")

# Control SSE events whose payload carries a nextRevision resume cursor.
_SYNC_EVENTS = ("fullSync", "partialFullSync")
# Events that never carry operations (control / keepalive).
_SKIP_EVENTS = ("ping", "reconnect", "connInfoRevision", *_SYNC_EVENTS)


@dataclass
class SSEEvent:
    """One parsed Server-Sent-Event."""

    event: str  # "" / "ping" / "connInfoRevision" / ...
    data: Any  # decoded JSON if possible, else raw str
    id: str | None = None
    raw: str = ""


@dataclass
class Operation:
    """A single talk operation (see :class:`okline.enums.OpType`)."""

    revision: int | None = None
    type: int | None = None
    reqSeq: int | None = None
    checksum: str | None = None
    param1: str | None = None
    param2: str | None = None
    param3: str | None = None
    message: dict | None = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> Operation:
        return cls(
            revision=d.get("revision"),
            type=d.get("type"),
            reqSeq=d.get("reqSeq"),
            checksum=d.get("checksum"),
            param1=d.get("param1"),
            param2=d.get("param2"),
            param3=d.get("param3"),
            message=d.get("message"),
            raw=d,
        )


class OperationReceiver:
    """Streams operations from the gateway."""

    def __init__(self, transport: Transport, *, local_rev: int | None = None) -> None:
        self._t = transport
        # SSE resume cursor.  ``None`` means "not known yet" — the first
        # _open_sse seeds it from getLastOpRevision (the extension's Nj call).
        # Pre-seed it to skip that call / resume from a stored revision.
        self.local_rev = local_rev
        # Mirror of the extension's lastPartialFullSyncs query param (a JSON
        # map of sync-category -> timestamp).  Reset after every open.
        self.last_partial_full_syncs: dict[str, str] = {}

    # -- SSE -----------------------------------------------------------------
    def stream(
        self,
        *,
        reconnect: bool = True,
        full_sync_request_reason: str | None = None,
    ) -> Iterator[SSEEvent]:
        """Yield :class:`SSEEvent` objects forever (until the caller stops).

        Automatically reopens the stream on disconnect when ``reconnect`` is
        true (mirrors the extension's ``handleError`` behaviour), re-sending
        the tracked ``localRev`` cursor so no operations are missed.
        ``full_sync_request_reason`` is sent on the first open only (the
        extension drops it after the connect attempt).
        """
        first = True
        while True:
            try:
                yield from self._open_sse(
                    full_sync_request_reason=full_sync_request_reason if first else None
                )
            except Exception as exc:  # pragma: no cover - network
                log.warning("SSE stream error: %s", exc)
                if not reconnect:
                    raise
            if not reconnect:
                break
            first = False

    # -- query params (the extension's sT.connect) ---------------------------
    def _ensure_local_rev(self) -> int | str | None:
        """Seed the resume cursor from ``getLastOpRevision`` if unknown."""
        if self.local_rev is None:
            self.local_rev = self._t.call("Talk.TalkService.getLastOpRevision", [])
        return self.local_rev

    def _note_revision(self, revision: Any) -> bool:
        """Monotonic cursor update; True when ``revision`` was new."""
        if not isinstance(revision, int) or revision <= 0:
            return False
        if self.local_rev is None or revision > self.local_rev:
            self.local_rev = revision
            return True
        return False

    def _sse_query(self, full_sync_request_reason: str | None) -> dict[str, Any]:
        local_rev = self._ensure_local_rev()
        params: dict[str, Any] = {
            "version": APP_VERSION,
            "language": _LAL_MAP.get(self._t.config.locale, "en_US"),
            "lastPartialFullSyncs": json.dumps(
                self.last_partial_full_syncs, separators=(",", ":")
            ),
        }
        if local_rev is not None:
            params["localRev"] = local_rev
        if full_sync_request_reason:
            params["fullSyncRequestReason"] = full_sync_request_reason
        legy_host = getattr(self._t.config, "legy_host", None)
        if isinstance(legy_host, str) and legy_host.strip():
            params["legyHost"] = legy_host.strip()
        return params

    def _open_sse(self, *, full_sync_request_reason: str | None = None) -> Iterator[SSEEvent]:
        path = "/" + ep.SPECIAL_ENDPOINTS["operation.receive"]
        params = self._sse_query(full_sync_request_reason)
        url = self._t.config.gateway_base + path
        # Minimal header set + header auth (documented deviation — the
        # extension's EventSource authenticates via cookies and cannot set
        # custom headers; Python has no session cookie, so we keep
        # X-Line-Access + X-Hmac).  User-Agent / Accept-Language stay because
        # the browser supplies them on a real EventSource request too; the
        # custom X-LAL / X-Line-Chrome-Version headers are dropped.
        headers = {
            "accept": "text/event-stream",
            "cache-control": "no-cache",
            "Accept-Language": self._t.config.locale,
            "User-Agent": self._t.config.user_agent,
        }
        if self._t.tokens.access_token:
            headers["X-Line-Access"] = self._t.tokens.access_token
        sig_path = path + "?" + urlencode(params)
        self._t._sign(headers, sig_path, "")
        resp = self._t._send(
            "GET", url, headers=headers, params=params, stream=True, timeout=None
        )
        if resp.status_code != 200:
            resp.close()
            raise RuntimeError(f"SSE open failed: HTTP {resp.status_code}")
        # The extension resets lastPartialFullSyncs once the stream is open.
        self.last_partial_full_syncs = {}
        event_name = ""
        data_lines: list[str] = []
        last_id: str | None = None
        try:
            for raw_line in resp.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = raw_line.rstrip("\r")
                if line == "":
                    # dispatch
                    if data_lines:
                        data_str = "\n".join(data_lines)
                        yield SSEEvent(
                            event_name or "message",
                            _maybe_json(data_str),
                            id=last_id,
                            raw=data_str,
                        )
                    event_name, data_lines = "", []
                    continue
                if line.startswith(":"):
                    continue  # comment / keep-alive
                key, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if key == "event":
                    event_name = value
                elif key == "data":
                    data_lines.append(value)
                elif key == "id":
                    last_id = value
        finally:
            # release the connection when the generator is closed/exhausted/raises
            resp.close()

    def _events(
        self, *, reconnect: bool, full_sync_request_reason: str | None
    ) -> Iterator[SSEEvent]:
        for ev in self.stream(
            reconnect=reconnect, full_sync_request_reason=full_sync_request_reason
        ):
            # Track the resume cursor from sync control events.
            if ev.event in _SYNC_EVENTS and isinstance(ev.data, dict):
                self._note_revision(ev.data.get("nextRevision"))
            yield ev

    def iter_operations(
        self,
        *,
        reconnect: bool = True,
        full_sync_request_reason: str | None = None,
    ) -> Iterator[Operation]:
        """Convenience: yield individual :class:`Operation` objects from SSE.

        Tracks the ``localRev`` cursor: each operation's revision updates it,
        and operations at or below the current cursor (re-delivered after a
        reconnect) are dropped, like the extension's ``handleReceiveOpEvent``.
        """
        for ev in self._events(
            reconnect=reconnect, full_sync_request_reason=full_sync_request_reason
        ):
            if ev.event in _SKIP_EVENTS:
                continue
            payload = ev.data
            ops = payload.get("operations") if isinstance(payload, dict) else payload
            if isinstance(ops, list):
                for o in ops:
                    if isinstance(o, dict):
                        op = Operation.from_dict(o)
                        if self._seen(op):
                            continue
                        yield op
            elif isinstance(payload, dict) and payload.get("type") is not None:
                op = Operation.from_dict(payload)
                if self._seen(op):
                    continue
                yield op

    def _seen(self, op: Operation) -> bool:
        """Update the cursor from ``op`` and report whether it is stale."""
        rev = op.revision
        if isinstance(rev, int) and rev > 0:
            if self.local_rev is not None and rev <= self.local_rev:
                return True  # already delivered before the cursor
            self._note_revision(rev)
        return False

    # -- long-poll (login PIN verification) ------------------------------------
    def long_poll(
        self,
        session_id: str,
        *,
        endpoint: str = "LF1",
        timeout_ms: int | None = None,
    ) -> Any:
        """One blocking long-poll round-trip; returns the decoded
        (envelope-unwrapped) body — e.g. ``{"result": {"verifier": ...}}`` for
        the device-confirm polls.

        In the extension these endpoints serve the e-mail-login device-confirm
        phase, with the ``verifier`` from the preceding ``loginV2`` step as
        ``X-Line-Session-ID`` (the displayed PIN is display-only — see
        ``auth._device_confirm_poll``):

        * ``JQ`` — ``checkPinCodeVerifiedForEmail`` (plain login), X-LST 180000;
        * ``LF1`` — ``checkPinCodeVerifiedForEmailWithE2EE`` (E2EE login),
          X-LST 110000.

        ``timeout_ms`` defaults to the endpoint's bundle constant (180000 for
        JQ, 110000 for LF1).
        """
        if timeout_ms is None:
            timeout_ms = 180000 if endpoint == "JQ" else 110000
        key = f"longpoll.{endpoint}"
        path = "/" + ep.SPECIAL_ENDPOINTS[key]
        resp = self._t.get(
            path,
            extra_headers={"X-Line-Session-ID": session_id, "X-LST": str(timeout_ms)},
            timeout=(timeout_ms / 1000.0) + 15,
        )
        return self._t._decode(resp, path=path)

    # -- service notices -------------------------------------------------------
    def lan_notice(
        self,
        lang: str,
        country: str,
        next_seq: int | None = None,
        *,
        include_body: bool = True,
    ) -> Any:
        """``GET /api/lan/notice`` — localised service notices/banners.

        The extension fetches these before and after login.  Returns the
        decoded ``{"documents": [...], "nextSeq": N}`` page; keep calling
        with the returned ``nextSeq`` until it is absent/``None`` to walk
        every page (the bundle filters ``documents`` on
        ``extras.showTimingWhenLogin`` client-side).
        """
        params: dict[str, Any] = {"lang": lang, "country": country}
        if next_seq is not None:
            params["nextSeq"] = next_seq
        params["includeBody"] = include_body
        path = "/" + ep.SPECIAL_ENDPOINTS["lan.notice"]
        resp = self._t.get(path, params=params)
        return self._t._decode(resp, path=path)


def _maybe_json(s: str) -> Any:
    s = s.strip()
    if not s:
        return s
    if s[0] in "[{":
        try:
            return json.loads(s)
        except ValueError:
            return s
    return s

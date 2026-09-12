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

  **Keepalive** (``stream(..., keepalive=True)``): the extension wires a
  ``PingInterceptor`` onto its SSE transport with
  ``{range: [2e4, 2e4], step: 0, spare: 1e4}`` (main.js) — a ping interval
  of 20 s with a 10 s spare window.  The interceptor's "ping" is not a
  frame on the wire: after every received event it re-arms an
  ``interval + spare`` (30 s) silence watchdog, and when *no event* arrives
  within that window it tears the transport down and reopens it.  The port
  mirrors both halves: a daemon thread issues a cheap request
  (``Talk.TalkService.getServerTime``) through the transport every 20 s
  while the ``stream()`` generator is active, and the streamed GET carries
  a per-read socket timeout of ``interval + spare`` (30 s) — a silently
  dead connection (NAT/middlebox timeout, no FIN) raises out of
  ``iter_lines`` and feeds the reconnect loop above with the ``localRev``
  cursor, exactly the interceptor's reopen.  Opt-in (default off): the
  pings are real requests the caller did not otherwise ask for, share the
  transport/session (``requests.Session`` is not documented thread-safe)
  and consume rate-limiter tokens, so surprising existing callers is worse
  than missing the keepalive.

  **Reconnect backoff**: the extension's ``sT.connect`` retries failed
  connects with ``min(2**attempt * 1000 ms, 600 s)`` (bundle ``Dd``/``_R``,
  giving up after 144 consecutive attempts), while a stream that *was*
  open is reopened immediately by ``handleError``.  ``stream(reconnect=
  True)`` mirrors that: each failed connection (open error, or a
  connection that yielded no events at all) doubles the delay — starting
  at ``backoff_start`` (1 s) and capped at ``backoff_max`` (60 s; the
  bundle's cap is 600 s, pass ``backoff_max=600`` for exact parity) — and
  a connection that yielded at least one event resets the counter and
  reconnects immediately.  Unlike the extension we never give up
  (``reconnect=True`` keeps retrying forever with the capped delay).

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
import threading
import time
from collections.abc import Generator, Iterator
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

# SSE keepalive ping interval, in seconds — the extension's PingInterceptor
# config on its SSE transport (main.js): {range: [2e4, 2e4], step: 0,
# spare: 1e4} i.e. a 20 s ping interval with a 10 s spare window.  Our
# pings are independent getServerTime requests on their own 20 s cadence.
SSE_KEEPALIVE_INTERVAL = 20.0
# The PingInterceptor's spare window: how far the no-traffic window extends
# past the interval before the silence watchdog tears the stream down and
# reopens it.  Ported as the per-read socket timeout on the streamed GET
# (see OperationReceiver.stream), so a silently dead connection surfaces as
# a read error and feeds the reconnect loop.
SSE_KEEPALIVE_SPARE = 10.0
# Reconnect backoff, ported from the extension's sT.connect:
# delay = min(2**attempt * 1e3 ms, 6e5 ms).  The pragmatic default cap is
# 60 s; pass OperationReceiver(backoff_max=600) for exact bundle parity.
BACKOFF_START = 1.0
BACKOFF_MAX = 60.0


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


class _SSEKeepalive:
    """Background keepalive for an active ``stream()`` generator.

    Port of the extension's ``PingInterceptor`` (main.js, config
    ``{range: [2e4, 2e4], step: 0, spare: 1e4}``): a ping every 20 s with
    a 10 s spare window.  The interceptor's ping is a transport reopen when
    no event arrives within ``interval + spare``; Python ``requests`` has
    no EventSource ping frame, so this half of the port is a daemon thread
    issuing a cheap ``Talk.TalkService.getServerTime`` call through the
    transport on that 20 s cadence.  The *silence watchdog* half is not
    here: it is enforced as the per-read socket timeout on the streamed GET
    (see :meth:`OperationReceiver.stream`), which reopens a quietly dead
    connection.  A failed ping is logged and skipped — the keepalive must
    never take the stream down.
    """

    def __init__(self, transport: Transport, interval: float = SSE_KEEPALIVE_INTERVAL) -> None:
        self._t = transport
        self.interval = float(interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the ping thread (no-op if it is already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="okline-sse-keepalive", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._t.call("Talk.TalkService.getServerTime", [])
            except Exception:
                # a failed ping is not fatal — try again on the next tick
                log.debug("SSE keepalive getServerTime failed", exc_info=True)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the ping thread to stop and join it (bounded wait)."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None


class OperationReceiver:
    """Streams operations from the gateway."""

    def __init__(
        self,
        transport: Transport,
        *,
        local_rev: int | None = None,
        backoff_start: float = BACKOFF_START,
        backoff_max: float = BACKOFF_MAX,
    ) -> None:
        self._t = transport
        # SSE resume cursor.  ``None`` means "not known yet" — the first
        # _open_sse seeds it from getLastOpRevision (the extension's Nj call).
        # Pre-seed it to skip that call / resume from a stored revision.
        self.local_rev = local_rev
        # Mirror of the extension's lastPartialFullSyncs query param (a JSON
        # map of sync-category -> timestamp).  Reset after every open.
        self.last_partial_full_syncs: dict[str, str] = {}
        # Reconnect backoff (the extension's sT.connect):
        # min(2**attempt * backoff_start, backoff_max) seconds between
        # consecutive failed connections.  backoff_start=0 keeps the old
        # immediate-reconnect behaviour.
        self.backoff_start = max(0.0, float(backoff_start))
        self.backoff_max = max(0.0, float(backoff_max))
        # Active-stream tracking for request_reconnect(): the response of the
        # currently open SSE connection plus whether its loop reconnects.
        # (The extension's tT.renewToken reconnects its operation stream
        # after a token renewal only when it was open:
        # ``sdk.readyState === ReadyState.OPENED && t.connect()``.)
        self._stream_lock = threading.Lock()
        self._active_resp: Any = None
        self._active_reconnect = False

    # -- SSE -----------------------------------------------------------------
    def stream(
        self,
        *,
        reconnect: bool = True,
        full_sync_request_reason: str | None = None,
        keepalive: bool = False,
    ) -> Generator[SSEEvent, None, None]:
        """Yield :class:`SSEEvent` objects forever (until the caller stops).

        Automatically reopens the stream on disconnect when ``reconnect`` is
        true (mirrors the extension's ``handleError`` behaviour), re-sending
        the tracked ``localRev`` cursor so no operations are missed.
        ``full_sync_request_reason`` is sent on the first open only (the
        extension drops it after the connect attempt).

        Consecutive failed connections (open error, or a connection that
        yields no events) back off exponentially: ``min(2**n *
        backoff_start, backoff_max)`` seconds, ported from the extension's
        ``sT.connect`` (``min(2**a * 1e3, 6e5)`` ms; ``backoff_start=0``
        restores immediate reconnects).  A connection that yields at least
        one event resets the counter and reconnects immediately, like the
        extension's ``handleError`` on an opened stream.

        ``keepalive=True`` starts a daemon thread that pings
        ``Talk.TalkService.getServerTime`` every ~20 s while this generator
        is active (the extension's PingInterceptor, ``2e4`` ms interval
        with ``1e4`` ms spare), and arms the interceptor's silence
        watchdog: the stream is opened with a per-read socket timeout of
        ``interval + spare`` (30 s), so a connection that receives nothing
        for that long — the NAT/middlebox-timeout case, no FIN — raises out
        of ``iter_lines`` and is reopened with the tracked ``localRev``
        cursor.  The pinger is stopped when the generator is closed or
        exhausted.  Off by default — see the module docstring.
        """
        pinger = _SSEKeepalive(self._t, interval=SSE_KEEPALIVE_INTERVAL) if keepalive else None
        # The PingInterceptor's silence watchdog, ported as a per-read
        # socket timeout on the streamed GET: the extension re-opens its
        # transport when no event arrives within interval + spare (30 s) —
        # a quietly dead connection raising out of iter_lines feeds the
        # reconnect loop below with the localRev cursor.
        read_timeout = SSE_KEEPALIVE_INTERVAL + SSE_KEEPALIVE_SPARE if keepalive else None
        failures = 0  # consecutive connections that yielded nothing
        first = True
        try:
            with self._stream_lock:
                self._active_reconnect = reconnect
            if pinger is not None:
                pinger.start()
            while True:
                got_event = False
                events = self._open_sse(
                    full_sync_request_reason=full_sync_request_reason if first else None,
                    read_timeout=read_timeout,
                )
                try:
                    for ev in events:
                        got_event = True
                        yield ev
                except Exception as exc:
                    log.warning("SSE stream error: %s", exc)
                    if not reconnect:
                        raise
                finally:
                    events.close()
                if not reconnect:
                    break
                first = False
                if got_event:
                    # a productive connection: reconnect immediately and
                    # reset the backoff (the extension's handleError path)
                    failures = 0
                    delay = 0.0
                else:
                    delay = self._reconnect_delay(failures)
                    failures += 1
                if delay > 0:
                    log.info("SSE reconnect #%d in %.1fs", failures, delay)
                    time.sleep(delay)
        finally:
            with self._stream_lock:
                self._active_reconnect = False
            if pinger is not None:
                pinger.stop()

    def request_reconnect(self) -> bool:
        """Tear down the active SSE connection so the streaming loop reopens
        it with fresh credentials.

        The extension's token-refresh lifecycle (the ``tT`` class, main.js
        @~1850300) reconnects its operation stream after a successful
        ``renewToken`` when it was open (``sdk.readyState ===
        ReadyState.OPENED && t.connect()``); :meth:`okline.OkLine` calls this
        from its background renewal.  Closing the response interrupts the
        blocked ``iter_lines`` read; the ``stream(reconnect=True)`` loop then
        swallows the error and reopens with the current token.

        Returns ``True`` when an active, reconnecting stream was closed.
        Thread-safe and never raises: with no open stream — or a stream the
        caller opened with ``reconnect=False`` — it is a no-op (a
        non-reconnecting stream is left alone rather than killed).
        """
        with self._stream_lock:
            resp = self._active_resp if self._active_reconnect else None
        if resp is None:
            return False
        try:
            resp.close()
        except Exception:
            log.debug("SSE reconnect: closing the active stream failed", exc_info=True)
        return True

    def _reconnect_delay(self, failures: int) -> float:
        """Backoff before reopen attempt ``failures + 1`` (0 = first retry).

        The extension's sT.connect: ``min(2**a * 1e3 ms, 6e5 ms)`` — 1 s,
        2 s, 4 s, ... capped.  ``backoff_start=0`` disables the backoff.
        """
        if self.backoff_start <= 0:
            return 0.0
        # clamp the exponent: a forever-failing endpoint must not overflow
        # the float once the cap has already been reached
        shift = min(failures, 62)
        return min(self.backoff_start * (2**shift), self.backoff_max)

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

    def _open_sse(
        self,
        *,
        full_sync_request_reason: str | None = None,
        read_timeout: float | None = None,
    ) -> Generator[SSEEvent, None, None]:
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
        # timeout is per socket read: with the keepalive silence watchdog it
        # is interval + spare (30 s) so a quietly dead stream raises out of
        # iter_lines and is reopened; None keeps the request open-ended.
        resp = self._t._send(
            "GET", url, headers=headers, params=params, stream=True, timeout=read_timeout
        )
        if resp.status_code != 200:
            resp.close()
            raise RuntimeError(f"SSE open failed: HTTP {resp.status_code}")
        # Register the open connection so request_reconnect() can tear it
        # down after a token renewal (the extension's tT.renewToken ->
        # ``t.connect()``).
        with self._stream_lock:
            self._active_resp = resp
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
            with self._stream_lock:
                self._active_resp = None
            resp.close()

    def _events(
        self,
        *,
        reconnect: bool,
        full_sync_request_reason: str | None,
        keepalive: bool = False,
    ) -> Generator[SSEEvent, None, None]:
        for ev in self.stream(
            reconnect=reconnect,
            full_sync_request_reason=full_sync_request_reason,
            keepalive=keepalive,
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
        keepalive: bool = False,
    ) -> Iterator[Operation]:
        """Convenience: yield individual :class:`Operation` objects from SSE.

        Tracks the ``localRev`` cursor: each operation's revision updates it,
        and operations at or below the current cursor (re-delivered after a
        reconnect) are dropped, like the extension's ``handleReceiveOpEvent``.

        ``keepalive`` and the reconnect backoff are passed through to
        :meth:`stream` (see its docstring and the module docstring).
        """
        for ev in self._events(
            reconnect=reconnect,
            full_sync_request_reason=full_sync_request_reason,
            keepalive=keepalive,
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
        # the endpoint JSON-schema-validates the query: a Python bool urlencodes
        # as "True" and is rejected (10003) — the wire needs lowercase "true"
        params["includeBody"] = "true" if include_body else "false"
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

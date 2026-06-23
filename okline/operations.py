"""Receiving incoming operations (new messages, invitations, ...).

The Chrome client has two mechanisms:

* **SSE** — ``GET /api/operation/receive`` returns a ``text/event-stream``.
  Named events (``ping``, ``connInfoRevision``, ``reconnect``, ``talkException``,
  ``fullSync``, ``partialFullSync``) carry control info; the default/unnamed
  ``message`` events carry batches of :class:`Operation` JSON.  This is the
  modern path the extension uses.
* **Long-poll** — ``GET /api/talk/long-polling/LF1`` (and ``/JQ``) with the
  ``X-Line-Session-ID`` header and an ``X-LST`` timeout (ms).  A blocking GET
  that returns when something happens or the timeout elapses.

Both are exposed here; :meth:`OperationReceiver.stream` is the high-level
iterator most callers want.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from . import endpoints as ep
from .transport import Transport

log = logging.getLogger("okline.ops")


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

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    # -- SSE -----------------------------------------------------------------
    def stream(self, *, reconnect: bool = True) -> Iterator[SSEEvent]:
        """Yield :class:`SSEEvent` objects forever (until the caller stops).

        Automatically reopens the stream on disconnect when ``reconnect`` is
        true (mirrors the extension's ``handleError`` behaviour).
        """
        while True:
            try:
                yield from self._open_sse()
            except Exception as exc:  # pragma: no cover - network
                log.warning("SSE stream error: %s", exc)
                if not reconnect:
                    raise
            if not reconnect:
                break

    def _open_sse(self) -> Iterator[SSEEvent]:
        path = "/" + ep.SPECIAL_ENDPOINTS["operation.receive"]
        resp = self._t.get(
            path,
            stream=True,
            extra_headers={"accept": "text/event-stream", "cache-control": "no-cache"},
            timeout=None,
        )
        if resp.status_code != 200:
            resp.close()
            raise RuntimeError(f"SSE open failed: HTTP {resp.status_code}")
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

    def iter_operations(self, *, reconnect: bool = True) -> Iterator[Operation]:
        """Convenience: yield individual :class:`Operation` objects from SSE."""
        for ev in self.stream(reconnect=reconnect):
            if ev.event in ("ping", "reconnect", "connInfoRevision"):
                continue
            payload = ev.data
            ops = payload.get("operations") if isinstance(payload, dict) else payload
            if isinstance(ops, list):
                for o in ops:
                    if isinstance(o, dict):
                        yield Operation.from_dict(o)
            elif isinstance(payload, dict) and payload.get("type") is not None:
                yield Operation.from_dict(payload)

    # -- long-poll fallback --------------------------------------------------
    def long_poll(
        self, session_id: str, *, endpoint: str = "LF1", timeout_ms: int = 180000
    ) -> Any:
        """One blocking long-poll round-trip; returns the decoded body."""
        key = f"longpoll.{endpoint}"
        path = "/" + ep.SPECIAL_ENDPOINTS[key]
        resp = self._t.get(
            path,
            extra_headers={"X-Line-Session-ID": session_id, "X-LST": str(timeout_ms)},
            timeout=(timeout_ms / 1000.0) + 15,
        )
        try:
            return resp.json()
        except ValueError:
            return resp.text


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

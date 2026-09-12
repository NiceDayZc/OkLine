"""Offline tests for the SSE keepalive + reconnect backoff (the extension's
PingInterceptor / sT.connect retry loop, main.js).

Everything runs against :class:`conftest.FakeSession` — no network, no Node.
``time.sleep`` is monkeypatched so the backoff progression is asserted on the
recorded call sequence, never by really waiting.
"""

from __future__ import annotations

import threading
import time

import pytest
from conftest import FakeSession, enveloped

import okline.operations as ops
from okline.operations import OperationReceiver
from okline.transport import LineConfig, Tokens, Transport


class FakeSSEResp:
    """A minimal streamed ``text/event-stream`` response."""

    def __init__(self, lines: list[str], status: int = 200) -> None:
        self.status_code = status
        self._lines = lines
        self.headers = {"content-type": "text/event-stream"}
        self.closed = False

    def iter_lines(self, decode_unicode: bool = False):
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class GatedSSEResp:
    """A 200 SSE response that yields ``lines`` then blocks like a quiet
    stream, until ``close()`` releases it."""

    def __init__(self, lines: list[str]) -> None:
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}
        self._lines = lines
        self._gate = threading.Event()
        self.closed = False

    def iter_lines(self, decode_unicode: bool = False):
        yield from self._lines
        self._gate.wait(30)

    def close(self) -> None:
        self.closed = True
        self._gate.set()


class StopStream(BaseException):
    """BaseException so stream()'s ``except Exception`` never swallows it."""


def make_transport(responder, **cfg_kw) -> Transport:
    cfg = LineConfig(enable_hmac=False, **cfg_kw)
    return Transport(cfg, Tokens(access_token="TKN"), session=FakeSession(responder))


def sse_calls(session: FakeSession) -> list[dict]:
    return [c for c in session.calls if "/api/operation/receive" in c["url"]]


def pk_calls(session: FakeSession) -> list[dict]:
    return [c for c in session.calls if "getServerTime" in c["url"]]


ONE_OP = ['data: {"operations": [{"revision": 5, "type": 25}]}', ""]


# ---------------------------------------------------------------------------
# reconnect backoff (the extension's sT.connect: min(2**a * 1e3, 6e5) ms)
# ---------------------------------------------------------------------------
def _record_sleeps(monkeypatch, limit: int) -> list[float]:
    """Monkeypatch time.sleep to record delays; raise StopStream at ``limit``."""
    delays: list[float] = []

    def fake_sleep(d: float) -> None:
        delays.append(d)
        if len(delays) >= limit:
            raise StopStream

    monkeypatch.setattr(time, "sleep", fake_sleep)
    return delays


def test_backoff_progression(monkeypatch):
    # every connect fails (HTTP 503) -> delays double from 1 s
    delays = _record_sleeps(monkeypatch, 6)
    t = make_transport(lambda m, u, kw: FakeSSEResp([], status=503))
    rx = OperationReceiver(t, local_rev=1)
    with pytest.raises(StopStream):
        for _ in rx.stream(reconnect=True):
            pass
    assert delays == [1, 2, 4, 8, 16, 32]


def test_backoff_cap(monkeypatch):
    delays = _record_sleeps(monkeypatch, 10)
    t = make_transport(lambda m, u, kw: FakeSSEResp([], status=503))
    rx = OperationReceiver(t, local_rev=1)
    with pytest.raises(StopStream):
        for _ in rx.stream(reconnect=True):
            pass
    assert delays == [1, 2, 4, 8, 16, 32, 60, 60, 60, 60]  # capped at 60 s


def test_backoff_reset_on_success(monkeypatch):
    # connect #1 yields one op (success), #2/#3 fail, #4 succeeds again,
    # #5/#6 fail -> progression restarts from 1 s after each success
    delays = _record_sleeps(monkeypatch, 4)
    opens = {"n": 0}

    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            opens["n"] += 1
            if opens["n"] in (1, 4):
                return FakeSSEResp(ONE_OP)
            return FakeSSEResp([], status=503)
        return enveloped({})

    t = make_transport(responder)
    rx = OperationReceiver(t, local_rev=1)
    with pytest.raises(StopStream):
        for _ in rx.iter_operations(reconnect=True):
            pass
    # success -> immediate (no sleep); failures -> 1, 2; success -> immediate;
    # failures after the reset -> 1, 2 again
    assert delays == [1, 2, 1, 2]


def test_backoff_start_zero_immediate_reconnect(monkeypatch):
    # backoff_start=0 keeps the old behaviour: reopen immediately, no sleeps
    delays = _record_sleeps(monkeypatch, 10_000)  # would stop on ANY sleep
    opens = {"n": 0}

    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            opens["n"] += 1
            if opens["n"] >= 5:
                raise StopStream  # BaseException: escapes except Exception
            return FakeSSEResp([], status=503)
        return enveloped({})

    t = make_transport(responder)
    rx = OperationReceiver(t, local_rev=1, backoff_start=0)
    with pytest.raises(StopStream):
        for _ in rx.stream(reconnect=True):
            pass
    assert opens["n"] == 5  # five open attempts with nothing in between
    assert delays == []


def test_backoff_custom_cap(monkeypatch):
    delays = _record_sleeps(monkeypatch, 5)
    t = make_transport(lambda m, u, kw: FakeSSEResp([], status=503))
    # bundle parity: the extension caps at 6e5 ms = 600 s
    rx = OperationReceiver(t, local_rev=1, backoff_max=600)
    with pytest.raises(StopStream):
        for _ in rx.stream(reconnect=True):
            pass
    assert delays == [1, 2, 4, 8, 16]


def test_backoff_loop_never_overflows():
    # a forever-failing receiver must not overflow 2**failures
    t = make_transport(lambda m, u, kw: FakeSSEResp([], status=503))
    rx = OperationReceiver(t, local_rev=1)
    assert rx._reconnect_delay(10_000) == 60.0


# ---------------------------------------------------------------------------
# keepalive (the extension's PingInterceptor: 2e4 ms interval, 1e4 spare)
# ---------------------------------------------------------------------------
def _keepalive_transport(interval: float, resp, monkeypatch, **rx_kw):
    monkeypatch.setattr(ops, "SSE_KEEPALIVE_INTERVAL", interval)
    t = make_transport(resp)
    return t, OperationReceiver(t, local_rev=1, **rx_kw)


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _keepalive_threads() -> list[threading.Thread]:
    return [th for th in threading.enumerate() if th.name == "okline-sse-keepalive"]


def test_keepalive_thread_lifecycle(monkeypatch):
    # one thread per stream(), stopped on close(), no ping before the
    # generator is even started, none while it runs without keepalive
    resp = GatedSSEResp(ONE_OP)

    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            return resp
        return enveloped({})

    t, rx = _keepalive_transport(0.05, responder, monkeypatch)
    before = threading.active_count()
    gen = rx.stream(reconnect=False, keepalive=True)
    # lazily started: the thread appears only once iteration begins
    assert not _keepalive_threads()
    ev = next(gen)
    assert ev.event == "message"
    keepalive_threads = _keepalive_threads()
    assert len(keepalive_threads) == 1  # started once
    # the ping fired while the generator was active
    assert _wait_for(lambda: len(pk_calls(t.session)) >= 2)
    gen.close()
    assert resp.closed  # the SSE connection was released
    # thread joined, no leak
    assert _wait_for(lambda: not _keepalive_threads())
    assert threading.active_count() == before


def test_keepalive_thread_survives_reconnects(monkeypatch):
    # the pinger spans the whole generator: one thread across a failed and a
    # productive connection, gone after the generator exits
    opens = {"n": 0}
    seen_threads: list[int] = []

    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            opens["n"] += 1
            if opens["n"] == 2:
                return FakeSSEResp(ONE_OP)
            return FakeSSEResp([], status=503)
        return enveloped({})

    delays = _record_sleeps(monkeypatch, 2)

    def responder_with_sleep(method, url, kw):
        seen_threads.append(len(_keepalive_threads()))
        return responder(method, url, kw)

    t = make_transport(responder_with_sleep)
    monkeypatch.setattr(ops, "SSE_KEEPALIVE_INTERVAL", 0.05)
    rx = OperationReceiver(t, local_rev=1)
    ops_seen = []
    with pytest.raises(StopStream):
        for op in rx.iter_operations(reconnect=True, keepalive=True):
            ops_seen.append(op)
    assert [op.revision for op in ops_seen] == [5]
    # one failed connect before the productive one, and the failure after
    # the success restarted from 1 s (reset-on-success)
    assert delays == [1, 1]
    # at most one keepalive thread observed at any request, across reconnects
    assert set(seen_threads) <= {0, 1}
    assert _wait_for(lambda: not _keepalive_threads())


def test_keepalive_stops_on_exhaustion(monkeypatch):
    # a stream that simply ends (reconnect=False) also stops the pinger
    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            return FakeSSEResp(ONE_OP)
        return enveloped({})

    t, rx = _keepalive_transport(20.0, responder, monkeypatch)  # never fires
    before = threading.active_count()
    assert [op.revision for op in rx.iter_operations(reconnect=False, keepalive=True)] == [5]
    assert threading.active_count() == before
    assert pk_calls(t.session) == []  # short stream: no ping had time to fire


def test_keepalive_survives_ping_failures(monkeypatch):
    # a failing getServerTime is logged and retried, never fatal
    resp = GatedSSEResp(ONE_OP)
    pings = {"n": 0}

    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            return resp
        if url.endswith("getServerTime"):
            pings["n"] += 1
            raise RuntimeError("ping failed")
        return enveloped({})

    _, rx = _keepalive_transport(0.05, responder, monkeypatch)
    gen = rx.stream(reconnect=False, keepalive=True)
    assert next(gen).event == "message"
    assert _wait_for(lambda: pings["n"] >= 3)  # kept pinging through failures
    gen.close()
    assert _wait_for(lambda: not _keepalive_threads())


def test_keepalive_off_by_default(monkeypatch):
    # no keepalive param -> no thread, no getServerTime, ever
    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            return FakeSSEResp(ONE_OP)
        return enveloped({})

    t = make_transport(responder)
    rx = OperationReceiver(t, local_rev=1)
    before = threading.active_count()
    assert [op.revision for op in rx.iter_operations(reconnect=False)] == [5]
    assert threading.active_count() == before
    assert pk_calls(t.session) == []


def test_keepalive_watchdog_sets_silence_read_timeout(monkeypatch):
    # keepalive=True arms the PingInterceptor's silence watchdog as a
    # per-read socket timeout of interval + spare (30 s) on the streamed
    # GET; without keepalive the request stays open-ended (no timeout)
    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            return FakeSSEResp(ONE_OP)
        return enveloped({})

    monkeypatch.setattr(ops, "SSE_KEEPALIVE_INTERVAL", 20.0)
    monkeypatch.setattr(ops, "SSE_KEEPALIVE_SPARE", 10.0)
    t = make_transport(responder)
    rx = OperationReceiver(t, local_rev=1)
    assert [op.revision for op in rx.iter_operations(reconnect=False, keepalive=True)] == [5]
    assert sse_calls(t.session)[0]["timeout"] == 30.0

    t2 = make_transport(responder)
    rx2 = OperationReceiver(t2, local_rev=1)
    assert [op.revision for op in rx2.iter_operations(reconnect=False)] == [5]
    assert sse_calls(t2.session)[0]["timeout"] is None


def test_silent_stream_read_timeout_reconnects_with_cursor():
    # the watchdog firing on a quietly dead connection (no bytes for
    # interval + spare): the read error raises out of iter_lines, the
    # reconnect loop reopens immediately (the connection had yielded
    # events) and re-sends the tracked localRev cursor
    class TimedOutSSEResp(FakeSSEResp):
        """Yields its lines, then dies like a socket read timeout."""

        def iter_lines(self, decode_unicode: bool = False):
            yield from self._lines
            raise RuntimeError("read timed out")

    opens = {"n": 0}

    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            opens["n"] += 1
            if opens["n"] >= 3:
                raise StopStream  # BaseException: ends the reconnect loop
            return TimedOutSSEResp(ONE_OP)
        return enveloped({})

    t = make_transport(responder)
    rx = OperationReceiver(t, local_rev=1)
    revs = []
    with pytest.raises(StopStream):
        for op in rx.iter_operations(reconnect=True):
            revs.append(op.revision)
    assert revs == [5]  # the redelivered op is dropped by the cursor
    assert opens["n"] == 3  # two silent-death reopens, then the stop
    sse = sse_calls(t.session)
    assert len(sse) == 3
    assert sse[0]["params"]["localRev"] == 1
    assert sse[1]["params"]["localRev"] == 5  # cursor tracked across reopen
    assert sse[2]["params"]["localRev"] == 5


# ---------------------------------------------------------------------------
# request_reconnect (the tT.renewToken -> t.connect() path: after a token
# renewal the extension tears its open operation stream down and reopens it)
# ---------------------------------------------------------------------------
def test_request_reconnect_reopens_with_fresh_token():
    # a token renewed mid-stream: request_reconnect() closes the active
    # connection, the loop swallows it and reopens with the NEW token
    resp1 = GatedSSEResp(ONE_OP)
    resp2 = FakeSSEResp(ONE_OP)
    opens = {"n": 0}

    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            opens["n"] += 1
            return resp1 if opens["n"] == 1 else resp2
        return enveloped({})

    t = make_transport(responder)
    rx = OperationReceiver(t, local_rev=1)
    gen = rx.stream(reconnect=True)
    assert next(gen).event == "message"  # first event from connection #1

    t.tokens.access_token = "TKN2"  # renewed while the stream was open
    assert rx.request_reconnect() is True
    assert resp1.closed  # the old connection was torn down

    # connection #2 opens with the fresh token and yields its event
    assert next(gen).event == "message"
    assert opens["n"] == 2
    sse = sse_calls(t.session)
    assert len(sse) == 2
    assert sse[0]["headers"]["X-Line-Access"] == "TKN"
    assert sse[1]["headers"]["X-Line-Access"] == "TKN2"
    gen.close()


def test_request_reconnect_noop_without_reconnecting_stream():
    # no open stream at all -> False; a reconnect=False stream is left
    # alone rather than killed (the caller opted out of reconnects)
    t = make_transport(lambda m, u, kw: enveloped({}))
    rx = OperationReceiver(t, local_rev=1)
    assert rx.request_reconnect() is False  # nothing is streaming

    resp = GatedSSEResp(ONE_OP)

    def responder(method, url, kw):
        if url.endswith("/api/operation/receive"):
            return resp
        return enveloped({})

    t2 = make_transport(responder)
    rx2 = OperationReceiver(t2, local_rev=1)
    gen = rx2.stream(reconnect=False)
    assert next(gen).event == "message"
    assert rx2.request_reconnect() is False
    assert not resp.closed  # the non-reconnecting stream survives
    gen.close()
    assert resp.closed  # closing the generator still releases it

"""Offline tests for :mod:`okline.recorder`.

Covers the recording layer's public surface:

* :class:`Exchange` formatting — ``pretty()``, ``to_dict()``, ``to_har_entry()``
  and the redaction of secret request headers (``X-Line-Access``/``X-Hmac``)
  and secret response-body keys (``accessToken``/``refreshToken``/``certificate``).
* :class:`Recorder` ring buffer — capacity (oldest dropped), ``last``/``entries``/
  ``clear``/``find`` and the ``save()`` exporters (text / json / har).

Everything here is built directly from :class:`Exchange` objects, plus a couple
of end-to-end checks that drive a recording through ``build_api`` + a real
(fake-transport) call.  No network and no Node.js are touched.
"""

from __future__ import annotations

import json

from conftest import build_api, route

from okline import Exchange, Recorder
from okline.recorder import _MASK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_exchange(
    seq=1,
    *,
    endpoint="TalkService.TalkService.getProfile",
    request_headers=None,
    request_body=None,
    response_body=None,
    response_text="",
    status=200,
    ok=True,
    error=None,
    duration_ms=12.0,
    started_at=1_700_000_000.5,
):
    """Build a fully-populated :class:`Exchange` for assertions.

    Defaults carry a secret request header (``X-Line-Access``) and a secret
    response-body key (``accessToken``) so redaction is exercised by default.
    """
    if request_headers is None:
        request_headers = {
            "content-type": "application/json",
            "X-Line-Access": "SECRET-TOKEN",
            "X-Hmac": "SIGNATURE==",
            "X-Line-Chrome-Version": "3.7.2",
        }
    if response_body is None:
        response_body = {
            "accessToken": "AT-123",
            "refreshToken": "RT-456",
            "certificate": "CERT-789",
            "displayName": "Tester",
        }
    if response_text == "" and response_body is not None:
        response_text = json.dumps({"message": "OK", "data": response_body})
    return Exchange(
        seq=seq,
        method="POST",
        url="https://gw.line.naver.jp/api/talk/thrift/X/Y/getProfile",
        path="/api/talk/thrift/X/Y/getProfile",
        endpoint=endpoint,
        request_headers=request_headers,
        request_body=request_body if request_body is not None else [0],
        status=status,
        response_headers={"content-type": "application/json", "x-line-resp-code": "0"},
        response_body=response_body,
        response_text=response_text,
        duration_ms=duration_ms,
        ok=ok,
        error=error,
        started_at=started_at,
    )


# ---------------------------------------------------------------------------
# Exchange.pretty() redaction
# ---------------------------------------------------------------------------
def test_pretty_masks_secret_request_header_when_redacting():
    """X-Line-Access / X-Hmac values are masked in the default (redact) view."""
    out = make_exchange().pretty()  # redact=True is the default
    assert "SECRET-TOKEN" not in out
    assert "SIGNATURE==" not in out
    # the header *name* still shows, with the masked value next to it.
    assert "X-Line-Access" in out
    assert _MASK in out


def test_pretty_masks_secret_response_body_keys_when_redacting():
    """accessToken / refreshToken / certificate are masked in the response body."""
    out = make_exchange().pretty(redact=True)
    assert "AT-123" not in out
    assert "RT-456" not in out
    assert "CERT-789" not in out
    # a non-secret field is left untouched.
    assert "Tester" in out


def test_pretty_reveals_secrets_when_redact_false():
    """With redact=False every secret is shown verbatim (header + body)."""
    out = make_exchange().pretty(redact=False)
    assert "SECRET-TOKEN" in out
    assert "SIGNATURE==" in out
    assert "AT-123" in out
    assert "RT-456" in out
    assert "CERT-789" in out


def test_pretty_includes_endpoint_and_status_line():
    """The transcript header shows the OK tag, method/path and endpoint."""
    out = make_exchange().pretty()
    assert "[OK]" in out
    assert "POST /api/talk/thrift/X/Y/getProfile" in out
    assert "(TalkService.TalkService.getProfile)" in out
    assert "HTTP 200" in out


def test_pretty_marks_errors():
    """A failed exchange is tagged ERR and shows the error string."""
    ex = make_exchange(ok=False, status=500, error="boom")
    out = ex.pretty()
    assert "[ERR]" in out
    assert "error: boom" in out


# ---------------------------------------------------------------------------
# Exchange.to_dict() redaction
# ---------------------------------------------------------------------------
def test_to_dict_redacts_by_default():
    """to_dict() masks secret request headers and secret response-body keys."""
    d = make_exchange().to_dict()  # redact defaults to True
    assert d["request_headers"]["X-Line-Access"] == _MASK
    assert d["request_headers"]["X-Hmac"] == _MASK
    # non-secret header preserved
    assert d["request_headers"]["X-Line-Chrome-Version"] == "3.7.2"
    assert d["response_body"]["accessToken"] == _MASK
    assert d["response_body"]["refreshToken"] == _MASK
    assert d["response_body"]["certificate"] == _MASK
    assert d["response_body"]["displayName"] == "Tester"


def test_to_dict_keeps_secrets_when_redact_false():
    """to_dict(redact=False) round-trips the raw values."""
    d = make_exchange().to_dict(redact=False)
    assert d["request_headers"]["X-Line-Access"] == "SECRET-TOKEN"
    assert d["response_body"]["accessToken"] == "AT-123"
    assert d["response_body"]["certificate"] == "CERT-789"


def test_to_dict_carries_core_metadata():
    """The dict exposes the structural fields a consumer expects."""
    d = make_exchange(seq=7).to_dict()
    assert d["seq"] == 7
    assert d["method"] == "POST"
    assert d["status"] == 200
    assert d["ok"] is True
    assert d["endpoint"] == "TalkService.TalkService.getProfile"
    assert d["error"] is None


# ---------------------------------------------------------------------------
# Exchange.to_har_entry()
# ---------------------------------------------------------------------------
def test_to_har_entry_shape_and_redaction():
    """A HAR entry has request/response sections and masks secret headers."""
    entry = make_exchange().to_har_entry()
    assert set(entry) >= {"startedDateTime", "time", "request", "response"}
    # request headers are a name/value list; the access token is masked.
    hdrs = {h["name"]: h["value"] for h in entry["request"]["headers"]}
    assert hdrs["X-Line-Access"] == _MASK
    assert entry["request"]["method"] == "POST"
    assert entry["response"]["status"] == 200


# ---------------------------------------------------------------------------
# Recorder ring buffer
# ---------------------------------------------------------------------------
def test_recorder_respects_capacity_dropping_oldest():
    """Once capacity is exceeded the oldest exchanges are evicted."""
    rec = Recorder(capacity=3)
    for i in range(1, 6):  # record seq 1..5
        rec.record(make_exchange(seq=i))
    seqs = [e.seq for e in rec.entries]
    assert seqs == [3, 4, 5]  # 1 and 2 dropped, newest kept
    assert len(rec.entries) == 3


def test_recorder_last_and_entries():
    """`last` is the newest entry; `entries` returns a copy (defensive)."""
    rec = Recorder()
    assert rec.last is None
    a = make_exchange(seq=1)
    b = make_exchange(seq=2)
    rec.record(a)
    rec.record(b)
    assert rec.last is b
    entries = rec.entries
    assert entries == [a, b]
    # mutating the returned list must not affect the recorder.
    entries.clear()
    assert len(rec.entries) == 2


def test_recorder_clear():
    """clear() empties the buffer."""
    rec = Recorder()
    rec.record(make_exchange())
    assert rec.entries
    rec.clear()
    assert rec.entries == []
    assert rec.last is None


def test_recorder_find_by_endpoint():
    """find() returns only exchanges whose endpoint matches exactly."""
    rec = Recorder()
    rec.record(make_exchange(seq=1, endpoint="TalkService.TalkService.getProfile"))
    rec.record(make_exchange(seq=2, endpoint="TalkService.TalkService.sendMessage"))
    rec.record(make_exchange(seq=3, endpoint="TalkService.TalkService.getProfile"))
    hits = rec.find("TalkService.TalkService.getProfile")
    assert [e.seq for e in hits] == [1, 3]
    assert rec.find("NoSuch.Service.method") == []


# ---------------------------------------------------------------------------
# Recorder exporters (save to tmp_path)
# ---------------------------------------------------------------------------
def test_save_text(tmp_path):
    """save(fmt='text') writes a redacted transcript covering every entry."""
    rec = Recorder()
    rec.record(make_exchange(seq=1))
    rec.record(make_exchange(seq=2))
    path = tmp_path / "session.txt"
    rec.save(str(path), fmt="text")
    text = path.read_text(encoding="utf-8")
    assert "#1" in text and "#2" in text
    # redaction (the recorder's default) is applied.
    assert "SECRET-TOKEN" not in text
    assert "AT-123" not in text


def test_save_json_is_parseable_and_redacted(tmp_path):
    """save(fmt='json') writes a JSON array of redacted to_dict() entries."""
    rec = Recorder()
    rec.record(make_exchange(seq=1))
    path = tmp_path / "session.json"
    rec.save(str(path), fmt="json")
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, list) and len(parsed) == 1
    assert parsed[0]["seq"] == 1
    assert parsed[0]["request_headers"]["X-Line-Access"] == _MASK
    assert parsed[0]["response_body"]["accessToken"] == _MASK


def test_save_har_structure(tmp_path):
    """save(fmt='har') writes {log:{entries:[...]}} with one entry per record."""
    rec = Recorder()
    rec.record(make_exchange(seq=1))
    rec.record(make_exchange(seq=2))
    path = tmp_path / "session.har"
    rec.save(str(path), fmt="har")
    har = json.loads(path.read_text(encoding="utf-8"))
    assert "log" in har
    assert "entries" in har["log"]
    assert len(har["log"]["entries"]) == 2
    assert har["log"]["entries"][0]["request"]["method"] == "POST"


def test_to_har_top_level_shape():
    """to_har() returns the canonical HAR envelope even when empty."""
    rec = Recorder()
    har = rec.to_har()
    assert har == {
        "log": {
            "version": "1.2",
            "creator": {"name": "OkLine", "version": "1.0.0"},
            "entries": [],
        }
    }


def test_save_redact_override_reveals_secrets(tmp_path):
    """An explicit redact=False on save() overrides the recorder default."""
    rec = Recorder(redact=True)
    rec.record(make_exchange(seq=1))
    path = tmp_path / "raw.json"
    rec.save(str(path), fmt="json", redact=False)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed[0]["request_headers"]["X-Line-Access"] == "SECRET-TOKEN"
    assert parsed[0]["response_body"]["accessToken"] == "AT-123"


# ---------------------------------------------------------------------------
# End-to-end: drive a recording through the real (fake-transport) client
# ---------------------------------------------------------------------------
def test_build_api_records_a_real_call():
    """A client call populates api.history / api.last with a sensible Exchange."""
    responder = route({"getProfile": {"mid": "u" + "a" * 32, "displayName": "Tester"}})
    api = build_api(responder, access_token="LIVE-TOKEN")
    api.get_profile()

    assert len(api.history) == 1
    ex = api.last
    assert ex.method == "POST"
    assert ex.endpoint and ex.endpoint.endswith("getProfile")
    # the real request carried the access token header...
    assert ex.request_headers.get("X-Line-Access") == "LIVE-TOKEN"
    # ...but the recorder masks it on the way out.
    assert "LIVE-TOKEN" not in ex.pretty()
    assert ex.to_dict()["request_headers"]["X-Line-Access"] == _MASK


def test_build_api_dump_and_save_log(tmp_path):
    """OkLine.dump()/save_log() proxy to the recorder and mask the live token."""
    responder = route({"getProfile": {"mid": "u" + "a" * 32}})
    api = build_api(responder, access_token="LIVE-TOKEN")
    api.get_profile()

    dump = api.dump()
    assert "#1" in dump
    assert "LIVE-TOKEN" not in dump

    har_path = tmp_path / "live.har"
    api.save_log(str(har_path), fmt="har")
    har = json.loads(har_path.read_text(encoding="utf-8"))
    assert len(har["log"]["entries"]) == 1
    hdrs = {h["name"]: h["value"] for h in har["log"]["entries"][0]["request"]["headers"]}
    assert hdrs.get("X-Line-Access") == _MASK

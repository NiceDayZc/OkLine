"""Tests for the v2.1 additions: entities, session, rate-limiter, bot, media."""

from __future__ import annotations

import json
import time

import pytest
from conftest import GROUP_MID, USER_MID, USER_MID2, enveloped, route

from okline import Bot, Contact, Group, OkLine, Profile, RateLimiter, Session, enums
from okline.auth import LoginResult
from okline.bot import MessageContext
from okline.entities import parse_contacts
from okline.models import Message
from okline.operations import Operation


# --- entities --------------------------------------------------------------
def test_profile_from_dict():
    p = Profile.from_dict(
        {"mid": "uX", "displayName": "Me", "regionCode": "TH", "userid": "me"}
    )
    assert p.mid == "uX" and p.display_name == "Me" and p.region_code == "TH"
    assert p.raw["userid"] == "me"


def test_contact_from_dict_and_wrapper():
    c = Contact.from_dict(
        {"mid": "uA", "displayName": "A", "displayNameOverridden": "Bee", "capableBuddy": True}
    )
    assert c.name == "Bee"  # override wins
    assert c.is_official is True
    # accepts the getContactsV2 wrapper too
    c2 = Contact.from_dict({"contact": {"mid": "uB", "displayName": "B"}})
    assert c2.mid == "uB"


def test_group_from_dict_members():
    g = Group.from_dict(
        {
            "chatMid": GROUP_MID,
            "chatName": "G",
            "extra": {
                "groupExtra": {"memberMids": {"u1": 1, "u2": 2}, "inviteeMids": {"u3": 3}}
            },
        }
    )
    assert g.chat_mid == GROUP_MID and g.name == "G"
    assert set(g.member_mids) == {"u1", "u2"} and g.member_count == 2
    assert g.invitee_mids == ["u3"]


def test_parse_contacts():
    res = {"contacts": {"uA": {"contact": {"mid": "uA", "displayName": "A"}}}}
    parsed = parse_contacts(res)
    assert parsed["uA"].display_name == "A"


# --- session ---------------------------------------------------------------
def test_session_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    Session(access_token="AT", refresh_token="RT", mid="uX").save(str(p))
    s = Session.load(str(p))
    assert s.access_token == "AT" and s.refresh_token == "RT" and s.mid == "uX"


def test_okline_save_and_from_tokens_file(tmp_path, make_api):
    api = make_api(route({"getProfile": {"mid": "uX"}}))
    api.transport.tokens.refresh_token = "RT"
    p = str(tmp_path / "session.json")
    api.save_tokens(p)
    api2 = OkLine.from_tokens_file(p, record=False)
    try:
        assert api2.tokens.access_token == api.tokens.access_token
        assert api2._session_path == p
    finally:
        api2.close()


# --- rate limiter ----------------------------------------------------------
def test_rate_limiter_blocks_when_empty():
    rl = RateLimiter(rate=100, per=1.0, burst=2)
    assert rl.acquire() == 0.0  # token 1 (burst)
    assert rl.acquire() == 0.0  # token 2 (burst)
    waited = rl.acquire()  # must wait for a refill
    assert waited > 0.0


def test_rate_limiter_attaches_to_transport(make_api):
    api = make_api(route({"getServerTime": 1}))
    api.transport.rate_limiter = RateLimiter(rate=1000, per=1.0, burst=5)
    api.get_server_time()  # should not raise
    assert api.last.endpoint == "Talk.TalkService.getServerTime"


# --- bot -------------------------------------------------------------------
def _msg_op(text, frm=USER_MID2, to=USER_MID):
    return Operation.from_dict(
        {
            "type": int(enums.OpType.RECEIVE_MESSAGE),
            "message": {"from": frm, "to": to, "text": text, "contentType": 0, "id": "1"},
        }
    )


def test_bot_on_message_and_reply(make_api):
    sent = {}
    api = make_api(route({"sendMessage": {"id": "2"}}))
    api.send_text = lambda to, text, **kw: sent.update(to=to, text=text)  # type: ignore
    bot = Bot(api)

    @bot.on_message
    def echo(ctx: MessageContext):
        ctx.reply(f"got: {ctx.text}")

    bot.dispatch(_msg_op("hello"))
    # DM -> reply goes back to the sender
    assert sent == {"to": USER_MID2, "text": "got: hello"}


def test_bot_reply_target_group(make_api):
    sent = {}
    api = make_api()
    api.send_text = lambda to, text, **kw: sent.update(to=to)  # type: ignore
    bot = Bot(api)
    bot.on_message(lambda ctx: ctx.reply("hi"))
    bot.dispatch(_msg_op("yo", to=GROUP_MID))
    assert sent["to"] == GROUP_MID  # group -> reply to the group


def test_bot_command_routing(make_api):
    hits = []
    api = make_api()
    bot = Bot(api)

    @bot.command("ping")
    def ping(ctx):
        hits.append(ctx.text)

    bot.dispatch(_msg_op("/ping now"))
    assert hits == ["/ping now"]


def test_bot_ignores_self(make_api):
    hits = []
    api = make_api()
    api.transport.tokens.mid = USER_MID
    bot = Bot(api)
    bot._self_mid = USER_MID
    bot.on_message(lambda ctx: hits.append(1))
    bot.dispatch(_msg_op("hey", frm=USER_MID))  # from myself -> ignored
    assert hits == []


def test_bot_handler_errors_are_caught(make_api):
    api = make_api()
    bot = Bot(api)

    @bot.on_message
    def boom(ctx):
        raise RuntimeError("kaboom")

    bot.dispatch(_msg_op("x"))  # must not raise


def test_bot_run_forwards_keepalive(make_api):
    """Bot.run passes keepalive through to iter_operations (default off)."""
    api = make_api(route({"getProfile": {"mid": USER_MID}}))
    seen: list[dict] = []

    class _Ops:
        def iter_operations(self, **kw):
            seen.append(kw)
            raise RuntimeError("stop the run loop")

    api.ops = _Ops()  # type: ignore[assignment]
    bot = Bot(api)
    with pytest.raises(RuntimeError, match="stop the run loop"):
        bot.run()
    assert seen[-1] == {"reconnect": True, "keepalive": False}
    with pytest.raises(RuntimeError, match="stop the run loop"):
        bot.run(keepalive=True)
    assert seen[-1] == {"reconnect": True, "keepalive": True}


# --- media builders --------------------------------------------------------
def test_mid_to_type_is_case_insensitive():
    """Modern LINE mids are upper-case (U/C/R) — must classify correctly."""
    from okline.models import mid_to_type

    assert mid_to_type("U" + "a" * 32) == int(enums.MIDType.USER)
    assert mid_to_type("C" + "a" * 32) == int(enums.MIDType.GROUP)
    assert mid_to_type("R" + "a" * 32) == int(enums.MIDType.ROOM)
    assert mid_to_type("c" + "a" * 32) == int(enums.MIDType.GROUP)  # legacy lower-case
    # a group message built for an upper-case mid gets toType GROUP
    assert Message.text("C" + "1" * 32, "hi")["toType"] == int(enums.MIDType.GROUP)


def test_media_message_builders():
    img = Message.image(USER_MID)
    assert img["contentType"] == int(enums.ContentType.IMAGE) and img["hasContent"]
    vid = Message.video(USER_MID, duration_ms=4200)
    assert vid["contentMetadata"]["DURATION"] == "4200"
    f = Message.file(USER_MID, "a.pdf", 1234)
    assert f["contentMetadata"] == {"FILE_NAME": "a.pdf", "FILE_SIZE": "1234"}
    assert f["contentType"] == int(enums.ContentType.FILE)


def test_send_image_flow(make_api):
    import base64
    import json as _json

    from conftest import FakeResp

    def responder(method, url, kw):
        if url.endswith("sendMessage"):
            return enveloped({"id": "15001", "text": ""})
        if url.endswith("acquireEncryptedAccessToken"):
            return enveloped("meta\x1eENCTOK")  # VR(result)[1][0] == ENCTOK
        if "/r/talk/m/" in url:
            return FakeResp(200, {"ok": True})  # OBS upload
        return enveloped({})

    api = make_api(responder)
    api.send_image(USER_MID, b"\xff\xd8imagebytes", name="pic.jpg")

    urls = [c["url"] for c in api.transport.session.calls]
    assert any(u.endswith("sendMessage") for u in urls)
    obs = [c for c in api.transport.session.calls if "/r/talk/m/15001" in c["url"]]
    assert obs, "OBS upload to /r/talk/m/<messageId> not made"
    h = obs[0]["headers"]
    assert h["X-Line-Access"] == "ENCTOK"  # encrypted OBS token
    params = _json.loads(base64.b64decode(h["X-Obs-Params"]))
    assert params == {"ver": "2.0", "name": "pic.jpg", "type": "image", "cat": "original"}
    assert obs[0]["data"] == b"\xff\xd8imagebytes"


def test_obs_object_endpoints_via_client(make_api):
    """OBS .obs metadata endpoints use FD auth (encrypted token) and the
    X-Talk-Meta builder, mirroring the extension."""
    from conftest import FakeResp

    from okline.obs import build_talk_meta

    def responder(method, url, kw):
        if url.endswith("acquireEncryptedAccessToken"):
            return enveloped("hdr\x1eFEATENC\x1ftail")
        if url.endswith(".obs"):
            return FakeResp(200, {"size": 1})
        return enveloped({})

    api = make_api(responder)
    api.obs.resource_info("/r/talk/m/msg9")
    last = api.transport.session.last
    assert last["url"].endswith("/r/talk/m/msg9/info.obs")
    h = last["headers"]
    assert h["X-Line-Access"] == "FEATENC"  # encrypted, lazily acquired
    assert h["X-Line-Application"] == "CHROMEOS\t3.7.2\tChrome_OS\t"

    api.obs.playback_info("/r/talk/v/msg9", message_id="msg9")
    last = api.transport.session.last
    assert last["url"].endswith("/r/talk/v/msg9/playback.obs")
    assert last["params"]["modelName"] == "CHROMEOS"
    assert last["params"]["networkType"] == "WiFi"
    assert last["headers"]["X-Talk-Meta"] == build_talk_meta("msg9")


def test_cli_has_send_command():
    from okline.__main__ import build_parser

    a = build_parser().parse_args(["send", "u123", "hi", "--token", "T"])
    assert a.command == "send" and a.to == "u123" and a.text == "hi"


def test_nested_thrift_error_is_surfaced(make_api):
    """A wrapped TalkException must surface its inner code/reason, not 10051."""
    from conftest import FakeResp

    from okline.exceptions import LineApiError

    body = {
        "code": 10051,
        "message": "RESPONSE_ERROR",
        "data": {
            "name": "TalkException",
            "code": 82,
            "reason": "can not send using plain mode",
        },
    }
    api = make_api(lambda m, u, kw: FakeResp(400, body))
    with pytest.raises(LineApiError) as ei:
        api.get_server_time()
    assert ei.value.code == 82
    assert "plain mode" in (ei.value.reason or "")


# ---------------------------------------------------------------------------
# token-refresh lifecycle — session persistence + the proactive scheduler
# (the extension's tT class, main.js @~1850300)
# ---------------------------------------------------------------------------
SCHEDULE_POLICY = {
    "initialDelayInMillis": 1000,
    "maxDelayInMillis": 30000,
    "multiplier": 2,
    "jitterRate": 0.1,
}


def test_session_roundtrip_refresh_schedule(tmp_path):
    """The renewal schedule persists under camelCase keys and reloads."""
    p = tmp_path / "s.json"
    Session(
        access_token="AT",
        refresh_token="RT",
        token_issue_time_epoch_sec=1700000000.0,
        duration_until_refresh_sec=3600.0,
        refresh_api_retry_policy=dict(SCHEDULE_POLICY),
    ).save(str(p))
    raw = json.loads(p.read_text())
    assert raw["tokenIssueTimeEpochSec"] == 1700000000.0
    assert raw["durationUntilRefreshInSec"] == 3600.0
    assert raw["refreshApiRetryPolicy"] == SCHEDULE_POLICY

    s = Session.load(str(p))
    assert s.token_issue_time_epoch_sec == 1700000000.0
    assert s.duration_until_refresh_sec == 3600.0
    assert s.refresh_api_retry_policy == SCHEDULE_POLICY


def test_session_load_pre_v29_file_without_schedule(tmp_path):
    """A pre-v2.9 session file (no schedule keys) loads with None schedule —
    backward compatible."""
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"accessToken": "AT", "refreshToken": "RT"}))

    s = Session.load(str(p))
    assert s.access_token == "AT"
    assert s.token_issue_time_epoch_sec is None
    assert s.duration_until_refresh_sec is None
    assert s.refresh_api_retry_policy is None


def test_session_save_is_atomic(tmp_path, monkeypatch):
    """Session.save writes temp-then-``os.replace`` so concurrent saves (the
    background renewal timer and a user thread both calling save_tokens) can
    never interleave into a corrupted file — and no temp residue is left."""
    import os

    import okline.session as session_mod

    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src: str, dst: str) -> None:
        replaced.append((src, dst))
        real_replace(src, dst)

    monkeypatch.setattr(session_mod.os, "replace", spy_replace)

    p = tmp_path / "s.json"
    Session(access_token="AT", refresh_token="RT").save(str(p))

    assert len(replaced) == 1  # written via os.replace, not in place
    assert os.path.basename(replaced[0][0]).startswith(".okline-session-")
    assert replaced[0][1] == str(p)
    assert Session.load(str(p)).access_token == "AT"
    assert [e.name for e in tmp_path.iterdir()] == ["s.json"]  # no temp residue


class _FakeTimerFactory:
    """Deterministic threading.Timer stand-in: records timers instead of
    arming real OS timers, so tests can fire them synchronously."""

    def __init__(self) -> None:
        self.timers: list[_FakeTimer] = []

    def __call__(self, interval, function, args=None, kwargs=None):
        timer = _FakeTimer(interval, function)
        self.timers.append(timer)
        return timer


class _FakeTimer:
    def __init__(self, interval, function) -> None:
        self.interval = interval
        self.function = function
        self.cancelled = False
        self.started = False
        self.daemon = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        """Run the timer body (a cancelled timer never fires)."""
        if not self.cancelled:
            self.function()


@pytest.fixture
def fake_timers(monkeypatch):
    """Patch threading.Timer (as used by okline.client) with the fake."""
    factory = _FakeTimerFactory()
    monkeypatch.setattr("okline.client.threading.Timer", factory)
    return factory


def _refresh_responder(access: str, refresh: str, duration: float):
    """A tokenRefresh responder returning a scheduled tokenV3IssueResult."""
    return route(
        {
            "tokenRefresh": {
                "tokenV3IssueResult": {
                    "accessToken": access,
                    "refreshToken": refresh,
                    "tokenIssueTimeEpochSec": int(time.time()),
                    "durationUntilRefreshInSec": int(duration),
                    "refreshApiRetryPolicy": dict(SCHEDULE_POLICY),
                }
            }
        }
    )


def test_scheduler_arms_after_refresh_and_rearms_after_renewal(fake_timers, make_api):
    """auto_refresh_schedule=True: every tokenRefresh response arms a renewal
    timer firing at the absolute epoch (issue + duration), approximated on
    the local clock (minus <=1s early margin); each renewal re-arms it."""
    api = make_api(_refresh_responder("A2", "R2", 3600), auto_refresh_schedule=True)
    api.transport.tokens.refresh_token = "R1"

    assert api.auth.refresh_access_token() == "A2"
    assert len(fake_timers.timers) == 1
    timer = fake_timers.timers[0]
    assert timer.started and timer.daemon

    # delay ~= 3600s minus (elapsed + 0..1s jitter)
    assert 3595.0 < timer.interval <= 3600.0

    # the timer fires -> silent renewal -> a fresh timer is armed
    timer.fire()
    assert api.transport.tokens.access_token == "A2"
    assert len(fake_timers.timers) == 2
    assert fake_timers.timers[1].started

    api.close()
    assert fake_timers.timers[1].cancelled


def test_scheduler_failure_never_crashes(fake_timers, make_api, caplog):
    """A failed scheduled renewal only logs — the old token stays in place
    and no new timer is armed (the 119/401 defensive path still applies)."""
    import logging

    from conftest import FakeResp

    calls = {"n": 0}

    def responder(method, url, kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _refresh_responder("A2", "R2", 3600)(method, url, kw)
        return FakeResp(400, {"error": {"code": 10201, "message": "KICKOUT"}})

    api = make_api(responder, auto_refresh_schedule=True)
    api.transport.tokens.refresh_token = "R1"

    api.auth.refresh_access_token()  # arms the first timer
    timer = fake_timers.timers[0]

    with caplog.at_level(logging.WARNING, logger="okline"):
        timer.fire()  # renewal rejected -> must not raise

    assert calls["n"] == 2
    assert api.transport.tokens.access_token == "A2"  # old token kept
    assert len(fake_timers.timers) == 1  # nothing re-armed
    assert "scheduled token renewal failed" in caplog.text


def test_scheduler_is_opt_in(fake_timers, make_api):
    """Without auto_refresh_schedule the client never arms a timer, even when
    responses carry a full schedule."""
    api = make_api(_refresh_responder("A2", "R2", 3600))
    api.transport.tokens.refresh_token = "R1"

    assert api.auth.refresh_access_token() == "A2"
    assert api.auth.token_schedule is not None  # state is still recorded...
    assert api.auth.on_token_issued is None  # ...but no hook -> no timer
    assert fake_timers.timers == []


def test_close_cancels_and_disarms_the_schedule(fake_timers, make_api):
    """close() cancels the armed timer; a stale timer firing afterwards is a
    no-op (no HTTP call, no new timer)."""
    api = make_api(_refresh_responder("A2", "R2", 3600), auto_refresh_schedule=True)
    api.transport.tokens.refresh_token = "R1"
    api.auth.refresh_access_token()

    timer = fake_timers.timers[0]
    api.close()
    assert timer.cancelled
    assert api._refresh_timer is None

    calls_before = len(api.transport.session.calls)
    timer.fire()  # the fake itself refuses a cancelled timer...
    api._scheduled_renew()  # ...a genuine stale fire reaches the client guard
    assert len(api.transport.session.calls) == calls_before
    assert len(fake_timers.timers) == 1


def test_concurrent_refreshes_are_single_flight(fake_timers, make_api):
    """The scheduled renewal and the defensive 119/401 refresh share a
    single-flight lock: a caller arriving while another refresh is in flight
    waits, sees the already-rotated token and never issues a second
    tokenRefresh POST (whose slower response could overwrite the newer pair,
    or whose single-used refresh token could fail with a spurious 10201)."""
    import threading

    gate = threading.Event()
    calls = {"n": 0}
    ok = _refresh_responder("A2", "R2", 3600)

    def responder(method, url, kw):
        calls["n"] += 1
        if calls["n"] == 1:
            gate.wait(5.0)  # hold the first refresh in flight
        return ok(method, url, kw)

    api = make_api(responder, auto_refresh_schedule=True)
    api.transport.tokens.refresh_token = "R1"
    api.transport.tokens.access_token = "A1"

    results: list[bool] = []
    threads = [
        threading.Thread(target=lambda: results.append(api._auto_refresh())) for _ in range(2)
    ]
    threads[0].start()
    deadline = time.time() + 5.0  # wait until refresh #1 is in flight
    while calls["n"] == 0 and time.time() < deadline:
        time.sleep(0.005)
    assert calls["n"] == 1
    threads[1].start()  # arrives mid-refresh: blocks on the refresh lock
    time.sleep(0.2)
    gate.set()
    for th in threads:
        th.join(5.0)

    assert calls["n"] == 1  # exactly one tokenRefresh POST
    assert results == [True, True]  # both callers see the fresh outcome
    assert api.transport.tokens.access_token == "A2"
    assert api.transport.tokens.refresh_token == "R2"


def test_scheduler_arms_on_login(fake_timers, make_api):
    """A successful login (not just a refresh) arms the first renewal timer."""
    api = make_api(route({"getProfile": {"mid": "uX"}}), auto_refresh_schedule=True)
    issue = time.time()
    api.auth._adopt(
        LoginResult.parse(  # type: ignore[arg-type]
            {
                "type": 1,
                "tokenV3IssueResult": {
                    "accessToken": "A",
                    "refreshToken": "R",
                    "tokenIssueTimeEpochSec": issue,
                    "durationUntilRefreshInSec": 1800,
                    "refreshApiRetryPolicy": dict(SCHEDULE_POLICY),
                },
            }
        )
    )

    assert len(fake_timers.timers) == 1
    assert 1795.0 < fake_timers.timers[0].interval <= 1800.0


def test_scheduler_renews_into_session_file(fake_timers, make_api, tmp_path):
    """A successful scheduled renewal persists the fresh token + schedule to
    the attached session file."""
    api = make_api(_refresh_responder("A2", "R2", 3600), auto_refresh_schedule=True)
    api.transport.tokens.refresh_token = "R1"
    p = str(tmp_path / "session.json")
    api.save_tokens(p)

    api.auth.refresh_access_token()  # arms the timer
    fake_timers.timers[0].fire()  # scheduled renewal -> auto-save

    saved = json.loads((tmp_path / "session.json").read_text())
    assert saved["accessToken"] == "A2"
    assert saved["tokenIssueTimeEpochSec"] is not None
    assert saved["refreshApiRetryPolicy"] == SCHEDULE_POLICY


def test_from_tokens_file_arms_schedule(fake_timers, tmp_path):
    """from_tokens_file(+auto_refresh_schedule=True) re-arms the renewal timer
    from the persisted schedule — no fresh login needed."""
    p = tmp_path / "session.json"
    Session(
        access_token="AT",
        refresh_token="RT",
        token_issue_time_epoch_sec=time.time(),
        duration_until_refresh_sec=7200.0,
        refresh_api_retry_policy=dict(SCHEDULE_POLICY),
    ).save(str(p))

    api = OkLine.from_tokens_file(str(p), auto_refresh_schedule=True, record=False)
    try:
        assert api.auth.token_schedule is not None
        assert api.auth.token_schedule["refreshApiRetryPolicy"] == SCHEDULE_POLICY
        # the restored schedule also lands on the Tokens dataclass, so a
        # later save_tokens persists it without consulting the auth layer
        assert api.transport.tokens.token_issue_time_epoch_sec is not None
        assert api.transport.tokens.duration_until_refresh_sec == 7200.0
        assert api.transport.tokens.refresh_api_retry_policy == SCHEDULE_POLICY
        assert len(fake_timers.timers) == 1
        assert 7195.0 < fake_timers.timers[0].interval <= 7200.0
    finally:
        api.close()


def test_from_tokens_file_without_flag_arms_nothing(fake_timers, tmp_path):
    """The default from_tokens_file does not start a background timer."""
    p = tmp_path / "session.json"
    Session(
        access_token="AT",
        refresh_token="RT",
        token_issue_time_epoch_sec=time.time(),
        duration_until_refresh_sec=7200.0,
    ).save(str(p))

    api = OkLine.from_tokens_file(str(p), record=False)
    try:
        assert api.auth.token_schedule is not None  # state restored...
        assert fake_timers.timers == []  # ...but nothing armed
    finally:
        api.close()


def test_refresh_mirrors_schedule_onto_tokens(make_api):
    """Every recorded schedule also lands on the Tokens dataclass (maintained
    by AuthFlows._set_token_schedule), which is what save_tokens persists
    via Session.from_tokens."""
    api = make_api(_refresh_responder("A2", "R2", 3600))
    api.transport.tokens.refresh_token = "R1"
    api.auth.refresh_access_token()

    toks = api.transport.tokens
    assert toks.access_token == "A2"
    assert toks.token_issue_time_epoch_sec is not None
    assert toks.duration_until_refresh_sec == 3600
    assert toks.refresh_api_retry_policy == SCHEDULE_POLICY


def test_scheduled_renewal_reconnects_the_stream(fake_timers, make_api):
    """A successful scheduled renewal asks the operation receiver to reopen
    its stream — the extension's tT.renewToken does
    ``readyState === ReadyState.OPENED && t.connect()``."""
    api = make_api(_refresh_responder("A2", "R2", 3600), auto_refresh_schedule=True)
    api.transport.tokens.refresh_token = "R1"
    reconnects = []
    api.ops.request_reconnect = lambda: reconnects.append(1)  # type: ignore[method-assign]

    api.auth.refresh_access_token()  # arms the timer
    assert reconnects == []  # an explicit refresh never touches the stream
    fake_timers.timers[0].fire()  # scheduled renewal -> stream reconnect

    assert api.transport.tokens.access_token == "A2"
    assert reconnects == [1]


def test_failed_scheduled_renewal_leaves_stream_alone(fake_timers, make_api, monkeypatch):
    """A failed scheduled renewal keeps the old token AND the current stream
    (no reconnect, no new timer — the 119/401 defensive path still applies)."""
    from conftest import FakeResp

    monkeypatch.setattr("okline.auth.time.sleep", lambda s: None)  # no real backoff
    calls = {"n": 0}

    def responder(method, url, kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _refresh_responder("A2", "R2", 3600)(method, url, kw)
        return FakeResp(400, {"error": {"code": 10202, "message": "RETRY"}})

    api = make_api(responder, auto_refresh_schedule=True)
    api.transport.tokens.refresh_token = "R1"
    reconnects = []
    api.ops.request_reconnect = lambda: reconnects.append(1)  # type: ignore[method-assign]

    api.auth.refresh_access_token()  # arms the timer
    fake_timers.timers[0].fire()  # renewal keeps hitting 10202 -> fails

    assert api.transport.tokens.access_token == "A2"  # old token kept
    assert reconnects == []  # no stream reconnect on failure
    assert len(fake_timers.timers) == 1  # nothing re-armed

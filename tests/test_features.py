"""Tests for the v2.1 additions: entities, session, rate-limiter, bot, media."""

from __future__ import annotations

import pytest
from conftest import GROUP_MID, USER_MID, USER_MID2, enveloped, route

from okline import Bot, Contact, Group, Profile, RateLimiter, Session, enums
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
    from okline import OkLine

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

"""Offline tests for :mod:`okline.services.messaging` (the ``MessagingMixin``).

Every test drives a real :class:`okline.OkLine` wired to the fake HTTP session
from ``conftest`` and asserts on **what the client sent**:

* the gateway URL (which Thrift method was hit), and
* the positional-args JSON body (``last_request(api)``).

``reqSeq`` values are auto-generated, so we assert their *position/structure*
(an int in the right slot), never their exact value.

No network and no Node.js are required.
"""

from __future__ import annotations

import json

from conftest import GROUP_MID, ROOM_MID, USER_MID, USER_MID2, enveloped

from okline.enums import (
    ContentType,
    MessageRelationType,
    PredefinedReactionType,
    SyncReason,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
GW = "https://line-chrome-gw.line-apps.com"
PREFIX = "/api/talk/thrift/Talk/TalkService/"


def url_of(api) -> str:
    """The URL of the most recent request the client sent."""
    return api.transport.session.last["url"]


def assert_method(api, method: str) -> None:
    """Assert the last request hit ``Talk/TalkService/<method>``."""
    assert url_of(api) == GW + PREFIX + method


# ---------------------------------------------------------------------------
# send_message / send_text
# ---------------------------------------------------------------------------
def test_send_message_passes_reqseq_and_message_dict(api, last_request):
    """``send_message`` -> sendMessage with body ``[reqSeq, message]``."""
    msg = {"to": USER_MID, "toType": 0, "text": "hi", "contentType": 0}
    api.send_message(msg)

    assert_method(api, "sendMessage")
    body = last_request(api)
    assert isinstance(body, list) and len(body) == 2
    assert isinstance(body[0], int)  # auto-generated reqSeq
    assert body[1] == msg  # message struct forwarded verbatim


def test_send_message_honours_explicit_req_seq(api, last_request):
    """An explicit ``req_seq`` is used instead of the auto counter."""
    api.send_message({"to": USER_MID, "text": "x"}, req_seq=4242)
    body = last_request(api)
    assert body[0] == 4242


def test_send_text_builds_text_message(api, last_request):
    """``send_text`` -> [reqSeq, {to, toType, text, contentType:NONE}]."""
    api.send_text(USER_MID, "hello world")

    assert_method(api, "sendMessage")
    body = last_request(api)
    assert isinstance(body[0], int)
    payload = body[1]
    assert payload["to"] == USER_MID
    assert payload["toType"] == 0  # user mid -> USER
    assert payload["text"] == "hello world"
    assert payload["contentType"] == int(ContentType.NONE) == 0
    assert payload["sessionId"] == 0


def test_send_text_infers_totype_from_mid_prefix(api, last_request):
    """A group mid (``c...``) yields ``toType`` GROUP (2)."""
    api.send_text(GROUP_MID, "yo")
    assert last_request(api)[1]["toType"] == 2

    api.send_text(ROOM_MID, "yo")
    assert last_request(api)[1]["toType"] == 1  # room -> ROOM


def test_reqseq_auto_increments_across_calls(api, last_request):
    """Consecutive auto-seq sends produce strictly increasing reqSeq."""
    api.send_text(USER_MID, "one")
    first = last_request(api)[0]
    api.send_text(USER_MID, "two")
    second = last_request(api)[0]
    assert second == first + 1


# ---------------------------------------------------------------------------
# send_sticker
# ---------------------------------------------------------------------------
def test_send_sticker_builds_sticker_metadata(api, last_request):
    """``send_sticker`` -> STICKER content with STKID/STKPKGID/STKVER meta."""
    api.send_sticker(USER_MID, package_id="11537", sticker_id="52002734", version=3)

    assert_method(api, "sendMessage")
    payload = last_request(api)[1]
    assert payload["contentType"] == int(ContentType.STICKER) == 7
    assert payload["text"] == ""
    meta = payload["contentMetadata"]
    assert meta["STKID"] == "52002734"
    assert meta["STKPKGID"] == "11537"
    assert meta["STKVER"] == "3"


def test_send_sticker_default_version_is_one(api, last_request):
    """Version defaults to 1 and is stringified on the wire."""
    api.send_sticker(USER_MID, "1", "2")
    assert last_request(api)[1]["contentMetadata"]["STKVER"] == "1"


# ---------------------------------------------------------------------------
# send_location
# ---------------------------------------------------------------------------
def test_send_location_builds_location_struct(api, last_request):
    """``send_location`` -> LOCATION content with a ``location`` struct."""
    api.send_location(USER_MID, 35.6586, 139.7454, title="Tokyo Tower", address="Minato")

    assert_method(api, "sendMessage")
    payload = last_request(api)[1]
    assert payload["contentType"] == int(ContentType.LOCATION) == 15
    loc = payload["location"]
    assert loc["title"] == "Tokyo Tower"
    assert loc["address"] == "Minato"
    assert loc["latitude"] == 35.6586
    assert loc["longitude"] == 139.7454


# ---------------------------------------------------------------------------
# send_contact
# ---------------------------------------------------------------------------
def test_send_contact_builds_contact_metadata(api, last_request):
    """``send_contact`` -> CONTACT content carrying mid + displayName."""
    api.send_contact(USER_MID, USER_MID2, display_name="Friend")

    assert_method(api, "sendMessage")
    payload = last_request(api)[1]
    assert payload["contentType"] == int(ContentType.CONTACT) == 13
    meta = payload["contentMetadata"]
    assert meta["mid"] == USER_MID2
    assert meta["displayName"] == "Friend"


# ---------------------------------------------------------------------------
# send_flex
# ---------------------------------------------------------------------------
def test_send_flex_serialises_contents_to_json(api, last_request):
    """``send_flex`` -> FLEX content with FLEX_JSON + ALT_TEXT metadata."""
    contents = {"type": "bubble", "body": {"type": "box", "layout": "vertical"}}
    api.send_flex(USER_MID, "alt!", contents)

    assert_method(api, "sendMessage")
    payload = last_request(api)[1]
    assert payload["contentType"] == int(ContentType.FLEX) == 22
    meta = payload["contentMetadata"]
    assert meta["ALT_TEXT"] == "alt!"
    # FLEX_JSON is a JSON *string* of the contents.
    assert json.loads(meta["FLEX_JSON"]) == contents


# ---------------------------------------------------------------------------
# reply_text
# ---------------------------------------------------------------------------
def test_reply_text_sets_relation_fields(api, last_request):
    """``reply_text`` -> a text message tagged as a REPLY to another message."""
    api.reply_text(USER_MID, "re: hi", related_message_id="9001")

    assert_method(api, "sendMessage")
    payload = last_request(api)[1]
    assert payload["text"] == "re: hi"
    assert payload["contentType"] == int(ContentType.NONE)
    assert payload["relatedMessageId"] == "9001"
    assert payload["messageRelationType"] == int(MessageRelationType.REPLY) == 3
    assert payload["relatedMessageServiceCode"] == 1


# ---------------------------------------------------------------------------
# unsend_message
# ---------------------------------------------------------------------------
def test_unsend_message_body_shape(api, last_request):
    """``unsend_message`` -> [reqSeq, str(messageId)]."""
    api.unsend_message(123456789)

    assert_method(api, "unsendMessage")
    body = last_request(api)
    assert len(body) == 2
    assert isinstance(body[0], int)
    assert body[1] == "123456789"  # coerced to string


def test_unsend_message_explicit_req_seq(api, last_request):
    api.unsend_message("abc", req_seq=7)
    body = last_request(api)
    assert body == [7, "abc"]


# ---------------------------------------------------------------------------
# send_postback
# ---------------------------------------------------------------------------
def test_send_postback_uses_uppercase_mid_fields(api, last_request):
    """``send_postback`` -> single request dict with chatMID / originMID."""
    api.send_postback("m1", url="https://x.test/cb", chat_mid=GROUP_MID, origin_mid=USER_MID)

    assert_method(api, "sendPostback")
    body = last_request(api)
    assert isinstance(body, list) and len(body) == 1
    req = body[0]
    assert req["messageId"] == "m1"
    assert req["url"] == "https://x.test/cb"
    assert req["chatMID"] == GROUP_MID  # note the uppercase MID
    assert req["originMID"] == USER_MID


# ---------------------------------------------------------------------------
# react / cancel_reaction
# ---------------------------------------------------------------------------
def test_react_default_reaction_is_nice(api, last_request):
    """``react`` -> [{reqSeq, messageId, reactionType:{predefinedReactionType}}]."""
    api.react("msg-1")

    assert_method(api, "react")
    body = last_request(api)
    assert isinstance(body, list) and len(body) == 1
    req = body[0]
    assert isinstance(req["reqSeq"], int)
    assert req["messageId"] == "msg-1"
    assert req["reactionType"] == {"predefinedReactionType": int(PredefinedReactionType.NICE)}


def test_react_custom_reaction(api, last_request):
    """A custom reaction enum is passed through as ``predefinedReactionType``."""
    api.react("msg-2", reaction=int(PredefinedReactionType.LOVE))
    req = last_request(api)[0]
    assert req["reactionType"]["predefinedReactionType"] == 3


def test_cancel_reaction_body_shape(api, last_request):
    """``cancel_reaction`` -> [{reqSeq, messageId}]."""
    api.cancel_reaction("msg-3")

    assert_method(api, "cancelReaction")
    body = last_request(api)
    req = body[0]
    assert isinstance(req["reqSeq"], int)
    assert req["messageId"] == "msg-3"
    assert "reactionType" not in req


# ---------------------------------------------------------------------------
# send_chat_checked / mark_as_read / send_chat_removed
# ---------------------------------------------------------------------------
def test_send_chat_checked_positional_args(api, last_request):
    """``sendChatChecked(reqSeq, consumer, lastMessageId, sessionId)``."""
    api.send_chat_checked(GROUP_MID, last_message_id=555)

    assert_method(api, "sendChatChecked")
    body = last_request(api)
    assert len(body) == 4
    assert isinstance(body[0], int)  # reqSeq
    assert body[1] == GROUP_MID  # consumer / chat mid
    assert body[2] == "555"  # stringified last message id
    assert body[3] == 0  # default sessionId


def test_mark_as_read_is_alias_of_send_chat_checked(api, last_request):
    """``mark_as_read`` is the same method object as ``send_chat_checked``."""
    assert api.mark_as_read.__func__ is api.send_chat_checked.__func__
    api.mark_as_read(USER_MID, "777", session_id=9)
    assert_method(api, "sendChatChecked")
    body = last_request(api)
    assert body[1:] == [USER_MID, "777", 9]


def test_send_chat_removed_positional_args(api, last_request):
    """``sendChatRemoved(reqSeq, chatMid, lastMessageId, sessionId)``."""
    api.send_chat_removed(ROOM_MID, last_message_id=42, session_id=2)

    assert_method(api, "sendChatRemoved")
    body = last_request(api)
    assert isinstance(body[0], int)
    assert body[1:] == [ROOM_MID, "42", 2]


# ---------------------------------------------------------------------------
# set_chat_hidden_status
# ---------------------------------------------------------------------------
def test_set_chat_hidden_status_default_hidden_true(api, last_request):
    """``set_chat_hidden_status`` -> [{reqSeq, chatMid, lastMessageId, hidden}]."""
    api.set_chat_hidden_status(GROUP_MID, last_message_id=99)

    assert_method(api, "setChatHiddenStatus")
    body = last_request(api)
    req = body[0]
    assert isinstance(req["reqSeq"], int)
    assert req["chatMid"] == GROUP_MID
    assert req["lastMessageId"] == "99"
    assert req["hidden"] is True


def test_set_chat_hidden_status_can_unhide(api, last_request):
    api.set_chat_hidden_status(GROUP_MID, "1", hidden=False)
    assert last_request(api)[0]["hidden"] is False


# ---------------------------------------------------------------------------
# get_recent_messages
# ---------------------------------------------------------------------------
def test_get_recent_messages_body_shape(api, last_request):
    """``getRecentMessagesV2(messageBoxId, messagesCount)``."""
    api.get_recent_messages(USER_MID, count=25)

    assert_method(api, "getRecentMessagesV2")
    assert last_request(api) == [USER_MID, 25]


def test_get_recent_messages_default_count(api, last_request):
    api.get_recent_messages(USER_MID)
    assert last_request(api) == [USER_MID, 50]


# ---------------------------------------------------------------------------
# get_previous_messages
# ---------------------------------------------------------------------------
def test_get_previous_messages_request_and_sync_reason(api, last_request):
    """``getPreviousMessagesV2WithRequest(request, syncReason)``."""
    api.get_previous_messages(
        USER_MID, end_message_id=1000, delivered_time=1700000000000, count=30
    )

    assert_method(api, "getPreviousMessagesV2WithRequest")
    body = last_request(api)
    assert len(body) == 2
    request, sync_reason = body
    assert request["messageBoxId"] == USER_MID
    assert request["endMessageId"] == {
        "messageId": "1000",
        "deliveredTime": 1700000000000,
    }
    assert request["messagesCount"] == 30
    assert sync_reason == int(SyncReason.OPERATION)  # default sync reason


def test_get_previous_messages_custom_sync_reason(api, last_request):
    api.get_previous_messages(USER_MID, "1", 1, sync_reason=int(SyncReason.FULL_SYNC))
    assert last_request(api)[1] == int(SyncReason.FULL_SYNC)


# ---------------------------------------------------------------------------
# get_message_boxes
# ---------------------------------------------------------------------------
def test_get_message_boxes_request_struct(api, last_request):
    """``getMessageBoxes(request, syncReason)`` with sane defaults."""
    api.get_message_boxes()

    assert_method(api, "getMessageBoxes")
    body = last_request(api)
    assert len(body) == 2
    request, sync_reason = body
    assert request["minChatId"] is None
    assert request["activeOnly"] is True
    assert request["unreadOnly"] is False
    assert request["messageBoxCountLimit"] == 100
    assert request["withUnreadCount"] is True
    assert request["lastMessagesPerMessageBoxCount"] == 5
    assert sync_reason == int(SyncReason.INITIALIZATION)


def test_get_message_boxes_overrides(api, last_request):
    """Keyword overrides map onto the wire field names."""
    api.get_message_boxes(
        min_chat_id=GROUP_MID,
        active_only=False,
        unread_only=True,
        limit=10,
        with_unread_count=False,
        last_messages_per_box=0,
        sync_reason=int(SyncReason.OPERATION),
    )
    request, sync_reason = last_request(api)
    assert request["minChatId"] == GROUP_MID
    assert request["activeOnly"] is False
    assert request["unreadOnly"] is True
    assert request["messageBoxCountLimit"] == 10
    assert request["withUnreadCount"] is False
    assert request["lastMessagesPerMessageBoxCount"] == 0
    assert sync_reason == int(SyncReason.OPERATION)


# ---------------------------------------------------------------------------
# get_message_boxes_by_ids
# ---------------------------------------------------------------------------
def test_get_message_boxes_by_ids_request_struct(api, last_request):
    """``getMessageBoxesByIds(request, syncReason)`` with an id list."""
    api.get_message_boxes_by_ids([GROUP_MID, ROOM_MID], last_messages_count=3)

    assert_method(api, "getMessageBoxesByIds")
    body = last_request(api)
    request, sync_reason = body
    assert request["messageBoxIds"] == [GROUP_MID, ROOM_MID]
    assert request["withUnreadCount"] is True
    assert request["lastMessagesCount"] == 3
    assert sync_reason == int(SyncReason.OPERATION)


def test_get_message_boxes_by_ids_materialises_iterable(api, last_request):
    """A generator argument is materialised into a concrete JSON list."""
    api.get_message_boxes_by_ids(iter([USER_MID]))
    assert last_request(api)[0]["messageBoxIds"] == [USER_MID]


# ---------------------------------------------------------------------------
# get_message_read_range
# ---------------------------------------------------------------------------
def test_get_message_read_range_body_shape(api, last_request):
    """``getMessageReadRange(chatIds, syncReason)``."""
    api.get_message_read_range([GROUP_MID, ROOM_MID])

    assert_method(api, "getMessageReadRange")
    body = last_request(api)
    assert body[0] == [GROUP_MID, ROOM_MID]
    assert body[1] == int(SyncReason.OPERATION)


def test_get_message_read_range_custom_sync_reason(api, last_request):
    api.get_message_read_range(iter([USER_MID]), sync_reason=int(SyncReason.FULL_SYNC))
    body = last_request(api)
    assert body[0] == [USER_MID]  # iterable materialised
    assert body[1] == int(SyncReason.FULL_SYNC)


# ---------------------------------------------------------------------------
# determine_media_message_flow
# ---------------------------------------------------------------------------
def test_determine_media_message_flow_body_shape(api, last_request):
    """``determineMediaMessageFlow({chatMid})``."""
    api.determine_media_message_flow(GROUP_MID)

    assert_method(api, "determineMediaMessageFlow")
    body = last_request(api)
    assert body == [{"chatMid": GROUP_MID}]


# ---------------------------------------------------------------------------
# get_last_op_revision
# ---------------------------------------------------------------------------
def test_get_last_op_revision_takes_no_args(api, last_request):
    """``getLastOpRevision()`` -> empty positional-args list."""
    api.get_last_op_revision()

    assert_method(api, "getLastOpRevision")
    assert last_request(api) == []


# ---------------------------------------------------------------------------
# Return-value unwrapping (the client returns the envelope's ``.data``)
# ---------------------------------------------------------------------------
def test_result_is_unwrapped_from_envelope(make_api):
    """Whatever the gateway returns under ``data`` is what the caller gets."""
    sentinel = {"revision": 4242}
    api = make_api(lambda m, u, kw: enveloped(sentinel))
    assert api.get_last_op_revision() == sentinel


def test_calls_are_recorded_in_history(api):
    """Each send is captured as an Exchange on ``api.history`` / ``api.last``."""
    api.send_text(USER_MID, "tracked")
    assert api.last is not None
    assert api.last.endpoint == "Talk.TalkService.sendMessage"
    assert api.history[-1] is api.last

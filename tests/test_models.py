"""Unit tests for :mod:`okline.models`.

These are pure, offline tests of the request-struct builders — ``mid_to_type``
and the :class:`Message` factory methods.  They assert on the exact dict shape
(``contentType`` + ``contentMetadata`` / ``location``) that each helper emits,
so future contributors can rely on the wire format staying stable.

No network and no Node.js are involved; we only construct plain dicts.
"""

from __future__ import annotations

import json

import pytest

from okline.enums import ContentType, MessageRelationType, MIDType
from okline.models import Message, mid_to_type, now_ms

# Reuse the canonical sample mids from conftest for readable assertions.
from conftest import GROUP_MID, ROOM_MID, USER_MID, USER_MID2

SQUARE_CHAT_MID = "s" + "3" * 32


# ---------------------------------------------------------------------------
# mid_to_type
# ---------------------------------------------------------------------------
class TestMidToType:
    """``mid_to_type`` maps a mid's leading char to its ``MIDType``."""

    def test_user_prefix(self):
        """A ``u…`` mid is a 1:1 user chat."""
        assert mid_to_type(USER_MID) == int(MIDType.USER) == 0

    def test_group_prefix(self):
        """A ``c…`` mid (chat/group) maps to GROUP."""
        assert mid_to_type(GROUP_MID) == int(MIDType.GROUP) == 2

    def test_room_prefix(self):
        """An ``r…`` mid maps to ROOM."""
        assert mid_to_type(ROOM_MID) == int(MIDType.ROOM) == 1

    def test_square_chat_prefix(self):
        """An ``s…`` mid maps to SQUARE_CHAT."""
        assert mid_to_type(SQUARE_CHAT_MID) == int(MIDType.SQUARE_CHAT) == 4

    def test_empty_mid_defaults_to_user(self):
        """An empty mid falls back to USER rather than raising."""
        assert mid_to_type("") == int(MIDType.USER)

    @pytest.mark.parametrize("mid", ["xabc", "1abc", "Uabc", "uabc"])
    def test_unknown_or_user_prefix_defaults_to_user(self, mid):
        """Anything that is not c/r/s defaults to USER."""
        assert mid_to_type(mid) == int(MIDType.USER)

    @pytest.mark.parametrize("mid,expected", [
        ("Cabc", MIDType.GROUP), ("cabc", MIDType.GROUP),
        ("Rabc", MIDType.ROOM), ("Sabc", MIDType.SQUARE_CHAT),
    ])
    def test_prefix_is_case_insensitive(self, mid, expected):
        """Modern mids are upper-case (U/C/R/S) — match either case."""
        assert mid_to_type(mid) == int(expected)

    def test_returns_plain_int(self):
        """The result is a bare ``int`` (JSON-serialisable), not an IntEnum."""
        assert type(mid_to_type(GROUP_MID)) is int


# ---------------------------------------------------------------------------
# Shared base fields
# ---------------------------------------------------------------------------
class TestBaseFields:
    """Every builder threads the ``_base`` fields onto the message."""

    def test_base_fields_present_on_every_builder(self):
        """``to``/``toType``/``contentMetadata``/``sessionId`` are always set."""
        builders = [
            Message.text(GROUP_MID, "hi"),
            Message.sticker(GROUP_MID, "1", "2"),
            Message.location(GROUP_MID, 1.0, 2.0),
            Message.contact(GROUP_MID, USER_MID2),
            Message.flex(GROUP_MID, "alt", {"type": "bubble"}),
            Message.media_ref(GROUP_MID, int(ContentType.IMAGE), "obj"),
        ]
        for msg in builders:
            assert msg["to"] == GROUP_MID
            assert msg["toType"] == int(MIDType.GROUP)
            assert msg["sessionId"] == 0
            assert isinstance(msg["contentMetadata"], dict)

    def test_totype_inferred_from_recipient(self):
        """``toType`` is derived from the recipient mid, per chat kind."""
        assert Message.text(USER_MID, "x")["toType"] == int(MIDType.USER)
        assert Message.text(ROOM_MID, "x")["toType"] == int(MIDType.ROOM)
        assert Message.text(GROUP_MID, "x")["toType"] == int(MIDType.GROUP)

    def test_extra_kwargs_are_passed_through(self):
        """Unknown kwargs land on the message verbatim (client parity)."""
        msg = Message.text(USER_MID, "hi", **{"from": "uME"})
        assert msg["from"] == "uME"


# ---------------------------------------------------------------------------
# Message.text
# ---------------------------------------------------------------------------
class TestText:
    """Plain text messages use ContentType.NONE with an empty metadata dict."""

    def test_basic_text(self):
        """A simple text message carries the text and NONE content type."""
        msg = Message.text(USER_MID, "hello world")
        assert msg["text"] == "hello world"
        assert msg["contentType"] == int(ContentType.NONE) == 0
        assert msg["contentMetadata"] == {}

    def test_custom_content_metadata(self):
        """Caller-supplied metadata replaces the empty default and is copied."""
        meta = {"EMTVER": "4"}
        msg = Message.text(USER_MID, "hi", content_metadata=meta)
        assert msg["contentMetadata"] == {"EMTVER": "4"}
        # A copy is stored, not the caller's object.
        assert msg["contentMetadata"] is not meta

    def test_empty_text_allowed(self):
        """An empty string is a valid text payload."""
        assert Message.text(USER_MID, "")["text"] == ""


# ---------------------------------------------------------------------------
# Reply / message relations
# ---------------------------------------------------------------------------
class TestReply:
    """``related_message_id`` turns a text message into a reply."""

    def test_reply_sets_relation_fields(self):
        """A reply records the target id, relation type and service code."""
        msg = Message.text(
            GROUP_MID,
            "re: that",
            related_message_id="1234567890",
            message_relation_type=int(MessageRelationType.REPLY),
        )
        assert msg["relatedMessageId"] == "1234567890"
        assert msg["messageRelationType"] == int(MessageRelationType.REPLY) == 3
        assert msg["relatedMessageServiceCode"] == 1

    def test_relation_type_defaults_to_none_when_unspecified(self):
        """Passing only the id leaves ``messageRelationType`` as ``None``."""
        msg = Message.text(GROUP_MID, "hi", related_message_id="42")
        assert msg["relatedMessageId"] == "42"
        assert msg["messageRelationType"] is None
        assert msg["relatedMessageServiceCode"] == 1

    def test_no_relation_fields_without_related_id(self):
        """A normal message has none of the relation keys."""
        msg = Message.text(GROUP_MID, "hi")
        assert "relatedMessageId" not in msg
        assert "messageRelationType" not in msg
        assert "relatedMessageServiceCode" not in msg

    def test_forward_relation_type(self):
        """Arbitrary relation types (e.g. FORWARD) are honoured."""
        msg = Message.text(
            GROUP_MID, "fwd",
            related_message_id="9",
            message_relation_type=int(MessageRelationType.FORWARD),
        )
        assert msg["messageRelationType"] == int(MessageRelationType.FORWARD) == 0


# ---------------------------------------------------------------------------
# Message.sticker
# ---------------------------------------------------------------------------
class TestSticker:
    """Stickers carry STKID/STKPKGID/STKVER (stringified) in metadata."""

    def test_basic_sticker(self):
        """Package/sticker/version land as strings in contentMetadata."""
        msg = Message.sticker(USER_MID, package_id="11537", sticker_id="52002734")
        assert msg["contentType"] == int(ContentType.STICKER) == 7
        assert msg["text"] == ""
        assert msg["contentMetadata"] == {
            "STKID": "52002734",
            "STKPKGID": "11537",
            "STKVER": "1",
        }

    def test_numeric_ids_are_stringified(self):
        """Integer ids are coerced to strings so the wire format is stable."""
        msg = Message.sticker(USER_MID, package_id=1, sticker_id=2, version=3)
        meta = msg["contentMetadata"]
        assert meta == {"STKID": "2", "STKPKGID": "1", "STKVER": "3"}
        assert all(isinstance(v, str) for v in meta.values())

    def test_sticker_text_adds_stktxt(self):
        """A non-empty ``sticker_text`` adds the optional STKTXT field."""
        msg = Message.sticker(USER_MID, "1", "2", sticker_text="hello")
        assert msg["contentMetadata"]["STKTXT"] == "hello"

    def test_no_stktxt_when_empty(self):
        """STKTXT is omitted when no sticker text is supplied."""
        msg = Message.sticker(USER_MID, "1", "2")
        assert "STKTXT" not in msg["contentMetadata"]


# ---------------------------------------------------------------------------
# Message.location
# ---------------------------------------------------------------------------
class TestLocation:
    """Location messages attach a ``location`` object (not metadata)."""

    def test_basic_location(self):
        """Lat/long and title/address populate the location dict."""
        msg = Message.location(
            USER_MID, latitude=35.659, longitude=139.700,
            title="Shibuya", address="Tokyo",
        )
        assert msg["contentType"] == int(ContentType.LOCATION) == 15
        assert msg["text"] == ""
        assert msg["location"] == {
            "title": "Shibuya",
            "address": "Tokyo",
            "latitude": 35.659,
            "longitude": 139.700,
            "phone": None,
        }

    def test_location_defaults(self):
        """Title/address default to empty strings; phone defaults to None."""
        msg = Message.location(USER_MID, 1.0, 2.0)
        loc = msg["location"]
        assert loc["title"] == ""
        assert loc["address"] == ""
        assert loc["phone"] is None
        assert loc["latitude"] == 1.0 and loc["longitude"] == 2.0

    def test_phone_passed_via_extra(self):
        """A ``phone`` kwarg is surfaced into the location dict."""
        msg = Message.location(USER_MID, 1.0, 2.0, phone="0312345678")
        assert msg["location"]["phone"] == "0312345678"

    def test_location_not_in_content_metadata(self):
        """The location lives at the top level, leaving metadata empty."""
        msg = Message.location(USER_MID, 1.0, 2.0)
        assert msg["contentMetadata"] == {}
        assert "location" in msg


# ---------------------------------------------------------------------------
# Message.contact
# ---------------------------------------------------------------------------
class TestContact:
    """Contact messages carry the shared mid + display name in metadata."""

    def test_basic_contact(self):
        """mid + displayName go into contentMetadata with CONTACT type."""
        msg = Message.contact(USER_MID, contact_mid=USER_MID2,
                              display_name="Friend")
        assert msg["contentType"] == int(ContentType.CONTACT) == 13
        assert msg["text"] == ""
        assert msg["contentMetadata"] == {
            "mid": USER_MID2,
            "displayName": "Friend",
        }

    def test_display_name_defaults_empty(self):
        """displayName defaults to an empty string when not provided."""
        msg = Message.contact(USER_MID, USER_MID2)
        assert msg["contentMetadata"]["displayName"] == ""
        assert msg["contentMetadata"]["mid"] == USER_MID2


# ---------------------------------------------------------------------------
# Message.flex
# ---------------------------------------------------------------------------
class TestFlex:
    """Flex messages serialise their bubble into FLEX_JSON + ALT_TEXT."""

    def test_basic_flex(self):
        """ALT_TEXT is verbatim; FLEX_JSON is the JSON-encoded contents."""
        contents = {"type": "bubble", "body": {"type": "box"}}
        msg = Message.flex(USER_MID, alt_text="see app", contents=contents)
        assert msg["contentType"] == int(ContentType.FLEX) == 22
        assert msg["text"] == ""
        assert msg["contentMetadata"]["ALT_TEXT"] == "see app"
        # FLEX_JSON round-trips back to the original structure.
        assert json.loads(msg["contentMetadata"]["FLEX_JSON"]) == contents

    def test_flex_json_preserves_unicode(self):
        """Non-ASCII content is kept literal (ensure_ascii=False)."""
        contents = {"type": "text", "text": "こんにちは"}
        msg = Message.flex(USER_MID, "あいさつ", contents)
        flex_json = msg["contentMetadata"]["FLEX_JSON"]
        assert "こんにちは" in flex_json
        assert "\\u" not in flex_json
        assert json.loads(flex_json) == contents


# ---------------------------------------------------------------------------
# Message.media_ref
# ---------------------------------------------------------------------------
class TestMediaRef:
    """media_ref builds a message for an already-uploaded OBS object."""

    def test_basic_media_ref(self):
        """The supplied content type is used and metadata starts empty."""
        msg = Message.media_ref(USER_MID, content_type=int(ContentType.IMAGE),
                               object_id="obs-123")
        assert msg["contentType"] == int(ContentType.IMAGE) == 1
        assert msg["text"] == ""
        assert msg["contentMetadata"] == {}

    def test_media_ref_with_metadata(self):
        """Caller-supplied metadata is copied onto the message."""
        meta = {"FILE_NAME": "doc.pdf", "FILE_SIZE": "1024"}
        msg = Message.media_ref(USER_MID, int(ContentType.FILE), "obs-9",
                               content_metadata=meta)
        assert msg["contentType"] == int(ContentType.FILE) == 14
        assert msg["contentMetadata"] == meta
        assert msg["contentMetadata"] is not meta  # copied, not aliased

    def test_media_ref_accepts_various_content_types(self):
        """Any media content type can be referenced (image/video/audio)."""
        for ct in (ContentType.IMAGE, ContentType.VIDEO, ContentType.AUDIO):
            msg = Message.media_ref(USER_MID, int(ct), "obj")
            assert msg["contentType"] == int(ct)


# ---------------------------------------------------------------------------
# now_ms
# ---------------------------------------------------------------------------
def test_now_ms_is_millisecond_epoch():
    """``now_ms`` returns an int that looks like a millisecond timestamp."""
    import time
    before = int(time.time() * 1000)
    value = now_ms()
    after = int(time.time() * 1000)
    assert isinstance(value, int)
    assert before <= value <= after
    # 13-digit range: comfortably after 2001 and before ~2286.
    assert value > 1_000_000_000_000

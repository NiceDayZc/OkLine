"""Offline tests for the contacts and chats/rooms service mixins.

These cover the public surface of ``okline/services/contacts.py`` and
``okline/services/chats.py`` as exposed on the :class:`OkLine` facade.

For every endpoint we assert three things where relevant:

* the **URL** the client POSTed to (``Namespace/Service/method`` path), and
* the **body** it serialised (the positional Thrift-args array), and
* that the client correctly **unwraps** the ``{"message":"OK","data":...}``
  envelope and returns the bare result.

reqSeq values are auto-generated, so we assert their *position/presence* and
that they are positive ints -- never an exact sequence number.
"""

from __future__ import annotations

import pytest
from conftest import (
    GROUP_MID,
    ROOM_MID,
    SAMPLE_CONTACT,
    USER_MID,
    USER_MID2,
    enveloped,
    route,
)

from okline.enums import (
    ChatType,
    ContactSetting,
    SyncReason,
    UpdateChatRequestAttribute,
)


# ---------------------------------------------------------------------------
# Small assertion helpers
# ---------------------------------------------------------------------------
def url_of(api):
    """The URL of the most recent request the client sent."""
    return api.transport.session.last["url"]


def headers_of(api):
    """The headers dict of the most recent request the client sent."""
    return api.transport.session.last["headers"]


def assert_endpoint(api, *path_parts):
    """Assert the last request URL ends with the given Service/method path."""
    suffix = "/".join(path_parts)
    assert url_of(api).endswith(suffix), f"{url_of(api)} !~ ...{suffix}"


# ===========================================================================
# contacts.py -- listing / lookup
# ===========================================================================
class TestContactListingLookup:
    def test_get_all_contact_ids_default_sync_reason(self, make_api, last_request):
        """getAllContactIds defaults to FULL_SYNC and returns the unwrapped list."""
        api = make_api(route({"getAllContactIds": [USER_MID, USER_MID2]}))
        result = api.get_all_contact_ids()

        assert result == [USER_MID, USER_MID2]
        assert_endpoint(api, "TalkService", "getAllContactIds")
        assert last_request(api) == [int(SyncReason.FULL_SYNC)]

    def test_get_all_contact_ids_custom_sync_reason(self, make_api, last_request):
        """A caller-supplied syncReason is forwarded verbatim."""
        api = make_api(route({"getAllContactIds": []}))
        api.get_all_contact_ids(int(SyncReason.OPERATION))

        assert last_request(api) == [int(SyncReason.OPERATION)]

    def test_get_contacts_request_shape(self, make_api, last_request):
        """get_contacts builds [{targetUserMids, neededContactCalendarEvents:[]}, syncReason]."""
        data = {"contacts": {USER_MID: {"contact": SAMPLE_CONTACT}}}
        api = make_api(route({"getContactsV2": data}))
        result = api.get_contacts([USER_MID, USER_MID2])

        assert result == data
        assert_endpoint(api, "TalkService", "getContactsV2")
        body = last_request(api)
        assert body[0] == {
            "targetUserMids": [USER_MID, USER_MID2],
            "neededContactCalendarEvents": [],
        }
        assert body[1] == int(SyncReason.FULL_SYNC)

    def test_get_contacts_accepts_any_iterable(self, make_api, last_request):
        """A generator of mids is materialised into a list in the request body."""
        api = make_api(route({"getContactsV2": {}}))
        api.get_contacts(m for m in (USER_MID, USER_MID2))

        assert last_request(api)[0]["targetUserMids"] == [USER_MID, USER_MID2]

    def test_get_contacts_chunks_over_the_limit(self, make_api):
        """>100 mids are split into <=100-mid requests and the maps merged."""
        api = make_api()
        sizes = []

        def fake_call(endpoint, args, **kw):
            mids = args[0]["targetUserMids"]
            sizes.append(len(mids))
            return {"contacts": {m: {"contact": {"mid": m}} for m in mids}}

        api.transport.call = fake_call
        res = api.get_contacts([f"U{i}" for i in range(250)])
        assert sizes == [100, 100, 50]
        assert len(res["contacts"]) == 250

    def test_find_contact_by_userid(self, make_api, last_request):
        """findContactByUserid sends the search id as the single positional arg."""
        api = make_api(route({"findContactByUserid": SAMPLE_CONTACT}))
        result = api.find_contact_by_userid("okline")

        assert result == SAMPLE_CONTACT
        assert_endpoint(api, "TalkService", "findContactByUserid")
        assert last_request(api) == ["okline"]

    def test_find_contacts_by_phone(self, make_api, last_request):
        """findContactsByPhone wraps the phone numbers in a list positional arg."""
        phones = ["+81 9012345678", "+66 812345678"]
        api = make_api(route({"findContactsByPhone": {"+81 9012345678": SAMPLE_CONTACT}}))
        api.find_contacts_by_phone(phones)

        assert_endpoint(api, "TalkService", "findContactsByPhone")
        assert last_request(api) == [phones]


# ===========================================================================
# contacts.py -- blocking
# ===========================================================================
class TestContactBlocking:
    def test_block_contact_autogenerates_req_seq(self, make_api, last_request):
        """blockContact -> [reqSeq, mid] with an auto-generated positive reqSeq."""
        api = make_api(route({"blockContact": {}}))
        api.block_contact(USER_MID2)

        assert_endpoint(api, "TalkService", "blockContact")
        body = last_request(api)
        assert isinstance(body[0], int) and body[0] > 0
        assert body[1] == USER_MID2

    def test_block_contact_explicit_req_seq(self, make_api, last_request):
        """An explicit req_seq overrides the auto-generated one."""
        api = make_api(route({"blockContact": {}}))
        api.block_contact(USER_MID2, req_seq=4242)

        assert last_request(api) == [4242, USER_MID2]

    def test_unblock_contact_default_reference(self, make_api, last_request):
        """unblockContact -> [reqSeq, mid, reference] with reference defaulting to ''."""
        api = make_api(route({"unblockContact": {}}))
        api.unblock_contact(USER_MID2)

        assert_endpoint(api, "TalkService", "unblockContact")
        body = last_request(api)
        assert isinstance(body[0], int) and body[0] > 0
        assert body[1] == USER_MID2
        assert body[2] == ""

    def test_unblock_contact_with_reference(self, make_api, last_request):
        """A caller-supplied reference is forwarded as the third positional arg."""
        api = make_api(route({"unblockContact": {}}))
        api.unblock_contact(USER_MID2, reference="square", req_seq=7)

        assert last_request(api) == [7, USER_MID2, "square"]


# ===========================================================================
# contacts.py -- settings / favourites
# ===========================================================================
class TestContactSettings:
    def test_update_contact_setting_arg_order(self, make_api, last_request):
        """updateContactSetting -> [reqSeq, mid, int(flag), str(value)]."""
        api = make_api(route({"updateContactSetting": {}}))
        api.update_contact_setting(USER_MID2, ContactSetting.CONTACT_SETTING_FAVORITE, "true")

        assert_endpoint(api, "TalkService", "updateContactSetting")
        body = last_request(api)
        assert isinstance(body[0], int) and body[0] > 0
        assert body[1] == USER_MID2
        assert body[2] == int(ContactSetting.CONTACT_SETTING_FAVORITE)
        assert body[3] == "true"

    def test_update_contact_setting_coerces_value_to_str(self, make_api, last_request):
        """The value argument is always stringified before transmission."""
        api = make_api(route({"updateContactSetting": {}}))
        api.update_contact_setting(USER_MID2, 8, 123, req_seq=1)

        body = last_request(api)
        assert body[3] == "123"
        assert isinstance(body[3], str)

    def test_set_favorite_true(self, make_api, last_request):
        """set_favorite(True) toggles the FAVORITE flag with value 'true'."""
        api = make_api(route({"updateContactSetting": {}}))
        api.set_favorite(USER_MID2, True)

        body = last_request(api)
        assert_endpoint(api, "TalkService", "updateContactSetting")
        assert body[1] == USER_MID2
        assert body[2] == int(ContactSetting.CONTACT_SETTING_FAVORITE)
        assert body[3] == "true"

    def test_set_favorite_false(self, make_api, last_request):
        """set_favorite(False) sends the value 'false'."""
        api = make_api(route({"updateContactSetting": {}}))
        api.set_favorite(USER_MID2, False)

        assert last_request(api)[3] == "false"

    def test_set_favorite_defaults_to_true(self, make_api, last_request):
        """Calling set_favorite with no flag favourites the contact."""
        api = make_api(route({"updateContactSetting": {}}))
        api.set_favorite(USER_MID2)

        assert last_request(api)[3] == "true"


# ===========================================================================
# contacts.py -- relations / buddy
# ===========================================================================
class TestRelationsBuddy:
    def test_add_friend_by_mid_default_tracking(self, make_api, last_request):
        """addFriendByMid wraps everything in one request struct with tracking meta."""
        api = make_api(route({"addFriendByMid": {}}))
        api.add_friend_by_mid(USER_MID2)

        assert_endpoint(api, "RelationService", "addFriendByMid")
        body = last_request(api)
        assert len(body) == 1
        req = body[0]
        assert isinstance(req["reqSeq"], int) and req["reqSeq"] > 0
        assert req["userMid"] == USER_MID2
        assert req["tracking"]["reference"] == ""
        # default path -> friendRecommendation tracking meta
        assert req["tracking"]["trackingMetaV2"] == {"friendRecommendation": {}}

    def test_add_friend_by_mid_from_chat(self, make_api, last_request):
        """A from_chat_mid switches the tracking meta to a chat reference."""
        api = make_api(route({"addFriendByMid": {}}))
        api.add_friend_by_mid(USER_MID2, from_chat_mid=GROUP_MID, req_seq=9)

        req = last_request(api)[0]
        assert req["reqSeq"] == 9
        assert req["tracking"]["trackingMetaV2"] == {"chat": {"chatMid": GROUP_MID}}

    def test_get_buddy_detail(self, make_api, last_request):
        """getBuddyDetail (BuddyService) sends the buddy mid as a positional arg."""
        api = make_api(route({"getBuddyDetail": {"mid": USER_MID, "displayName": "OA"}}))
        result = api.get_buddy_detail(USER_MID)

        assert result == {"mid": USER_MID, "displayName": "OA"}
        assert_endpoint(api, "BuddyService", "getBuddyDetail")
        assert last_request(api) == [USER_MID]


# ===========================================================================
# chats.py -- create / rename
# ===========================================================================
class TestChatCreateRename:
    def test_create_chat_request_shape(self, make_api, last_request):
        """createChat -> [{reqSeq, type, name, targetUserMids}]."""
        api = make_api(route({"createChat": {"chat": {"chatMid": GROUP_MID}}}))
        result = api.create_chat("My Group", [USER_MID, USER_MID2])

        assert result == {"chat": {"chatMid": GROUP_MID}}
        assert_endpoint(api, "TalkService", "createChat")
        body = last_request(api)
        assert len(body) == 1
        req = body[0]
        assert isinstance(req["reqSeq"], int) and req["reqSeq"] > 0
        assert req["type"] == int(ChatType.GROUP)
        assert req["name"] == "My Group"
        assert req["targetUserMids"] == [USER_MID, USER_MID2]

    def test_create_group_is_create_chat_alias(self, make_api):
        """create_group is the exact same callable as create_chat."""
        api = make_api(route({"createChat": {}}))
        assert api.create_group == api.create_chat

    def test_create_chat_none_name_becomes_empty_string(self, make_api, last_request):
        """A None name is normalised to '' so the wire payload stays a string."""
        api = make_api(route({"createChat": {}}))
        api.create_chat(None, [USER_MID])

        assert last_request(api)[0]["name"] == ""

    def test_create_chat_explicit_type_and_req_seq(self, make_api, last_request):
        """Caller-supplied chat_type / req_seq are forwarded unchanged."""
        api = make_api(route({"createChat": {}}))
        api.create_chat("Room", [USER_MID], chat_type=ChatType.ROOM, req_seq=11)

        req = last_request(api)[0]
        assert req["reqSeq"] == 11
        assert req["type"] == int(ChatType.ROOM)

    def test_rename_chat_uses_name_attribute(self, make_api, last_request):
        """rename_chat -> updateChat with updatedAttribute == NAME (1)."""
        api = make_api(route({"updateChat": {}}))
        api.rename_chat(GROUP_MID, "Renamed")

        assert_endpoint(api, "TalkService", "updateChat")
        req = last_request(api)[0]
        assert isinstance(req["reqSeq"], int) and req["reqSeq"] > 0
        assert req["updatedAttribute"] == int(UpdateChatRequestAttribute.NAME) == 1
        assert req["chat"]["chatMid"] == GROUP_MID
        assert req["chat"]["chatName"] == "Renamed"
        assert req["chat"]["type"] == int(ChatType.GROUP)


# ===========================================================================
# chats.py -- membership
# ===========================================================================
class TestChatMembership:
    def test_invite_into_chat(self, make_api, last_request):
        """inviteIntoChat -> [{reqSeq, chatMid, targetUserMids}]."""
        api = make_api(route({"inviteIntoChat": {}}))
        api.invite_into_chat(GROUP_MID, [USER_MID, USER_MID2])

        assert_endpoint(api, "TalkService", "inviteIntoChat")
        req = last_request(api)[0]
        assert isinstance(req["reqSeq"], int) and req["reqSeq"] > 0
        assert req["chatMid"] == GROUP_MID
        assert req["targetUserMids"] == [USER_MID, USER_MID2]

    def test_kick_from_chat_uses_delete_other_endpoint(self, make_api, last_request):
        """kick_from_chat routes to deleteOtherFromChat."""
        api = make_api(route({"deleteOtherFromChat": {}}))
        api.kick_from_chat(GROUP_MID, [USER_MID2])

        assert_endpoint(api, "TalkService", "deleteOtherFromChat")
        req = last_request(api)[0]
        assert req["chatMid"] == GROUP_MID
        assert req["targetUserMids"] == [USER_MID2]

    def test_leave_chat_uses_delete_self_endpoint(self, make_api, last_request):
        """leave_chat routes to deleteSelfFromChat with only reqSeq + chatMid."""
        api = make_api(route({"deleteSelfFromChat": {}}))
        api.leave_chat(GROUP_MID)

        assert_endpoint(api, "TalkService", "deleteSelfFromChat")
        req = last_request(api)[0]
        assert set(req) == {"reqSeq", "chatMid"}
        assert req["chatMid"] == GROUP_MID
        assert isinstance(req["reqSeq"], int) and req["reqSeq"] > 0

    def test_accept_chat_invitation(self, make_api, last_request):
        """acceptChatInvitation -> [{reqSeq, chatMid}]."""
        api = make_api(route({"acceptChatInvitation": {}}))
        api.accept_chat_invitation(GROUP_MID)

        assert_endpoint(api, "TalkService", "acceptChatInvitation")
        req = last_request(api)[0]
        assert set(req) == {"reqSeq", "chatMid"}
        assert req["chatMid"] == GROUP_MID

    def test_reject_chat_invitation(self, make_api, last_request):
        """rejectChatInvitation -> [{reqSeq, chatMid}]."""
        api = make_api(route({"rejectChatInvitation": {}}))
        api.reject_chat_invitation(GROUP_MID, req_seq=3)

        assert_endpoint(api, "TalkService", "rejectChatInvitation")
        assert last_request(api)[0] == {"reqSeq": 3, "chatMid": GROUP_MID}


# ===========================================================================
# chats.py -- listing
# ===========================================================================
class TestChatListing:
    def test_get_all_chat_mids_request_shape(self, make_api, last_request):
        """getAllChatMids -> [{withMemberChats, withInvitedChats}, syncReason]."""
        data = {"memberChatMids": [GROUP_MID], "invitedChatMids": []}
        api = make_api(route({"getAllChatMids": data}))
        result = api.get_all_chat_mids()

        assert result == data
        assert_endpoint(api, "TalkService", "getAllChatMids")
        body = last_request(api)
        assert body[0] == {"withMemberChats": True, "withInvitedChats": True}
        assert body[1] == int(SyncReason.FULL_SYNC)

    def test_get_all_chat_mids_flags_and_sync_reason(self, make_api, last_request):
        """The boolean filters and syncReason are all overridable."""
        api = make_api(route({"getAllChatMids": {}}))
        api.get_all_chat_mids(
            with_member_chats=False,
            with_invited_chats=False,
            sync_reason=int(SyncReason.OPERATION),
        )

        body = last_request(api)
        assert body[0] == {"withMemberChats": False, "withInvitedChats": False}
        assert body[1] == int(SyncReason.OPERATION)

    def test_get_chats_request_shape(self, make_api, last_request):
        """getChats -> [{chatMids, withMembers, withInvitees}]."""
        api = make_api(route({"getChats": {"chats": [{"chatMid": GROUP_MID}]}}))
        result = api.get_chats([GROUP_MID])

        assert result == {"chats": [{"chatMid": GROUP_MID}]}
        assert_endpoint(api, "TalkService", "getChats")
        body = last_request(api)
        assert len(body) == 2  # struct + syncReason (matches the real client)
        assert body[0] == {
            "chatMids": [GROUP_MID],
            "withMembers": True,
            "withInvitees": True,
        }
        assert body[1] == int(SyncReason.FULL_SYNC)

    def test_get_chats_flags_overridable(self, make_api, last_request):
        """withMembers / withInvitees default to True but can be turned off."""
        api = make_api(route({"getChats": {}}))
        api.get_chats([GROUP_MID], with_members=False, with_invitees=False)

        body = last_request(api)[0]
        assert body["withMembers"] is False
        assert body["withInvitees"] is False

    def test_get_chats_chunks_over_the_limit(self, make_api):
        """>100 chat mids are split into <=100-mid requests and merged.

        The gateway rejects a longer list with ``Invalid Length`` (code 6).
        """
        api = make_api()
        sizes = []

        def fake_call(endpoint, args, **kw):
            mids = args[0]["chatMids"]
            sizes.append(len(mids))
            return {"chats": [{"chatMid": m} for m in mids]}

        api.transport.call = fake_call
        res = api.get_chats([f"C{i}" for i in range(230)])
        assert sizes == [100, 100, 30]  # chunked at GET_CHATS_LIMIT
        assert len(res["chats"]) == 230  # merged back together

        sizes.clear()
        api.get_chats([f"C{i}" for i in range(50)])
        assert sizes == [50]  # <= limit -> a single request


# ===========================================================================
# chats.py -- legacy rooms
# ===========================================================================
class TestLegacyRooms:
    def test_invite_into_room_is_flat_positional(self, make_api, last_request):
        """inviteIntoRoom -> [reqSeq, roomMid, [mids]] (flat positional, no struct)."""
        api = make_api(route({"inviteIntoRoom": {}}))
        api.invite_into_room(ROOM_MID, [USER_MID, USER_MID2])

        assert_endpoint(api, "TalkService", "inviteIntoRoom")
        body = last_request(api)
        assert isinstance(body[0], int) and body[0] > 0
        assert body[1] == ROOM_MID
        assert body[2] == [USER_MID, USER_MID2]

    def test_invite_into_room_explicit_req_seq(self, make_api, last_request):
        """An explicit req_seq is honoured for inviteIntoRoom."""
        api = make_api(route({"inviteIntoRoom": {}}))
        api.invite_into_room(ROOM_MID, [USER_MID], req_seq=55)

        assert last_request(api) == [55, ROOM_MID, [USER_MID]]

    def test_leave_room_flat_positional(self, make_api, last_request):
        """leaveRoom -> [reqSeq, roomMid]."""
        api = make_api(route({"leaveRoom": {}}))
        api.leave_room(ROOM_MID)

        assert_endpoint(api, "TalkService", "leaveRoom")
        body = last_request(api)
        assert isinstance(body[0], int) and body[0] > 0
        assert body[1] == ROOM_MID

    def test_get_rooms_request_shape(self, make_api, last_request):
        """getRoomsV2 -> [[roomMids]] and returns the unwrapped room list."""
        rooms = [{"mid": ROOM_MID, "contacts": [SAMPLE_CONTACT]}]
        api = make_api(route({"getRoomsV2": rooms}))
        result = api.get_rooms([ROOM_MID])

        assert result == rooms
        assert_endpoint(api, "TalkService", "getRoomsV2")
        assert last_request(api) == [[ROOM_MID]]


# ===========================================================================
# Cross-cutting: envelope unwrap, headers, recording, error mapping
# ===========================================================================
class TestCrossCutting:
    def test_response_envelope_is_unwrapped(self, make_api):
        """The client returns ``.data`` from the OK envelope, not the wrapper."""
        api = make_api(route({"getAllContactIds": [USER_MID]}))
        # data was [USER_MID]; if the wrapper leaked we'd see a dict instead.
        assert api.get_all_contact_ids() == [USER_MID]

    def test_request_carries_chrome_headers(self, make_api):
        """Every Thrift call carries the CHROMEOS application + access headers."""
        api = make_api(route({"getAllContactIds": []}))
        api.get_all_contact_ids()

        headers = headers_of(api)
        assert headers["X-Line-Access"] == "TKN"
        assert headers["X-Line-Application"] == "CHROMEOS\t3.7.2\tChrome_OS\t"
        assert headers["X-Line-Chrome-Version"] == "3.7.2"

    def test_calls_are_recorded(self, make_api):
        """With record=True each call lands in history / api.last."""
        api = make_api(route({"getAllContactIds": [], "getChats": {}}))
        api.get_all_contact_ids()
        api.get_chats([GROUP_MID])

        assert len(api.history) == 2
        assert api.last.endpoint == "Talk.TalkService.getChats"
        assert api.last.ok is True

    def test_auto_req_seq_increments_across_calls(self, make_api, last_request):
        """Auto-generated reqSeq values are monotonically increasing."""
        api = make_api(route({"blockContact": {}}))
        api.block_contact(USER_MID)
        first = last_request(api)[0]
        api.block_contact(USER_MID2)
        second = last_request(api)[0]

        assert second == first + 1

    def test_non_ok_envelope_raises_line_api_error(self, make_api):
        """A non-OK envelope (HTTP 200) is surfaced as a LineApiError."""
        from okline.exceptions import LineApiError

        api = make_api(
            route(
                {
                    "getAllContactIds": enveloped(
                        {"code": 5, "message": "NOT_FOUND"}, message="NOT_FOUND"
                    )
                }
            )
        )
        with pytest.raises(LineApiError):
            api.get_all_contact_ids()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

"""Offline tests for :mod:`okline.enums`.

These tests pin down the *values* of representative members of each
enumeration (the numbers are part of LINE's wire protocol — changing them
silently would break the client), confirm that every enum is a real
``IntEnum`` (so members compare/serialize as plain ints), and verify the
convenience aliases ``ToType`` / ``ReactionType`` (and the deprecated
``MessageReactionType``) are exported.

No network and no Node.js are required — this module only imports
``okline.enums``.
"""

from __future__ import annotations

import json
from enum import IntEnum

import pytest

from okline import enums
from okline.enums import (
    AddFriendResult,
    ApplicationType,
    ConfigurationSyncParam,
    ContentType,
    E2EEMediaFlow,
    ErrorCode,
    IdentityProvider,
    LoginResultType,
    LoginType,
    MIDType,
    NameTextStatus,
    OpType,
    PaidReactionResourceType,
    PredefinedReactionType,
    ReactionType,
    StickerResourceType,
    ToType,
)


# ---------------------------------------------------------------------------
# Representative values (the wire-protocol numbers we depend on)
# ---------------------------------------------------------------------------
def test_login_type_values():
    """LoginType keys map to the bundle's numeric codes."""
    assert LoginType.ID_CREDENTIAL == 0
    assert LoginType.QRCODE == 1
    assert LoginType.ID_CREDENTIAL_WITH_E2EE == 2


def test_identity_provider_line_is_1():
    """IdentityProvider.LINE is the canonical provider id used at login."""
    assert IdentityProvider.UNKNOWN == 0
    assert IdentityProvider.LINE == 1
    assert IdentityProvider.NAVER_KR == 2
    assert IdentityProvider.LINE_PHONE == 3


def test_login_result_success_is_1():
    """A successful login is signalled by LoginResultType.SUCCESS == 1."""
    assert LoginResultType.SUCCESS == 1
    assert LoginResultType.REQUIRE_QRCODE == 2
    assert LoginResultType.REQUIRE_DEVICE_CONFIRM == 3
    assert LoginResultType.REQUIRE_SMS_CONFIRM == 4


def test_mid_type_group_is_2():
    """MIDType discriminates a mid by its leading character class."""
    assert MIDType.USER == 0
    assert MIDType.ROOM == 1
    assert MIDType.GROUP == 2
    assert MIDType.SQUARE == 3


def test_content_type_values():
    """ContentType covers the message content kinds we send/receive."""
    assert ContentType.NONE == 0
    assert ContentType.IMAGE == 1
    assert ContentType.STICKER == 7
    assert ContentType.FLEX == 22


def test_predefined_reaction_love_is_3():
    """PredefinedReactionType.LOVE is the default heart reaction."""
    assert PredefinedReactionType.NICE == 2
    assert PredefinedReactionType.LOVE == 3
    assert PredefinedReactionType.FUN == 4
    assert PredefinedReactionType.OMG == 7


def test_paid_reaction_resource_type_values():
    """PaidReactionResourceType is the paid-reaction union's resourceType.

    Verified verbatim against main.js @2072675 (var xU), adjacent to the
    NICE..OMG predefined-reaction enum (var bU).
    """
    assert PaidReactionResourceType.STATIC == 1
    assert PaidReactionResourceType.ANIMATION == 2
    assert len(PaidReactionResourceType) == 2


def test_sticker_resource_type_full_member_set():
    """StickerResourceType carries all 8 members from main.js @2052777 ($D)."""
    assert StickerResourceType.STATIC == 1
    assert StickerResourceType.ANIMATION == 2
    assert StickerResourceType.SOUND == 3
    assert StickerResourceType.ANIMATION_SOUND == 4
    assert StickerResourceType.POPUP == 5
    assert StickerResourceType.POPUP_SOUND == 6
    assert StickerResourceType.NAME_TEXT == 7
    assert StickerResourceType.PER_STICKER_TEXT == 8
    assert len(StickerResourceType) == 8


def test_name_text_status_values():
    """NameTextStatus matches main.js @2053127 (var ej), incl. members 4-5."""
    assert NameTextStatus.OK == 0
    assert NameTextStatus.PRODUCT_UNSUPPORTED == 1
    assert NameTextStatus.TEXT_NOT_SPECIFIED == 2
    assert NameTextStatus.TEXT_STYLE_UNAVAILABLE == 3
    assert NameTextStatus.CHARACTER_COUNT_LIMIT_EXCEEDED == 4
    assert NameTextStatus.CONTAINS_INVALID_WORD == 5
    # The former hand-guessed name is gone: lookups must hit the real one.
    assert not hasattr(NameTextStatus, "CHARACTER_COUNT_LIMIT_EXCEEDED_or_similar")
    assert NameTextStatus["CHARACTER_COUNT_LIMIT_EXCEEDED"] is (
        NameTextStatus.CHARACTER_COUNT_LIMIT_EXCEEDED
    )


def test_add_friend_result_values():
    """AddFriendResult models addFriendByMid codes (main.js @2052268, YD)."""
    assert AddFriendResult.UNKNOWN == 0
    assert AddFriendResult.INVALID_TARGET_USER == 1
    assert AddFriendResult.AGE_VALIDATION == 2
    assert AddFriendResult.TOO_MANY_FRIENDS == 3
    assert AddFriendResult.TOO_MANY_REQUESTS == 4
    assert AddFriendResult.MALFORMED_REQUEST == 5
    assert AddFriendResult.TRACKING_META_QRCODE_FAVORED == 6
    assert AddFriendResult.TRACKING_META_UPGRADE_FAVORED == 7
    assert len(AddFriendResult) == 8


def test_e2ee_media_flow_values():
    """E2EEMediaFlow V1/V2 back the media-message flow map (main.js @2088396)."""
    assert E2EEMediaFlow.V1 == 1
    assert E2EEMediaFlow.V2 == 2
    assert len(E2EEMediaFlow) == 2


def test_configuration_sync_param_values():
    """ConfigurationSyncParam decodes settings-op param3 (main.js @2086888)."""
    assert ConfigurationSyncParam.SYNC == 0
    assert ConfigurationSyncParam.REPORT == 1
    assert len(ConfigurationSyncParam) == 2


def test_application_type_chromeos_is_368():
    """CHROMEOS is the application-type this client identifies as."""
    assert ApplicationType.CHROMEOS == 368
    # Neighbouring variants keep their documented offsets.
    assert ApplicationType.CHROMEOS_RC == 369
    assert ApplicationType.CHROMEOS_BETA == 370
    assert ApplicationType.CHROMEOS_ALPHA == 371


def test_op_type_message_codes():
    """OpType.SEND_MESSAGE / RECEIVE_MESSAGE are 25 / 26 in the op stream."""
    assert OpType.END_OF_OPERATION == 0
    assert OpType.SEND_MESSAGE == 25
    assert OpType.RECEIVE_MESSAGE == 26


def test_error_code_must_upgrade_is_50():
    """ErrorCode.MUST_UPGRADE drives the 'please update' path."""
    assert ErrorCode.ILLEGAL_ARGUMENT == 0
    assert ErrorCode.AUTHENTICATION_FAILED == 1
    assert ErrorCode.MUST_UPGRADE == 50


# ---------------------------------------------------------------------------
# IntEnum-ness: members behave as plain ints
# ---------------------------------------------------------------------------
ALL_ENUMS = [
    LoginType,
    IdentityProvider,
    LoginResultType,
    MIDType,
    ContentType,
    PredefinedReactionType,
    PaidReactionResourceType,
    ApplicationType,
    OpType,
    ErrorCode,
    StickerResourceType,
    NameTextStatus,
    AddFriendResult,
    E2EEMediaFlow,
    ConfigurationSyncParam,
]


@pytest.mark.parametrize("enum_cls", ALL_ENUMS, ids=lambda c: c.__name__)
def test_enum_is_intenum_subclass(enum_cls):
    """Every public enum derives from IntEnum (and therefore from int)."""
    assert issubclass(enum_cls, IntEnum)
    assert issubclass(enum_cls, int)


def test_member_is_instance_of_int():
    """A member is itself an int instance and equals/hashes like its value."""
    assert isinstance(ContentType.STICKER, int)
    assert ContentType.STICKER == 7
    # Hash/equality interop with plain ints (e.g. dict lookups by raw value).
    assert {ContentType.STICKER: "s"}[7] == "s"


def test_member_arithmetic_and_indexing():
    """IntEnum members participate in arithmetic and as sequence indices."""
    assert OpType.RECEIVE_MESSAGE - OpType.SEND_MESSAGE == 1
    row = list(range(40))
    assert row[OpType.SEND_MESSAGE] == 25


def test_member_is_json_serialisable_as_number():
    """Because members are ints they serialise to bare JSON numbers."""
    assert json.dumps({"opType": OpType.SEND_MESSAGE}) == '{"opType": 25}'
    assert json.dumps([ApplicationType.CHROMEOS]) == "[368]"


# ---------------------------------------------------------------------------
# Lookup by value / by name round-trips
# ---------------------------------------------------------------------------
def test_lookup_by_value():
    """Constructing an enum from its int value returns the right member."""
    assert MIDType(2) is MIDType.GROUP
    assert ContentType(22) is ContentType.FLEX
    assert ErrorCode(50) is ErrorCode.MUST_UPGRADE


def test_lookup_by_name():
    """Members are reachable by name via subscription."""
    assert OpType["SEND_MESSAGE"] is OpType.SEND_MESSAGE
    assert ApplicationType["CHROMEOS"] is ApplicationType.CHROMEOS


def test_unknown_value_raises():
    """An int with no matching member raises ValueError."""
    with pytest.raises(ValueError):
        MIDType(999)


# ---------------------------------------------------------------------------
# Convenience aliases
# ---------------------------------------------------------------------------
def test_totype_alias_is_midtype():
    """ToType is exported as an alias of MIDType."""
    assert ToType is MIDType
    assert ToType.GROUP == 2


def test_reactiontype_alias_is_predefined_reaction_type():
    """ReactionType is exported as an alias of PredefinedReactionType."""
    assert ReactionType is PredefinedReactionType
    assert ReactionType.LOVE == 3


def test_aliases_present_on_module():
    """Both aliases are attributes of the enums module."""
    assert hasattr(enums, "ToType")
    assert hasattr(enums, "ReactionType")
    assert enums.ToType is enums.MIDType
    assert enums.ReactionType is enums.PredefinedReactionType


# ---------------------------------------------------------------------------
# Intra-enum consistency
# ---------------------------------------------------------------------------
def test_message_reaction_type_is_deprecated_alias():
    """MessageReactionType is a deprecated alias of PredefinedReactionType.

    The extension bundle has no such enum — its reaction wire struct is a
    union of {predefinedReactionType: 2-7} and {paidReactionType:
    {productId, emojiId, resourceType: 1|2}} — so the name is kept purely
    for backwards compatibility.
    """
    assert enums.MessageReactionType is PredefinedReactionType
    assert enums.MessageReactionType.LOVE == 3
    assert enums.MessageReactionType.OMG == 7


def test_enum_member_names_are_unique_values_where_expected():
    """ContentType has no accidental value collisions among its members."""
    values = [m.value for m in ContentType]
    assert len(values) == len(set(values))

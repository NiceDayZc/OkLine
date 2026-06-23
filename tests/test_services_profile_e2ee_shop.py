"""Offline tests for the Profile, E2EE and Channel/Shop service mixins.

Covers :mod:`okline.services.profile`, :mod:`okline.services.e2ee` and
:mod:`okline.services.channel_shop` exercised through the public
:class:`okline.OkLine` surface.

Everything is offline: the client talks to a :class:`FakeSession` (see
``tests/conftest.py``).  Each test drives one public method, then asserts on the
exact positional-argument array the client put on the wire (via
``last_request``), on the request URL/headers, and on any client-side state the
call mutates (e.g. ``tokens.mid`` / ``tokens.channel_access_token``).
"""

from __future__ import annotations

import pytest
from conftest import (
    GROUP_MID,
    SAMPLE_PROFILE,
    USER_MID,
    USER_MID2,
    enveloped,
    route,
)

from okline.enums import (
    ApplicationType,
    ProductType,
    ProfileAttribute,
    ReportSource,
    SettingsAttribute,
    SpammerReason,
    SyncReason,
)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
class TestProfile:
    def test_get_profile_sends_default_sync_reason(self, make_api, last_request):
        """``get_profile()`` posts ``[INITIALIZATION]`` to getProfile."""
        api = make_api(route({"getProfile": SAMPLE_PROFILE}))
        result = api.get_profile()
        assert result == SAMPLE_PROFILE
        assert last_request(api) == [int(SyncReason.INITIALIZATION)]
        # URL is the Talk gateway path.
        assert api.transport.session.last["url"].endswith(
            "/api/talk/thrift/Talk/TalkService/getProfile"
        )

    def test_get_profile_custom_sync_reason(self, make_api, last_request):
        """A caller-supplied sync_reason is forwarded verbatim."""
        api = make_api(route({"getProfile": SAMPLE_PROFILE}))
        api.get_profile(int(SyncReason.OPERATION))
        assert last_request(api) == [int(SyncReason.OPERATION)]

    def test_get_profile_adopts_mid_into_tokens(self, make_api):
        """A profile with a mid is recorded on the token store."""
        api = make_api(route({"getProfile": SAMPLE_PROFILE}))
        assert api.transport.tokens.mid is None
        api.get_profile()
        assert api.transport.tokens.mid == USER_MID

    def test_get_profile_without_mid_leaves_tokens_untouched(self, make_api):
        """A profile lacking a usable mid must not clobber tokens.mid."""
        api = make_api(route({"getProfile": {"displayName": "NoMid"}}))
        api.transport.tokens.mid = "u" + "9" * 32
        api.get_profile()
        assert api.transport.tokens.mid == "u" + "9" * 32

    def test_get_profile_non_dict_result_is_safe(self, make_api):
        """A non-dict result must not raise when adopting the mid."""
        api = make_api(route({"getProfile": None}))
        assert api.get_profile() is None
        assert api.transport.tokens.mid is None

    def test_update_profile_attributes_shapes_request(self, make_api, last_request):
        """updateProfileAttributes -> [seq, {profileAttributes:{'k':{value,meta}}}]."""
        api = make_api(route({"updateProfileAttributes": {}}))
        api.update_profile_attributes({int(ProfileAttribute.STATUS_MESSAGE): "yo"})
        seq, request = last_request(api)
        assert isinstance(seq, int)
        assert request == {"profileAttributes": {"16": {"value": "yo", "meta": {}}}}

    def test_update_profile_attributes_multiple_keys(self, make_api, last_request):
        """All attribute keys are stringified and carried through."""
        api = make_api(route({"updateProfileAttributes": {}}))
        api.update_profile_attributes(
            {
                int(ProfileAttribute.DISPLAY_NAME): "Name",
                int(ProfileAttribute.STATUS_MESSAGE): "Status",
            }
        )
        _seq, request = last_request(api)
        attrs = request["profileAttributes"]
        assert attrs["2"] == {"value": "Name", "meta": {}}
        assert attrs["16"] == {"value": "Status", "meta": {}}

    def test_update_profile_attributes_explicit_seq(self, make_api, last_request):
        """An explicit req_seq is used instead of the auto-generated one."""
        api = make_api(route({"updateProfileAttributes": {}}))
        api.update_profile_attributes({int(ProfileAttribute.DISPLAY_NAME): "X"}, req_seq=4242)
        seq, _request = last_request(api)
        assert seq == 4242

    def test_set_display_name_uses_attribute_2(self, make_api, last_request):
        """set_display_name maps to ProfileAttribute.DISPLAY_NAME ('2')."""
        api = make_api(route({"updateProfileAttributes": {}}))
        api.set_display_name("Alice")
        _seq, request = last_request(api)
        assert request == {"profileAttributes": {"2": {"value": "Alice", "meta": {}}}}

    def test_set_status_message_uses_attribute_16(self, make_api, last_request):
        """set_status_message maps to ProfileAttribute.STATUS_MESSAGE ('16')."""
        api = make_api(route({"updateProfileAttributes": {}}))
        api.set_status_message("busy")
        _seq, request = last_request(api)
        assert request == {"profileAttributes": {"16": {"value": "busy", "meta": {}}}}


# ---------------------------------------------------------------------------
# Settings / configuration / time
# ---------------------------------------------------------------------------
class TestSettings:
    def test_get_settings_default_sync_reason(self, make_api, last_request):
        """get_settings posts the INITIALIZATION sync reason by default."""
        api = make_api(route({"getSettings": {"notificationEnable": True}}))
        result = api.get_settings()
        assert result == {"notificationEnable": True}
        assert last_request(api) == [int(SyncReason.INITIALIZATION)]

    def test_get_settings_custom_sync_reason(self, make_api, last_request):
        api = make_api(route({"getSettings": {}}))
        api.get_settings(int(SyncReason.FULL_SYNC))
        assert last_request(api) == [int(SyncReason.FULL_SYNC)]

    def test_get_settings_attributes2_wraps_ids_in_list(self, make_api, last_request):
        """getSettingsAttributes2 -> [[id, id, ...]] (a list wrapped in a list)."""
        api = make_api(route({"getSettingsAttributes2": {}}))
        api.get_settings_attributes2(
            [
                SettingsAttribute.NOTIFICATION_ENABLE,
                SettingsAttribute.E2EE_ENABLE,
            ]
        )
        assert last_request(api) == [[0, 33]]

    def test_get_settings_attributes2_coerces_to_int(self, make_api, last_request):
        """Enum members and plain ints both serialise as ints."""
        api = make_api(route({"getSettingsAttributes2": {}}))
        api.get_settings_attributes2([SettingsAttribute.PREFERENCE_LOCALE, 17])
        assert last_request(api) == [[15, 17]]

    def test_update_settings_attributes2_shape(self, make_api, last_request):
        """updateSettingsAttributes2 -> [seq, [ids], settings]."""
        api = make_api(route({"updateSettingsAttributes2": {}}))
        api.update_settings_attributes2(
            [SettingsAttribute.NOTIFICATION_ENABLE], {"notificationEnable": "true"}
        )
        seq, ids, settings = last_request(api)
        assert isinstance(seq, int)
        assert ids == [0]
        assert settings == {"notificationEnable": "true"}

    def test_update_settings_attributes2_explicit_seq(self, make_api, last_request):
        api = make_api(route({"updateSettingsAttributes2": {}}))
        api.update_settings_attributes2(
            [SettingsAttribute.E2EE_ENABLE], {"e2eeEnable": "true"}, req_seq=77
        )
        seq, ids, settings = last_request(api)
        assert seq == 77
        assert ids == [33]
        assert settings == {"e2eeEnable": "true"}

    def test_get_configurations_default_region(self, make_api, last_request):
        """getConfigurations -> ['', '', '', region, '', syncReason]."""
        api = make_api(route({"getConfigurations": {}}))
        api.get_configurations()
        assert last_request(api) == ["", "", "", "", "", int(SyncReason.INITIALIZATION)]

    def test_get_configurations_custom_region_and_reason(self, make_api, last_request):
        """The region lands in slot 3 and the sync reason in the last slot."""
        api = make_api(route({"getConfigurations": {}}))
        api.get_configurations("TH", int(SyncReason.OPERATION))
        args = last_request(api)
        assert args == ["", "", "", "TH", "", int(SyncReason.OPERATION)]
        assert args[3] == "TH"

    def test_get_server_time_sends_empty_args(self, make_api, last_request):
        """getServerTime takes no positional arguments."""
        api = make_api(route({"getServerTime": 1700000000000}))
        result = api.get_server_time()
        assert result == 1700000000000
        assert last_request(api) == []


# ---------------------------------------------------------------------------
# Abuse reporting
# ---------------------------------------------------------------------------
class TestReportAbuse:
    def test_report_abuse_builds_nested_message(self, make_api, last_request):
        """reportAbuseEx -> [{abuseReportEntry:{message:{...}}}]."""
        api = make_api(route({"reportAbuseEx": {}}))
        api.report_abuse(
            report_source=int(ReportSource.GROUP_CHAT),
            spammer_reason=int(SpammerReason.SCAM),
            metadata={"groupMid": GROUP_MID},
        )
        (entry,) = last_request(api)
        message = entry["abuseReportEntry"]["message"]
        assert message["reportSource"] == int(ReportSource.GROUP_CHAT)
        assert message["spammerReasons"] == [int(SpammerReason.SCAM)]
        assert message["metadata"] == {"groupMid": GROUP_MID}
        assert message["abuseMessages"] == []

    def test_report_abuse_default_application_type_is_chromeos(self, make_api, last_request):
        """The default applicationType is CHROMEOS (368)."""
        api = make_api(route({"reportAbuseEx": {}}))
        api.report_abuse(
            report_source=int(ReportSource.DIRECT_CHAT),
            spammer_reason=int(SpammerReason.ADVERTISING),
            metadata={},
        )
        (entry,) = last_request(api)
        message = entry["abuseReportEntry"]["message"]
        assert message["applicationType"] == int(ApplicationType.CHROMEOS)

    def test_report_abuse_custom_application_type_and_messages(self, make_api, last_request):
        """Custom application_type and abuse_messages are forwarded."""
        api = make_api(route({"reportAbuseEx": {}}))
        api.report_abuse(
            report_source=int(ReportSource.DIRECT_CHAT_SELECTED),
            spammer_reason=int(SpammerReason.HARASSMENT),
            metadata={"k": "v"},
            abuse_messages=[{"id": "1"}],
            application_type=int(ApplicationType.ANDROID),
        )
        (entry,) = last_request(api)
        message = entry["abuseReportEntry"]["message"]
        assert message["applicationType"] == int(ApplicationType.ANDROID)
        assert message["abuseMessages"] == [{"id": "1"}]


# ---------------------------------------------------------------------------
# E2EE
# ---------------------------------------------------------------------------
class TestE2EE:
    def test_get_e2ee_public_key_default_version_and_key_id(self, make_api, last_request):
        """getE2EEPublicKey -> [mid, keyVersion, keyId] with defaults 1 / 0."""
        api = make_api(route({"getE2EEPublicKey": {"keyId": 5}}))
        result = api.get_e2ee_public_key(USER_MID)
        assert result == {"keyId": 5}
        assert last_request(api) == [USER_MID, 1, 0]

    def test_get_e2ee_public_key_custom_version_and_key_id(self, make_api, last_request):
        api = make_api(route({"getE2EEPublicKey": {}}))
        api.get_e2ee_public_key(USER_MID2, key_version=2, key_id=9)
        assert last_request(api) == [USER_MID2, 2, 9]

    def test_negotiate_e2ee_public_key_sends_only_mid(self, make_api, last_request):
        """negotiateE2EEPublicKey -> [mid]."""
        api = make_api(route({"negotiateE2EEPublicKey": {"specVersion": 2}}))
        result = api.negotiate_e2ee_public_key(USER_MID)
        assert result == {"specVersion": 2}
        assert last_request(api) == [USER_MID]

    def test_register_e2ee_group_key_argument_order(self, make_api, last_request):
        """registerE2EEGroupKey -> [version, chatMid, members, keyIds, encKeys]."""
        api = make_api(route({"registerE2EEGroupKey": {}}))
        api.register_e2ee_group_key(
            GROUP_MID,
            members=[USER_MID, USER_MID2],
            key_ids=[1, 2],
            encrypted_shared_keys=["encA", "encB"],
        )
        version, chat_mid, members, key_ids, enc_keys = last_request(api)
        assert version == 1
        assert chat_mid == GROUP_MID
        assert members == [USER_MID, USER_MID2]
        assert key_ids == [1, 2]
        assert enc_keys == ["encA", "encB"]

    def test_register_e2ee_group_key_coerces_iterables(self, make_api, last_request):
        """Generators / tuples become lists and key ids become ints."""
        api = make_api(route({"registerE2EEGroupKey": {}}))
        api.register_e2ee_group_key(
            GROUP_MID,
            members=(m for m in [USER_MID]),
            key_ids=(k for k in ["3", "4"]),
            encrypted_shared_keys=(e for e in ["enc"]),
            version=2,
        )
        version, _chat_mid, members, key_ids, enc_keys = last_request(api)
        assert version == 2
        assert members == [USER_MID]
        assert key_ids == [3, 4]
        assert enc_keys == ["enc"]


# ---------------------------------------------------------------------------
# Channel token
# ---------------------------------------------------------------------------
class TestChannelToken:
    def test_issue_channel_token_default_channel(self, make_api, last_request):
        """issueChannelToken posts the hard-coded Timeline channel id."""
        api = make_api(route({"issueChannelToken": {"channelAccessToken": "CHTOK"}}))
        result = api.issue_channel_token()
        assert result == {"channelAccessToken": "CHTOK"}
        assert last_request(api) == ["1341209850"]

    def test_issue_channel_token_custom_channel(self, make_api, last_request):
        api = make_api(route({"issueChannelToken": {"channelAccessToken": "CHTOK"}}))
        api.issue_channel_token("999")
        assert last_request(api) == ["999"]

    def test_issue_channel_token_caches_access_token(self, make_api):
        """The returned channelAccessToken is cached on the token store."""
        api = make_api(route({"issueChannelToken": {"channelAccessToken": "CACHED"}}))
        assert api.transport.tokens.channel_access_token is None
        api.issue_channel_token()
        assert api.transport.tokens.channel_access_token == "CACHED"

    def test_issue_channel_token_caches_legacy_token_field(self, make_api):
        """A legacy ``token`` field is also recognised and cached."""
        api = make_api(route({"issueChannelToken": {"token": "LEGACY"}}))
        api.issue_channel_token()
        assert api.transport.tokens.channel_access_token == "LEGACY"

    def test_cached_channel_token_sent_as_header_on_next_call(self, make_api):
        """Once cached, subsequent requests carry X-Line-ChannelToken."""
        api = make_api(
            route(
                {
                    "issueChannelToken": {"channelAccessToken": "CHTOK"},
                    "getServerTime": 1,
                }
            )
        )
        api.issue_channel_token()
        api.get_server_time()
        assert api.transport.session.last["headers"]["X-Line-ChannelToken"] == "CHTOK"

    def test_issue_channel_token_without_token_leaves_cache_empty(self, make_api):
        """A response without a usable token must not set the cache."""
        api = make_api(route({"issueChannelToken": {"other": "x"}}))
        api.issue_channel_token()
        assert api.transport.tokens.channel_access_token is None


# ---------------------------------------------------------------------------
# Sticker / Sticon shop
# ---------------------------------------------------------------------------
class TestShop:
    def test_get_owned_product_summaries_defaults(self, make_api, last_request):
        """getOwnedProductSummaries -> [shopId, offset, limit, {language,country}]."""
        api = make_api(
            route({"getOwnedProductSummaries": {"productList": [], "totalSize": 0}})
        )
        result = api.get_owned_product_summaries()
        assert result == {"productList": [], "totalSize": 0}
        shop_id, offset, limit, display = last_request(api)
        assert shop_id == "stickershop"
        assert offset == 0
        assert limit == 1000
        assert display == {"language": "en", "country": "JP"}

    def test_get_owned_product_summaries_uses_shop_thrift_path(self, make_api):
        """Shop endpoints live under the /api/shop/thrift prefix, not talk."""
        api = make_api(route({"getOwnedProductSummaries": {}}))
        api.get_owned_product_summaries()
        assert api.transport.session.last["url"].endswith(
            "/api/shop/thrift/ShopService/ShopService/getOwnedProductSummaries"
        )

    def test_get_owned_product_summaries_custom_args(self, make_api, last_request):
        api = make_api(route({"getOwnedProductSummaries": {}}))
        api.get_owned_product_summaries(
            "sticonshop", offset=10, limit=50, language="th", country="TH"
        )
        shop_id, offset, limit, display = last_request(api)
        assert shop_id == "sticonshop"
        assert offset == 10
        assert limit == 50
        assert display == {"language": "th", "country": "TH"}

    def test_iter_owned_products_pages_then_stops(self, make_api):
        """iter_owned_products yields each product and stops at totalSize."""
        pages = iter(
            [
                {"productList": [{"id": "a"}, {"id": "b"}], "totalSize": 3},
                {"productList": [{"id": "c"}], "totalSize": 3},
            ]
        )

        def responder(method, url, kw):
            return enveloped(next(pages))

        api = make_api(responder)
        products = list(api.iter_owned_products())
        assert [p["id"] for p in products] == ["a", "b", "c"]

    def test_iter_owned_products_empty_first_page(self, make_api):
        """An empty first page terminates iteration immediately."""
        api = make_api(
            route({"getOwnedProductSummaries": {"productList": [], "totalSize": 0}})
        )
        assert list(api.iter_owned_products()) == []

    def test_preview_customized_image_text_shape(self, make_api, last_request):
        """previewCustomizedImageText -> [{productType, productId, nameRequestEntry}]."""
        api = make_api(route({"previewCustomizedImageText": {}}))
        api.preview_customized_image_text("SP1", "Hi")
        (request,) = last_request(api)
        assert request == {
            "productType": int(ProductType.STICKER),
            "productId": "SP1",
            "nameRequestEntry": {"text": "Hi"},
        }

    def test_set_customized_image_text_shape(self, make_api, last_request):
        """setCustomizedImageText mirrors the preview request shape."""
        api = make_api(route({"setCustomizedImageText": {}}))
        api.set_customized_image_text("SP2", "Bye", product_type=int(ProductType.STICON))
        (request,) = last_request(api)
        assert request == {
            "productType": int(ProductType.STICON),
            "productId": "SP2",
            "nameRequestEntry": {"text": "Bye"},
        }

    def test_customized_image_text_coerces_product_id_to_str(self, make_api, last_request):
        """A non-string product id is stringified before sending."""
        api = make_api(route({"previewCustomizedImageText": {}}))
        api.preview_customized_image_text(12345, "Name")
        (request,) = last_request(api)
        assert request["productId"] == "12345"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

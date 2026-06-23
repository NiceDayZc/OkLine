"""Tests for the read-only live self-test harness (run offline against mocks)."""

from __future__ import annotations

from conftest import GROUP_MID, USER_MID2, enveloped, route

from okline.selftest import CheckResult, print_results, run_selftest

# A canned server that answers every endpoint the self-test touches.
_OK_TABLE = {
    "getProfile": {"mid": "uME", "regionCode": "TH", "displayName": "Me"},
    "getAllContactIds": [USER_MID2],
    "getAllChatMids": {"memberChatMids": [GROUP_MID], "invitedChatMids": []},
    "getServerTime": 1782139257889,
    "getSettings": {"e2eeEnable": True},
    "getSettingsAttributes2": {"e2eeEnable": True},
    "getConfigurations": {"revision": 1},
    "getFavoriteMids": [],
    "getBlockedContactIds": [],
    "getRecommendationIds": [],
    "getBlockedRecommendationIds": [],
    "getLastOpRevision": "12345",
    "getE2EEPublicKeysEx": [{"keyId": 1}],
    "getMessageBoxes": {"messageBoxes": [], "hasNext": False},
    "issueChannelToken": {"channelAccessToken": "ct", "expiration": "9999999999"},
    "getContactsV2": {"contacts": {}},
    "getTargetProfileNotice": {"notice": {}},
    "getChats": {"chats": []},
    "getRecentMessagesV2": [],
    "getMessageBoxesByIds": {"messageBoxesByIds": {}},
    "getMessageReadRange": [],
}


def test_selftest_all_ok(make_api):
    api = make_api(route(_OK_TABLE))
    results = run_selftest(api)
    assert results and all(isinstance(r, CheckResult) for r in results)
    assert all(r.ok for r in results)
    # the discovery endpoints must be present
    names = {r.endpoint for r in results}
    assert "Talk.TalkService.getProfile" in names
    assert "Talk.TalkService.getContactsV2" in names  # needed a contact
    assert "Talk.TalkService.getChats" in names  # needed a chat


def test_selftest_reports_failures(make_api):
    # getServerTime returns a non-OK envelope -> should be marked failed, others OK
    table = dict(_OK_TABLE)
    table["getServerTime"] = enveloped(None, message="INTERNAL_ERROR", status=500)
    api = make_api(route(table))
    results = run_selftest(api)
    failed = [r for r in results if not r.ok]
    assert any(r.endpoint == "Talk.TalkService.getServerTime" for r in failed)
    assert len(failed) == 1


def test_selftest_skips_contact_chat_checks_when_empty(make_api):
    table = dict(_OK_TABLE)
    table["getAllContactIds"] = []
    table["getAllChatMids"] = {"memberChatMids": [], "invitedChatMids": []}
    api = make_api(route(table))
    names = {r.endpoint for r in run_selftest(api)}
    assert "Talk.TalkService.getContactsV2" not in names
    assert "Talk.TalkService.getChats" not in names


def test_print_results_counts(make_api, capsys):
    api = make_api(route(_OK_TABLE))
    fails = print_results(run_selftest(api))
    out = capsys.readouterr().out
    assert "endpoints OK" in out
    assert fails == 0


def test_cli_has_selftest_command():
    from okline.__main__ import build_parser

    args = build_parser().parse_args(["selftest", "--verbose"])
    assert args.command == "selftest" and args.verbose is True

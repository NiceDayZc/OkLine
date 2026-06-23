"""Live self-test — exercise the read-only endpoints against the real server.

This turns "the endpoints should work" into "the endpoints DID work": given a
valid session it calls every *safe, read-only* endpoint with sensible arguments
(discovering your own mid / first contact / first chat as it goes) and reports a
pass/fail table.

State-changing endpoints (sendMessage, createChat, block, react, leave, report,
logout, …) are **not** run by default — they would modify your account / message
people.  Pass ``include_writes=True`` only if you really want that and supply a
target via the relevant kwargs.

Usage::

    from okline import OkLine
    from okline.selftest import run_selftest, print_results
    api = OkLine(access_token="...", refresh_token="...")
    print_results(run_selftest(api))

or from the CLI::

    python -m okline selftest --token "$TOKEN"
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class CheckResult:
    endpoint: str
    ok: bool
    status: int | None
    ms: float
    detail: str


def _summary(value: Any) -> str:
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        keys = list(value)[:4]
        return "{" + ", ".join(keys) + ("…" if len(value) > 4 else "") + "}"
    s = str(value)
    return s[:60]


def run_selftest(api, *, verbose: bool = False) -> list[CheckResult]:
    """Run the read-only checks and return a list of :class:`CheckResult`."""
    results: list[CheckResult] = []

    def check(endpoint: str, fn: Callable[[], Any]) -> Any:
        t = time.monotonic()
        try:
            value = fn()
            ex = getattr(api, "last", None)
            results.append(
                CheckResult(
                    endpoint,
                    True,
                    getattr(ex, "status", 200),
                    (time.monotonic() - t) * 1000.0,
                    _summary(value),
                )
            )
            if verbose:
                print(f"  OK  {endpoint}")
            return value
        except Exception as exc:
            ex = getattr(api, "last", None)
            results.append(
                CheckResult(
                    endpoint,
                    False,
                    getattr(ex, "status", None),
                    (time.monotonic() - t) * 1000.0,
                    str(exc)[:100],
                )
            )
            if verbose:
                print(f"  ERR {endpoint}: {exc}")
            return None

    # --- identity / discovery ------------------------------------------------
    profile = check("Talk.TalkService.getProfile", lambda: api.get_profile())
    region = profile.get("regionCode", "") if isinstance(profile, dict) else ""

    contact_ids = check("Talk.TalkService.getAllContactIds", lambda: api.get_all_contact_ids())
    first_contact = contact_ids[0] if isinstance(contact_ids, list) and contact_ids else None

    chat_mids = check("Talk.TalkService.getAllChatMids", lambda: api.get_all_chat_mids())
    member_chats = chat_mids.get("memberChatMids") if isinstance(chat_mids, dict) else None
    first_chat = member_chats[0] if isinstance(member_chats, list) and member_chats else None

    # --- account-wide reads --------------------------------------------------
    check("Talk.TalkService.getServerTime", lambda: api.get_server_time())
    check("Talk.TalkService.getSettings", lambda: api.get_settings())
    check(
        "Talk.TalkService.getSettingsAttributes2",
        lambda: api.get_settings_attributes2([16, 33, 25, 60, 61]),
    )
    check("Talk.TalkService.getConfigurations", lambda: api.get_configurations(region=region))
    check("Talk.TalkService.getFavoriteMids", lambda: api.get_favorite_mids())
    check("Talk.TalkService.getBlockedContactIds", lambda: api.get_blocked_contact_ids())
    check("Talk.TalkService.getRecommendationIds", lambda: api.get_recommendation_ids())
    check(
        "Talk.TalkService.getBlockedRecommendationIds",
        lambda: api.get_blocked_recommendation_ids(),
    )
    check("Talk.TalkService.getLastOpRevision", lambda: api.get_last_op_revision())
    check("Talk.TalkService.getE2EEPublicKeysEx", lambda: api.get_e2ee_public_keys_ex())
    check("Talk.TalkService.getMessageBoxes", lambda: api.get_message_boxes(limit=5))
    check("Talk.ChannelService.issueChannelToken", lambda: api.issue_channel_token())

    # --- reads that need a contact ------------------------------------------
    if first_contact:
        check("Talk.TalkService.getContactsV2", lambda: api.get_contacts([first_contact]))
        check(
            "Relation.RelationService.getTargetProfileNotice",
            lambda: api.get_target_profile_notice(first_contact),
        )

    # --- reads that need a chat ---------------------------------------------
    if first_chat:
        check("Talk.TalkService.getChats", lambda: api.get_chats([first_chat]))
        check(
            "Talk.TalkService.getRecentMessagesV2",
            lambda: api.get_recent_messages(first_chat, 5),
        )
        check(
            "Talk.TalkService.getMessageBoxesByIds",
            lambda: api.get_message_boxes_by_ids([first_chat]),
        )
        check(
            "Talk.TalkService.getMessageReadRange",
            lambda: api.get_message_read_range([first_chat]),
        )

    return results


def print_results(results: list[CheckResult]) -> int:
    """Pretty-print a results table; return the number of failures."""
    width = max((len(r.endpoint) for r in results), default=40)
    ok = sum(1 for r in results if r.ok)
    print(f"\nOkLine self-test - {ok}/{len(results)} endpoints OK\n")
    for r in results:
        mark = "OK " if r.ok else "ERR"
        status = r.status if r.status is not None else "-"
        print(f"  [{mark}] {r.endpoint:<{width}}  {status!s:>3}  {r.ms:6.0f}ms  {r.detail}")
    fails = [r for r in results if not r.ok]
    if fails:
        print(f"\n{len(fails)} failed:")
        for r in fails:
            print(f"  - {r.endpoint}: {r.detail}")
    else:
        print("\nAll read-only endpoints responded successfully. [PASS]")
    return len(fails)

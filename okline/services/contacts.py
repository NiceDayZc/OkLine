"""Contacts, relations, recommendations and buddy endpoints."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..enums import ContactSetting, ContactType, SyncReason
from ._base import ServiceMixin


class ContactsMixin(ServiceMixin):
    # -- listing / lookup ----------------------------------------------------
    def get_all_contact_ids(self, sync_reason: int = int(SyncReason.FULL_SYNC)) -> Any:
        """``getAllContactIds(syncReason)`` -> list of contact mids."""
        return self.transport.call("Talk.TalkService.getAllContactIds", [sync_reason])

    #: ``getContactsV2`` rejects more than this many mids per call (``Invalid
    #: Length``), so requests are chunked and the ``contacts`` maps merged.
    GET_CONTACTS_LIMIT = 100

    def get_contacts(
        self, mids: Iterable[str], sync_reason: int = int(SyncReason.FULL_SYNC)
    ) -> Any:
        """``getContactsV2(request, syncReason)``.

        Returns ``{contacts: {mid: {contact: Contact}}}``.  Automatically chunked
        at :attr:`GET_CONTACTS_LIMIT` mids per request, so you can pass any number
        of mids.
        """
        all_mids = list(mids)

        def _call(batch):
            return self.transport.call(
                "Talk.TalkService.getContactsV2",
                [
                    {
                        "targetUserMids": batch,
                        "neededContactCalendarEvents": [],
                    },
                    sync_reason,
                ],
            )

        if len(all_mids) <= self.GET_CONTACTS_LIMIT:
            return _call(all_mids)
        contacts: dict = {}
        for i in range(0, len(all_mids), self.GET_CONTACTS_LIMIT):
            res = _call(all_mids[i : i + self.GET_CONTACTS_LIMIT])
            if isinstance(res, dict):
                contacts.update(res.get("contacts", {}) or {})
        return {"contacts": contacts}

    def find_contact_by_userid(self, user_id: str) -> Any:
        """``findContactByUserid(searchId)`` -> single Contact."""
        return self.transport.call("Talk.TalkService.findContactByUserid", [user_id])

    def find_contacts_by_phone(self, phones: Iterable[str]) -> Any:
        """``findContactsByPhone(phones)``.

        Phone numbers must be international, e.g. ``"+81 9012345678"``.
        """
        return self.transport.call("Talk.TalkService.findContactsByPhone", [list(phones)])

    def find_and_add_contacts_by_mid(
        self,
        mids: Iterable[str],
        contact_type: int = int(ContactType.MID),
        req_seq: int | None = None,
    ) -> Any:
        """``findAndAddContactsByMid(reqSeq, type, ids)`` (arg shape inferred)."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.findAndAddContactsByMid", [req_seq, contact_type, list(mids)]
        )

    # -- blocking ------------------------------------------------------------
    def block_contact(self, mid: str, req_seq: int | None = None) -> Any:
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call("Talk.TalkService.blockContact", [req_seq, mid])

    def unblock_contact(
        self, mid: str, reference: str = "", req_seq: int | None = None
    ) -> Any:
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.unblockContact", [req_seq, mid, reference]
        )

    def get_blocked_contact_ids(self, sync_reason: int = int(SyncReason.FULL_SYNC)) -> Any:
        return self.transport.call("Talk.TalkService.getBlockedContactIds", [sync_reason])

    # -- settings / favourites ----------------------------------------------
    def update_contact_setting(
        self, mid: str, flag: int, value: str, req_seq: int | None = None
    ) -> Any:
        """``updateContactSetting(reqSeq, mid, flag, value)``.

        ``flag`` is a :class:`ContactSetting`; ``value`` is the stringified
        value (e.g. ``"true"``/``"false"`` for the favourite toggle).
        """
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.updateContactSetting", [req_seq, mid, int(flag), str(value)]
        )

    def set_favorite(self, mid: str, favorite: bool = True) -> Any:
        return self.update_contact_setting(
            mid, int(ContactSetting.CONTACT_SETTING_FAVORITE), "true" if favorite else "false"
        )

    def hide_contact(self, mid: str, hidden: bool = True) -> Any:
        return self.update_contact_setting(
            mid,
            int(ContactSetting.CONTACT_SETTING_CONTACT_HIDE),
            "true" if hidden else "false",
        )

    def get_favorite_mids(self) -> Any:
        return self.transport.call("Talk.TalkService.getFavoriteMids", [])

    # -- recommendations -----------------------------------------------------
    def get_recommendation_ids(self, sync_reason: int = int(SyncReason.FULL_SYNC)) -> Any:
        return self.transport.call("Talk.TalkService.getRecommendationIds", [sync_reason])

    def get_blocked_recommendation_ids(
        self, sync_reason: int = int(SyncReason.FULL_SYNC)
    ) -> Any:
        return self.transport.call(
            "Talk.TalkService.getBlockedRecommendationIds", [sync_reason]
        )

    def block_recommendation(self, mid: str, req_seq: int | None = None) -> Any:
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call("Talk.TalkService.blockRecommendation", [req_seq, mid])

    # -- relations / buddy ---------------------------------------------------
    def add_friend_by_mid(
        self, user_mid: str, *, from_chat_mid: str | None = None, req_seq: int | None = None
    ) -> Any:
        """``addFriendByMid(request)`` (Relation.RelationService)."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        if from_chat_mid:
            meta = {"chat": {"chatMid": from_chat_mid}}
        else:
            meta = {"friendRecommendation": {}}
        return self.transport.call(
            "Relation.RelationService.addFriendByMid",
            [
                {
                    "reqSeq": req_seq,
                    "userMid": user_mid,
                    "tracking": {"reference": "", "trackingMetaV2": meta},
                }
            ],
        )

    def get_target_profile_notice(self, target_user_mid: str) -> Any:
        return self.transport.call(
            "Relation.RelationService.getTargetProfileNotice",
            [{"targetUserMid": target_user_mid}],
        )

    def get_buddy_detail(self, buddy_mid: str) -> Any:
        """``getBuddyDetail(buddyMid)`` (Talk.BuddyService) — official account info."""
        return self.transport.call("Talk.BuddyService.getBuddyDetail", [buddy_mid])

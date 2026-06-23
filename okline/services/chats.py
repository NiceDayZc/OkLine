"""Groups / chats / rooms endpoints.

Modern LINE uses the unified *chat* model (a group is a ``Chat`` with type
GROUP); legacy *rooms* are still supported via the room endpoints.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..enums import ChatType, SyncReason, UpdateChatRequestAttribute
from ._base import ServiceMixin


class ChatsMixin(ServiceMixin):
    # -- create / update -----------------------------------------------------
    def create_chat(
        self,
        name: str,
        target_user_mids: Iterable[str],
        *,
        chat_type: int = int(ChatType.GROUP),
        req_seq: int | None = None,
    ) -> Any:
        """``createChat(request)`` -> ``{chat: Chat}`` (new ``chat.chatMid``)."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.createChat",
            [
                {
                    "reqSeq": req_seq,
                    "type": int(chat_type),
                    "name": name or "",
                    "targetUserMids": list(target_user_mids),
                }
            ],
        )

    # convenient alias
    create_group = create_chat

    def update_chat(
        self, chat: dict, updated_attribute: int, req_seq: int | None = None
    ) -> Any:
        """``updateChat(request)``.

        ``chat`` should be the full Chat object with the changed field set;
        ``updated_attribute`` is the :class:`UpdateChatRequestAttribute` bitmask
        selecting which field changed.
        """
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.updateChat",
            [
                {
                    "reqSeq": req_seq,
                    "chat": chat,
                    "updatedAttribute": int(updated_attribute),
                }
            ],
        )

    def rename_chat(
        self, chat_mid: str, new_name: str, chat_type: int = int(ChatType.GROUP)
    ) -> Any:
        return self.update_chat(
            {"chatMid": chat_mid, "chatName": new_name, "type": int(chat_type)},
            int(UpdateChatRequestAttribute.NAME),
        )

    def set_chat_favorite(
        self, chat_mid: str, favorite_timestamp: int, chat_type: int = int(ChatType.GROUP)
    ) -> Any:
        return self.update_chat(
            {
                "chatMid": chat_mid,
                "favoriteTimestamp": favorite_timestamp,
                "type": int(chat_type),
            },
            int(UpdateChatRequestAttribute.FAVORITE_TIMESTAMP),
        )

    def set_chat_prevented_join_by_ticket(
        self, chat_mid: str, prevented: bool, chat_type: int = int(ChatType.GROUP)
    ) -> Any:
        return self.update_chat(
            {"chatMid": chat_mid, "preventedJoinByTicket": prevented, "type": int(chat_type)},
            int(UpdateChatRequestAttribute.PREVENTED_JOIN_BY_TICKET),
        )

    # -- membership ----------------------------------------------------------
    def invite_into_chat(
        self, chat_mid: str, target_user_mids: Iterable[str], req_seq: int | None = None
    ) -> Any:
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.inviteIntoChat",
            [
                {
                    "reqSeq": req_seq,
                    "chatMid": chat_mid,
                    "targetUserMids": list(target_user_mids),
                }
            ],
        )

    def kick_from_chat(
        self, chat_mid: str, target_user_mids: Iterable[str], req_seq: int | None = None
    ) -> Any:
        """``deleteOtherFromChat`` — remove member(s) from a group/chat."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.deleteOtherFromChat",
            [
                {
                    "reqSeq": req_seq,
                    "chatMid": chat_mid,
                    "targetUserMids": list(target_user_mids),
                }
            ],
        )

    def cancel_chat_invitation(
        self, chat_mid: str, target_user_mids: Iterable[str], req_seq: int | None = None
    ) -> Any:
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.cancelChatInvitation",
            [
                {
                    "reqSeq": req_seq,
                    "chatMid": chat_mid,
                    "targetUserMids": list(target_user_mids),
                }
            ],
        )

    def leave_chat(self, chat_mid: str, req_seq: int | None = None) -> Any:
        """``deleteSelfFromChat`` — leave a group/chat."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.deleteSelfFromChat",
            [
                {
                    "reqSeq": req_seq,
                    "chatMid": chat_mid,
                }
            ],
        )

    def accept_chat_invitation(self, chat_mid: str, req_seq: int | None = None) -> Any:
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.acceptChatInvitation", [{"reqSeq": req_seq, "chatMid": chat_mid}]
        )

    def reject_chat_invitation(self, chat_mid: str, req_seq: int | None = None) -> Any:
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.rejectChatInvitation", [{"reqSeq": req_seq, "chatMid": chat_mid}]
        )

    # -- listing -------------------------------------------------------------
    def get_all_chat_mids(
        self,
        *,
        with_member_chats: bool = True,
        with_invited_chats: bool = True,
        sync_reason: int = int(SyncReason.FULL_SYNC),
    ) -> Any:
        """``getAllChatMids(request, syncReason)`` ->
        ``{memberChatMids, invitedChatMids}``."""
        return self.transport.call(
            "Talk.TalkService.getAllChatMids",
            [
                {
                    "withMemberChats": with_member_chats,
                    "withInvitedChats": with_invited_chats,
                },
                sync_reason,
            ],
        )

    #: The server rejects ``getChats`` with more than this many mids per call
    #: (``Invalid Length`` / code 6), so requests are chunked.
    GET_CHATS_LIMIT = 100

    def get_chats(
        self,
        chat_mids: Iterable[str],
        *,
        with_members: bool = True,
        with_invitees: bool = True,
        sync_reason: int = int(SyncReason.FULL_SYNC),
    ) -> Any:
        """``getChats(request, syncReason)`` -> ``{chats: [Chat]}``.

        Automatically chunked at :attr:`GET_CHATS_LIMIT` mids per request and the
        ``chats`` lists merged, so you can pass an unbounded number of chat mids.
        """
        mids = list(chat_mids)

        def _call(batch):
            return self.transport.call(
                "Talk.TalkService.getChats",
                [
                    {
                        "chatMids": batch,
                        "withMembers": with_members,
                        "withInvitees": with_invitees,
                    },
                    sync_reason,
                ],
            )

        if len(mids) <= self.GET_CHATS_LIMIT:
            return _call(mids)
        merged: list = []
        for i in range(0, len(mids), self.GET_CHATS_LIMIT):
            res = _call(mids[i : i + self.GET_CHATS_LIMIT])
            if isinstance(res, dict):
                merged.extend(res.get("chats", []) or [])
        return {"chats": merged}

    # -- legacy rooms --------------------------------------------------------
    def invite_into_room(
        self, room_mid: str, contact_ids: Iterable[str], req_seq: int | None = None
    ) -> Any:
        """``inviteIntoRoom(reqSeq, roomMid, contactIds)`` (flat positional)."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.inviteIntoRoom", [req_seq, room_mid, list(contact_ids)]
        )

    def leave_room(self, room_mid: str, req_seq: int | None = None) -> Any:
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call("Talk.TalkService.leaveRoom", [req_seq, room_mid])

    def get_rooms(self, room_mids: Iterable[str]) -> Any:
        """``getRoomsV2(roomMids)`` -> ``[Room]``."""
        return self.transport.call("Talk.TalkService.getRoomsV2", [list(room_mids)])

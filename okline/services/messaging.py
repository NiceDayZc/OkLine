"""Talk.TalkService — messaging endpoints.

Every method maps 1:1 onto a Thrift call; argument *order* matches the wire
exactly (extracted from the bundle).  ``reqSeq`` arguments are auto-generated
from :meth:`LineApi.next_req_seq` when omitted.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..enums import PredefinedReactionType, SyncReason
from ..exceptions import LineApiError
from ..models import Message
from ._base import ServiceMixin


class MessagingMixin(ServiceMixin):
    # -- sending -------------------------------------------------------------
    def send_message(
        self, message: dict, req_seq: int | None = None, encrypt: bool | None = None
    ) -> Any:
        """``sendMessage(reqSeq, Message)`` — body ``[reqSeq, message]``.

        If the conversation requires Letter Sealing the server rejects a plain
        send with code 82 ("can not send using plain mode"); when an E2EE manager
        is ready (after :meth:`qr_login`) the message is then encrypted and
        re-sent automatically.  Pass ``encrypt=True`` to seal up-front.
        """
        e2ee = getattr(self, "e2ee", None)
        if encrypt and e2ee is not None and not message.get("chunks"):
            message = e2ee.encrypt(message)
        if req_seq is None:
            req_seq = self.next_req_seq()
        try:
            return self.transport.call("Talk.TalkService.sendMessage", [req_seq, message])
        except LineApiError as exc:
            # 82 == E2EE_RETRY_ENCRYPT — seal and resend, once.  Only text/location
            # (contentType NONE=0) can be Letter-Sealed this way; a media/sticker
            # placeholder (IMAGE/VIDEO/…) must NOT be re-sealed (it has no text).
            if (
                exc.code == 82
                and e2ee is not None
                and e2ee.is_ready()
                and not message.get("chunks")
                and int(message.get("contentType", 0)) == 0
            ):
                sealed = e2ee.encrypt(message)
                return self.transport.call(
                    "Talk.TalkService.sendMessage", [self.next_req_seq(), sealed]
                )
            raise

    def send_text(self, to: str, text: str, **kw: Any) -> Any:
        """Convenience: build a text :class:`Message` and send it."""
        return self.send_message(Message.text(to, text, **kw))

    def send_sticker(
        self, to: str, package_id: str, sticker_id: str, version: int = 1, **kw: Any
    ) -> Any:
        return self.send_message(Message.sticker(to, package_id, sticker_id, version, **kw))

    def send_location(
        self,
        to: str,
        latitude: float,
        longitude: float,
        title: str = "",
        address: str = "",
        **kw: Any,
    ) -> Any:
        return self.send_message(
            Message.location(to, latitude, longitude, title=title, address=address, **kw)
        )

    def send_contact(
        self, to: str, contact_mid: str, display_name: str = "", **kw: Any
    ) -> Any:
        return self.send_message(Message.contact(to, contact_mid, display_name, **kw))

    def send_flex(self, to: str, alt_text: str, contents: dict, **kw: Any) -> Any:
        return self.send_message(Message.flex(to, alt_text, contents, **kw))

    def reply_text(self, to: str, text: str, related_message_id: str, **kw: Any) -> Any:
        from ..enums import MessageRelationType

        return self.send_text(
            to,
            text,
            related_message_id=related_message_id,
            message_relation_type=int(MessageRelationType.REPLY),
            **kw,
        )

    def unsend_message(self, message_id: str, req_seq: int | None = None) -> Any:
        """``unsendMessage(reqSeq, messageId)`` — recall a sent message."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.unsendMessage", [req_seq, str(message_id)]
        )

    def send_postback(self, message_id: str, url: str, chat_mid: str, origin_mid: str) -> Any:
        """``sendPostback(request)`` — trigger a flex/template postback action.

        Note the wire field names use uppercase ``MID`` (``chatMID``/``originMID``).
        """
        return self.transport.call(
            "Talk.TalkService.sendPostback",
            [
                {
                    "messageId": str(message_id),
                    "url": url,
                    "chatMID": chat_mid,
                    "originMID": origin_mid,
                }
            ],
        )

    # -- reactions -----------------------------------------------------------
    def react(
        self,
        message_id: str,
        reaction: int = int(PredefinedReactionType.NICE),
        req_seq: int | None = None,
    ) -> Any:
        """``react(request{reqSeq, messageId, reactionType})``."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.react",
            [
                {
                    "reqSeq": req_seq,
                    "messageId": str(message_id),
                    "reactionType": {"predefinedReactionType": int(reaction)},
                }
            ],
        )

    def cancel_reaction(self, message_id: str, req_seq: int | None = None) -> Any:
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.cancelReaction",
            [
                {
                    "reqSeq": req_seq,
                    "messageId": str(message_id),
                }
            ],
        )

    # -- read / removed state -----------------------------------------------
    def send_chat_checked(
        self,
        chat_mid: str,
        last_message_id: str,
        session_id: int = 0,
        req_seq: int | None = None,
    ) -> Any:
        """``sendChatChecked(reqSeq, consumer, lastMessageId, sessionId)`` —
        mark a chat read up to ``last_message_id``."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.sendChatChecked",
            [req_seq, chat_mid, str(last_message_id), session_id],
        )

    # alias matching common terminology
    mark_as_read = send_chat_checked

    def send_chat_removed(
        self,
        chat_mid: str,
        last_message_id: str,
        session_id: int = 0,
        req_seq: int | None = None,
    ) -> Any:
        """``sendChatRemoved(reqSeq, chatMid, lastMessageId, sessionId)`` —
        delete/clear a chat from the list."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.sendChatRemoved",
            [req_seq, chat_mid, str(last_message_id), session_id],
        )

    def set_chat_hidden_status(
        self,
        chat_mid: str,
        last_message_id: str,
        hidden: bool = True,
        req_seq: int | None = None,
    ) -> Any:
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.setChatHiddenStatus",
            [
                {
                    "reqSeq": req_seq,
                    "chatMid": chat_mid,
                    "lastMessageId": str(last_message_id),
                    "hidden": hidden,
                }
            ],
        )

    # -- fetching ------------------------------------------------------------
    def get_recent_messages(self, message_box_id: str, count: int = 50) -> Any:
        """``getRecentMessagesV2(messageBoxId, messagesCount)``."""
        return self.transport.call(
            "Talk.TalkService.getRecentMessagesV2", [message_box_id, count]
        )

    def get_previous_messages(
        self,
        message_box_id: str,
        end_message_id: str,
        delivered_time: int,
        count: int = 100,
        sync_reason: int = int(SyncReason.OPERATION),
    ) -> Any:
        """``getPreviousMessagesV2WithRequest(request, syncReason)``."""
        return self.transport.call(
            "Talk.TalkService.getPreviousMessagesV2WithRequest",
            [
                {
                    "messageBoxId": message_box_id,
                    "endMessageId": {
                        "messageId": str(end_message_id),
                        "deliveredTime": delivered_time,
                    },
                    "messagesCount": count,
                },
                sync_reason,
            ],
        )

    def get_messages_by_ids(self, message_ids: Iterable[str]) -> Any:
        return self.transport.call("Talk.TalkService.getMessagesByIds", [list(message_ids)])

    def get_message_boxes(
        self,
        *,
        min_chat_id: str | None = None,
        active_only: bool = True,
        unread_only: bool = False,
        limit: int = 100,
        with_unread_count: bool = True,
        last_messages_per_box: int = 5,
        sync_reason: int = int(SyncReason.INITIALIZATION),
    ) -> Any:
        """``getMessageBoxes(request, syncReason)`` — paginated chat list."""
        return self.transport.call(
            "Talk.TalkService.getMessageBoxes",
            [
                {
                    "minChatId": min_chat_id,
                    "activeOnly": active_only,
                    "unreadOnly": unread_only,
                    "messageBoxCountLimit": limit,
                    "withUnreadCount": with_unread_count,
                    "lastMessagesPerMessageBoxCount": last_messages_per_box,
                },
                sync_reason,
            ],
        )

    def get_message_boxes_by_ids(
        self,
        message_box_ids: Iterable[str],
        *,
        with_unread_count: bool = True,
        last_messages_count: int = 1,
        sync_reason: int = int(SyncReason.OPERATION),
    ) -> Any:
        return self.transport.call(
            "Talk.TalkService.getMessageBoxesByIds",
            [
                {
                    "messageBoxIds": list(message_box_ids),
                    "withUnreadCount": with_unread_count,
                    "lastMessagesCount": last_messages_count,
                },
                sync_reason,
            ],
        )

    def get_message_read_range(
        self, chat_ids: Iterable[str], sync_reason: int = int(SyncReason.OPERATION)
    ) -> Any:
        return self.transport.call(
            "Talk.TalkService.getMessageReadRange", [list(chat_ids), sync_reason]
        )

    def determine_media_message_flow(self, chat_mid: str) -> Any:
        return self.transport.call(
            "Talk.TalkService.determineMediaMessageFlow", [{"chatMid": chat_mid}]
        )

    def get_last_op_revision(self) -> Any:
        """``getLastOpRevision()`` — current op revision (sync cursor)."""
        return self.transport.call("Talk.TalkService.getLastOpRevision", [])

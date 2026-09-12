"""Builders for the request structs that callers construct most often.

The gateway accepts plain JSON objects with the Thrift field *names*, so these
helpers simply return ``dict`` payloads.  The :class:`Message` builders mirror
the message-construction helpers in ``static/js/main.js``::

    base = {from, to, toType, id, createdTime(str epoch-ms), sessionId:0}
    text = {...base, text, contentType: NONE, contentMetadata, hasContent:false}
    sticker = {...base, contentType: STICKER, hasContent:true,
               contentMetadata: {STKID, STKPKGID, STKVER, ...optional keys}}

The extension's base builder always populates ``from``/``id``/``createdTime``;
these helpers make them *optional* (``from_mid`` / ``msg_id`` /
``created_time``) and omit them by default — the gateway tolerates the omission
for plain sends, and E2EE sealed sends must not carry ``from`` (see
:mod:`okline.e2ee_crypto`), so the default wire shape stays unchanged.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from .enums import ContentType, MIDType


def mid_to_type(mid: str) -> int:
    """Infer ``toType`` from a mid prefix.

    Modern LINE mids are upper-case (``U`` user, ``C`` group/chat, ``R`` room,
    ``S`` square) — match case-insensitively.
    """
    if not mid:
        return int(MIDType.USER)
    head = mid[:1].lower()
    if head == "c":
        return int(MIDType.GROUP)
    if head == "r":
        return int(MIDType.ROOM)
    if head == "s":
        return int(MIDType.SQUARE_CHAT)
    return int(MIDType.USER)


def now_ms() -> int:
    return int(time.time() * 1000)


class Message:
    """Factory methods returning the ``Message`` dict for ``sendMessage``.

    Only ``to``, ``contentType`` (and ``text`` / ``contentMetadata``) are
    required on the wire; the rest are accepted for parity with the client and
    are harmless if present.  ``from`` / ``id`` / ``createdTime`` are optional:
    the extension's base builder always sends them, but the gateway tolerates
    their omission, so they are only included when the caller passes
    ``from_mid`` / ``msg_id`` / ``created_time`` (accepted by every builder
    below, either as an explicit parameter or through ``**extra``).
    """

    @staticmethod
    def _base(
        to: str,
        *,
        from_mid: str | None = None,
        msg_id: str | None = None,
        created_time: int | str | None = None,
        **extra: Any,
    ) -> dict:
        # The bundle's rP() always sends {from, id, createdTime(String(epoch
        # ms))}; here they stay opt-in (None = omitted) so the default wire
        # shape is unchanged.  createdTime is a STRING on the wire.
        msg = {
            "to": to,
            "toType": mid_to_type(to),
            "contentMetadata": {},
            "sessionId": 0,
        }
        if from_mid is not None:
            msg["from"] = from_mid
        if msg_id is not None:
            msg["id"] = msg_id
        if created_time is not None:
            msg["createdTime"] = str(created_time)
        msg.update(extra)
        return msg

    @classmethod
    def text(
        cls,
        to: str,
        text: str,
        *,
        content_metadata: Mapping[str, Any] | None = None,
        related_message_id: str | None = None,
        message_relation_type: int | None = None,
        from_mid: str | None = None,
        msg_id: str | None = None,
        created_time: int | str | None = None,
        **extra: Any,
    ) -> dict:
        # aP in the bundle: text messages carry an explicit hasContent:false.
        msg = cls._base(
            to,
            from_mid=from_mid,
            msg_id=msg_id,
            created_time=created_time,
            text=text,
            contentType=int(ContentType.NONE),
            hasContent=False,
            **extra,
        )
        if content_metadata:
            msg["contentMetadata"] = dict(content_metadata)
        if related_message_id is not None:
            msg["relatedMessageId"] = related_message_id
            msg["messageRelationType"] = message_relation_type
            msg["relatedMessageServiceCode"] = 1
        return msg

    @classmethod
    def sticker(
        cls,
        to: str,
        package_id: str,
        sticker_id: str,
        version: int = 1,
        *,
        sticker_text: str = "",
        sticker_option: str = "",
        sticker_hash: str = "",
        sticker_image_text: str = "",
        from_mid: str | None = None,
        msg_id: str | None = None,
        created_time: int | str | None = None,
        **extra: Any,
    ) -> dict:
        # iP in the bundle: pickBy({STKPKGID, STKID, STKTXT, STKVER, STKOPT,
        # STKHASH, STK_IMG_TXT}, isString && Boolean) — every optional key is
        # only sent when a non-empty string — and hasContent is true.
        meta = {
            "STKID": str(sticker_id),
            "STKPKGID": str(package_id),
            "STKVER": str(version),
        }
        if sticker_text:
            meta["STKTXT"] = sticker_text
        if sticker_option:
            meta["STKOPT"] = sticker_option
        if sticker_hash:
            meta["STKHASH"] = sticker_hash
        if sticker_image_text:
            meta["STK_IMG_TXT"] = sticker_image_text
        return cls._base(
            to,
            from_mid=from_mid,
            msg_id=msg_id,
            created_time=created_time,
            text="",
            contentType=int(ContentType.STICKER),
            contentMetadata=meta,
            hasContent=True,
            **extra,
        )

    @classmethod
    def location(
        cls,
        to: str,
        latitude: float,
        longitude: float,
        *,
        title: str = "",
        address: str = "",
        **extra: Any,
    ) -> dict:
        msg = cls._base(to, text="", contentType=int(ContentType.LOCATION), **extra)
        msg["location"] = {
            "title": title,
            "address": address,
            "latitude": latitude,
            "longitude": longitude,
            "phone": extra.get("phone"),
        }
        return msg

    @classmethod
    def contact(cls, to: str, contact_mid: str, display_name: str = "", **extra: Any) -> dict:
        meta = {"mid": contact_mid, "displayName": display_name}
        return cls._base(
            to, text="", contentType=int(ContentType.CONTACT), contentMetadata=meta, **extra
        )

    @classmethod
    def flex(cls, to: str, alt_text: str, contents: Mapping[str, Any], **extra: Any) -> dict:
        meta = {
            "FLEX_JSON": json.dumps(contents, ensure_ascii=False),
            "ALT_TEXT": alt_text,
        }
        return cls._base(
            to, text="", contentType=int(ContentType.FLEX), contentMetadata=meta, **extra
        )

    @classmethod
    def media_ref(
        cls,
        to: str,
        content_type: int,
        object_id: str,
        *,
        service: str = "talk",
        content_metadata: dict | None = None,
        **extra: Any,
    ) -> dict:
        """A media message that references an already-uploaded OBS object."""
        meta = dict(content_metadata or {})
        return cls._base(
            to, text="", contentType=int(content_type), contentMetadata=meta, **extra
        )

    # -- media builders (the Message half of a media send) -------------------
    # NOTE: a full media send is upload-then-send — the bytes go to OBS first,
    # then this Message is sent. Building the Message is exact; wiring the OBS
    # upload session is experimental (see docs/messaging.md).
    #
    # The optional E2EE-media keys mirror sP in the bundle: on the V2 media
    # flow the extension adds ENC_KM (base64 key material) to every media
    # type, MEDIA_THUMB_INFO (JSON string) to IMAGE/VIDEO, and
    # MEDIA_CONTENT_INFO (JSON string) to IMAGE.  Here they are accepted on
    # all four builders and only emitted when supplied (default wire shape
    # unchanged) — the E2EE layer generates them.  Group-image keys
    # (GID/GSEQ/GTOTAL) pass through ``content_metadata`` verbatim, like any
    # other hand-supplied key.
    @staticmethod
    def _media_meta(
        content_metadata: Mapping[str, Any] | None,
        *,
        enc_km: str | None = None,
        media_content_info: Mapping[str, Any] | None = None,
        media_thumb_info: Mapping[str, Any] | None = None,
    ) -> dict:
        meta = dict(content_metadata or {})
        if media_content_info:
            meta["MEDIA_CONTENT_INFO"] = json.dumps(
                media_content_info, ensure_ascii=False, separators=(",", ":")
            )
        if media_thumb_info:
            meta["MEDIA_THUMB_INFO"] = json.dumps(
                media_thumb_info, ensure_ascii=False, separators=(",", ":")
            )
        if enc_km:
            meta["ENC_KM"] = enc_km
        return meta

    @classmethod
    def image(
        cls,
        to: str,
        *,
        content_metadata: dict | None = None,
        enc_km: str | None = None,
        media_content_info: Mapping[str, Any] | None = None,
        media_thumb_info: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> dict:
        meta = cls._media_meta(
            content_metadata,
            enc_km=enc_km,
            media_content_info=media_content_info,
            media_thumb_info=media_thumb_info,
        )
        return cls._base(
            to,
            text="",
            contentType=int(ContentType.IMAGE),
            contentMetadata=meta,
            hasContent=True,
            **extra,
        )

    @classmethod
    def video(
        cls,
        to: str,
        duration_ms: int = 0,
        *,
        content_metadata: dict | None = None,
        enc_km: str | None = None,
        media_content_info: Mapping[str, Any] | None = None,
        media_thumb_info: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> dict:
        meta = {"DURATION": str(duration_ms)}
        meta.update(
            cls._media_meta(
                content_metadata,
                enc_km=enc_km,
                media_content_info=media_content_info,
                media_thumb_info=media_thumb_info,
            )
        )
        return cls._base(
            to,
            text="",
            contentType=int(ContentType.VIDEO),
            contentMetadata=meta,
            hasContent=True,
            **extra,
        )

    @classmethod
    def audio(
        cls,
        to: str,
        duration_ms: int = 0,
        *,
        content_metadata: dict | None = None,
        enc_km: str | None = None,
        media_content_info: Mapping[str, Any] | None = None,
        media_thumb_info: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> dict:
        meta = {"DURATION": str(duration_ms)}
        meta.update(
            cls._media_meta(
                content_metadata,
                enc_km=enc_km,
                media_content_info=media_content_info,
                media_thumb_info=media_thumb_info,
            )
        )
        return cls._base(
            to,
            text="",
            contentType=int(ContentType.AUDIO),
            contentMetadata=meta,
            hasContent=True,
            **extra,
        )

    @classmethod
    def file(
        cls,
        to: str,
        file_name: str,
        file_size: int,
        *,
        content_metadata: dict | None = None,
        enc_km: str | None = None,
        media_content_info: Mapping[str, Any] | None = None,
        media_thumb_info: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> dict:
        meta = {"FILE_NAME": file_name, "FILE_SIZE": str(file_size)}
        meta.update(
            cls._media_meta(
                content_metadata,
                enc_km=enc_km,
                media_content_info=media_content_info,
                media_thumb_info=media_thumb_info,
            )
        )
        return cls._base(
            to,
            text="",
            contentType=int(ContentType.FILE),
            contentMetadata=meta,
            hasContent=True,
            **extra,
        )

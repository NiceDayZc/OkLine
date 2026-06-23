"""Typed response models.

The transport returns raw ``dict``/``list`` results (faithful to the wire). These
lightweight dataclasses wrap the common ones so you get attribute access and
editor autocomplete, while still keeping the original payload on ``.raw``.

They are **optional** — every OkLine method still returns plain dicts. Use the
``.from_dict`` constructors when you want typed access::

    from okline.entities import Profile, Contact
    me = Profile.from_dict(api.get_profile())
    print(me.display_name, me.mid)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _g(d: dict[str, Any], *names: str, default: Any = None) -> Any:
    for n in names:
        if isinstance(d, dict) and n in d and d[n] is not None:
            return d[n]
    return default


@dataclass
class Profile:
    mid: str = ""
    userid: str | None = None
    display_name: str = ""
    status_message: str = ""
    picture_status: str | None = None
    picture_path: str | None = None
    region_code: str | None = None
    phone: str | None = None
    allow_search_by_userid: bool | None = None
    allow_search_by_email: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Profile:
        d = d or {}
        return cls(
            mid=_g(d, "mid", default=""),
            userid=_g(d, "userid"),
            display_name=_g(d, "displayName", default=""),
            status_message=_g(d, "statusMessage", default=""),
            picture_status=_g(d, "pictureStatus"),
            picture_path=_g(d, "picturePath"),
            region_code=_g(d, "regionCode"),
            phone=_g(d, "phone"),
            allow_search_by_userid=_g(d, "allowSearchByUserid"),
            allow_search_by_email=_g(d, "allowSearchByEmail"),
            raw=d,
        )


@dataclass
class Contact:
    mid: str = ""
    display_name: str = ""
    display_name_overridden: str | None = None
    status_message: str = ""
    picture_path: str | None = None
    type: int | None = None
    status: int | None = None
    relation: int | None = None
    capable_buddy: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Best display name (user override wins, like the client)."""
        return self.display_name_overridden or self.display_name

    @property
    def is_official(self) -> bool:
        return bool(self.capable_buddy)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Contact:
        # accept either a bare Contact or a getContactsV2 wrapper {contact: {...}}
        if isinstance(d, dict) and "contact" in d and isinstance(d["contact"], dict):
            d = d["contact"]
        d = d or {}
        return cls(
            mid=_g(d, "mid", default=""),
            display_name=_g(d, "displayName", default=""),
            display_name_overridden=_g(d, "displayNameOverridden"),
            status_message=_g(d, "statusMessage", default=""),
            picture_path=_g(d, "picturePath"),
            type=_g(d, "type"),
            status=_g(d, "status"),
            relation=_g(d, "relation"),
            capable_buddy=bool(_g(d, "capableBuddy", default=False)),
            raw=d,
        )


@dataclass
class Group:
    chat_mid: str = ""
    name: str = ""
    picture_path: str | None = None
    type: int | None = None
    member_mids: list[str] = field(default_factory=list)
    invitee_mids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def member_count(self) -> int:
        return len(self.member_mids)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Group:
        d = d or {}
        extra = _g(d, "extra", default={}) or {}
        gx = extra.get("groupExtra", {}) if isinstance(extra, dict) else {}
        members = list(gx.get("memberMids", []) or [])
        invitees = list(gx.get("inviteeMids", []) or [])
        return cls(
            chat_mid=_g(d, "chatMid", "mid", default=""),
            name=_g(d, "chatName", "name", default=""),
            picture_path=_g(d, "picturePath"),
            type=_g(d, "type"),
            member_mids=members,
            invitee_mids=invitees,
            raw=d,
        )


@dataclass
class Room:
    mid: str = ""
    member_mids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Room:
        d = d or {}
        return cls(
            mid=_g(d, "mid", default=""),
            member_mids=list(_g(d, "memberMids", default=[]) or []),
            raw=d,
        )


@dataclass
class Message:
    id: str = ""
    from_mid: str = ""
    to: str = ""
    to_type: int | None = None
    text: str | None = None
    content_type: int = 0
    content_metadata: dict[str, Any] = field(default_factory=dict)
    created_time: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_text(self) -> bool:
        return self.content_type == 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Message:
        d = d or {}
        ct = _g(d, "createdTime")
        return cls(
            id=str(_g(d, "id", default="")),
            from_mid=_g(d, "from", default=""),
            to=_g(d, "to", default=""),
            to_type=_g(d, "toType"),
            text=_g(d, "text"),
            content_type=int(_g(d, "contentType", default=0) or 0),
            content_metadata=_g(d, "contentMetadata", default={}) or {},
            created_time=int(ct) if ct not in (None, "") else None,
            raw=d,
        )


def parse_contacts(result: dict[str, Any]) -> dict[str, Contact]:
    """Turn a ``getContactsV2`` result into ``{mid: Contact}``."""
    out: dict[str, Contact] = {}
    contacts = result.get("contacts", result) if isinstance(result, dict) else {}
    for mid, wrapper in (contacts or {}).items():
        out[mid] = Contact.from_dict(wrapper)
    return out

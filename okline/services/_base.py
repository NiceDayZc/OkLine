"""Shared typed base for the service mixins.

The :class:`okline.OkLine` client is composed of several mixins (messaging,
contacts, chats, profile, …).  At runtime ``OkLine`` provides the ``transport``
and ``next_req_seq`` that every mixin uses; this base merely *declares* them so
type checkers understand the contract — without the mixins importing the client
(which would be circular).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..transport import Transport


class ServiceMixin:
    """Attributes/methods supplied by :class:`OkLine` to every service mixin."""

    transport: Transport

    def next_req_seq(self) -> int:  # provided by OkLine
        raise NotImplementedError

"""Service mixins — one typed method per LINE Chrome Thrift endpoint.

:class:`AllServices` simply unions every mixin so :class:`okline.LineApi`
exposes all of them.  Each mixin only relies on ``self.transport`` and
``self.next_req_seq`` provided by the client.
"""

from __future__ import annotations

from .auth_service import AuthServiceMixin
from .channel_shop import ChannelShopMixin
from .chats import ChatsMixin
from .contacts import ContactsMixin
from .e2ee import E2EEMixin
from .messaging import MessagingMixin
from .profile import ProfileMixin


class AllServices(
    AuthServiceMixin,
    MessagingMixin,
    ContactsMixin,
    ChatsMixin,
    ProfileMixin,
    E2EEMixin,
    ChannelShopMixin,
):
    """Aggregate of every service mixin."""


__all__ = [
    "AllServices",
    "AuthServiceMixin",
    "ChannelShopMixin",
    "ChatsMixin",
    "ContactsMixin",
    "E2EEMixin",
    "MessagingMixin",
    "ProfileMixin",
]

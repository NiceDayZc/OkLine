"""OkLine — a complete, high-level Python client for LINE's Chrome extension API.

OkLine reproduces, end to end, the HTTP API used by the official LINE Chrome
extension (app type ``CHROMEOS``, version 3.7.2): the Thrift-over-JSON gateway
at ``line-chrome-gw.line-apps.com``, the mandatory ``X-Hmac`` request signing
(via LINE's real ``ltsm.wasm`` module), the RSA / QR login flows, the SSE
operation stream and the OBS media endpoints — plus full request/response
recording so you can paste the response of every endpoint.

Quick start
-----------
>>> from okline import OkLine
>>> api = OkLine()
>>> res = api.auth.email_login("me@example.com", "password")   # doctest: +SKIP
>>> if res.success:                                            # doctest: +SKIP
...     api.send_text("u0123...", "hello, world")              # doctest: +SKIP

Reuse a token and paste every response:
>>> api = OkLine(access_token="...", refresh_token="...")      # doctest: +SKIP
>>> api.get_profile()                                          # doctest: +SKIP
>>> print(api.last.pretty())                                   # doctest: +SKIP
>>> print(api.dump())                                          # doctest: +SKIP
"""

from __future__ import annotations

from . import e2ee_crypto, entities, enums
from .auth import AuthFlows, LoginResult
from .bot import Bot, EventContext, MessageContext
from .client import LineApi, OkLine
from .e2ee import E2EEManager
from .crypto import RSAKeyInfo, rsa_encrypt_credentials
from .entities import Contact, Group, Profile, Room
from .ratelimit import RateLimiter
from .session import Session
from .endpoints import (
    GATEWAY_BASE,
    OBS_BASE,
    SPECIAL_ENDPOINTS,
    THRIFT_ENDPOINTS,
    all_method_names,
    thrift_path,
)
from .exceptions import (
    LineApiError,
    LineAuthError,
    LineConfigError,
    LineError,
    LineLoginRequired,
    LineMustUpgradeError,
    LineTransportError,
)
from .hmac_signer import HmacSigner, HmacSignerError, LtsmBridge
from .models import Message, mid_to_type
from .obs import ObsClient, encode_obs_params
from .operations import Operation, OperationReceiver, SSEEvent
from .qrterm import print_qr, qr_to_ascii
from .recorder import Exchange, Recorder
from .transport import (
    APP_VERSION,
    DEFAULT_APPLICATION_HEADER,
    LineConfig,
    Tokens,
    Transport,
)

__version__ = "2.5.3"
#: The LINE client version this library emulates.
LINE_APP_VERSION = APP_VERSION

__all__ = [
    "OkLine",
    "LineApi",
    "Bot",
    "MessageContext",
    "EventContext",
    "Session",
    "RateLimiter",
    "E2EEManager",
    "e2ee_crypto",
    "entities",
    "Profile",
    "Contact",
    "Group",
    "Room",
    "Exchange",
    "Recorder",
    "LineConfig",
    "Tokens",
    "Transport",
    "AuthFlows",
    "LoginResult",
    "Message",
    "mid_to_type",
    "Operation",
    "OperationReceiver",
    "SSEEvent",
    "ObsClient",
    "encode_obs_params",
    "print_qr",
    "qr_to_ascii",
    "RSAKeyInfo",
    "rsa_encrypt_credentials",
    "HmacSigner",
    "HmacSignerError",
    "LtsmBridge",
    "enums",
    "THRIFT_ENDPOINTS",
    "SPECIAL_ENDPOINTS",
    "GATEWAY_BASE",
    "OBS_BASE",
    "thrift_path",
    "all_method_names",
    "LineError",
    "LineApiError",
    "LineAuthError",
    "LineConfigError",
    "LineTransportError",
    "LineLoginRequired",
    "LineMustUpgradeError",
    "DEFAULT_APPLICATION_HEADER",
    "LINE_APP_VERSION",
    "__version__",
]

"""Complete endpoint registry for the LINE Chrome client (CHROMEOS 3.7.2).

Every Thrift RPC the extension can make is exposed by the gateway at::

    POST https://line-chrome-gw.line-apps.com/api/<PATH>

where ``<PATH>`` is one of the ``THRIFT_ENDPOINTS`` values below.  The request
body is a JSON **array** of the positional Thrift arguments; struct arguments
are plain JSON objects with *named* (camelCase) fields.

The list was extracted verbatim from ``static/js/main.js`` (the ``SD("...")``
registrations) so it matches exactly what the real extension sends.

Besides the Thrift endpoints there are a handful of "special" REST endpoints
(operation streaming, long-polling, token refresh, OBS media) collected in
``SPECIAL_ENDPOINTS``.
"""

from __future__ import annotations

#: Base URL of the Chrome gateway that fronts every Thrift service.
GATEWAY_BASE = "https://line-chrome-gw.line-apps.com"

#: LEGY edge (used for a few page-info / sticker-shop helpers).
LEGY_BASE = "https://legy-jp.line-apps.com"
LEGY_BACKUP_BASE = "https://legy-backup.line-apps.com"

#: Object storage service (media upload / download).
OBS_BASE = "https://obs.line-apps.com"

#: All Thrift-over-JSON endpoints, keyed by ``<Namespace>.<Service>.<method>``.
#: The value is the path appended to ``/api/``.
THRIFT_ENDPOINTS = {
    # --- Secondary QR-code login (LoginQrCode namespace) ---------------------
    "LoginQrCode.SecondaryQrCodeLoginService.createSession": "talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/createSession",
    "LoginQrCode.SecondaryQrCodeLoginService.createQrCode": "talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/createQrCode",
    "LoginQrCode.SecondaryQrCodeLoginService.verifyCertificate": "talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/verifyCertificate",
    "LoginQrCode.SecondaryQrCodeLoginService.createPinCode": "talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/createPinCode",
    "LoginQrCode.SecondaryQrCodeLoginService.qrCodeLoginV2": "talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/qrCodeLoginV2",
    "LoginQrCode.SecondaryQrCodeLoginPermitNoticeService.checkQrCodeVerified": "talk/thrift/LoginQrCode/SecondaryQrCodeLoginPermitNoticeService/checkQrCodeVerified",
    "LoginQrCode.SecondaryQrCodeLoginPermitNoticeService.checkPinCodeVerified": "talk/thrift/LoginQrCode/SecondaryQrCodeLoginPermitNoticeService/checkPinCodeVerified",
    # --- Auth (Talk.AuthService) --------------------------------------------
    "Talk.AuthService.loginV2": "talk/thrift/Talk/AuthService/loginV2",
    "Talk.AuthService.logoutV2": "talk/thrift/Talk/AuthService/logoutV2",
    "Talk.AuthService.confirmE2EELogin": "talk/thrift/Talk/AuthService/confirmE2EELogin",
    # --- Channel (Talk.ChannelService) --------------------------------------
    "Talk.ChannelService.issueChannelToken": "talk/thrift/Talk/ChannelService/issueChannelToken",
    # --- Buddy (Talk.BuddyService) ------------------------------------------
    "Talk.BuddyService.getBuddyDetail": "talk/thrift/Talk/BuddyService/getBuddyDetail",
    # --- Relation (Relation.RelationService) --------------------------------
    "Relation.RelationService.addFriendByMid": "talk/thrift/Relation/RelationService/addFriendByMid",
    "Relation.RelationService.getTargetProfileNotice": "talk/thrift/Relation/RelationService/getTargetProfileNotice",
    # --- Shop (ShopService.ShopService) -------------------------------------
    "ShopService.ShopService.getOwnedProductSummaries": "shop/thrift/ShopService/ShopService/getOwnedProductSummaries",
    "ShopService.ShopService.setCustomizedImageText": "shop/thrift/ShopService/ShopService/setCustomizedImageText",
    "ShopService.ShopService.previewCustomizedImageText": "shop/thrift/ShopService/ShopService/previewCustomizedImageText",
    # --- Identity / keys (Talk.TalkService) ---------------------------------
    "Talk.TalkService.getRSAKeyInfo": "talk/thrift/Talk/TalkService/getRSAKeyInfo",
    "Talk.TalkService.getEncryptedIdentityV3": "talk/thrift/Talk/TalkService/getEncryptedIdentityV3",
    "Talk.TalkService.acquireEncryptedAccessToken": "talk/thrift/Talk/TalkService/acquireEncryptedAccessToken",
    # --- Profile / settings -------------------------------------------------
    "Talk.TalkService.getProfile": "talk/thrift/Talk/TalkService/getProfile",
    "Talk.TalkService.updateProfileAttributes": "talk/thrift/Talk/TalkService/updateProfileAttributes",
    "Talk.TalkService.getSettings": "talk/thrift/Talk/TalkService/getSettings",
    "Talk.TalkService.getSettingsAttributes2": "talk/thrift/Talk/TalkService/getSettingsAttributes2",
    "Talk.TalkService.updateSettingsAttributes2": "talk/thrift/Talk/TalkService/updateSettingsAttributes2",
    "Talk.TalkService.getConfigurations": "talk/thrift/Talk/TalkService/getConfigurations",
    "Talk.TalkService.getServerTime": "talk/thrift/Talk/TalkService/getServerTime",
    "Talk.TalkService.reportAbuseEx": "talk/thrift/Talk/TalkService/reportAbuseEx",
    # --- Contacts / relations ----------------------------------------------
    "Talk.TalkService.findAndAddContactsByMid": "talk/thrift/Talk/TalkService/findAndAddContactsByMid",
    "Talk.TalkService.getAllContactIds": "talk/thrift/Talk/TalkService/getAllContactIds",
    "Talk.TalkService.getContactsV2": "talk/thrift/Talk/TalkService/getContactsV2",
    "Talk.TalkService.blockContact": "talk/thrift/Talk/TalkService/blockContact",
    "Talk.TalkService.unblockContact": "talk/thrift/Talk/TalkService/unblockContact",
    "Talk.TalkService.getBlockedContactIds": "talk/thrift/Talk/TalkService/getBlockedContactIds",
    "Talk.TalkService.findContactsByPhone": "talk/thrift/Talk/TalkService/findContactsByPhone",
    "Talk.TalkService.findContactByUserid": "talk/thrift/Talk/TalkService/findContactByUserid",
    "Talk.TalkService.updateContactSetting": "talk/thrift/Talk/TalkService/updateContactSetting",
    "Talk.TalkService.getFavoriteMids": "talk/thrift/Talk/TalkService/getFavoriteMids",
    "Talk.TalkService.blockRecommendation": "talk/thrift/Talk/TalkService/blockRecommendation",
    "Talk.TalkService.getRecommendationIds": "talk/thrift/Talk/TalkService/getRecommendationIds",
    "Talk.TalkService.getBlockedRecommendationIds": "talk/thrift/Talk/TalkService/getBlockedRecommendationIds",
    # --- Messaging ----------------------------------------------------------
    "Talk.TalkService.getLastOpRevision": "talk/thrift/Talk/TalkService/getLastOpRevision",
    "Talk.TalkService.sendMessage": "talk/thrift/Talk/TalkService/sendMessage",
    "Talk.TalkService.unsendMessage": "talk/thrift/Talk/TalkService/unsendMessage",
    "Talk.TalkService.sendChatChecked": "talk/thrift/Talk/TalkService/sendChatChecked",
    "Talk.TalkService.sendChatRemoved": "talk/thrift/Talk/TalkService/sendChatRemoved",
    "Talk.TalkService.setChatHiddenStatus": "talk/thrift/Talk/TalkService/setChatHiddenStatus",
    "Talk.TalkService.react": "talk/thrift/Talk/TalkService/react",
    "Talk.TalkService.cancelReaction": "talk/thrift/Talk/TalkService/cancelReaction",
    "Talk.TalkService.sendPostback": "talk/thrift/Talk/TalkService/sendPostback",
    "Talk.TalkService.determineMediaMessageFlow": "talk/thrift/Talk/TalkService/determineMediaMessageFlow",
    "Talk.TalkService.getMessageReadRange": "talk/thrift/Talk/TalkService/getMessageReadRange",
    "Talk.TalkService.getMessageBoxesByIds": "talk/thrift/Talk/TalkService/getMessageBoxesByIds",
    "Talk.TalkService.getMessagesByIds": "talk/thrift/Talk/TalkService/getMessagesByIds",
    "Talk.TalkService.getMessageBoxes": "talk/thrift/Talk/TalkService/getMessageBoxes",
    "Talk.TalkService.getPreviousMessagesV2WithRequest": "talk/thrift/Talk/TalkService/getPreviousMessagesV2WithRequest",
    "Talk.TalkService.getRecentMessagesV2": "talk/thrift/Talk/TalkService/getRecentMessagesV2",
    # --- Rooms / chats / groups --------------------------------------------
    "Talk.TalkService.inviteIntoRoom": "talk/thrift/Talk/TalkService/inviteIntoRoom",
    "Talk.TalkService.leaveRoom": "talk/thrift/Talk/TalkService/leaveRoom",
    "Talk.TalkService.getRoomsV2": "talk/thrift/Talk/TalkService/getRoomsV2",
    "Talk.TalkService.createChat": "talk/thrift/Talk/TalkService/createChat",
    "Talk.TalkService.updateChat": "talk/thrift/Talk/TalkService/updateChat",
    "Talk.TalkService.inviteIntoChat": "talk/thrift/Talk/TalkService/inviteIntoChat",
    "Talk.TalkService.deleteOtherFromChat": "talk/thrift/Talk/TalkService/deleteOtherFromChat",
    "Talk.TalkService.cancelChatInvitation": "talk/thrift/Talk/TalkService/cancelChatInvitation",
    "Talk.TalkService.deleteSelfFromChat": "talk/thrift/Talk/TalkService/deleteSelfFromChat",
    "Talk.TalkService.rejectChatInvitation": "talk/thrift/Talk/TalkService/rejectChatInvitation",
    "Talk.TalkService.acceptChatInvitation": "talk/thrift/Talk/TalkService/acceptChatInvitation",
    "Talk.TalkService.getAllChatMids": "talk/thrift/Talk/TalkService/getAllChatMids",
    "Talk.TalkService.getChats": "talk/thrift/Talk/TalkService/getChats",
    # --- E2EE keys ----------------------------------------------------------
    "Talk.TalkService.getE2EEPublicKey": "talk/thrift/Talk/TalkService/getE2EEPublicKey",
    "Talk.TalkService.negotiateE2EEPublicKey": "talk/thrift/Talk/TalkService/negotiateE2EEPublicKey",
    "Talk.TalkService.getE2EEPublicKeysEx": "talk/thrift/Talk/TalkService/getE2EEPublicKeysEx",
    "Talk.TalkService.registerE2EEGroupKey": "talk/thrift/Talk/TalkService/registerE2EEGroupKey",
    "Talk.TalkService.getE2EEGroupSharedKey": "talk/thrift/Talk/TalkService/getE2EEGroupSharedKey",
    "Talk.TalkService.getLastE2EEGroupSharedKey": "talk/thrift/Talk/TalkService/getLastE2EEGroupSharedKey",
    "Talk.TalkService.getLastE2EEPublicKeys": "talk/thrift/Talk/TalkService/getLastE2EEPublicKeys",
}

#: Non-Thrift REST helpers used by the extension.
SPECIAL_ENDPOINTS = {
    # Server-Sent-Events stream of incoming operations (the modern fetchOps).
    "operation.receive": "api/operation/receive",
    # Classic long-poll fallbacks.
    "longpoll.LF1": "api/talk/long-polling/LF1",
    "longpoll.JQ": "api/talk/long-polling/JQ",
    # OAuth-style token refresh: body {"refreshToken": "..."}.
    "auth.tokenRefresh": "api/auth/tokenRefresh",
    # OBS media helpers.
    "obs.uploadProfile": "api/obs/uploadProfile",
    "obs.copyForMessage": "api/obs/copyForMessage",
    # Timeline / VOOM helpers.
    "timeline.home": "api/timeline/homeId",
    "timeline.getCover": "api/timeline/getCover",
    "timeline.updateCover": "api/timeline/updateCover",
    # LEGY page-info helper.
    "legy.pageinfo": "sc/api/v2/pageinfo/get",
}


def thrift_path(name: str) -> str:
    """Return the ``/api/...`` path for a ``Namespace.Service.method`` key."""
    try:
        return "/api/" + THRIFT_ENDPOINTS[name]
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError(f"unknown thrift endpoint {name!r}") from exc


def all_method_names() -> list[str]:
    """Every fully-qualified endpoint key (handy for tests / introspection)."""
    return sorted(THRIFT_ENDPOINTS)

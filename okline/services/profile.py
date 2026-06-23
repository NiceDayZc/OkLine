"""Profile, settings, configuration, server-time and abuse-reporting endpoints."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..enums import (
    ApplicationType,
    ProfileAttribute,
    SyncReason,
)
from ._base import ServiceMixin


class ProfileMixin(ServiceMixin):
    # -- profile -------------------------------------------------------------
    def get_profile(self, sync_reason: int = int(SyncReason.INITIALIZATION)) -> Any:
        """``getProfile(syncReason)`` -> Profile (mid, displayName, regionCode...)."""
        data = self.transport.call("Talk.TalkService.getProfile", [sync_reason])
        if isinstance(data, dict) and data.get("mid"):
            self.transport.tokens.mid = data["mid"]
        return data

    def update_profile_attributes(
        self, attributes: Mapping[int, str], req_seq: int | None = None
    ) -> Any:
        """``updateProfileAttributes(reqSeq, request)``.

        ``attributes`` maps a :class:`ProfileAttribute` to its new string value.
        """
        if req_seq is None:
            req_seq = self.next_req_seq()
        profile_attributes = {
            str(int(k)): {"value": v, "meta": {}} for k, v in attributes.items()
        }
        return self.transport.call(
            "Talk.TalkService.updateProfileAttributes",
            [req_seq, {"profileAttributes": profile_attributes}],
        )

    def set_display_name(self, name: str) -> Any:
        return self.update_profile_attributes({int(ProfileAttribute.DISPLAY_NAME): name})

    def set_status_message(self, message: str) -> Any:
        return self.update_profile_attributes({int(ProfileAttribute.STATUS_MESSAGE): message})

    # -- settings ------------------------------------------------------------
    def get_settings(self, sync_reason: int = int(SyncReason.INITIALIZATION)) -> Any:
        return self.transport.call("Talk.TalkService.getSettings", [sync_reason])

    def get_settings_attributes2(self, attributes: Iterable[int]) -> Any:
        return self.transport.call(
            "Talk.TalkService.getSettingsAttributes2", [[int(a) for a in attributes]]
        )

    def update_settings_attributes2(
        self,
        attributes_to_update: Iterable[int],
        settings: Mapping[str, Any],
        req_seq: int | None = None,
    ) -> Any:
        """``updateSettingsAttributes2(reqSeq, attributesToUpdate, settings)``."""
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call(
            "Talk.TalkService.updateSettingsAttributes2",
            [req_seq, [int(a) for a in attributes_to_update], dict(settings)],
        )

    # -- configuration / time -----------------------------------------------
    def get_configurations(
        self, region: str = "", sync_reason: int = int(SyncReason.INITIALIZATION)
    ) -> Any:
        """``getConfigurations("","","",region,"",syncReason)``."""
        return self.transport.call(
            "Talk.TalkService.getConfigurations", ["", "", "", region, "", sync_reason]
        )

    def get_server_time(self) -> Any:
        return self.transport.call("Talk.TalkService.getServerTime", [])

    # -- abuse reporting -----------------------------------------------------
    def report_abuse(
        self,
        *,
        report_source: int,
        spammer_reason: int,
        metadata: Mapping[str, Any],
        abuse_messages: list | None = None,
        application_type: int = int(ApplicationType.CHROMEOS),
    ) -> Any:
        """``reportAbuseEx(request)`` — report a user/group/message for abuse."""
        return self.transport.call(
            "Talk.TalkService.reportAbuseEx",
            [
                {
                    "abuseReportEntry": {
                        "message": {
                            "reportSource": int(report_source),
                            "applicationType": int(application_type),
                            "spammerReasons": [int(spammer_reason)],
                            "abuseMessages": abuse_messages or [],
                            "metadata": dict(metadata),
                        }
                    },
                }
            ],
        )

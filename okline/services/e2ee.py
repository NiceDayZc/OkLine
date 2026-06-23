"""End-to-end-encryption key endpoints (Letter Sealing).

These manage the Curve25519 public-key material and group shared keys.  The
client wrappers return the raw key structs; performing the actual ECDH + AES
message encryption is the caller's job (see :mod:`okline.e2ee_crypto`
for helpers).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..enums import SyncReason
from ._base import ServiceMixin


class E2EEMixin(ServiceMixin):
    def get_e2ee_public_key(self, mid: str, key_version: int = 1, key_id: int = 0) -> Any:
        """``getE2EEPublicKey(mid, keyVersion, keyId)`` -> E2EEPublicKey."""
        return self.transport.call(
            "Talk.TalkService.getE2EEPublicKey", [mid, int(key_version), int(key_id)]
        )

    def negotiate_e2ee_public_key(self, mid: str) -> Any:
        """``negotiateE2EEPublicKey(mid)`` ->
        ``{publicKey, allowedTypes, specVersion}``."""
        return self.transport.call("Talk.TalkService.negotiateE2EEPublicKey", [mid])

    def get_e2ee_public_keys_ex(
        self, ignore_group_key: bool = False, sync_reason: int = int(SyncReason.UNKNOWN)
    ) -> Any:
        """``getE2EEPublicKeysEx(ignoreE2EEGroupKey, syncReason)`` ->
        list of the caller's own public keys."""
        return self.transport.call(
            "Talk.TalkService.getE2EEPublicKeysEx", [bool(ignore_group_key), sync_reason]
        )

    def get_last_e2ee_public_keys(self, chat_mid: str) -> Any:
        """``getLastE2EEPublicKeys(chatMid)`` -> ``{mid: E2EEPublicKey}``."""
        return self.transport.call("Talk.TalkService.getLastE2EEPublicKeys", [chat_mid])

    def register_e2ee_group_key(
        self,
        chat_mid: str,
        members: Iterable[str],
        key_ids: Iterable[int],
        encrypted_shared_keys: Iterable[str],
        version: int = 1,
    ) -> Any:
        """``registerE2EEGroupKey(version, chatMid, members, keyIds,
        encryptedSharedKeys)``."""
        return self.transport.call(
            "Talk.TalkService.registerE2EEGroupKey",
            [
                int(version),
                chat_mid,
                list(members),
                [int(k) for k in key_ids],
                list(encrypted_shared_keys),
            ],
        )

    def get_e2ee_group_shared_key(
        self, chat_mid: str, group_key_id: int, version: int = 1
    ) -> Any:
        """``getE2EEGroupSharedKey(version, chatMid, groupKeyId)``."""
        return self.transport.call(
            "Talk.TalkService.getE2EEGroupSharedKey",
            [int(version), chat_mid, int(group_key_id)],
        )

    def get_last_e2ee_group_shared_key(self, chat_mid: str, version: int = 1) -> Any:
        """``getLastE2EEGroupSharedKey(version, chatMid)``."""
        return self.transport.call(
            "Talk.TalkService.getLastE2EEGroupSharedKey", [int(version), chat_mid]
        )

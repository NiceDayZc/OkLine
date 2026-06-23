"""X-Hmac request signer.

The LINE Chrome gateway rejects every request that is missing a valid
``X-Hmac`` header (error ``REQUEST_INVALID_HMAC`` / 10005).  The signature is
produced by LINE's secure WASM module (``ltsm.wasm``); the key derivation and
HMAC are implemented inside that binary, so the only reliable way to reproduce
it is to *run the real module*.

:class:`HmacSigner` launches a small persistent Node.js bridge
(``ltsm/ltsm_bridge.js``) that loads ``ltsm.wasm`` and computes the signature
exactly as the extension does::

    X-Hmac = base64( Hmac(deriveKey(SHA256("3.7.2"), SHA256(accessToken)))
                       .digest(path + body) )

Requires Node.js on ``PATH`` (or set ``LINE_NODE`` / pass ``node_path``).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from .exceptions import LineError

_LTSM_DIR = Path(__file__).resolve().parent / "ltsm"
_BRIDGE_JS = _LTSM_DIR / "ltsm_bridge.js"


class HmacSignerError(LineError):
    """Raised when the LTSM bridge cannot be started or a command fails."""


class LtsmBridge:
    """Persistent Node bridge running LINE's real LTSM WASM module.

    A single shared instance serves both the per-request ``X-Hmac`` signing and
    the Curve25519 / E2EE-keychain operations needed for QR login (the curve
    key handle lives inside the one WASM instance, so the same process must be
    reused across calls).

    Thread-safe: one lock serialises the request/response round-trips.  The
    Node process is started lazily on first use and reused for the lifetime of
    the bridge.
    """

    def __init__(
        self,
        node_path: str | None = None,
        origin: str | None = None,
        start_timeout: float = 60.0,
    ) -> None:
        self.node_path = node_path or os.environ.get("LINE_NODE") or "node"
        self.origin = origin or os.environ.get("LTSM_ORIGIN")
        self.start_timeout = start_timeout
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._id = 0

    # -- lifecycle -----------------------------------------------------------
    def _ensure_started(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        if not _BRIDGE_JS.exists():
            raise HmacSignerError(f"bridge script missing: {_BRIDGE_JS}")
        env = dict(os.environ)
        if self.origin:
            env["LTSM_ORIGIN"] = self.origin
        try:
            self._proc = subprocess.Popen(
                [self.node_path, str(_BRIDGE_JS)],
                cwd=str(_LTSM_DIR),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
            )
        except FileNotFoundError as exc:
            raise HmacSignerError(
                f"Node.js not found ({self.node_path!r}). Install Node 18+ or set "
                f"LINE_NODE / node_path. X-Hmac signing is required by the gateway."
            ) from exc

        # wait for the readiness line
        ready_line = self._readline()
        try:
            msg = json.loads(ready_line)
        except (TypeError, ValueError) as exc:
            raise HmacSignerError(f"bridge did not start: {ready_line!r}") from exc
        if not msg.get("ready"):
            raise HmacSignerError(f"bridge init failed: {msg.get('error')}")

    def _readline(self) -> str:
        assert self._proc and self._proc.stdout
        # readline() blocks; the bridge always responds promptly so we rely on
        # the OS pipe.  (A watchdog could be added if needed.)
        line = self._proc.stdout.readline()
        if line == "":
            raise HmacSignerError("bridge process exited unexpectedly")
        return line.strip()

    # -- generic command -----------------------------------------------------
    def _call(self, op: str, **fields: Any) -> Any:
        with self._lock:
            self._ensure_started()
            assert self._proc and self._proc.stdin
            self._id += 1
            rid = self._id
            req = json.dumps({"id": rid, "op": op, **fields})
            try:
                self._proc.stdin.write(req + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError) as exc:
                raise HmacSignerError(f"bridge write failed: {exc}") from exc
            line = self._readline()
            try:
                resp = json.loads(line)
            except ValueError as exc:
                raise HmacSignerError(f"bad bridge response: {line!r}") from exc
            if resp.get("error"):
                raise HmacSignerError(f"{op} failed: {resp['error']}")
            return resp.get("result")

    # -- X-Hmac --------------------------------------------------------------
    def sign(self, access_token: str, path: str, body: str = "") -> str:
        """Return the ``X-Hmac`` value for a request.

        ``path`` is the request path (everything after the gateway base, incl.
        any query string); ``body`` is the exact serialized request body (empty
        string for GET / bodyless requests).
        """
        return self._call("hmac", accessToken=access_token or "", path=path, body=body or "")

    # -- Curve25519 / E2EE (QR login) ----------------------------------------
    def curvekey_generate(self) -> int:
        """Generate a Curve25519 keypair in the WASM; returns its handle id."""
        return self._call("curvekey_generate")

    def e2ee_public_key(self, key_id: int) -> str:
        """Base64 public key for a previously generated curve key handle."""
        return self._call("e2ee_public_key", keyId=key_id)

    def e2ee_create_channel(self, key_id: int, server_pubkey_b64: str) -> int:
        """Create an E2EE channel between our curve key and a peer public key."""
        return self._call(
            "e2ee_create_channel", keyId=key_id, serverPubKeyB64=server_pubkey_b64
        )

    def e2ee_unwrap_keychain(self, channel_id: int, enc_keychain_b64: str) -> Any:
        """Unwrap the encrypted E2EE keychain returned by qrCodeLoginV2.

        Returns a list of *our* unwrapped E2EE key handles (numbers)."""
        return self._call(
            "e2ee_unwrap_keychain", channelId=channel_id, encKeyChainB64=enc_keychain_b64
        )

    def e2ee_get_key_id(self, key_handle: int) -> int:
        """Numeric keyId of one of our unwrapped E2EE key handles."""
        return self._call("e2ee_get_key_id", keyHandle=key_handle)

    def e2ee_public_key_for_handle(self, key_handle: int) -> str:
        return self._call("e2ee_public_key_for_handle", keyHandle=key_handle)

    def e2ee_create_channel_with_pubkey(self, key_handle: int, peer_pubkey_b64: str) -> int:
        """Channel between our key handle and a peer's public key."""
        return self._call(
            "e2ee_create_channel_with_pubkey",
            keyHandle=key_handle,
            peerPubKeyB64=peer_pubkey_b64,
        )

    def e2ee_encrypt_v2(
        self,
        channel_id: int,
        *,
        to: str,
        frm: str,
        sender_key_id: int,
        receiver_key_id: int,
        content_type: int,
        sequence_number: int,
        plaintext_b64: str,
    ) -> str:
        """Encrypt (V2) -> base64 ciphertext."""
        return self._call(
            "e2ee_encrypt_v2",
            channelId=channel_id,
            to=to,
            **{"from": frm},
            senderKeyId=sender_key_id,
            receiverKeyId=receiver_key_id,
            contentType=content_type,
            sequenceNumber=sequence_number,
            plaintextB64=plaintext_b64,
        )

    def e2ee_decrypt_v2(
        self,
        channel_id: int,
        *,
        to: str,
        frm: str,
        sender_key_id: int,
        receiver_key_id: int,
        content_type: int,
        ciphertext_b64: str,
    ) -> str:
        """Decrypt (V2) -> base64 plaintext."""
        return self._call(
            "e2ee_decrypt_v2",
            channelId=channel_id,
            to=to,
            **{"from": frm},
            senderKeyId=sender_key_id,
            receiverKeyId=receiver_key_id,
            contentType=content_type,
            ciphertextB64=ciphertext_b64,
        )

    def e2ee_decrypt_v1(self, channel_id: int, *, ciphertext_b64: str) -> str:
        """Decrypt (V1) -> base64 plaintext.

        The old Letter-Sealing format: ``e2eeChannelDecryptV1(channel, ciphertext)``
        takes only the channel + ciphertext (no to/from/keyIds/contentType — those
        are not part of the V1 AAD).
        """
        return self._call(
            "e2ee_decrypt_v1", channelId=channel_id, ciphertextB64=ciphertext_b64
        )

    # -- cross-session key persistence --------------------------------------
    def e2ee_export_key(self, key_handle: int) -> str:
        """Serialize a private-key handle (``E2EEKey.exportKey()``) -> base64."""
        return self._call("e2ee_export_key", keyHandle=key_handle)

    def e2ee_load_key(self, exported_b64: str) -> int:
        """Re-import an exported key blob -> a fresh key handle."""
        return self._call("e2ee_load_key", exportedB64=exported_b64)

    # -- group Letter Sealing -----------------------------------------------
    def e2ee_unwrap_group_shared_key(self, channel_id: int, *, enc_shared_key_b64: str) -> int:
        """Unwrap a group's encrypted shared key -> a group-key handle.

        ``channel_id`` must be a channel built from *my* key handle + the group
        creator's public key (``e2ee_create_channel_with_pubkey``).
        """
        return self._call(
            "e2ee_unwrap_group_shared_key",
            channelId=channel_id,
            encSharedKeyB64=enc_shared_key_b64,
        )

    # -- teardown ------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.stdin.close()  # type: ignore[union-attr]
                except Exception:
                    pass
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            self._proc = None

    def __del__(self) -> None:  # best-effort cleanup
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def is_available(node_path: str | None = None) -> bool:
        """True if Node.js and the bridge artifacts are present."""
        node = node_path or os.environ.get("LINE_NODE") or "node"
        if not _BRIDGE_JS.exists() or not (_LTSM_DIR / "ltsm.wasm").exists():
            return False
        try:
            subprocess.run([node, "--version"], capture_output=True, timeout=10)
            return True
        except Exception:
            return False


# Backwards-compatible name: the bridge started life as an HMAC-only signer.
HmacSigner = LtsmBridge

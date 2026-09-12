"""The main :class:`OkLine` facade.

It wires together the transport, the auth flows, the operation receiver, the
OBS client and the response recorder, and mixes in one typed method per Thrift
endpoint (see ``services/``).  Every endpoint is *also* reachable generically
via :meth:`OkLine.call`, and every call can be recorded and pasted in full
detail via :pyattr:`OkLine.last` / :meth:`OkLine.dump`.
"""

from __future__ import annotations

import logging
import random
import sys
import threading
import time
from typing import Any, Callable

from ._util import reconfigure_stdout_utf8
from .auth import AuthFlows, LoginResult
from .obs import ObsClient
from .operations import OperationReceiver
from .recorder import Exchange, Recorder
from .services import AllServices
from .transport import LineConfig, Tokens, Transport

log = logging.getLogger("okline")


class OkLine(AllServices):
    """A complete, high-level LINE Chrome (CHROMEOS 3.7.2) API client.

    Examples
    --------
    Log in and send a message::

        api = OkLine()
        if api.auth.email_login("me@example.com", "secret").success:
            api.send_text("uXXXX...", "hello from python")

    Re-use a token and paste every response::

        api = OkLine(access_token="...", refresh_token="...")
        api.get_profile()
        print(api.last.pretty())          # the last exchange
        print(api.dump())                 # everything this session
        api.save_log("session.har", fmt="har")

    Parameters
    ----------
    record:
        Capture every request/response (default ``True``).  Access via
        :pyattr:`history` / :pyattr:`last`, format via :meth:`dump`.
    redact:
        Mask secrets (tokens, X-Hmac, passwords) in recorded output
        (default ``True``).  Pass ``redact=False`` to reveal them.
    on_exchange:
        Optional callback invoked with each :class:`Exchange` as it completes.
    auto_refresh_schedule:
        Opt in to the extension's proactive token renewal (its ``tT`` class,
        main.js @~1850300): a daemon :class:`threading.Timer` is armed to
        fire at the absolute epoch ``tokenIssueTimeEpochSec +
        durationUntilRefreshInSec`` after every login / token refresh, and
        re-armed after each renewal.  A failed silent renewal only logs —
        the old token keeps working until a 119/401 triggers the defensive
        refresh path.  Cancelled by :meth:`close` /
        :meth:`cancel_refresh_schedule`.
    """

    def __init__(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        certificate: str | None = None,
        mid: str | None = None,
        config: LineConfig | None = None,
        transport: Transport | None = None,
        record: bool = True,
        record_capacity: int = 500,
        redact: bool = True,
        on_exchange: Callable[[Exchange], None] | None = None,
        auto_refresh_schedule: bool = False,
    ) -> None:
        tokens = Tokens(
            access_token=access_token,
            refresh_token=refresh_token,
            certificate=certificate,
            mid=mid,
        )
        self.transport = transport or Transport(config or LineConfig(), tokens)
        self.auth = AuthFlows(self.transport)
        self.ops = OperationReceiver(self.transport)
        self.obs = ObsClient(self.transport)
        from .e2ee import E2EEManager

        self.e2ee = E2EEManager(self)  # Letter Sealing (ready after qr_login)
        self._reqseq = 0

        # Response recording.
        self.recorder: Recorder | None = (
            Recorder(capacity=record_capacity, redact=redact) if record else None
        )
        self.transport.recorder = self.recorder
        if on_exchange:
            self.transport.hooks.append(on_exchange)

        # When loaded from a session file, persist refreshed tokens back to it.
        self._session_path: str | None = None

        # Proactive token renewal (the extension's tT class): a daemon
        # threading.Timer armed after every token issuance, cancelled on
        # close().  Opt-in — see the class docstring.
        self._refresh_schedule_active = bool(auto_refresh_schedule)
        self._refresh_timer: threading.Timer | None = None
        self._refresh_timer_lock = threading.Lock()
        if self._refresh_schedule_active:
            self.auth.on_token_issued = self._on_token_issued

        # Single-flight guard for the token-refresh path: the renewal Timer,
        # the SSE keepalive ping thread and user threads can all reach
        # refresh_access_token around the same token-expiry boundary.  The
        # extension's tT class runs on the single-threaded JS event loop and
        # cannot race itself; the port's threads need the lock so two
        # concurrent tokenRefresh POSTs cannot interleave (see
        # _refresh_tokens).
        self._refresh_lock = threading.Lock()

        # Auto-refresh the access token on a 401 if we hold a refresh token.
        self.transport._refresh_hook = self._auto_refresh

    # -- credential helpers --------------------------------------------------
    @property
    def tokens(self) -> Tokens:
        return self.transport.tokens

    @property
    def config(self) -> LineConfig:
        return self.transport.config

    def set_access_token(self, token: str) -> None:
        self.transport.tokens.access_token = token

    def _auto_refresh(self) -> bool:
        if not self.transport.tokens.refresh_token:
            return False
        try:
            self._refresh_tokens(self.transport.tokens.access_token)
        except Exception as exc:  # pragma: no cover - network
            log.warning("token refresh failed: %s", exc)
            return False
        log.info("access token refreshed")
        return True

    def _refresh_tokens(self, stale_access_token: str | None) -> None:
        """Single-flight token refresh, shared by :meth:`_auto_refresh` and
        :meth:`_scheduled_renew`.

        The renewal ``Timer`` fires at exactly the token-expiry boundary
        where in-flight calls also start failing with 119/401, so both paths
        can reach ``refresh_access_token`` concurrently.  The lock serializes
        them and the stale-token check skips the second POST: a caller that
        waited on the lock while another thread completed a refresh sees the
        token already rotated and adopts that refresh's outcome — the slower
        of two concurrent responses would otherwise overwrite the newer
        rotated pair, and a single-used refresh token would fail with a
        spurious 10201 hard-kickout.  Raises on refresh failure (the callers
        log and keep the old token)."""
        with self._refresh_lock:
            if (
                stale_access_token is not None
                and self.transport.tokens.access_token != stale_access_token
            ):
                log.debug("token already refreshed by a concurrent call")
                return
            self.auth.refresh_access_token()
            if self._session_path:  # persist the new token
                self.save_tokens(self._session_path)

    # -- proactive token-renewal schedule (the extension's tT class) ---------
    def _on_token_issued(self, schedule: dict) -> None:
        """on_token_issued hook: (re-)arm the renewal timer for a fresh
        tokenV3IssueResult schedule.  Must never raise (it runs inside login
        and refresh call sites)."""
        if not self._refresh_schedule_active:
            return
        try:
            self._schedule_token_refresh(schedule)
        except Exception:  # pragma: no cover - scheduling must never break a login
            log.warning("proactive token-refresh scheduling failed", exc_info=True)

    def _schedule_token_refresh(self, schedule: dict) -> None:
        """Arm the one-shot renewal timer to fire at the absolute epoch
        ``tokenIssueTimeEpochSec + durationUntilRefreshInSec`` — the
        extension's ``setTokenV3IssueResult`` (main.js @~1850660) computes a
        due-epoch-minus-now delay: ``setTimeout(renewToken, (issue +
        duration) * 1000 - RI(1))``, where ``RI(1)`` is its server-clock-
        aligned *current* time in ms (ceiled), not a jitter term.  The port
        approximates the same due-minus-now delay on the local clock
        (``fire_at - time.time()``) plus a random 0-1 s early margin that is
        a port-side addition, not in the bundle.  Any previously armed timer
        is cancelled first."""
        issue = schedule.get("tokenIssueTimeEpochSec")
        duration = schedule.get("durationUntilRefreshInSec")
        if issue is None or duration is None:
            return
        fire_at = float(issue) + float(duration)  # epoch seconds
        delay = fire_at - time.time() - random.uniform(0.0, 1.0)
        timer = threading.Timer(max(delay, 0.0), self._scheduled_renew)
        timer.daemon = True
        with self._refresh_timer_lock:
            if self._refresh_timer is not None:
                self._refresh_timer.cancel()
            self._refresh_timer = timer
        timer.start()
        log.debug(
            "proactive token renewal armed in %.1fs (fires at %d)",
            max(delay, 0.0),
            int(fire_at),
        )

    def _scheduled_renew(self) -> None:
        """Timer body: silently renew the token.  Never raises — a failed
        renewal only logs, leaving the old token working until a 119/401
        triggers the defensive refresh path in :meth:`_auto_refresh`."""
        if not self._refresh_schedule_active:
            return  # cancelled (e.g. close()) between arming and firing
        try:
            self._refresh_tokens(self.transport.tokens.access_token)
        except Exception:  # pragma: no cover - network
            log.warning(
                "scheduled token renewal failed; keeping the old token "
                "(the 119/401 defensive refresh path still applies)",
                exc_info=True,
            )
            return
        log.info("access token renewed by the background schedule")
        # The extension's tT.renewToken reconnects its operation stream
        # after a successful renewal when it was open (``sdk.readyState
        # === ReadyState.OPENED && t.connect()``): tear the active SSE
        # connection down so the stream loop reopens with the new token.
        self.ops.request_reconnect()

    def cancel_refresh_schedule(self) -> None:
        """Cancel the armed background token-renewal timer (if any) and
        deactivate the schedule — no new timers are armed afterwards."""
        self._refresh_schedule_active = False
        with self._refresh_timer_lock:
            if self._refresh_timer is not None:
                self._refresh_timer.cancel()
                self._refresh_timer = None

    # -- session persistence -------------------------------------------------
    def save_tokens(self, path: str | None = None) -> None:
        """Save the current credentials to a JSON session file.

        If E2EE is active (after ``qr_login``), the unwrapped keychain is exported
        into the file too, so Letter Sealing keeps working across runs without a
        fresh QR login.
        """
        from .session import Session

        path = path or self._session_path
        if not path:
            raise ValueError("no path given and no session file attached")
        self._session_path = path
        # Session.from_tokens carries the renewal schedule from the Tokens
        # dataclass (maintained by AuthFlows._set_token_schedule on every
        # login/refresh), so from_tokens_file(+auto_refresh_schedule=True)
        # can re-arm the timer without a fresh login.
        s = Session.from_tokens(self.transport.tokens)
        try:
            if self.e2ee.is_ready():
                s.e2ee = self.e2ee.export_keys()
        except Exception as exc:  # pragma: no cover - e2ee optional
            log.warning("E2EE export for session failed (non-fatal): %s", exc)
        s.save(path)

    @classmethod
    def from_tokens_file(cls, path: str, **kw: Any) -> OkLine:
        """Build a client from a session file (and auto-save refreshed tokens).

        Restores the E2EE keychain from the file if present, so Letter Sealing is
        available without re-scanning a QR code.
        """
        from .session import Session

        s = Session.load(path)
        api = cls(
            access_token=s.access_token,
            refresh_token=s.refresh_token,
            certificate=s.certificate,
            mid=s.mid,
            **kw,
        )
        api._session_path = path
        if (
            s.token_issue_time_epoch_sec is not None
            and s.duration_until_refresh_sec is not None
        ):
            # Restore the renewal schedule recorded by save_tokens, so the
            # proactive renewal (auto_refresh_schedule=True) can be armed
            # right away — the extension does the same on startup from its
            # persisted tokenV3IssueResult.  _set_token_schedule records it
            # on AuthFlows/Tokens *and* fires the on_token_issued hook.
            api.auth._set_token_schedule(
                s.token_issue_time_epoch_sec,
                s.duration_until_refresh_sec,
                s.refresh_api_retry_policy,
            )
        if s.e2ee:
            try:
                api.e2ee.load_from_export(s.e2ee)
            except Exception as exc:  # pragma: no cover - e2ee optional
                log.warning("E2EE restore from session failed (non-fatal): %s", exc)
        return api

    def next_req_seq(self) -> int:
        self._reqseq += 1
        return self._reqseq

    # -- generic escape hatch ------------------------------------------------
    def call(self, endpoint_key: str, *args: Any, **kw: Any) -> Any:
        """Invoke any registered endpoint by ``Namespace.Service.method`` key.

        ``api.call("Talk.TalkService.getProfile", 0)`` == ``api.get_profile()``.
        """
        return self.transport.call(endpoint_key, list(args), **kw)

    # -- recording / "paste resp" -------------------------------------------
    @property
    def history(self) -> list[Exchange]:
        """Every recorded :class:`Exchange` this session (newest last)."""
        return self.recorder.entries if self.recorder else []

    @property
    def last(self) -> Exchange | None:
        """The most recent recorded :class:`Exchange` (or ``None``)."""
        return self.recorder.last if self.recorder else None

    def on_exchange(self, hook: Callable[[Exchange], None]) -> Callable:
        """Register a per-exchange callback (also usable as a decorator)."""
        self.transport.hooks.append(hook)
        return hook

    def print_last(self, *, redact: bool | None = None, file=None) -> None:
        """Print the last exchange as an HTTP transcript."""
        if not self.last:
            return
        r = self.recorder.redact if redact is None else redact  # type: ignore[union-attr]
        _safe_print(self.last.pretty(redact=r), file)

    def dump(self, *, redact: bool | None = None) -> str:
        """Return every recorded exchange as one big transcript string."""
        return self.recorder.dump_text(redact=redact) if self.recorder else ""

    def save_log(self, path: str, *, fmt: str = "text", redact: bool | None = None) -> None:
        """Save the session log. ``fmt`` is ``"text"``, ``"json"`` or ``"har"``."""
        if self.recorder:
            self.recorder.save(path, fmt=fmt, redact=redact)

    def clear_log(self) -> None:
        if self.recorder:
            self.recorder.clear()

    # -- convenience login passthroughs --------------------------------------
    def login_with_email(self, email: str, password: str, **kw: Any) -> LoginResult:
        return self.auth.email_login(email, password, **kw)

    def qr_login(self, **kw: Any) -> LoginResult:
        """QR login, then load our E2EE (Letter Sealing) keys for this session."""
        result = self.auth.qr_login(**kw)
        info = getattr(self.auth, "last_e2ee_login", None)
        if info:
            try:
                self.e2ee.load_from_login(info["curve_key_id"], info["metadata"])
            except Exception as exc:  # pragma: no cover - e2ee optional
                log.warning("E2EE init failed (non-fatal): %s", exc)
        return result

    # -- E2EE (Letter Sealing) ----------------------------------------------
    def decrypt_message(self, message: dict) -> dict:
        """Decrypt a received Letter-Sealed message (needs E2EE keys from
        :meth:`qr_login` this session). Returns the message with plaintext
        ``text``; non-sealed messages are returned unchanged."""
        from .e2ee_crypto import is_e2ee_message

        if not is_e2ee_message(message):
            return message
        return self.e2ee.decrypt(message)

    def send_encrypted_text(self, to: str, text: str, **kw: Any) -> Any:
        """Send a Letter-Sealed text message (1:1)."""
        from .models import Message as _M

        return self.send_message(_M.text(to, text, **kw), encrypt=True)

    # -- media send (V1 / non-E2EE) -----------------------------------------
    def _send_media(
        self,
        to: str,
        data: bytes,
        content_type: int,
        *,
        name: str,
        obs_type: str,
        cat: str | None = None,
        duration_ms: int = 0,
    ) -> Any:
        """Send a media message: post a placeholder, then upload the bytes to
        OBS at ``/r/talk/m/<messageId>`` (the V1, non-E2EE flow).

        Experimental — works for non-Letter-Sealed chats.
        """
        from .enums import ContentType, EncryptedAccessTokenFeatureType
        from .exceptions import LineApiError
        from .models import Message as _M

        ct = int(content_type)
        if ct == int(ContentType.IMAGE):
            msg = _M.image(to)
        elif ct == int(ContentType.VIDEO):
            msg = _M.video(to, duration_ms)
        elif ct == int(ContentType.AUDIO):
            msg = _M.audio(to, duration_ms)
        else:
            msg = _M.file(to, name, len(data))
        sent = self.send_message(msg)
        msg_id = sent.get("id") if isinstance(sent, dict) else None
        if not msg_id:
            raise LineApiError("sendMessage returned no message id for media", raw=sent)
        enc = self.get_encrypted_access_token(int(EncryptedAccessTokenFeatureType.OBS_GENERAL))
        self.obs.upload_message_object(
            str(msg_id), data, name=name, obs_type=obs_type, cat=cat, enc_token=enc
        )
        return sent

    def send_image(self, to: str, file: Any, *, name: str | None = None) -> Any:
        data, name = _read_media(file, name, "image.jpg")
        return self._send_media(
            to, data, _ct().IMAGE, name=name, obs_type="image", cat="original"
        )

    def send_video(
        self, to: str, file: Any, *, name: str | None = None, duration_ms: int = 0
    ) -> Any:
        data, name = _read_media(file, name, "video.mp4")
        return self._send_media(
            to, data, _ct().VIDEO, name=name, obs_type="video", duration_ms=duration_ms
        )

    def send_audio(
        self, to: str, file: Any, *, name: str | None = None, duration_ms: int = 0
    ) -> Any:
        data, name = _read_media(file, name, "audio.m4a")
        return self._send_media(
            to, data, _ct().AUDIO, name=name, obs_type="audio", duration_ms=duration_ms
        )

    def send_file(self, to: str, file: Any, *, name: str | None = None) -> Any:
        data, name = _read_media(file, name, "file.bin")
        return self._send_media(to, data, _ct().FILE, name=name, obs_type="file")

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        """Release the LTSM Node bridge subprocess (if started) and cancel
        the background token-renewal timer (if armed)."""
        self.cancel_refresh_schedule()
        signer = getattr(self.transport, "_signer", None)
        if signer is not None:
            signer.close()

    def __enter__(self) -> OkLine:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        who = self.transport.tokens.mid or "anonymous"
        n = len(self.history)
        return (
            f"<OkLine {who} app={self.config.application_header.split(chr(9))[0]} calls={n}>"
        )


def _ct():
    from .enums import ContentType

    return ContentType


def _read_media(file: Any, name: str | None, default: str):
    """Accept a path (str/Path) or raw bytes; return (data, name)."""
    import os

    if isinstance(file, (bytes, bytearray)):
        return bytes(file), (name or default)
    path = os.fspath(file)
    with open(path, "rb") as fh:
        data = fh.read()
    return data, (name or os.path.basename(path) or default)


def _safe_print(text: str, file=None) -> None:
    """Print possibly-non-ASCII text without dying on a cp1252 Windows console."""
    stream = file or sys.stdout
    reconfigure_stdout_utf8(stream)
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", "ascii") or "ascii"
        print(text.encode(enc, "replace").decode(enc), file=stream)


# Backwards-compatible alias (the library used to be called LineApi).
LineApi = OkLine

# Changelog

All notable changes to OkLine are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [2.9.2] - 2026-09-13

Group-A live-test follow-up (every fix below was found by exercising the
live servers and is live-verified):

- **`tokenRefresh` actually works now** — two bugs cancelled each other
  out only in offline tests: (1) the request must carry the (possibly
  expired) access token as `X-Line-Access` like the extension's gateway
  headerMapper does (without it the endpoint answers 10004
  REQUEST_NEED_LOGIN forever — live-tested); (2) a 401 from tokenRefresh
  itself must never fire the refresh hook — that recursion looped
  tokenRefresh endlessly on a consumed refresh token (live-tested).
  Verified live: refresh rotates the refresh token, the new pair keeps
  working, and the schedule round-trips through the session file.
- **`send_location` auto-seal on code 82** — the re-seal fallback only
  matched contentType 0 (text); location (sealable per the extension's
  sL whitelist) was rejected by sealed chats and never re-sent. Now uses
  `SEALABLE_CONTENT_TYPES` (live-tested: location to self-chat sends).
- **`lan_notice` boolean serialization** — `includeBody` urlencoded as
  Python `True` fails the endpoint's JSON-schema validation (10003);
  it now serializes lowercase `true` (live-tested 200 + documents).
- Live-verified this round: owned-sticker send (after
  `get_owned_product_summaries` with the account's region), contact /
  file / audio / video sends, timeline homeId + getCover (needs the
  channel token issued first), myhome cover download via the OBS
  channel-token branch (132 KB JPEG), `chatlog` CLI decrypting sealed
  history, and op-stream delivery (SEND_REACTION observed live).
- Known limitation (not a bug): FLEX sends are rejected with code 11
  "Incompatible app version" — the Chrome extension itself never sends
  FLEX (it only renders it); flex sending is bot/official-account-only.

## [2.9.1] - 2026-09-13

Live-testing follow-up (everything below was found by exercising 2.9.0
against the real gateway and is live-verified):

- **`E2EEManager.download_sealed_media(message, *, info=False)`** — the
  extension's `$P`/`GD` sealed-media receive flow as one call: decrypt the
  message (restores `ENC_KM`), fetch the object from `/r/talk/<SID>/<OID>`
  with the `X-Talk-Meta` header, decrypt the blob with the HKDF
  `FileEncryption` keys. `info=True` also returns the `object_info.obs`
  dict (name/mime/size). Live-verified end-to-end against a real sealed
  image. `ObsClient.object_info` gained the same `message_id=` parameter
  `download_object` has (E2EE media needs `X-Talk-Meta` on the info query
  too).
- **Internal retry/refresh markers no longer escape** — after the retry
  budget ran out, outer-99999 errors surfaced to callers as the internal
  `_RetryableApiError` subclass (and 119-without-refresh as
  `_MustRefreshTokenError`); both now surface as plain `LineApiError` /
  `LineAuthError` as documented (live: a `determineMediaMessageFlow` 99999
  leaked the subclass). Tests assert on the exact type, not `isinstance`.
- Tests: 699 passing (live bridge suite self-skips without Node).

## [2.9.0] - 2026-09-12

A **parity-completion release**: the four items 2.8.0 listed as *not ported*
are now ported from the live 3.7.2 bundle — all additive, and opt-in wherever
they introduce background behaviour.

### Token-refresh lifecycle (the extension's `tT` class, main.js @~1850300)
- **Proactive renewal schedule** — `OkLine(..., auto_refresh_schedule=True)`
  arms a daemon `threading.Timer` to fire at the absolute epoch
  `tokenIssueTimeEpochSec + durationUntilRefreshInSec` after every login and
  every tokenRefresh response (a due-epoch-minus-now delay, like the
  extension's `setTimeout(renewToken, (issue + duration) * 1000 - RI(1))`
  where `RI(1)` is its server-clock-aligned now), re-arms itself after each
  renewal, persists through the session file (camelCase keys; pre-2.9 files
  still load), and is cancelled by `close()` / `cancel_refresh_schedule()`
  (with a stale-fire guard). A schedule-less issuance *clears* any previously
  stored schedule (the extension's clear step), so no stale retry policy or
  fire time survives into the next refresh or the session file. A failed
  silent renewal only logs — the old token keeps working until the reactive
  119/401 defensive path fires. The defensive refresh and the scheduled
  renewal share a single-flight lock: a caller arriving mid-refresh waits,
  sees the already-rotated token and never issues a second `tokenRefresh`
  POST (the extension's single-threaded `tT` cannot race itself; the port's
  Timer/ping threads can). `Session.save` is now atomic (temp file +
  `os.replace`), so concurrent token persistence cannot corrupt the file.
- **SSE reconnect after renewal** — a successful scheduled renewal tears the
  active operation stream down and reopens it with the fresh token
  (`OperationReceiver.request_reconnect()`, mirroring the extension's
  `readyState === ReadyState.OPENED && t.connect()`); a no-op when no
  reconnecting stream is active.
- **AUTH_RETRY_REQUIRED (10202) retry policy** — `refresh_access_token()`
  parses the stored `refreshApiRetryPolicy` (new exported
  `RefreshApiRetryPolicy` dataclass; sane defaults for the bundle's
  empty-string/zero placeholders) and retries 10202 with jittered
  exponential backoff (`initialDelayInMillis * multiplier**n`, each sleep
  jittered by `jitterRate`, capped at `maxDelayInMillis` — the tT.renewToken
  loop, attempt for attempt; the sleeps are blocking). Both delays are
  clamped client-side at `REFRESH_RETRY_DELAY_CAP_MS` (60 s): unlike the
  extension's async promise, our sleeps block the calling thread, so the
  server cannot park it arbitrarily. Without a stored
  policy the pre-2.9 single-attempt behaviour applies.
- **AUTH_INVALID_REQUEST (10201) hard kickout** — surfaces as
  `LineAuthError("token refresh rejected (AUTH_INVALID_REQUEST): re-login
  required")`, terminal.
- The 10201/10202 gateway-envelope constants now live once in
  `transport.py`'s `qU`-enum block (imported by `auth.py`): they are *not*
  TalkException codes, so they are deliberately absent from
  `enums.ErrorCode` and `exceptions._AUTH_CODES` — the talk-auth interceptor
  never classifies them. The renewal schedule is mirrored onto the `Tokens`
  dataclass (maintained by `AuthFlows._set_token_schedule`), which is how
  `Session.from_tokens` persists it.

### Talk-auth interceptor opt-outs + path scope (per-request)
- `Transport.call/post_json(..., ignore_auth_exception=True)` mirrors the
  extension's `ignoreTalkAuthException`; `ignore_must_upgrade=True` mirrors
  `ignoreMustUpgrade`. Both flags survive the 401- and 119-refresh replays
  (the extension replays `r(e.config)` with the same config).
- Talk-auth classification (inner codes 1/7/8 → `LineAuthError`, and the 119
  renew-and-retry) is now **path-scoped** exactly like the bundle's
  interceptor: `/api/talk/thrift/Talk*` URLs minus the `Talk/ChannelService`
  and `Talk/E2EEKeyBackupService` sub-services; outside that scope those
  codes raise plain `LineApiError` and 119 never renews.
- Per bundle evidence the must-upgrade interceptor has **no** URL check (only
  the flag), so `LineMustUpgradeError` stays unscoped on all paths;
  `ignoreGlobalAlert` gates a UI-alert interceptor and has no analogue.

### SSE keepalive + reconnect backoff
- `stream() / iter_operations(..., keepalive=True)` (opt-in, default off): a
  daemon thread pings `Talk.TalkService.getServerTime` every ~20 s while the
  generator is active (the PingInterceptor's `2e4` ms interval), stopped on
  close/exhaustion, surviving ping failures — and the interceptor's *silence
  watchdog* is armed as a per-read socket timeout of interval + spare (30 s)
  on the streamed GET, so a quietly dead connection (NAT/middlebox timeout,
  no FIN) raises out of `iter_lines` and is reopened with the `localRev`
  cursor, exactly the interceptor's reopen. `Bot.run(keepalive=True)` and
  `example.py` pass it through.
- Reconnect backoff ported from the extension's `sT.connect`
  (`min(2**a * 1e3, 6e5)` ms): consecutive failed connections wait
  `min(2**n * backoff_start, backoff_max)` seconds (defaults 1 s → 60 s;
  `backoff_max=600` for exact bundle parity, `backoff_start=0` restores the
  pre-2.9 immediate reconnect), while a connection that yielded at least one
  event resets the counter and reconnects immediately.

### Residue (deliberate, bundle-verified deviations)
- The keepalive pings are `getServerTime` requests rather than EventSource
  ping frames (`requests` has no such frame); the interceptor's
  silence-watchdog half *is* ported, via the per-read socket timeout above.
- The reconnect backoff retries forever; the bundle gives up after 144
  consecutive attempts.
- HTTP 401/403 → `LineAuthError` remains a port-level convenience on all
  paths (the extension's interceptors never inspect HTTP status), as does
  SSE header auth.

Docs: authentication (schedule + retry policy), receiving-events (backoff,
keepalive, mid-stream renewal), architecture (token-refresh lifecycle),
troubleshooting (10201/10202). 691 tests, all offline (the 16 real-bridge
tests in `test_hmac_bridge.py` skip, and the count drops to 675, when
Node.js is unavailable).

## [2.8.0] - 2026-09-12

A **drift-fix release**: the core of every surviving finding from a full
re-audit against the live extension bundle (3.7.2 `main.js`, byte-exact
re-extraction at every offset) has been fixed. Explicitly **not ported** at
the time (all four items were subsequently ported in [2.9.0]; documented
deviations, verified against the bundle): the extension's
*proactive* token-refresh scheduler (`setTimeout(renewToken)` at
`tokenIssueTimeEpochSec + durationUntilRefreshInSec` — renewal here stays
reactive on inner code 119), the `renewToken` retry policy
(AUTH_RETRY_REQUIRED/10202 exponential backoff per
`refreshApiRetryPolicy`) and its AUTH_INVALID_REQUEST (10201) kickout, the
per-endpoint talk-auth interceptor opt-outs
(`ignoreTalkAuthException`/`ignoreMustUpgrade`/`ignoreGlobalAlert`), and SSE
keepalive pings / reconnect backoff (the SSE query-param set and `localRev`
resume cursor *are* ported). No deliberate live-verified deviations were
changed (`from`-deletion in sealed messages, `keepLoggedIn=True`, SSE header
auth, the defensive 401 refresh hook — all preserved and documented).

### E2EE (Letter Sealing)
- **First-time group key creation** — `registerE2EEGroupKey` implemented
  end-to-end (curve-key generate, per-member `e2eeChannelWrapGroupSharedKey`,
  upload, unwrap), with automatic fallback when no group key exists yet and on
  `E2EE_RECREATE_GROUP_KEY` (99).
- **V1 send path** — the sealing version now follows the negotiated
  `specVersion` (spec-1 peers get V1-framed messages via the new
  `e2eechannel_encrypt_v1` bridge op).
- **Sealed media (V2 flow)** — `ENC_KM`/`FILE_NAME` are sealed into the
  ciphertext and restored on decrypt; the file blob itself is E2E-encrypted
  with HKDF-SHA256(`FileEncryption`) AES-CTR + HMAC (`encrypt_blob` /
  `decrypt_blob`); the media builders accept `enc_km=`,
  `media_content_info=` / `media_thumb_info=`.
- **Send-error retry semantics** — sealed sends go through
  `e2ee.send_with_retry` (wired into `send_message`): codes 84/86/87/88/90
  reset the negotiation and retry, up to 3 transparent retries (the
  extension's `sB` wrapper budget); 99 additionally re-registers the group
  key; 122 (REFRESH_MEDIA_FLOW) resets the negotiation and re-raises without
  retrying (the extension's `resetE2eeInfo` + rethrow).
- **Encrypt gating** — no double-sealing (chunks check), sealable
  content-type whitelist, negotiated `allowedTypes` check, media requires the
  V2 flow; decrypt-side `REPLACE`/`ENC_KM`/`FILE_NAME` restoration (yL parity)
  and the extension's control-char escaping sanitizer (xL).

### Login & session flows
- **E2EE email login** now sends the real `secret` (per-16-byte-block
  AES-CBC-encrypted curve25519 public key keyed by SHA-256 of a random 6-digit
  code); **REQUIRE_DEVICE_CONFIRM (type 3)** continuations implemented (JQ/LF1
  long-polls → `confirmE2EELogin` → `loginV2(type=QRCODE, verifier)`), plus
  the extension's three-tier email login ladder with per-email certificate
  store (`email_login_ladder`).
- PIN-verification poll uses the fixed X-LST 110000; the QR scan poll retries
  on 410 only (matching the extension), while the device-confirm JQ/LF1 polls
  treat 410 as a terminal PIN-code timeout (the extension's `PIN_CODE_TIMEOUT`)
  and never retry it; a 200 with a non-JSON body from those polls is retried,
  not an abort.

### Transport / headers / errors
- **Error mapping corrected**: upgrade is outer envelope 10006 only (code 86 is
  `E2EE_INVALID_VERSION` → plain `LineApiError`); auth kickout set is inner
  codes {1, 7, 8} (0/ILLEGAL_ARGUMENT no longer auth); inner **119**
  (`MUST_REFRESH_V3_TOKEN`) auto-refreshes the token and replays the request
  once; outer **99999** / inner **115** are retried within the retry budget;
  10052 surfaces `data.statusCode` + `rejectionReason`; success is strictly
  `message === "OK"` and non-enveloped gateway 2xx bodies are errors.
- **Header scoping matches the extension**: `X-Line-Application` never on
  gateway (OBS-private only), `X-Line-ChannelToken` only on gateway
  `/api/timeline/` paths, `X-LAL` gateway-only, no `content-type` on bodyless
  GETs; the invented `x-line-resp-code` header fallback is gone; the default
  User-Agent is documented as a synthetic CrOS Chrome/124 string
  (overridable via `LineConfig(user_agent=...)`).
- New `LineConfig(legy_host=...)` sends `X-Legy-Host` on gateway requests
  (and `legyHost` on the SSE connect).

### Special endpoints
- **SSE `/api/operation/receive`** now carries the full query set
  (`version`, `localRev`, `language`, `lastPartialFullSyncs`,
  `fullSyncRequestReason`, `legyHost`) with a **`localRev` resume cursor**
  (seeded from `getLastOpRevision`, updated from ops and full-sync events,
  re-sent on reconnect, stale ops deduped).
- **`/api/lan/notice`** service notices (`ops.lan_notice()`), OBS
  **`/info.obs`** + **`/playback.obs`** (`obs.resource_info()` /
  `obs.playback_info()`), the **`X-Talk-Meta`** thrift-blob builder
  (`obs.build_talk_meta()`), and the extension's **OBS auth headerMapper**
  (channel token for `/r/myhome/`, encrypted `OBS_GENERAL` token +
  `X-Line-Application` otherwise, no auth for public objects).
- Thin typed helpers for `timeline.homeId`/`getCover`/`updateCover` and legy
  `pageinfo` (`caller=LINE_CHROME`), plus CDN host constants and
  `download_object(cdn=...)` overrides.

### Models / enums / services
- `Message` base now supports optional `from`/`id`/`createdTime` (string epoch
  ms); `hasContent` correct for text (false) and sticker (true); sticker
  `STKOPT`/`STKHASH`/`STK_IMG_TXT`; `favoriteTimestamp` sent as string;
  `updateChat` documented as the full-Chat-entity escape hatch;
  `getChats`/`getContacts` take a `limit=` (server-config counterparts noted);
  `updateProfileAttributes`/`set_status_message` accept metadata.
- Enums: `StickerResourceType` completed (8 members), `NameTextStatus`
  corrected (+`CONTAINS_INVALID_WORD`), new `AddFriendResult`,
  `PaidReactionResourceType`, `E2EEMediaFlow` (V1/V2),
  `ConfigurationSyncParam`; `MessageReactionType` is now a documented
  deprecated alias of `PredefinedReactionType`.

## [2.7.1] - 2026-09-12

### Added
- **`scripts/check_extension_update.py`** — a drift checker against the *live*
  Chrome Web Store: downloads the latest LINE extension CRX, unpacks it,
  compares the MD5 of the bundled `ltsm.wasm` / `ltsmSandbox.js`, extracts the
  app header + every thrift path from `static/js/main.js` and diffs them
  against `okline/` (stdlib-only; `--apply` copies new crypto artifacts in).
  Exit code 0 = up to date, 2 = drift detected. Run it any time to know
  whether LINE shipped a new build.

### Verified
- **Re-audit against the newest Web Store build** (extension 3.7.2, updated
  February 25, 2026): `ltsm.wasm` and `ltsmSandbox.js` MD5-identical to the
  bundled copies, all 77 Thrift endpoints unchanged, gateway host, application
  header (`CHROMEOS\t3.7.2\tChrome_OS\t`), `X-LAL` locale map and the LTSM
  extension origin all match — **no protocol changes needed**.

### Fixed
- **Own messages now decrypt** when a chat is read back (`okline chatlog`,
  `get_recent_messages`, bots): our own sealed 1:1 messages were channelled
  against *our own* public key instead of the recipient's, so every message you
  had sent showed up as `[encrypted]`. The decrypt channel now mirrors the send
  side — `ECDH(our key, the peer's public key)` — for V1 and V2 framing alike
  (#2). Not related to QR-vs-PIN login; the keychain is delivered by the QR
  flow itself.
- The interactive menu's chat log shows your own display name instead of a
  truncated mid for your messages (your profile is now part of the name map).

### Docs
- README: `uv tool install "okline[qr]"` listed as an install method (#1).

## [2.7.0] - 2026-06-23

### Added
- **A full-featured interactive TUI.** Running `okline` (no args) now opens a
  **categorised** menu — 8 sections, ~40 actions — covering essentially every
  capability: account/profile/settings/logout, contacts (list/search/find/add/
  block/favorites/export), groups (list/members/leave/accept/boxes), sending
  (text/sticker/location/media/reply/react/unsend/broadcast), reading
  (chat log with E2EE decrypt / raw / search / backup), live bots
  (watch/auto-reply/notify), E2EE (status/send/decrypt/round-trip), and a
  developer section (call any endpoint, list endpoints, self-test, recording).
  Sub-menu navigation with Back/Quit; same soft palette.

## [2.6.0] - 2026-06-23

A code-quality / architecture pass. No behaviour or wire changes — the public API,
protocol, crypto and E2EE framing are unchanged, and the live integration test
still passes.

### Changed
- **Tooling**: adopted **ruff** (lint + format) and **mypy**, configured in
  `pyproject.toml`, with a `.pre-commit-config.yaml`, a `Makefile` (`make
  lint/format/typecheck/test/check`) and a `[dev]` extra.
- **Modernised type hints** to PEP 585/604 (`list`/`dict`/`X | None`) across the
  package; imports sorted; whole codebase auto-formatted.
- **Type-clean**: `mypy` now reports no issues. Introduced a typed
  `services._base.ServiceMixin` so the mixin architecture type-checks, a shared
  `_util.reconfigure_stdout_utf8` helper, and narrowed optional types.
- Minor robustness: `raise ... from` on a re-raise; `qr_login` no longer calls the
  PIN callback with `None`.

A full system audit (every endpoint cross-checked against the real LINE Chrome
bundle; 21/21 read endpoints re-verified live) plus a complete docs rewrite.

### Fixed
- **API fidelity** (the only two drifts found across 88 audited endpoints):
  `getChats` now sends the trailing `syncReason` arg, and `logoutV2` sends an
  empty arg array (it is a no-arg method).
- `get_contacts` auto-chunks at 100 mids (same `Invalid Length` cap as
  `get_chats`), so large accounts can fetch all contacts.
- The **bot framework now transparently decrypts** Letter-Sealed messages —
  `ctx.text` is the plaintext.
- `send_message` only auto-seals text/location on code 82 (no longer mangles
  media-placeholder sends).

### Added / Changed
- CLI: `qr-login` is an alias of `login`; new top-level `--version`/`-V`; new
  `logout` command; `react` takes the reaction as a positional
  (`okline react <id> LOVE`); `send <name>` resolves a contact name to its mid;
  a friendly Node.js preflight before login; `broadcast` stops on rate-limit/abuse.
- **Docs overhaul** — every page rewritten and accurate to this version, with new
  [E2EE](docs/e2ee.md), [Media](docs/media.md) and [Cookbook](docs/cookbook.md)
  pages and a documentation [index](docs/index.md).

## [2.5.2] - 2026-06-23

### Fixed
- `get_chats` now **auto-chunks** at 100 mids per request (and merges the results)
  — large accounts hit `Invalid Length` (code 6) listing groups. Fixes
  `okline groups` / the menu for 100+ chats.
- The CLI now forces UTF-8 stdout, so non-ASCII (e.g. Thai) group/contact names no
  longer raise `UnicodeEncodeError` on Windows code pages.

## [2.5.1] - 2026-06-23

### Changed
- The interactive menu now goes **straight to QR login** when there's no saved
  session (instead of a yes/no prompt) — `okline` on a fresh machine shows the QR
  immediately, scan it, and you're in the menu.

## [2.5.0] - 2026-06-23

### Added
- **Interactive terminal UI** — run `okline` with no arguments for a soft-coloured,
  menu-driven console: pick actions by number, no commands to memorise. It logs in
  by QR on first use and saves the session. New `okline.ui` toolkit (muted palette,
  TTY-aware, ASCII fallback) + `okline.menu`.
- **Full CLI** — `okline <command>` now covers ~30 actions: `login`, `whoami`,
  `profile`, `contacts` (search/export), `find`, `search`, `add`, `block`,
  `favorites`, `groups`, `members`, `leave`, `accept`, `send` (text/sticker/
  location/image/file/`--encrypt`), `react`, `unsend`, `broadcast`, `set-name`,
  `set-status`, `boxes`, `chatlog` (decrypts E2EE), `backup`, `watch`, `autoreply`,
  `notify`, plus the existing `endpoints`/`call`/`selftest`.
- Every command reuses a saved `tokens.json` by default (and restores E2EE keys),
  so after `okline login` the rest "just work".

## [2.4.0] - 2026-06-23

### Added
- **Cross-session E2EE** — the unwrapped keychain is now exported
  (`E2EEManager.export_keys`, via the WASM `e2eekey_export_key`) into the session
  file by `save_tokens` and restored by `from_tokens_file`
  (`load_from_export` / `e2eekey_load_key`). Letter Sealing now works from a saved
  token **without a fresh QR login**.
- **Group Letter Sealing** — decrypt group messages and send to groups that
  already have a key. The group shared key is fetched
  (`getLastE2EEGroupSharedKey` / `getE2EEGroupSharedKey`) and unwrapped via the new
  `e2eechannel_unwrap_group_shared_key` bridge op. `encrypt()`/`decrypt()` route
  group-vs-1:1 automatically. (Bootstrapping a brand-new group key —
  `registerE2EEGroupKey` — is still future work.)

Both live-verified (35/35 live checks pass, incl. group decrypt + cross-session
reload + roundtrip).

## [2.3.0] - 2026-06-23

### Added
- **E2EE / Letter Sealing — now fully live-verified (1:1).** Encrypted **send**
  (V2) and **decrypt of received messages** (both **V1** and **V2** formats) work
  end-to-end against the real servers. Added `E2EEManager.roundtrip()` as a
  self-test, and V1 framing (`build_chunks_v1`/`parse_chunks_v1`) plus the
  `e2ee_decrypt_v1` bridge op (`decryptV1(channel, ciphertext)` — no AAD args).
- **`examples/`** — eight runnable mini-tools (`whoami`, `find_contact`,
  `export_contacts`, `group_members`, `backup_chat`, `send_media`, `broadcast`,
  `watch`) with their own README.

### Fixed
- **Encrypted send was rejected (500/99999).** The sealed message must omit
  `text`/`location`/`from` entirely (the real `EL()` sets them to `undefined`);
  we were sending `text:null`/`from:<mid>`. Now deleted outright.
- **mid type detection is case-insensitive** — modern mids are upper-case
  (`U`/`C`/`R`/`S`); groups were being misclassified as users (wrong `toType`).

### Changed
- Safe, behaviour-preserving optimizations (wire bytes/crypto unchanged): signed
  GET path computed once, recorder uses a bounded `deque`, the SSE response is
  closed in a `finally`, dead imports removed.
- Removed the GitHub Actions workflow; slimmed the README to a clean overview
  with details under `docs/`.

## [2.2.0] - 2026-06-23

### Added
- **Media send (V1)** — `OkLine.send_image/send_video/send_audio/send_file`:
  posts a placeholder message then uploads the bytes to OBS
  (`/r/talk/m/<messageId>`) with the encrypted OBS token. New `okline send` CLI.
- **E2EE / Letter Sealing (experimental, 1:1)** — `okline.e2ee.E2EEManager`
  (loaded automatically by `qr_login`), `api.send_encrypted_text()`,
  `api.decrypt_message()`, and **auto-seal-and-retry** in `send_message` when the
  server rejects plain text with code 82. Framing in `okline.e2ee_crypto`
  (chunks/plaintext) is fully unit-tested; the crypto runs in the WASM bridge.
  Works **in the same session as `qr_login`** (cross-session reuse is future
  work). Group Letter Sealing is not wired yet.
- Errors now surface the **inner Thrift exception** code/reason (e.g. 82
  "can not send using plain mode") instead of a generic `RESPONSE_ERROR`.
- `live_test.py` — detailed live integration test (`--to`, `--image`, `--qr`,
  `--listen`).

## [2.1.0] - 2026-06-23

### Added
- **Bot framework** (`okline.bot.Bot`) — `@bot.on_message`, `@bot.command("…")`
  and `@bot.on(OpType…)` decorators with a `MessageContext.reply()` helper and a
  resilient `bot.run()` dispatch loop.
- **Typed entities** (`okline.entities`) — `Profile`, `Contact`, `Group`, `Room`
  dataclasses with `from_dict` (raw payload kept on `.raw`).
- **Session persistence** — `OkLine.from_tokens_file(path)` / `api.save_tokens()`
  (auto-saves on token refresh); `okline.Session`.
- **Rate limiter** — `okline.ratelimit.RateLimiter` (token bucket), attachable as
  `api.transport.rate_limiter`.
- **Media message builders** — `Message.image/video/audio/file`.
- `py.typed` marker (PEP 561) — the package now ships type information.

### Experimental / known limitations
- Full **media upload** (`send_image` over OBS) and **E2EE message
  encrypt/decrypt** (Letter Sealing) require a live session to finalise and are
  not yet shipped end-to-end; the building blocks (media metadata builders, the
  Curve25519/E2EE bridge primitives, the E2EE key endpoints) are in place.

## [2.0.0] - 2026-06-22

The library was renamed from `line_chrome_api` to **`okline`** (main class
`OkLine`; `LineApi` kept as an alias).

### Added
- **Full response recording** — every request/response is captured as an
  `Exchange`. Inspect via `api.last` / `api.history`, format with
  `Exchange.pretty()` / `api.dump()`, export with `api.save_log(..., fmt="text"|"json"|"har")`.
  Secrets are redacted by default; `on_exchange` hooks let you observe calls live.
- **Command-line interface** (`python -m okline` / `okline`): `call`, `qr-login`,
  `profile`, `endpoints`, `version`.
- Curve25519 key generation + E2EE keychain unwrap via the LTSM bridge, so
  **QR login works fully** (the QR now carries the required
  `?secret=<pubkey>&e2eeVersion=1`).
- Terminal ASCII/Unicode QR rendering (`okline.qrterm.print_qr`).
- Response-body secret redaction (access/refresh tokens, certificate, keychain).
- GitHub project scaffolding, multi-file test-suite and full docs.

### Fixed
- Correctly unwrap the `{"message":"OK","data":...}` gateway envelope (previously
  caused `KeyError`), and surface non-OK envelopes as `LineApiError`.

## [1.0.0] - 2026-06-22

### Added
- Initial client covering all 77 Thrift-over-JSON endpoints of the LINE Chrome
  extension (CHROMEOS 3.7.2): typed service methods + a generic `call()`.
- Mandatory **`X-Hmac`** request signing, computed by LINE's real `ltsm.wasm`
  module driven through a persistent Node.js bridge.
- E-mail (RSA/PKCS1v1.5) and secondary-device QR login flows.
- SSE + long-poll operation receiver, OBS media client, full enum/struct set,
  and message builders.

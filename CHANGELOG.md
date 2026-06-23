# Changelog

All notable changes to OkLine are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

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

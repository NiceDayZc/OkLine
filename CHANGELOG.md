# Changelog

All notable changes to OkLine are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

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

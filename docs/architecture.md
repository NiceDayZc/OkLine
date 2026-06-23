# How it works (architecture)

[← docs home](./index.md)

OkLine speaks the exact protocol of the LINE Chrome extension (`CHROMEOS`
3.7.2), reverse-engineered from the extension bundle. This page explains the
wire protocol, the mandatory `X-Hmac` signature, Letter Sealing (E2EE), and a
map of every module so you know where to look.

## The gateway protocol

| Layer | Detail |
|-------|--------|
| **Gateway** | `https://line-chrome-gw.line-apps.com` |
| **RPC** | `POST /api/talk/thrift/<Namespace>/<Service>/<method>` |
| **Request body** | a JSON **array of positional Thrift args**; struct args are plain JSON objects with **named** (camelCase) fields |
| **Response** | wrapped: `{"message":"OK","data":<result>}` — OkLine unwraps `.data`; a non-`OK` message becomes a `LineApiError` |
| **Auth** | `X-Line-Access: <accessToken>` |
| **App** | `X-Line-Application: CHROMEOS\t3.7.2\tChrome_OS\t` + `X-Line-Chrome-Version: 3.7.2` |
| **Signature** | `X-Hmac: <base64>` on every request (see below) |
| **Locale** | `X-LAL: en_US` + `Accept-Language: en-US` |
| **Receive** | SSE `GET /api/operation/receive` (+ long-poll `LF1`/`JQ`) |
| **Media** | OBS `obs.line-apps.com` + gateway `/api/obs/*` |

Example: `sendMessage(reqSeq, Message)` →

```http
POST /api/talk/thrift/Talk/TalkService/sendMessage
X-Line-Access: <token>
X-Hmac: <base64>
content-type: application/json

[0, {"to":"u...","toType":0,"text":"hi","contentType":0,"contentMetadata":{}}]
```

Every endpoint, with its argument fields, is listed in
[ENDPOINTS.md](./ENDPOINTS.md).

## The `X-Hmac` signature (and why Node.js)

The gateway rejects any request without a valid **`X-Hmac`** header
(`REQUEST_INVALID_HMAC`, code `10005`). The signature is computed over the exact
`path + body` bytes that are transmitted (for GETs the path includes the query
string and the body is the empty string):

```
X-Hmac = base64( Hmac( deriveKey( SHA256("3.7.2"), SHA256(accessToken) ) )
                   .digest(path + body) )
```

where the key is derived from `SecureKey.loadToken(<per-extension token>)`. The
`deriveKey` / `Hmac` / `loadToken` primitives live inside LINE's secure WASM
module **`ltsm.wasm`** (a C++ `LTSM::…` build). Rather than guess at that custom
crypto, OkLine **runs the real module**: a tiny persistent **Node.js bridge**
([`okline/ltsm/ltsm_bridge.js`](../okline/ltsm/ltsm_bridge.js)) loads
`ltsm.wasm` inside a minimal DOM shim and drives it through the same
`postMessage` command protocol the extension uses. The Python side
([`okline/hmac_signer.py`](../okline/hmac_signer.py), class `LtsmBridge`)
manages that subprocess and exposes `sign()`.

This is why **Node.js 18+ must be on your PATH**. If `node` lives somewhere
unusual, set `LINE_NODE=/path/to/node` (or pass
`OkLine(config=LineConfig(node_path=...))`). See
[troubleshooting.md](./troubleshooting.md) if signing fails.

The **same bridge** also generates the Curve25519 keypair for QR login
(`curvekey_generate` → `e2ee_public_key`), unwraps the E2EE keychain, and runs
the Letter Sealing encrypt/decrypt — so one WASM process serves signing, login,
and E2EE.

The bundled token is specific to this extension build
(`chrome-extension://ophjlpahpchlmihnnnihgmmeilfjmjjc`). Override with
`LineConfig(ltsm_origin=...)` / env `LTSM_ORIGIN` if you swap in a different
build's `ltsm.wasm` + `ltsmSandbox.js`.

## E2EE (Letter Sealing)

Messages can be **end-to-end encrypted** ("Letter Sealing"), for both 1:1 chats
and groups. Two modules cooperate:

* [`okline/e2ee_crypto.py`](../okline/e2ee_crypto.py) — the **pure-Python
  framing**: serialize the plaintext, split/re-assemble the ciphertext into the
  `chunks` array, and build the sealed `Message` struct (V1 and V2 wire
  formats). This is independent of the crypto and is round-trip unit-tested.
* [`okline/e2ee.py`](../okline/e2ee.py) — the **`E2EEManager`**, which ties the
  framing to the WASM bridge (key handles, ECDH channels, encrypt/decrypt) and
  routes a message to 1:1 or group sealing automatically.

The private keys come from the keychain unwrapped **during QR login**
(`qrCodeLoginV2` → `metaData.encryptedKeyChain`). They now **persist across
sessions**: `save_tokens()` exports them into the session file and
`from_tokens_file()` restores them, so Letter Sealing keeps working without a
fresh QR scan. Check readiness with `api.e2ee.is_ready()`. See the
[messaging guide](./messaging.md) for the high-level API.

## Request lifecycle

```
OkLine.send_text(...)                 # a typed service method
  -> Transport.call(endpoint, args)
       -> resolve path from endpoints.py
       -> JSON-encode the args array               (exact bytes)
       -> LtsmBridge.sign(token, path, body)       -> X-Hmac
       -> add headers, POST via requests.Session    (optional rate limiter)
       -> retry once on 401 if a refresh_token exists
       -> unwrap the {message,data} envelope        (errors -> LineApiError)
       -> record the Exchange                        (recorder.py)
  <- decoded result
```

## Module map

| Module | Responsibility |
|--------|----------------|
| `__init__.py` | public package surface (`OkLine`, `Bot`, `Session`, …) + `__version__` |
| `client.py` | `OkLine` facade: services + auth + ops + obs + e2ee + recorder; session save/load |
| `transport.py` | HTTP engine: headers, signing, envelope, errors, 401-refresh, recording |
| `hmac_signer.py` | `LtsmBridge` — manages the Node bridge (X-Hmac + Curve25519 + E2EE ops) |
| `ltsm/` | `ltsm.wasm`, `ltsmSandbox.js`, `ltsm_bridge.js` (the real LINE crypto module) |
| `auth.py` | e-mail / QR / token-refresh login flows |
| `crypto.py` | RSA / PKCS1v1.5 login-credential encryption |
| `e2ee.py` | `E2EEManager` — Letter Sealing: key handles, channels, encrypt/decrypt (1:1 + group) |
| `e2ee_crypto.py` | pure-Python E2EE **framing** (plaintext + chunks + sealed message; V1/V2) |
| `session.py` | `Session` — token **and** E2EE keychain persistence to a JSON file |
| `operations.py` | SSE + long-poll operation receiver (`Operation`, `SSEEvent`) |
| `bot.py` | `Bot` event framework: `@on_message` / `@command` / `@on`, auto-decrypt |
| `entities.py` | typed response models (`Profile`, `Contact`, `Group`, `Room`, `Message`) |
| `ratelimit.py` | `RateLimiter` token bucket (attach to `transport.rate_limiter`) |
| `obs.py` | object storage (media upload/download) |
| `recorder.py` | `Exchange` + `Recorder` (capture / redact / export text/json/har) |
| `qrterm.py` | terminal ASCII QR rendering |
| `menu.py` | interactive, numbered terminal **menu** (run `okline` with no args) |
| `ui.py` | tiny TUI toolkit (muted colours, boxes, tables, prompts) used by `menu.py` |
| `__main__.py` | the CLI (`okline <command>` / `python -m okline`) |
| `enums.py`, `models.py`, `endpoints.py`, `exceptions.py` | data + endpoint registry + errors |
| `services/` | one typed method per Thrift endpoint (messaging, contacts, chats, profile, e2ee, …) |

## Why not pure Python?

Everything *except* the `ltsm.wasm` crypto is pure Python. The WASM holds the
`X-Hmac` key-derivation and the Letter Sealing primitives; reproducing them
without the module would mean reverse-engineering custom C++ crypto. Running the
real module via Node is the reliable, faithful choice. (A pure-Python
re-implementation is an open research direction — the standard primitives exist,
only the exact `deriveKey`/`loadToken` construction would need to be recovered.)

---

Next: [recording](./recording.md) · [troubleshooting](./troubleshooting.md) ·
[contributing](./contributing.md)

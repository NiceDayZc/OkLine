# Media: send and download images, video, audio, files, avatars, covers

All snippets are standalone: they open the session from `tokens.json` and
`api.close()` it (releasing the Node bridge). Node 18+ must be on PATH — every
request fails with `LineApiError` code **10005** REQUEST_INVALID_HMAC without it
(see ./session.md). Media bytes live on LINE's OBS object storage; OkLine
handles OBS auth (encrypted access token / channel token) automatically — never
hand-roll those headers.

## Sending media (V1 / plain flow)

`api.send_image/send_video/send_audio/send_file(to, path_or_bytes, name=..., duration_ms=...)`
accept a filesystem path (`str`/`Path`) or raw `bytes`; `name=` overrides the
filename the recipient sees, `duration_ms=` (video/audio) sets the player length.
Each call posts a placeholder message, then uploads the bytes to
`/r/talk/m/<messageId>` and returns the sent-message dict (with its `id`).

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
try:
    api.send_image("u1234...", "photo.jpg")                    # name from filename
    api.send_file("c5678...", "report.pdf", name="Q3.pdf")     # rename on send
    api.send_video("c5678...", "clip.mp4", duration_ms=8000)
    api.send_audio("u1234...", "note.m4a", duration_ms=3000)
    data = open("photo.jpg", "rb").read()                      # in-memory bytes
    api.send_image("u1234...", data, name="photo.jpg")
finally:
    api.close()
```

CLI equivalents (contact display names resolve to mids):

```bash
python3 -m okline send "Alice" --image pic.jpg
python3 -m okline send c1234...group --file report.pdf
python3 -m okline send "Alice" --sticker 1234 5678   # one mode flag per send
```

Caveats:
- This is the **V1, non-E2EE** flow (per `OkLine._send_media`: "works for
  non-Letter-Sealed chats"). In a chat that requires Letter Sealing the server
  can reject the plain placeholder with code **82** (E2EE_RETRY_ENCRYPT); the
  automatic 82-reseal then depends on the chat's negotiated media flow being V2
  (media sealing is gated on it — if the flow is V1-only the error propagates).
  Test with a small image to that chat before sending anything that matters;
  sealed sends are covered under "Sending sealed media" below.
- The whole file is read into memory — no chunked upload; keep files to tens of MB.
- `LineApiError("sendMessage returned no message id for media")` or
  `LineApiError("OBS upload failed: HTTP <n>")` mean the flow died mid-way — the
  recipient sees a stuck placeholder. Retry once; do not tight-loop.
- Rate limits apply to media sends like any send: code 4 EXCESSIVE_ACCESS /
  35 ABUSE_BLOCK mean SLOW DOWN (install `RateLimiter` — see ./errors.md).

## Downloading plain (non-sealed) media

For a received IMAGE/VIDEO/AUDIO/FILE message (contentType 1/2/3/14), the
object id sits in `contentMetadata`: `OID` (object id) and `SID` (storage id,
usually `"m"`). Download via `api.obs.download_object`, passing `message_id=`
so the `X-Talk-Meta` header is attached (required for sealed-media paths, harmless
for plain ones):

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
try:
    for msg in api.get_recent_messages("u1234...", 50):
        if int(msg.get("contentType", 0) or 0) not in (1, 2, 3, 14):
            continue
        meta = msg.get("contentMetadata") or {}
        oid, sid = meta.get("OID"), meta.get("SID", "m")
        if not oid:
            continue  # sticker/link previews carry no OID
        data = api.obs.download_object("talk", sid, oid, message_id=msg.get("id"))
        with open(f"{oid}.bin", "wb") as fh:   # rename per content-type/name
            fh.write(data)
        print("saved", oid, len(data), "bytes")
finally:
    api.close()
```

`download_object` returns raw `bytes` and raises `requests.HTTPError` (via
`raise_for_status`) on a bad status — an expired OBS token shows up as HTTP 401/403;
do any gateway call first (e.g. `api.get_profile()`) so the auto token refresh
fires, then retry. Public objects need no auth: pass `public=True` (and optionally
`cdn="cdn_obs"` / `"cdn_profile"`, or `host="https://..."`; an unknown cdn key
raises `ValueError`).

## Sealed (E2EE) media — the one-call download

In Letter-Sealed chats the object is encrypted and the key material (`ENC_KM`)
is sealed inside the message ciphertext. `api.decrypt_message(msg)` restores
`ENC_KM`/`FILE_NAME` into `contentMetadata` (safe on any message — plain ones
pass through unchanged), and `api.e2ee.download_sealed_media` does the whole
receive flow: decrypt -> fetch `/r/talk/<SID>/<OID>` with `X-Talk-Meta` ->
HKDF/AES-CTR+HMAC decrypt the blob.

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
assert api.e2ee.is_ready(), "E2EE keys not loaded — qr_login and save_tokens first"
try:
    msg = api.get_recent_messages("u1234...", 50)[0]        # a sealed media message
    data, info = api.e2ee.download_sealed_media(msg, info=True)
    info = info or {}                                        # object_info.obs dict
    name = (info.get("name")
            or (msg.get("contentMetadata") or {}).get("FILE_NAME")
            or "sealed.bin")
    with open(name, "wb") as fh:
        fh.write(data)
    print("saved", name, len(data), "bytes, mime", info.get("mime"))
finally:
    api.close()
```

Behavior and failure modes:
- `info=True` adds the `object_info.obs` dict (name / mime / size); `info=False`
  (default) returns just the bytes.
- `LineApiError("message has no ENC_KM/OID after decryption — not sealed media")`
  -> it is plain media (or a sticker): use the plain path above.
- `ValueError("sealed-media blob HMAC verification failed")` or
  `"blob too short"` -> the key material does not match the blob (wrong keychain /
  corrupted download). Re-fetch; if it persists the E2EE keychain is stale —
  re-login via `api.qr_login(...)` and `api.save_tokens(...)`.
- The Node bridge is required here too (decryption runs through it): code 10005
  means Node is missing, `LINE_NODE=/path/to/node` overrides the binary.
- Sending sealed media: `from okline import Message`; the builders
  `Message.image/video/audio/file(to, ..., enc_km=...)` take key material minted by
  `api.e2ee.generate_enc_km()` (base64 of 32 random bytes), and the blob itself is
  encrypted with `okline.e2ee_crypto.encrypt_blob(b64decode(enc_km), data)` before
  its OBS upload. `api.send_message(msg, encrypt=True)` seals the metadata;
  `send_with_retry` retries E2EE codes 84/86/87/88/90/99 internally and re-raises
  on 122 (REFRESH_MEDIA_FLOW — retry the send yourself). Details and gating rules
  (V2 media flow, allowedTypes) live in `okline/e2ee.py` — verify with
  `api.e2ee.roundtrip(to, "ping")` before relying on it.

## Profile pictures

Where to get `picturePath` (and `pictureStatus`, which carries the same value;
absent/None = no picture set):
- own account: `api.get_profile()["picturePath"]`
- another user whose LINE userid you know: `api.find_contact_by_userid("name.x")["picturePath"]`
- chat/group icons: the `picturePath` field of `api.get_chats(ids)` entities
- NOT `api.get_contacts(mids)` — getContactsV2 returns only
  `{snapshotTimeMillis, userStatus}` per contact, no picture (live-verified)

The avatar is a **public** object at the profile CDN: URL =
`https://profile.line-scdn.net` + `picturePath` (a single path segment starting
with `/`), fetchable with **no auth** — appending `/small` returns the thumbnail
(both live-verified). There is no dedicated download helper; fetch it through
the client's HTTP session (a non-gateway `base` disables HMAC signing, and
`require_auth=False` sends no token):

```python
from okline import OkLine
from okline.endpoints import CDN_PROFILE_BASE   # https://profile.line-scdn.net

api = OkLine.from_tokens_file("tokens.json")
try:
    pp = api.get_profile()["picturePath"]
    if not pp:
        raise SystemExit("no picture set")
    full = api.transport.get(pp, base=CDN_PROFILE_BASE, require_auth=False)
    small = api.transport.get(pp + "/small", base=CDN_PROFILE_BASE, require_auth=False)
    full.raise_for_status(); small.raise_for_status()
    open("avatar.jpg", "wb").write(full.content)
    open("avatar_small.jpg", "wb").write(small.content)
finally:
    api.close()
```

The plain URL also works from a shell/browser with zero credentials
(`curl -o avatar.jpg "https://profile.line-scdn.net<picturePath>"`) — it contains
no secret, so it is safe to save or show. Do not confuse it with `/r/`-shaped
profile objects, which go through `download_object(..., public=True,
cdn="cdn_profile")`. To set your own picture, upload with
`api.obs.upload_profile_image(mid, data, content_type="image/jpeg")`.

## Timeline (VOOM) cover images

Covers live under the `myhome` OBS service and authenticate with the **timeline
channel token** (`api.issue_channel_token()` — also issued lazily on the first
`/r/myhome/` request and cached). Resolve the cover object, then download:

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
try:
    me = api.get_profile()
    my_mid = me["mid"]
    region = me.get("regionCode") or "JP"
    api.issue_channel_token()                     # optional; warms the cache
    home = api.obs.timeline_home_id("u1234...")   # -> {"homeId": ...}
    cover = api.obs.timeline_get_cover("u1234...", my_mid, region) or {}
    obs_info = cover.get("coverObsInfo") or {}
    # key names come from the live payload — print(api.dump()) once to inspect,
    # then pull the object id / storage id out, e.g.:
    oid = obs_info.get("oid") or obs_info.get("objectId")
    sid = obs_info.get("sid") or "c"
    if oid:
        data = api.obs.download_object("myhome", sid, oid)   # channel token auto-attached
        with open("cover.jpg", "wb") as fh:
            fh.write(data)
finally:
    api.close()
```

`timeline_get_cover` returns the decoded gateway payload (tests show the shape
`{"coverObsInfo": {...}}`); `timeline_home_id(e_mid)` returns `{"homeId": ...}`.
If the download 401/403s, the cached channel token expired — clear
`api.transport.tokens.channel_access_token = None` and retry.

## OBS metadata endpoints

For inspecting an object before/after download — all take the `/r/<svc>/<sid>/<oid>`
path string (no OBS host prefix). `object_info` returns the object's
name/mime/size; `playback_info` returns video playback data (fixed
`modelName=CHROMEOS`, `networkType=WiFi`, `lang` params):

```python
from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")
try:
    oid = "1234567890"          # the OID from contentMetadata
    msg_id = "1234567890123"    # the message id
    print(api.obs.object_info(f"/r/talk/m/{oid}", message_id=msg_id))
    print(api.obs.resource_info(f"/r/talk/m/{oid}"))
    print(api.obs.playback_info(f"/r/talk/v/{oid}", message_id=msg_id))
finally:
    api.close()
```

`object_info`/`resource_info`/`playback_info` raise `LineApiError("OBS request
failed: HTTP <n>")` on a bad status (unlike `download_object`, which raises
`requests.HTTPError`) — catch `Exception` if you do not care which.

## Quick reference

| Need | Call |
|---|---|
| Send image/video/audio/file | `api.send_image(to, path, name=)` … `send_file`; `duration_ms=` for video/audio |
| Save received media (plain) | parse `contentMetadata.OID`/`SID` -> `api.obs.download_object("talk", sid, oid, message_id=...)` |
| Save received media (sealed) | `api.e2ee.download_sealed_media(msg, info=True)` -> `(bytes, object_info)` |
| Inspect object | `api.obs.object_info("/r/talk/<sid>/<oid>", message_id=...)` |
| Avatar | `api.transport.get(picturePath, base=CDN_PROFILE_BASE, require_auth=False)` (`+ "/small"` for thumb) |
| Timeline cover | `api.obs.timeline_get_cover(...)` -> `coverObsInfo` -> `api.obs.download_object("myhome", sid, oid)` |

Related: ./messaging.md (text/stickers/location, code-82 auto-reseal),
./errors.md (codes 4/35, 10005, 10006),
./session.md (tokens.json, login, Node bridge).

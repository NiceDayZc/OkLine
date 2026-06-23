# Recording & pasting responses

[← docs home](./index.md)

OkLine captures **every** request/response by default, so you can inspect or
paste the full exchange for any endpoint. Implementation:
[`okline/recorder.py`](../okline/recorder.py).

## The basics

```python
from okline import OkLine

api = OkLine(access_token="...")     # record=True by default
api.get_profile()
api.send_text("u...", "hi")

api.last                 # the most recent Exchange (or None)
api.history              # list[Exchange], oldest → newest
print(api.last.pretty()) # one HTTP transcript, secrets redacted
print(api.dump())        # every call this session, as one string
```

A transcript looks like:

```
#1 [OK] POST /api/talk/thrift/Talk/TalkService/getProfile   (Talk.TalkService.getProfile)
======================================================================
  -> POST https://line-chrome-gw.line-apps.com/api/talk/thrift/Talk/TalkService/getProfile
  >  X-Line-Application: CHROMEOS	3.7.2	Chrome_OS
  >  X-Line-Access: <redacted>
  >  X-Hmac: <redacted>
  >  body: [ 2 ]
  <- HTTP 200   109 ms
  <  resp: { "mid": "u...", "displayName": "...", "regionCode": "TH" }
```

## The `Exchange` object

| Attribute | Meaning |
|-----------|---------|
| `seq` | call number this session |
| `method`, `url`, `path`, `endpoint` | the request target (`endpoint` = `Namespace.Service.method`) |
| `request_headers`, `request_body` | what was sent |
| `status`, `response_headers`, `response_body`, `response_text` | what came back (`response_body` is the unwrapped result; `response_text` is the raw body) |
| `duration_ms`, `ok`, `error`, `started_at` | timing / outcome |

Methods: `.pretty(redact=True)`, `.to_dict(redact=True)`, `.to_har_entry(redact=True)`.

## Filtering

```python
api.recorder.find("Talk.TalkService.sendMessage")   # all calls to one endpoint
api.history[-5:]                                     # last 5 calls
```

## Exporting

```python
api.save_log("session.txt")              # plain transcript (fmt="text", the default)
api.save_log("session.json", fmt="json") # structured JSON (a list of exchanges)
api.save_log("session.har", fmt="har")   # open in browser DevTools → Network
```

The accepted `fmt` values are `"text"`, `"json"` and `"har"`. HAR files import
straight into Chrome/Firefox DevTools for a familiar Network-tab view.

You can also print the last exchange directly (UTF-8 safe, even on a Windows
console):

```python
api.print_last()
```

## Redaction (important)

By default OkLine **masks secrets** so you can safely paste output:

- request headers: `X-Line-Access`, `X-Hmac`, `Authorization`,
  `X-Line-ChannelToken`, `Cookie`, `Set-Cookie`.
- request/response body keys: `password`, `accessToken`, `refreshToken`,
  `secret`, `encryptedKeyChain`, `verifier`, `certificate`.

Reveal them only when you really need to:

```python
print(api.last.pretty(redact=False))              # one-off
api = OkLine(access_token="...", redact=False)    # whole session
api.save_log("raw.json", fmt="json", redact=False)
```

> 🔒 Even with redaction on, **don't** paste production logs publicly without a
> second look — response bodies can contain personal data (mids, names, …), and
> a saved session file (and any `redact=False` export) contains live
> credentials, including the E2EE keychain.

## Live hooks

React to each call as it completes:

```python
@api.on_exchange
def _(ex):
    print(ex.seq, ex.endpoint, ex.status, f"{ex.duration_ms:.0f}ms")
```

Or pass `OkLine(on_exchange=callback)`. Hooks never break a call — exceptions in
a hook are swallowed.

## Controls

```python
OkLine(record=False)            # disable recording entirely (api.last is None)
OkLine(record_capacity=2000)    # keep more history (default 500, ring buffer)
api.clear_log()                 # drop captured exchanges
```

The recorder is a fixed-size ring buffer, so the oldest exchange is dropped once
you exceed `record_capacity`.

Set `LINE_DEBUG=1` in the environment to also dump raw response bodies to stderr
as they arrive:

```bash
LINE_DEBUG=1 python your_script.py
```

---

Next: [troubleshooting](./troubleshooting.md) ·
[architecture](./architecture.md)

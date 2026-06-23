# Contributing

[← docs home](./index.md)

Thanks for helping improve OkLine! This page covers dev setup, the project
layout, how to add an endpoint, and the release flow.

## Setup

```bash
git clone https://github.com/NiceDayZc/okline
cd OkLine
pip install -e ".[test,qr]"     # editable install + pytest + qrcode
# Node.js 18+ must be on PATH for X-Hmac (and the bridge tests)
node --version
```

`pip install -e .` installs OkLine in **editable** mode, so your source edits
take effect immediately. The `[test,qr]` extras pull in `pytest` and the
optional inline-QR dependency.

## Running the tests

```bash
python -m pytest -q              # the whole suite (offline; no network)
python -m pytest tests/test_services_messaging.py -q   # one file
```

The suite is **offline** — it fakes the HTTP layer and the LTSM bridge, so it
needs neither network nor Node. The one exception is
`tests/test_hmac_bridge.py`, which exercises the real WASM and **skips itself**
when Node isn't available.

## Project layout

```
okline/
  __init__.py        public API surface + __version__
  client.py          the OkLine facade (services + auth + ops + obs + e2ee + recorder)
  transport.py       HTTP engine: headers, X-Hmac, envelope, errors, recording
  hmac_signer.py     LtsmBridge — the Node subprocess running ltsm.wasm
  ltsm/              ltsm.wasm, ltsmSandbox.js, ltsm_bridge.js
  auth.py            email / QR / token-refresh login
  crypto.py          RSA login-credential encryption
  e2ee.py            E2EEManager (Letter Sealing: channels + encrypt/decrypt)
  e2ee_crypto.py     pure-Python E2EE framing (chunks, sealed message; V1/V2)
  session.py         token + E2EE keychain persistence
  operations.py      SSE / long-poll operation receiver
  bot.py             the Bot event framework
  entities.py        typed response models
  ratelimit.py       RateLimiter token bucket
  obs.py             media (object storage)
  recorder.py        Exchange + Recorder (capture / redact / export)
  qrterm.py          terminal QR rendering
  menu.py, ui.py     interactive terminal menu (run `okline` with no args)
  __main__.py        the CLI
  enums.py, models.py, endpoints.py, exceptions.py
  services/          one typed method per Thrift endpoint
tests/               offline pytest suite (+ conftest.py fakes)
docs/                this documentation
```

See [architecture.md](./architecture.md) for the full module map and how the
pieces fit together.

## Test layout

Shared fixtures live in [`tests/conftest.py`](../tests/conftest.py):

| Helper / fixture | Use |
|------------------|-----|
| `build_api(responder, *, access_token, bridge, enable_hmac, record, **kw)` | build an `OkLine` wired to a fake session |
| `enveloped(data)` | wrap data in the `{message:OK,data}` envelope |
| `route({suffix: data})` | build a responder from an endpoint→response table |
| `FakeResp`, `FakeSession`, `FakeBridge` | the fakes (HTTP + LTSM bridge) |
| `USER_MID`, `GROUP_MID`, `ROOM_MID`, `SAMPLE_*` | sample data |
| fixtures `make_api`, `api`, `fake_bridge`, `last_request` | convenience |

Files are split by concern: `test_transport.py`, `test_crypto.py`,
`test_enums.py`, `test_models.py`, `test_endpoints.py`, `test_recorder.py`,
`test_auth.py`, `test_e2ee.py`, `test_qrterm.py`, `test_cli.py`,
`test_features.py`, `test_selftest.py`, `test_hmac_bridge.py`, and
`test_services_*.py`.

### A typical service test

```python
def test_send_text_payload(make_api, last_request):
    from conftest import route, USER_MID
    api = make_api(route({"sendMessage": {"id": "1"}}))
    api.send_text(USER_MID, "hi")
    body = last_request(api)                      # the positional-args array
    assert api.transport.session.last["url"].endswith("/Talk/TalkService/sendMessage")
    req_seq, msg = body
    assert msg["text"] == "hi" and msg["contentType"] == 0
```

reqSeq values are auto-generated — assert structure, not exact numbers.

## Adding a new endpoint

1. **Register the path** in [`okline/endpoints.py`](../okline/endpoints.py)
   under `THRIFT_ENDPOINTS`, keyed `Namespace.Service.method`.
2. **Add a typed wrapper** to the right mixin in
   [`okline/services/`](../okline/services/). Follow the positional-arg
   convention — the body is a JSON array of the Thrift args in order; struct args
   are dicts with camelCase field names. Auto-generate `reqSeq` via
   `self.next_req_seq()` when the method takes one.
3. **Add a test** in the matching `test_services_*.py` asserting the URL and the
   body shape.
4. If you discover the exact argument fields, also add them to
   [`docs/ENDPOINTS.md`](./ENDPOINTS.md).

```python
# services/example.py
class ExampleMixin:
    def my_method(self, mid: str, req_seq=None):
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call("Talk.TalkService.myMethod", [req_seq, mid])
```

Then include the mixin in `services/__init__.py`'s `AllServices` aggregate.

## Style

- Type hints + concise docstrings on public methods.
- Stay **faithful to the bundle** — argument order, field names and enum values
  must match what the real client sends (cite the source when non-obvious).
- Keep secrets out of code and logs; redaction defaults must stay on.

## Releasing / publishing to PyPI

Releases are built and published by GitHub Actions using **PyPI trusted
publishing** (OIDC) — no API token is stored anywhere. The workflow lives in
[`.github/workflows/publish.yml`](../.github/workflows/publish.yml) and triggers
when you push a version tag (`vX.Y.Z`) or publish a GitHub Release. It builds the
sdist + wheel, runs `twine check`, then publishes via OIDC.

Typical release flow:

```bash
# 1. bump the version in pyproject.toml AND okline/__init__.py (__version__),
#    update CHANGELOG.md, commit.
# 2. (optional) build + validate locally first:
pip install build twine
python -m build                 # builds dist/*.whl and dist/*.tar.gz
twine check dist/*              # validate metadata
# 3. tag and push — this triggers the publish workflow:
git tag v2.5.3 && git push --tags
gh release create v2.5.3 --generate-notes
```

(Use the next version number, not necessarily `2.5.3`.) One-time PyPI setup adds
a *trusted publisher* for the repo/workflow/`pypi` environment; see the comments
at the top of the workflow file.

The wheel bundles the LTSM module (`ltsm.wasm`, `ltsmSandbox.js`, the bridge) and
`py.typed` via `[tool.setuptools.package-data]` in `pyproject.toml`. `dist/`,
`build/` and `*.egg-info/` are git-ignored.

## Scope & ethics

OkLine is for interoperability, research and use with **your own account**, in
compliance with LINE's Terms of Service. Please don't contribute features whose
primary purpose is spam, scraping others' data, or abuse.

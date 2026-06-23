"""Offline tests for the OkLine command-line interface (``okline/__main__.py``).

These exercise the argument parser and the individual ``cmd_*`` handlers
without any network access or Node.js.  The ``call`` / ``profile`` commands
normally build a real :class:`OkLine` client; we replace
``okline.__main__.OkLine`` with a small fake that records what was asked of it
so we can assert on parsing and dispatch behaviour instead of HTTP traffic.
"""

from __future__ import annotations

import json

import pytest

import okline.__main__ as cli
from okline import __version__
from okline.endpoints import THRIFT_ENDPOINTS

# A real endpoint key we know exists in the registry (see okline/endpoints.py).
KNOWN_ENDPOINT = "Talk.TalkService.getProfile"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeTransport:
    """Records ``call(endpoint, args, ...)`` and returns a canned dict."""

    def __init__(self, result):
        self.result = result
        self.calls = []  # list of (endpoint, args, kwargs)

    def call(self, endpoint, args, **kwargs):
        self.calls.append((endpoint, args, kwargs))
        return self.result


class FakeOkLine:
    """Stand-in for :class:`OkLine` used by ``cmd_call`` / ``cmd_profile``.

    Class-level ``result`` controls the dict the transport returns and
    ``instances`` collects every client built so tests can inspect them.
    """

    result = {"ok": True, "value": 42}
    instances = []

    def __init__(self, **kwargs):
        import types

        self.init_kwargs = kwargs
        self.transport = FakeTransport(self.result)
        self.last = None  # cmd_call only touches .last when --raw is given
        self.closed = False
        # commands that require auth check .tokens.access_token
        self.tokens = types.SimpleNamespace(
            access_token=kwargs.get("access_token") or "TKN", mid=None
        )
        FakeOkLine.instances.append(self)

    def get_profile(self):
        return self.transport.call("Talk.TalkService.getProfile", [2])

    def close(self):
        self.closed = True

    @classmethod
    def from_tokens_file(cls, path, **kwargs):
        # the CLI may load a session file (and restore E2EE) instead of raw tokens
        return cls(**kwargs)


@pytest.fixture
def fake_okline(monkeypatch):
    """Install :class:`FakeOkLine` as ``okline.__main__.OkLine`` and reset state."""
    FakeOkLine.instances = []
    FakeOkLine.result = {"ok": True, "value": 42}
    monkeypatch.setattr(cli, "OkLine", FakeOkLine)
    return FakeOkLine


def run(argv):
    """Parse ``argv`` and dispatch to the matching command; return its exit code."""
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


# ---------------------------------------------------------------------------
# build_parser — subcommand wiring
# ---------------------------------------------------------------------------
def test_parser_endpoints_no_grep():
    """`endpoints` parses with an optional, defaulted grep argument."""
    args = cli.build_parser().parse_args(["endpoints"])
    assert args.command == "endpoints"
    assert args.func is cli.cmd_endpoints
    assert args.grep is None


def test_parser_endpoints_with_grep():
    """`endpoints <substr>` captures the filter positional."""
    args = cli.build_parser().parse_args(["endpoints", "profile"])
    assert args.grep == "profile"


def test_parser_call_with_args_and_flags():
    """`call <endpoint> <argsJSON>` with flags maps to cmd_call defaults."""
    args = cli.build_parser().parse_args(["call", KNOWN_ENDPOINT, "[2]", "--raw", "--no-auth"])
    assert args.command == "call"
    assert args.func is cli.cmd_call
    assert args.endpoint == KNOWN_ENDPOINT
    assert args.args == "[2]"
    assert args.raw is True
    assert args.no_auth is True


def test_parser_call_args_default_is_empty_array():
    """Omitting the args positional defaults to the JSON empty-array literal."""
    args = cli.build_parser().parse_args(["call", KNOWN_ENDPOINT])
    assert args.args == "[]"
    assert args.raw is False
    assert args.no_auth is False


def test_parser_login_defaults():
    """`login` exposes save/wait/invert with sensible defaults."""
    args = cli.build_parser().parse_args(["login"])
    assert args.command == "login"
    assert args.func is cli.cmd_login
    assert args.save is None
    assert args.wait == pytest.approx(180.0)
    assert args.invert is False


def test_parser_profile_and_version():
    """`profile` and `version` dispatch to their handlers."""
    prof = cli.build_parser().parse_args(["profile"])
    assert prof.func is cli.cmd_profile
    ver = cli.build_parser().parse_args(["version"])
    assert ver.func is cli.cmd_version


def test_parser_auth_flags_after_subcommand():
    """Shared --token/--refresh/--tokens-file/--show-secrets attach to each
    subcommand, so they may be given *after* the subcommand name."""
    args = cli.build_parser().parse_args(
        [
            "profile",
            "--token",
            "TKN",
            "--refresh",
            "RFR",
            "--tokens-file",
            "t.json",
            "--show-secrets",
        ]
    )
    assert args.token == "TKN"
    assert args.refresh == "RFR"
    assert args.tokens_file == "t.json"
    assert args.show_secrets is True


def test_parser_no_subcommand_defaults_to_menu():
    """A bare invocation parses with no func; main() then launches the menu."""
    args = cli.build_parser().parse_args([])
    assert getattr(args, "func", None) is None


# ---------------------------------------------------------------------------
# cmd_version
# ---------------------------------------------------------------------------
def test_cmd_version_prints_version(capsys):
    """version prints the package version and emulated client string."""
    code = run(["version"])
    out = capsys.readouterr().out
    assert code == 0
    assert __version__ in out
    assert "OkLine" in out
    assert "CHROMEOS 3.7.2" in out


# ---------------------------------------------------------------------------
# cmd_endpoints
# ---------------------------------------------------------------------------
def test_cmd_endpoints_lists_all_keys(capsys):
    """endpoints prints one line per registered key, plus a stderr count."""
    code = run(["endpoints"])
    captured = capsys.readouterr()
    assert code == 0
    # Every endpoint key appears on stdout.
    for key in THRIFT_ENDPOINTS:
        assert key in captured.out
    # The trailing summary count goes to stderr and reflects the full registry.
    assert f"{len(THRIFT_ENDPOINTS)} endpoint(s)" in captured.err


def test_cmd_endpoints_output_is_sorted(capsys):
    """endpoints emits its keys in sorted order."""
    run(["endpoints"])
    out_lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    printed_keys = [ln.split()[0] for ln in out_lines]
    assert printed_keys == sorted(THRIFT_ENDPOINTS)


def test_cmd_endpoints_grep_filters_case_insensitively(capsys):
    """endpoints <substr> keeps only matching keys (case-insensitive)."""
    code = run(["endpoints", "GETPROFILE"])
    captured = capsys.readouterr()
    assert code == 0
    out = captured.out
    assert KNOWN_ENDPOINT in out
    # A non-matching key must be filtered out.
    assert "sendMessage" not in out
    # Count reflects only the matches, not the whole registry.
    expected = [k for k in THRIFT_ENDPOINTS if "getprofile" in k.lower()]
    assert f"{len(expected)} endpoint(s)" in captured.err


def test_cmd_endpoints_grep_no_match(capsys):
    """A grep with no matches prints nothing but a 0-count summary."""
    code = run(["endpoints", "zzz-no-such-endpoint"])
    captured = capsys.readouterr()
    assert code == 0
    assert "0 endpoint(s)" in captured.err
    # No endpoint lines on stdout.
    assert captured.out.strip() == ""


# ---------------------------------------------------------------------------
# cmd_call — happy path
# ---------------------------------------------------------------------------
def test_cmd_call_parses_array_and_prints_result(fake_okline, capsys):
    """A valid call parses the JSON args array and prints the JSON result."""
    code = run(["call", KNOWN_ENDPOINT, "[2]"])
    out = capsys.readouterr().out
    assert code == 0

    # Exactly one client was built and then closed.
    assert len(fake_okline.instances) == 1
    client = fake_okline.instances[0]
    assert client.closed is True

    # The transport received the endpoint and the *parsed* args (a list).
    assert len(client.transport.calls) == 1
    endpoint, args, _kwargs = client.transport.calls[0]
    assert endpoint == KNOWN_ENDPOINT
    assert args == [2]
    assert isinstance(args, list)

    # The printed text is the JSON-encoded result dict.
    assert json.loads(out) == fake_okline.result


def test_cmd_call_default_empty_args(fake_okline, capsys):
    """Omitting args sends an empty positional list to the transport."""
    code = run(["call", KNOWN_ENDPOINT])
    capsys.readouterr()
    assert code == 0
    _endpoint, args, _kwargs = fake_okline.instances[0].transport.calls[0]
    assert args == []


def test_cmd_call_require_auth_default_true(fake_okline, capsys):
    """Without --no-auth the transport is asked to require auth."""
    run(["call", KNOWN_ENDPOINT, "[2]"])
    capsys.readouterr()
    _, _, kwargs = fake_okline.instances[0].transport.calls[0]
    assert kwargs.get("require_auth") is True


def test_cmd_call_no_auth_flag_disables_require_auth(fake_okline, capsys):
    """--no-auth flips require_auth off for pre-login endpoints."""
    run(["call", KNOWN_ENDPOINT, "[2]", "--no-auth"])
    capsys.readouterr()
    _, _, kwargs = fake_okline.instances[0].transport.calls[0]
    assert kwargs.get("require_auth") is False


def test_cmd_call_object_argument(fake_okline, capsys):
    """A struct argument inside the array survives JSON parsing intact."""
    payload = [0, {"to": "uXYZ", "text": "hi", "contentType": 0}]
    code = run(["call", "Talk.TalkService.sendMessage", json.dumps(payload)])
    capsys.readouterr()
    assert code == 0
    _, args, _ = fake_okline.instances[0].transport.calls[0]
    assert args == payload


# ---------------------------------------------------------------------------
# cmd_call — error handling (exit code 2 for bad input)
# ---------------------------------------------------------------------------
def test_cmd_call_invalid_json_returns_2(fake_okline, capsys):
    """Malformed JSON for the args array exits with code 2 and no call."""
    code = run(["call", KNOWN_ENDPOINT, "[not json"])
    err = capsys.readouterr().err
    assert code == 2
    assert "JSON array" in err
    # No client should have been built (we bail before constructing one).
    assert fake_okline.instances == []


def test_cmd_call_non_array_json_returns_2(fake_okline, capsys):
    """Valid JSON that is not a list (e.g. an object) exits with code 2."""
    code = run(["call", KNOWN_ENDPOINT, '{"a": 1}'])
    err = capsys.readouterr().err
    assert code == 2
    assert "array" in err.lower()
    assert fake_okline.instances == []


def test_cmd_call_scalar_json_returns_2(fake_okline, capsys):
    """A bare scalar (number) is rejected as a non-array."""
    code = run(["call", KNOWN_ENDPOINT, "5"])
    capsys.readouterr()
    assert code == 2
    assert fake_okline.instances == []


def test_cmd_call_unknown_endpoint_returns_2(fake_okline, capsys):
    """An endpoint name absent from the registry exits with code 2."""
    code = run(["call", "Talk.TalkService.doesNotExist", "[]"])
    err = capsys.readouterr().err
    assert code == 2
    assert "unknown endpoint" in err
    # Validation happens before any client/transport work.
    assert fake_okline.instances == []


def test_cmd_call_transport_failure_returns_1(fake_okline, capsys):
    """If the transport raises, cmd_call surfaces it as exit code 1."""

    class Boom(FakeOkLine):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

            def explode(endpoint, args, **kw):
                raise RuntimeError("kaboom")

            self.transport.call = explode

    fake_okline.instances = []
    import okline.__main__ as m

    # Swap in the raising subclass for this test only.
    orig = m.OkLine
    m.OkLine = Boom
    try:
        code = run(["call", KNOWN_ENDPOINT, "[2]"])
    finally:
        m.OkLine = orig
    err = capsys.readouterr().err
    assert code == 1
    assert "kaboom" in err


# ---------------------------------------------------------------------------
# cmd_profile (uses the same fake client)
# ---------------------------------------------------------------------------
def test_cmd_profile_prints_profile(fake_okline, capsys):
    """profile fetches via the client and prints the JSON result."""
    fake_okline.result = {"mid": "u123", "displayName": "Tester"}
    code = run(["profile"])
    out = capsys.readouterr().out
    assert code == 0
    assert json.loads(out) == fake_okline.result
    assert fake_okline.instances[0].closed is True


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------
def test_main_dispatches_version(capsys):
    """main(argv) routes through build_parser to the right handler."""
    code = cli.main(["version"])
    assert code == 0
    assert __version__ in capsys.readouterr().out


def test_main_call_roundtrip(fake_okline, capsys):
    """main() end-to-end for a valid call returns 0 and prints the result."""
    code = cli.main(["call", KNOWN_ENDPOINT, "[2]"])
    out = capsys.readouterr().out
    assert code == 0
    assert json.loads(out) == fake_okline.result

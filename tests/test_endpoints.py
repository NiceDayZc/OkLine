"""Offline tests for :mod:`okline.endpoints`.

The endpoint registry is pure data plus two tiny helpers (``thrift_path`` and
``all_method_names``).  These tests pin down:

* the registry size and shape (>=77 Thrift endpoints, well-formed values),
* that several well-known keys map to the exact gateway paths,
* the behaviour of ``thrift_path`` (prefixing + ``KeyError`` on unknown),
* that ``all_method_names`` returns a sorted snapshot of the keys,
* the ``SPECIAL_ENDPOINTS`` REST helpers, and
* the module-level base-URL constants.

Everything here is pure import-time data: no network, no Node.js.
"""

from __future__ import annotations

import pytest

from okline import endpoints
from okline.endpoints import (
    GATEWAY_BASE,
    LEGY_BACKUP_BASE,
    LEGY_BASE,
    OBS_BASE,
    SPECIAL_ENDPOINTS,
    THRIFT_ENDPOINTS,
    all_method_names,
    thrift_path,
)


# ---------------------------------------------------------------------------
# THRIFT_ENDPOINTS — size and shape
# ---------------------------------------------------------------------------
def test_has_at_least_77_endpoints():
    """The registry must expose the full documented endpoint surface."""
    assert len(THRIFT_ENDPOINTS) >= 77


def test_endpoints_is_a_plain_dict():
    """The public registry is a mapping of key -> path string."""
    assert isinstance(THRIFT_ENDPOINTS, dict)


def test_keys_are_namespace_service_method_triples():
    """Every key is a dotted ``Namespace.Service.method`` triple."""
    for key in THRIFT_ENDPOINTS:
        parts = key.split(".")
        assert len(parts) == 3, f"key {key!r} is not Namespace.Service.method"
        assert all(parts), f"key {key!r} has an empty component"


def test_values_are_relative_paths():
    """Values are bare relative paths (no scheme, no leading slash, no /api/)."""
    for key, path in THRIFT_ENDPOINTS.items():
        assert isinstance(path, str) and path, f"{key!r} has a non-string value"
        assert not path.startswith("/"), f"{key!r} value starts with '/'"
        assert not path.startswith("http"), f"{key!r} value has a scheme"
        assert not path.startswith("api/"), f"{key!r} value already has 'api/'"


def test_values_are_unique():
    """No two endpoint keys should collide on the same path."""
    paths = list(THRIFT_ENDPOINTS.values())
    assert len(paths) == len(set(paths))


def test_thrift_endpoints_path_ends_with_method_name():
    """For ``thrift`` services the path tail matches the key's method name."""
    for key, path in THRIFT_ENDPOINTS.items():
        if "/thrift/" in path:
            method = key.rsplit(".", 1)[1]
            assert path.endswith("/" + method), (
                f"{key!r} -> {path!r} does not end with /{method}"
            )


# ---------------------------------------------------------------------------
# Spot-checks: well-known keys -> exact paths
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "key, expected_path",
    [
        ("Talk.TalkService.getProfile", "talk/thrift/Talk/TalkService/getProfile"),
        ("Talk.TalkService.sendMessage", "talk/thrift/Talk/TalkService/sendMessage"),
        ("Talk.AuthService.loginV2", "talk/thrift/Talk/AuthService/loginV2"),
        (
            "Talk.ChannelService.issueChannelToken",
            "talk/thrift/Talk/ChannelService/issueChannelToken",
        ),
        (
            "Relation.RelationService.addFriendByMid",
            "talk/thrift/Relation/RelationService/addFriendByMid",
        ),
        (
            "ShopService.ShopService.getOwnedProductSummaries",
            "shop/thrift/ShopService/ShopService/getOwnedProductSummaries",
        ),
        (
            "LoginQrCode.SecondaryQrCodeLoginService.createQrCode",
            "talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/createQrCode",
        ),
        ("Talk.TalkService.getE2EEPublicKey", "talk/thrift/Talk/TalkService/getE2EEPublicKey"),
    ],
)
def test_known_keys_map_to_expected_paths(key, expected_path):
    """Several anchor endpoints map to their documented gateway paths."""
    assert THRIFT_ENDPOINTS[key] == expected_path


def test_shop_endpoints_use_shop_prefix():
    """ShopService endpoints live under ``shop/thrift/`` not ``talk/thrift/``."""
    shop_keys = [k for k in THRIFT_ENDPOINTS if k.startswith("ShopService.")]
    assert shop_keys, "expected at least one ShopService endpoint"
    for key in shop_keys:
        assert THRIFT_ENDPOINTS[key].startswith("shop/thrift/")


# ---------------------------------------------------------------------------
# thrift_path()
# ---------------------------------------------------------------------------
def test_thrift_path_prefixes_with_api_slash():
    """``thrift_path`` prepends ``/api/`` to the stored relative path."""
    expected = "/api/" + THRIFT_ENDPOINTS["Talk.TalkService.getProfile"]
    assert thrift_path("Talk.TalkService.getProfile") == expected
    assert thrift_path("Talk.TalkService.getProfile") == (
        "/api/talk/thrift/Talk/TalkService/getProfile"
    )


def test_thrift_path_matches_registry_for_every_key():
    """For every registered key the helper is just ``/api/`` + the value."""
    for key, path in THRIFT_ENDPOINTS.items():
        assert thrift_path(key) == "/api/" + path


def test_thrift_path_always_starts_with_api():
    """The returned path is always rooted at ``/api/``."""
    for key in THRIFT_ENDPOINTS:
        assert thrift_path(key).startswith("/api/")


def test_thrift_path_raises_keyerror_on_unknown():
    """Unknown endpoint names raise ``KeyError`` (with the bad name echoed)."""
    with pytest.raises(KeyError) as excinfo:
        thrift_path("Talk.TalkService.doesNotExist")
    assert "doesNotExist" in str(excinfo.value)


def test_thrift_path_keyerror_on_empty_string():
    """An empty name is also an unknown endpoint."""
    with pytest.raises(KeyError):
        thrift_path("")


# ---------------------------------------------------------------------------
# all_method_names()
# ---------------------------------------------------------------------------
def test_all_method_names_returns_sorted_list():
    """``all_method_names`` returns the keys as a sorted list."""
    names = all_method_names()
    assert isinstance(names, list)
    assert names == sorted(THRIFT_ENDPOINTS)


def test_all_method_names_covers_every_key_exactly_once():
    """The snapshot is a complete, duplicate-free view of the registry keys."""
    names = all_method_names()
    assert set(names) == set(THRIFT_ENDPOINTS)
    assert len(names) == len(THRIFT_ENDPOINTS)


def test_all_method_names_returns_fresh_list_each_call():
    """Mutating the returned list must not corrupt the registry."""
    names = all_method_names()
    names.append("bogus")
    assert "bogus" not in all_method_names()
    assert "bogus" not in THRIFT_ENDPOINTS


# ---------------------------------------------------------------------------
# SPECIAL_ENDPOINTS
# ---------------------------------------------------------------------------
def test_special_endpoints_is_a_dict():
    """The REST-helper registry is a mapping too."""
    assert isinstance(SPECIAL_ENDPOINTS, dict)


@pytest.mark.parametrize(
    "key, expected_path",
    [
        ("operation.receive", "api/operation/receive"),
        ("longpoll.LF1", "api/talk/long-polling/LF1"),
        ("auth.tokenRefresh", "api/auth/tokenRefresh"),
    ],
)
def test_special_endpoints_known_paths(key, expected_path):
    """Operation-stream, long-poll, and token-refresh helpers are present."""
    assert SPECIAL_ENDPOINTS[key] == expected_path


def test_special_endpoints_has_obs_helpers():
    """At least one ``obs.*`` media helper is registered."""
    obs_keys = [k for k in SPECIAL_ENDPOINTS if k.startswith("obs.")]
    assert obs_keys, "expected obs.* helpers in SPECIAL_ENDPOINTS"
    for key in obs_keys:
        assert SPECIAL_ENDPOINTS[key].startswith("api/obs/")


def test_special_endpoints_do_not_overlap_thrift_keys():
    """Special REST keys are disjoint from the Thrift endpoint keys."""
    assert not (set(SPECIAL_ENDPOINTS) & set(THRIFT_ENDPOINTS))


# ---------------------------------------------------------------------------
# Base-URL constants
# ---------------------------------------------------------------------------
def test_gateway_base_url():
    """The Chrome gateway base URL is the canonical line-apps host."""
    assert GATEWAY_BASE == "https://line-chrome-gw.line-apps.com"


def test_obs_base_url():
    """The OBS media base URL is the canonical obs host."""
    assert OBS_BASE == "https://obs.line-apps.com"


def test_legy_base_urls():
    """LEGY primary and backup edges are HTTPS line-apps hosts."""
    assert LEGY_BASE == "https://legy-jp.line-apps.com"
    assert LEGY_BACKUP_BASE == "https://legy-backup.line-apps.com"


@pytest.mark.parametrize("base", [GATEWAY_BASE, OBS_BASE, LEGY_BASE, LEGY_BACKUP_BASE])
def test_base_urls_are_https_without_trailing_slash(base):
    """Every base URL is HTTPS and carries no trailing slash."""
    assert base.startswith("https://")
    assert not base.endswith("/")


def test_gateway_plus_thrift_path_forms_full_url():
    """``GATEWAY_BASE`` + ``thrift_path`` yields the real POST target."""
    url = GATEWAY_BASE + thrift_path("Talk.TalkService.getProfile")
    assert url == (
        "https://line-chrome-gw.line-apps.com/api/talk/thrift/Talk/TalkService/getProfile"
    )


# ---------------------------------------------------------------------------
# Module exports sanity
# ---------------------------------------------------------------------------
def test_module_exposes_public_helpers():
    """The two helper callables are exported from the module."""
    assert callable(endpoints.thrift_path)
    assert callable(endpoints.all_method_names)

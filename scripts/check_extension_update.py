#!/usr/bin/env python3
"""Check the Chrome Web Store for a newer LINE extension build and sync it.

OkLine faithfully reproduces the LINE Chrome extension (``CHROMEOS``): its
endpoints, its headers and — crucially — the bundled ``ltsm.wasm`` /
``ltsmSandbox.js`` crypto artifacts (``X-Hmac`` signing + Letter Sealing).
When LINE ships a new extension version, this script re-derives everything
from the live Web Store CRX:

1. download the latest CRX (extension id ``ophjlpahpchlmihnnnihgmmeilfjmjjc``);
2. unpack it (a CRX3 file is a small protobuf header followed by a plain zip);
3. compare the manifest version and the MD5 of ``ltsm.wasm`` /
   ``ltsmSandbox.js`` against the copies bundled in ``okline/ltsm/``;
4. extract every ``<ns>/thrift/...`` path from ``static/js/main.js`` and diff
   it against the registry in ``okline/endpoints.py``.

Usage::

    python scripts/check_extension_update.py             # report only
    python scripts/check_extension_update.py --apply     # also copy new artifacts

Exit code ``0`` = up to date, ``2`` = drift detected, ``1`` = error.

Only the standard library is used, so it runs anywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import ssl
import struct
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LTSM_DIR = REPO / "okline" / "ltsm"
ENDPOINTS_PY = REPO / "okline" / "endpoints.py"

EXTENSION_ID = "ophjlpahpchlmihnnnihgmmeilfjmjjc"
CRX_URL = (
    "https://clients2.google.com/service/update2/crx?response=redirect"
    "&os=win&arch=x86&os_arch=x86-64&nacl_arch=x86-64"
    "&prod=chromiumcrx&prodchannel=stable&prodversion={ver}"
    "&acceptformat=crx2,crx3"
    "&x=id%3D{id}%26uc"
)
# The update service rejects requests whose prodversion looks ancient — try a
# few plausible Chrome versions until one returns a body.
PRODVERSIONS = ("142.0.0.0", "138.0.0.0", "130.0.0.0", "124.0.0.0")

_THRIFT_PATH_RE = re.compile(r"[a-z]+/thrift/[A-Za-z]+/[A-Za-z]+/[A-Za-z0-9]+")
_APP_HEADER_RE = re.compile(r"CHROMEOS\\t([\d.]+)\\tChrome_OS\\t")


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _opener() -> urllib.request.OpenerDirector:
    """An HTTPS opener that uses certifi's CA bundle when available (macOS
    framework Pythons often ship without any default CA paths)."""
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


def download_crx() -> bytes:
    """Download the latest CRX from the Web Store, trying several prodversions."""
    last_err = "no attempt made"
    for ver in PRODVERSIONS:
        url = CRX_URL.format(ver=ver, id=EXTENSION_ID)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "okline-check"})
            with _opener().open(req, timeout=120) as resp:
                data = resp.read()
            if data[:4] == b"Cr24" and len(data) > 1000:
                print(f"downloaded CRX ({len(data):,} bytes, prodversion {ver})")
                return data
            last_err = f"prodversion {ver} returned {len(data)} bytes (HTTP 204?)"
        except Exception as exc:  # report and try the next prodversion
            last_err = f"prodversion {ver}: {exc}"
    die(f"could not download the CRX ({last_err}); is the Web Store reachable?")
    raise AssertionError  # unreachable


def unpack_crx(crx: bytes) -> zipfile.ZipFile:
    """Strip the CRX3 protobuf header and open the embedded zip."""
    if crx[:4] != b"Cr24":
        die("not a CRX file (missing 'Cr24' magic)")
    version = struct.unpack("<I", crx[4:8])[0]
    if version not in (2, 3):
        die(f"unsupported CRX version {version}")
    if version == 3:
        header_len = struct.unpack("<I", crx[8:12])[0]
        zip_start = 12 + header_len
    else:  # CRX2: magic + version + header len + key/sig lengths (4*4)
        zip_start = 16 + 16
    try:
        return zipfile.ZipFile(io.BytesIO(crx[zip_start:]))
    except zipfile.BadZipFile as exc:
        die(f"could not parse the CRX zip payload: {exc}")
        raise AssertionError from None  # unreachable


def project_thrift_paths() -> set[str]:
    src = ENDPOINTS_PY.read_text(encoding="utf-8")
    return set(_THRIFT_PATH_RE.findall(src))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--apply",
        action="store_true",
        help="copy the downloaded ltsm.wasm / ltsmSandbox.js into okline/ltsm/",
    )
    args = ap.parse_args()

    print(f"OkLine extension drift check (extension id {EXTENSION_ID})")
    crx = download_crx()
    zf = unpack_crx(crx)

    # --- manifest version ---------------------------------------------------
    manifest = zf.read("manifest.json").decode("utf-8", "replace")
    m = re.search(r'"version"\s*:\s*"([^"]+)"', manifest)
    store_version = m.group(1) if m else "?"

    # --- ltsm artifacts -----------------------------------------------------
    names = set(zf.namelist())
    drift = False

    for zip_name, local in (
        ("static/js/ltsm.wasm", LTSM_DIR / "ltsm.wasm"),
        ("static/js/ltsmSandbox.js", LTSM_DIR / "ltsmSandbox.js"),
    ):
        if zip_name not in names:
            print(f"  ! {zip_name} not found in the CRX (layout change?)")
            drift = True
            continue
        blob = zf.read(zip_name)
        new_md5 = md5(blob)
        old_md5 = md5(local.read_bytes()) if local.exists() else "<missing>"
        same = new_md5 == old_md5
        print(f"  {'=' if same else '!'} {local.name:16s} bundled={old_md5} store={new_md5}")
        if not same:
            drift = True
            if args.apply:
                local.write_bytes(blob)
                print(f"      -> updated {local}")

    # --- app header version -------------------------------------------------
    main_js = zf.read("static/js/main.js").decode("utf-8", "replace")
    hdr = _APP_HEADER_RE.search(main_js)
    if hdr:
        print(f"  = store app header: CHROMEOS\\t{hdr.group(1)}\\tChrome_OS\\t")
    else:
        print("  ! could not find the CHROMEOS app header in main.js")

    # --- endpoint registry --------------------------------------------------
    store_paths = set(_THRIFT_PATH_RE.findall(main_js))
    proj_paths = project_thrift_paths()
    added = sorted(store_paths - proj_paths)
    removed = sorted(proj_paths - store_paths)
    print(f"  = thrift endpoints: project={len(proj_paths)} store={len(store_paths)}")
    for p in added:
        print(f"      + new in store: {p}")
    for p in removed:
        print(f"      - missing in store: {p}")
    if added or removed:
        drift = True
        print("      -> update okline/endpoints.py (THRIFT_ENDPOINTS) to match")

    # --- verdict ------------------------------------------------------------
    print()
    if drift:
        print(f"DRIFT DETECTED against LINE extension {store_version}.")
        if not args.apply:
            print("Run with --apply to sync the ltsm artifacts, then review the")
            print("endpoint diff above before editing okline/endpoints.py.")
        return 2
    print(f"Up to date — the bundled build matches LINE extension {store_version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

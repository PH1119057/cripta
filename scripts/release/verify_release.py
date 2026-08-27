from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zipfile
from pathlib import Path

FORBIDDEN_MEMBERS = (
    "/.venv/",
    "/build/",
    "/dist/",
    "/var/",
    "/__pycache__/",
    "/.pytest_cache/",
    "/.mypy_cache/",
    "/.ruff_cache/",
)
FORBIDDEN_ENDINGS = (".db", ".db-shm", ".db-wal", ".pyc", ".pyo", "/.env")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_sha(path: Path) -> str:
    return path.read_text(encoding="ascii").split()[0].lower()


def machine(path: Path) -> int:
    with path.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise RuntimeError("EXE does not start with MZ")
        handle.seek(0x3C)
        offset = struct.unpack("<I", handle.read(4))[0]
        handle.seek(offset)
        if handle.read(4) != b"PE\0\0":
            raise RuntimeError("invalid PE signature")
        return struct.unpack("<H", handle.read(2))[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    dist = args.dist.resolve()

    exe = dist / "BybitStrategyWorkbench.exe"
    exe_sha = dist / "BybitStrategyWorkbench.exe.sha256"
    source = dist / f"bybit-strategy-workbench-{args.version}-source.zip"
    source_sha = source.with_name(source.name + ".sha256")
    bundle = dist / f"BybitStrategyWorkbench-{args.version}-windows-x64.zip"
    bundle_sha = bundle.with_name(bundle.name + ".sha256")
    manifest_path = dist / "RELEASE_MANIFEST.json"

    for path in (exe, exe_sha, source, source_sha, bundle, bundle_sha, manifest_path):
        if not path.is_file():
            raise RuntimeError(f"missing release artifact: {path}")

    if machine(exe) != 0x8664:
        raise RuntimeError("release EXE is not AMD64")
    for target, checksum in ((exe, exe_sha), (source, source_sha), (bundle, bundle_sha)):
        actual = sha256_file(target)
        expected = read_sha(checksum)
        if actual != expected:
            raise RuntimeError(f"checksum mismatch for {target.name}: {actual} != {expected}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["version"] != args.version:
        raise RuntimeError("release manifest version mismatch")
    if manifest["safety"]["full_live_available"] is not False:
        raise RuntimeError("release manifest unexpectedly enables full LIVE")
    if manifest["safety"]["mainnet_start_state"] != "SHADOW/DISARMED":
        raise RuntimeError("release manifest does not declare fail-closed Mainnet startup")

    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if not names or not all(name.startswith("cripta/") for name in names):
            raise RuntimeError("source archive must contain one top-level cripta/ folder")
        lowered = ["/" + name.lower() for name in names]
        bad = [
            name
            for name, low in zip(names, lowered, strict=True)
            if any(marker in low for marker in FORBIDDEN_MEMBERS)
            or low.endswith(FORBIDDEN_ENDINGS)
        ]
        if bad:
            raise RuntimeError("forbidden source archive members: " + ", ".join(bad[:20]))

    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        expected = {
            exe.name,
            exe_sha.name,
            source.name,
            source_sha.name,
            manifest_path.name,
            "RELEASE_README.txt",
            "verify_clean_windows.ps1",
        }
        if names != expected:
            raise RuntimeError(f"unexpected release bundle members: {sorted(names ^ expected)}")

    print("release_artifacts=verified pe=AMD64 checksums=ok source_hygiene=ok bundle=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

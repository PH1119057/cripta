from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import struct
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

EXCLUDED_DIRS = {
    ".git",
    ".hypothesis",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "var",
}
EXCLUDED_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".log",
    ".pyc",
    ".pyo",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}
EXCLUDED_NAMES = {".env", ".coverage"}
FORBIDDEN_TEXT = (
    "C:" + "\\" + "Users" + "\\",
    "/" + "home" + "/" + "oai",
    "/" + "mnt" + "/" + "data",
)
FIXED_ZIP_TIME = (2000, 1, 1, 0, 0, 0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256(path: Path) -> Path:
    digest = sha256_file(path)
    output = path.with_name(path.name + ".sha256")
    output.write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return output


def pe_machine(path: Path) -> int:
    with path.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise RuntimeError(f"not a PE executable: {path}")
        handle.seek(0x3C)
        pe_offset = struct.unpack("<I", handle.read(4))[0]
        handle.seek(pe_offset)
        if handle.read(4) != b"PE\0\0":
            raise RuntimeError(f"invalid PE header: {path}")
        return struct.unpack("<H", handle.read(2))[0]


def iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if not path.is_file():
            continue
        if path.name in EXCLUDED_NAMES:
            continue
        lower_name = path.name.lower()
        if any(lower_name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
            continue
        if lower_name.endswith((".zip", ".sha256")):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def scan_source(root: Path, files: list[Path]) -> None:
    violations: list[str] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for marker in FORBIDDEN_TEXT:
            if marker.lower() in text.lower():
                violations.append(f"{rel}: contains forbidden local path marker {marker!r}")
    if violations:
        raise RuntimeError("source hygiene failed:\n" + "\n".join(violations))


def deterministic_source_zip(root: Path, output: Path) -> Path:
    files = iter_source_files(root)
    scan_source(root, files)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            rel = Path("cripta") / path.relative_to(root)
            info = zipfile.ZipInfo(rel.as_posix(), FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output


def write_bundle(output: Path, members: list[Path]) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(members, key=lambda item: item.name.lower()):
            info = zipfile.ZipInfo(path.name, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    dist = args.dist.resolve()
    exe = dist / "BybitStrategyWorkbench.exe"
    if not exe.is_file():
        raise RuntimeError(f"missing executable: {exe}")
    machine = pe_machine(exe)
    if machine != 0x8664:
        raise RuntimeError(f"expected AMD64 PE machine 0x8664, got 0x{machine:04x}")

    source_zip = dist / f"bybit-strategy-workbench-{args.version}-source.zip"
    deterministic_source_zip(root, source_zip)
    exe_sha = write_sha256(exe)
    source_sha = write_sha256(source_zip)

    from bybit_workbench import __version__

    if __version__ != args.version:
        raise RuntimeError(f"version mismatch: package={__version__} requested={args.version}")

    manifest = {
        "schema": "bybit-workbench-release-v1",
        "version": __version__,
        "built_at_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "architecture": platform.machine(),
        "pe_machine": "AMD64/0x8664",
        "artifacts": {
            exe.name: {"sha256": sha256_file(exe), "bytes": exe.stat().st_size},
            source_zip.name: {
                "sha256": sha256_file(source_zip),
                "bytes": source_zip.stat().st_size,
            },
        },
        "inputs": {
            "uv.lock": sha256_file(root / "uv.lock"),
            "pyproject.toml": sha256_file(root / "pyproject.toml"),
            "pyinstaller_spec": sha256_file(root / "bybit_workbench.spec"),
        },
        "safety": {
            "default_profile": "replay",
            "mainnet_start_state": "SHADOW/DISARMED",
            "full_live_available": False,
            "micro_live_automatic": False,
            "secrets_embedded": False,
        },
        "build_environment": {
            "os_name": os.name,
            "system": platform.system(),
        },
    }
    manifest_path = dist / "RELEASE_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    release_readme = dist / "RELEASE_README.txt"
    release_readme.write_text(
        "Bybit Strategy Workbench " + __version__ + "\n"
        "Windows 10/11 x64 one-file release.\n"
        "Default startup is Replay; Mainnet always starts SHADOW/DISARMED.\n"
        "API credentials are not included in this release.\n"
        "Verify BybitStrategyWorkbench.exe.sha256 before execution.\n"
        "On another clean Windows x64 machine, run verify_clean_windows.ps1.\n",
        encoding="utf-8",
    )
    clean_verify = dist / "verify_clean_windows.ps1"
    clean_verify.write_bytes((root / "scripts" / "release" / "verify_clean_windows.ps1").read_bytes())

    bundle = dist / f"BybitStrategyWorkbench-{args.version}-windows-x64.zip"
    write_bundle(
        bundle,
        [
            exe,
            exe_sha,
            source_zip,
            source_sha,
            manifest_path,
            release_readme,
            clean_verify,
        ],
    )
    bundle_sha = write_sha256(bundle)

    print(f"exe={exe}")
    print(f"exe_sha256={sha256_file(exe)}")
    print(f"source={source_zip}")
    print(f"source_sha256={sha256_file(source_zip)}")
    print(f"bundle={bundle}")
    print(f"bundle_sha256={sha256_file(bundle)}")
    print(f"bundle_sha256_file={bundle_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Issue #158 / #24: prepare an isolated pyarrow decoder site on data-disk.

This is a research-only package materializer, not a system/runtime install.
It downloads one exact PyPI wheel, verifies size+SHA256, extracts it only under
a new /data/cripta/research/cache root, validates the import, and atomically
renames the completed staging root. No DELETE operation is performed.

The wheel identity was resolved on 2026-10-30 from PyPI metadata for the host
CPython 3.12 / x86_64 / glibc 2.39.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import re
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

VERSION = "R158_PYARROW26_ISOLATED_SITE_PREPARE_V1"
PACKAGE_VERSION = "26.0.0"
WHEEL_FILENAME = "pyarrow-26.0.0-cp312-cp312-manylinux_2_28_x86_64.whl"
WHEEL_URL = (
    "https://files.pythonhosted.org/packages/88/e4/"
    "9cd356d906e71bd79b0c3fc5c9a54e01a0020dcf14c152ccfbcb503c7298/"
    + WHEEL_FILENAME
)
WHEEL_SHA256 = "6a628922ba20705fa964ca73e4ef959c2fb2f14b9bbec5589a6a1e68e6257c85"
WHEEL_BYTES = 53_931_932
OUTPUT_ROOT = Path("/data/cripta/research/cache/r158_pyarrow26_site_20261030")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _effective_actor_uid(require_actor: bool) -> int | None:
    getter = getattr(os, "geteuid", None)
    if getter is None:
        if require_actor:
            raise RuntimeError("EFFECTIVE_UID_UNAVAILABLE")
        return None
    uid = int(getter())
    if require_actor:
        import pwd

        if uid != pwd.getpwnam("cripta").pw_uid:
            raise PermissionError("EFFECTIVE_ACTOR_NOT_CRIPTA")
    return uid


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_wheel(url: str) -> bytes:
    if url != WHEEL_URL:
        raise ValueError("UNAPPROVED_WHEEL_URL")
    request = urllib.request.Request(
        url, headers={"User-Agent": "CRIPTA-R158-PyArrow-Prep/1"}
    )
    with urllib.request.urlopen(request, timeout=120.0) as response:
        if int(getattr(response, "status", 200)) != 200:
            raise RuntimeError(f"WHEEL_HTTP_STATUS {response.status}")
        return response.read()


def safe_extract_wheel(wheel: Path, site: Path) -> int:
    count = 0
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            posix = PurePosixPath(info.filename)
            if (
                posix.is_absolute()
                or ".." in posix.parts
                or not posix.parts
            ):
                raise ValueError("UNSAFE_WHEEL_MEMBER")
            target = site.joinpath(*posix.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("xb") as dst:
                while True:
                    chunk = src.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            count += 1
    return count


def default_import_validator(site: Path) -> dict:
    sys.path.insert(0, str(site))
    try:
        pyarrow = importlib.import_module("pyarrow")
        parquet = importlib.import_module("pyarrow.parquet")
        if pyarrow.__version__ != PACKAGE_VERSION:
            raise ValueError("PYARROW_VERSION_MISMATCH")
        return {
            "pyarrow_version": pyarrow.__version__,
            "pyarrow_file": str(Path(pyarrow.__file__).resolve()),
            "parquet_module_file": str(Path(parquet.__file__).resolve()),
        }
    finally:
        try:
            sys.path.remove(str(site))
        except ValueError:
            pass


def prepare(
    source_commit: str,
    *,
    output_root: Path = OUTPUT_ROOT,
    fetcher: Callable[[str], bytes] = fetch_wheel,
    import_validator: Callable[[Path], dict] = default_import_validator,
    wheel_url: str = WHEEL_URL,
    wheel_sha256: str = WHEEL_SHA256,
    wheel_bytes: int = WHEEL_BYTES,
    require_actor: bool = True,
    enforce_platform: bool = True,
) -> dict:
    if not SHA_RE.fullmatch(source_commit):
        raise ValueError("SOURCE_COMMIT_MUST_BE_EXACT_40HEX")
    _effective_actor_uid(require_actor)
    if enforce_platform:
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError("PYTHON_ABI_NOT_CP312")
        if platform.machine() != "x86_64":
            raise RuntimeError("MACHINE_NOT_X86_64")
        libc_name, libc_version = platform.libc_ver()
        if libc_name != "glibc":
            raise RuntimeError("LIBC_NOT_GLIBC")
        glibc = tuple(int(part) for part in libc_version.split(".")[:2])
        if glibc < (2, 28):
            raise RuntimeError("GLIBC_TOO_OLD_FOR_WHEEL")
    if not output_root.parent.is_dir():
        raise FileNotFoundError("OUTPUT_PARENT_MISSING")
    if not os.access(output_root.parent, os.W_OK | os.X_OK):
        raise PermissionError("OUTPUT_PARENT_NOT_WRITABLE_BY_ACTOR")

    staging = output_root.with_name(output_root.name + ".partial")
    if output_root.exists():
        raise FileExistsError("IMMUTABLE_DECODER_SITE_ALREADY_EXISTS")
    if staging.exists():
        raise FileExistsError("INCOMPLETE_DECODER_STAGING_ALREADY_EXISTS")

    os.umask(0o007)
    staging.mkdir(mode=0o2770)
    wheel_dir = staging / "wheel"
    site = staging / "site"
    wheel_dir.mkdir(mode=0o2770)
    site.mkdir(mode=0o2770)
    wheel = wheel_dir / WHEEL_FILENAME

    payload = fetcher(wheel_url)
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("WHEEL_FETCHER_MUST_RETURN_BYTES")
    if len(payload) != wheel_bytes:
        raise ValueError("WHEEL_SIZE_MISMATCH")
    with wheel.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if sha256(wheel) != wheel_sha256:
        raise ValueError("WHEEL_SHA256_MISMATCH")

    extracted_files = safe_extract_wheel(wheel, site)
    validation = import_validator(site)
    manifest = {
        "version": VERSION,
        "status": "ISOLATED_DECODER_SITE_COMPLETE",
        "source_commit": source_commit,
        "package": "pyarrow",
        "package_version": PACKAGE_VERSION,
        "wheel_filename": WHEEL_FILENAME,
        "wheel_url": wheel_url,
        "wheel_bytes": wheel.stat().st_size,
        "wheel_sha256": sha256(wheel),
        "extracted_files": extracted_files,
        "python_version": platform.python_version(),
        "machine": platform.machine(),
        "libc": list(platform.libc_ver()),
        "validation": validation,
        "site_path": str(output_root / "site"),
        "system_runtime_modified": False,
        "trading_effect": "NONE",
        "delete_operations": "NONE",
    }
    with (staging / "MANIFEST.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(staging, output_root)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source_commit), sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_BACKUP_ROOT = Path(
    os.environ.get(
        "CRIPTA_RELEASE_BACKUP_ROOT",
        "/data/cripta/script_archive/release_backups",
    )
)
BACKUP_NAME = re.compile(r"^20\d{6}_\d{6}_[0-9a-f]{40}_to_[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class BackupHeadroom:
    backup_root: str
    free_bytes: int
    latest_backup: str | None
    reference_dump_bytes: int | None
    status: str
    detail: str


def inspect_backup_headroom(backup_root: Path) -> BackupHeadroom:
    if not backup_root.is_dir():
        return BackupHeadroom(
            backup_root=str(backup_root),
            free_bytes=0,
            latest_backup=None,
            reference_dump_bytes=None,
            status="FAIL",
            detail="BACKUP_ROOT_MISSING",
        )

    free_bytes = _free_bytes(backup_root)
    candidates = sorted(
        (
            item
            for item in backup_root.iterdir()
            if item.is_dir() and BACKUP_NAME.fullmatch(item.name)
        ),
        key=lambda item: item.name,
        reverse=True,
    )
    if not candidates:
        return BackupHeadroom(
            backup_root=str(backup_root),
            free_bytes=free_bytes,
            latest_backup=None,
            reference_dump_bytes=None,
            status="FAIL",
            detail="NO_REFERENCE_DEPLOY_BACKUP",
        )

    latest = candidates[0]
    dump = latest / "cripta_before.dump"
    if not dump.is_file():
        return BackupHeadroom(
            backup_root=str(backup_root),
            free_bytes=free_bytes,
            latest_backup=latest.name,
            reference_dump_bytes=None,
            status="FAIL",
            detail="REFERENCE_DB_DUMP_MISSING",
        )

    reference_dump_bytes = dump.stat().st_size
    if free_bytes <= reference_dump_bytes:
        status = "FAIL"
        detail = (
            "INSUFFICIENT_HEADROOM_AGAINST_LATEST_VERIFIED_DUMP "
            f"free_bytes={free_bytes} reference_dump_bytes={reference_dump_bytes}"
        )
    else:
        status = "PASS"
        detail = (
            "MINIMUM_HEADROOM_EXCEEDS_LATEST_VERIFIED_DUMP "
            f"free_bytes={free_bytes} reference_dump_bytes={reference_dump_bytes}"
        )
    return BackupHeadroom(
        backup_root=str(backup_root),
        free_bytes=free_bytes,
        latest_backup=latest.name,
        reference_dump_bytes=reference_dump_bytes,
        status=status,
        detail=detail,
    )


def _free_bytes(path: Path) -> int:
    stat = os.statvfs(path)
    return int(stat.f_bavail * stat.f_frsize)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    args = parser.parse_args()

    result = inspect_backup_headroom(args.backup_root)
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

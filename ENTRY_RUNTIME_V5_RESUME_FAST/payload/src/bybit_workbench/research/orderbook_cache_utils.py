from __future__ import annotations

import re
from datetime import date
from pathlib import Path

_DEPTH_RE = re.compile(r"_ob(?P<depth>\d+)\.data\.zip$", re.IGNORECASE)


def find_local_orderbook_archive(
    archive_dir: Path,
    *,
    symbol: str,
    day: date,
) -> tuple[Path, int] | None:
    prefix = f"{day.isoformat()}_{symbol.upper()}_ob"
    candidates: list[tuple[int, int, Path]] = []
    for path in archive_dir.glob(f"{prefix}*.data.zip"):
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        match = _DEPTH_RE.search(path.name)
        if match is None:
            continue
        depth = int(match.group("depth"))
        candidates.append((depth, path.stat().st_size, path))
    if not candidates:
        return None
    depth, _, path = max(candidates, key=lambda item: (item[0], item[1]))
    return path, depth

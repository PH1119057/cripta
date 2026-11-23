"""Read-only discovery of CRIPTA systemd service deployment coupling.

ARCH §50.1 / Issue #849. This inventory is generated from actual reviewed
unit files, not a hand-written service count; it has no mutation capability.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

UNIT_ROOTS = ("operations/systemd", "operations/dispatcher_v2")
SHARED_RUNTIME = "/srv/cripta/runtime/current"
KNOWN_CONTROL_UNITS = (
    "cripta-universal-entry-consumer.service",
    "cripta-universal-entry-consumer@.service",
    "cripta-universal-exit-consumer.service",
    "cripta-dashboard.service",
)


@dataclass(frozen=True, slots=True)
class UnitDependency:
    unit: str
    source_path: str
    exec_start: str
    working_directory: str
    wants: tuple[str, ...]
    requires: tuple[str, ...]
    after: tuple[str, ...]
    executable_uses_shared_runtime: bool
    configuration_uses_shared_runtime: bool
    scope: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _properties(text: str) -> dict[str, list[str]]:
    """Minimal systemd key reader; repeated keys are preserved, no shell run."""
    data: dict[str, list[str]] = {}
    logical: list[str] = []
    previous = ""
    for line in text.splitlines():
        if line.endswith("\\"):
            previous += line[:-1].strip() + " "
            continue
        logical.append(previous + line)
        previous = ""
    if previous:
        raise ValueError("UNIT_CONTINUATION_INCOMPLETE")
    for line in logical:
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, val = line.split("=", 1)
        data.setdefault(key.strip(), []).append(val.strip())
    return data


def _tokens(values: list[str]) -> tuple[str, ...]:
    return tuple(sorted({value for row in values for value in row.split() if value}))


def unit_inventory(root: Path) -> tuple[UnitDependency, ...]:
    """Every unit in the approved operations source trees is classified."""
    if not root.is_dir():
        raise ValueError("INVENTORY_ROOT_UNAVAILABLE")
    seen: set[str] = set()
    units: list[UnitDependency] = []
    for subtree in UNIT_ROOTS:
        directory = root / subtree
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.service")):
            if path.is_symlink() or not path.is_file():
                raise ValueError("INVENTORY_UNIT_NOT_REGULAR")
            if path.name in seen:
                raise ValueError("INVENTORY_UNIT_DUPLICATE:" + path.name)
            seen.add(path.name)
            content = path.read_text(encoding="utf-8")
            props = _properties(content)
            commands = " ".join(props.get("ExecStart", []))
            working = " ".join(props.get("WorkingDirectory", []))
            environment = " ".join(props.get("Environment", []))
            exposed = SHARED_RUNTIME in (commands + " " + working + " " + environment)
            scope = (
                "LEGACY_SHARED_RUNTIME"
                if exposed else "INDEPENDENCE_NOT_ATTESTED"
            )
            units.append(UnitDependency(
                unit=path.name,
                source_path=path.relative_to(root).as_posix(),
                exec_start=commands,
                working_directory=working,
                wants=_tokens(props.get("Wants", [])),
                requires=_tokens(props.get("Requires", [])),
                after=_tokens(props.get("After", [])),
                executable_uses_shared_runtime=SHARED_RUNTIME in (commands + " " + working),
                configuration_uses_shared_runtime=SHARED_RUNTIME in environment,
                scope=scope,
            ))
    if not units:
        raise ValueError("INVENTORY_EMPTY")
    return tuple(sorted(units, key=lambda row: row.unit))


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect service release boundaries")
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    try:
        units = unit_inventory(args.repo)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 2
    result = {
        "status": "READ_ONLY_AS_BUILT_INVENTORY",
        "unit_count": len(units),
        "shared_runtime_count": sum(unit.scope == "LEGACY_SHARED_RUNTIME" for unit in units),
        "units": [unit.to_dict() for unit in units],
        "deploy_authorized": False,
    }
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

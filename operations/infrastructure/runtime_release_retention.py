from __future__ import annotations

import argparse
import re
import shutil
import stat
from pathlib import Path

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


class RuntimeReleaseRetentionError(RuntimeError):
    pass


def _require_sha(value: str, *, label: str) -> str:
    if not _SHA40.fullmatch(value):
        raise RuntimeReleaseRetentionError(f"{label} must be exact 40-hex Git SHA")
    return value


def _validated_release_dirs(root: Path) -> dict[str, Path]:
    try:
        root_stat = root.stat()
    except FileNotFoundError as exc:
        raise RuntimeReleaseRetentionError(f"runtime release root missing: {root}") from exc
    if not stat.S_ISDIR(root_stat.st_mode):
        raise RuntimeReleaseRetentionError(f"runtime release root is not a directory: {root}")

    releases: dict[str, Path] = {}
    for entry in root.iterdir():
        name = entry.name
        entry_stat = entry.lstat()
        if entry.is_symlink() or not stat.S_ISDIR(entry_stat.st_mode) or not _SHA40.fullmatch(name):
            raise RuntimeReleaseRetentionError(
                f"unexpected runtime release entry: {entry}"
            )
        releases[name] = entry
    return releases


def validate_current(
    *,
    root: Path,
    current_link: Path,
    expected_sha: str,
) -> None:
    expected_sha = _require_sha(expected_sha, label="expected_sha")
    releases = _validated_release_dirs(root)
    if expected_sha not in releases:
        raise RuntimeReleaseRetentionError(
            f"expected runtime release directory missing: {root / expected_sha}"
        )
    try:
        current_target = current_link.resolve(strict=True)
        expected_target = releases[expected_sha].resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeReleaseRetentionError("runtime current identity missing") from exc
    if current_target != expected_target:
        raise RuntimeReleaseRetentionError(
            f"runtime current mismatch: actual={current_target} expected={expected_target}"
        )


def prune_runtime_releases(
    *,
    root: Path,
    current_link: Path,
    current_sha: str,
    previous_sha: str,
) -> tuple[str, ...]:
    current_sha = _require_sha(current_sha, label="current_sha")
    previous_sha = _require_sha(previous_sha, label="previous_sha")
    if current_sha == previous_sha:
        raise RuntimeReleaseRetentionError(
            "current_sha and previous_sha must identify distinct releases"
        )

    releases = _validated_release_dirs(root)
    for required in (current_sha, previous_sha):
        if required not in releases:
            raise RuntimeReleaseRetentionError(
                f"required retained runtime release missing: {root / required}"
            )

    validate_current(root=root, current_link=current_link, expected_sha=current_sha)

    pruned: list[str] = []
    for sha, path in sorted(releases.items()):
        if sha in {current_sha, previous_sha}:
            print(
                f"RUNTIME_RELEASE_RETAINED={path} "
                f"reason={'current' if sha == current_sha else 'previous'}"
            )
            continue

        current_stat = path.lstat()
        if path.is_symlink() or not stat.S_ISDIR(current_stat.st_mode) or path.parent != root:
            raise RuntimeReleaseRetentionError(
                f"runtime release changed before prune: {path}"
            )
        shutil.rmtree(path)
        pruned.append(sha)
        print(f"RUNTIME_RELEASE_PRUNED={path}")

    remaining = _validated_release_dirs(root)
    expected_remaining = {current_sha, previous_sha}
    if set(remaining) != expected_remaining:
        raise RuntimeReleaseRetentionError(
            "runtime release retention postcondition mismatch: "
            f"remaining={sorted(remaining)} expected={sorted(expected_remaining)}"
        )

    print(
        "RUNTIME_RELEASE_RETENTION=PASS "
        f"current={current_sha} previous={previous_sha} pruned={len(pruned)}"
    )
    return tuple(pruned)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--current-link", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--expected-sha", required=True)

    prune = sub.add_parser("prune")
    prune.add_argument("--current-sha", required=True)
    prune.add_argument("--previous-sha", required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "validate":
            validate_current(
                root=args.root,
                current_link=args.current_link,
                expected_sha=args.expected_sha,
            )
            print(
                "RUNTIME_RELEASE_RETENTION_PREFLIGHT=PASS "
                f"current={args.expected_sha}"
            )
            return

        prune_runtime_releases(
            root=args.root,
            current_link=args.current_link,
            current_sha=args.current_sha,
            previous_sha=args.previous_sha,
        )
    except RuntimeReleaseRetentionError as exc:
        raise SystemExit(f"RUNTIME_RELEASE_RETENTION_BLOCKED: {exc}") from exc


if __name__ == "__main__":
    main()

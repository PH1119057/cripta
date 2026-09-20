from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

SOURCE_REPOSITORY = "PH1119057/cripta"
RELEASE_REF = "refs/heads/main"
DEFAULT_INSTALLER = "operations/infrastructure/install_verified_release.sh"


def _run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _git_bytes(repo: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],
        check=True,
        capture_output=True,
    )
    return result.stdout


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe(path: str) -> str:
    value = PurePosixPath(path)
    if value.is_absolute() or ".." in value.parts or not path:
        raise ValueError(f"unsafe repository path: {path!r}")
    return value.as_posix()


def _payload_digest(files: dict[str, bytes]) -> str:
    lines = [
        f"{_sha256(body)}  {path}\n"
        for path, body in sorted(files.items())
    ]
    return _sha256("".join(lines).encode())


def _changed_paths(
    repo: Path,
    baseline: str,
    release: str,
) -> tuple[list[tuple[str, str]], list[str]]:
    raw = _run(repo, "diff", "--name-status", "--no-renames", baseline, release)
    changed: list[tuple[str, str]] = []
    deleted: list[str] = []
    for line in raw.splitlines():
        if not line:
            continue
        status_code, raw_path = line.split("\t", 1)
        path = _safe(raw_path)
        if status_code == "A":
            changed.append((path, "NEW_FILE"))
        elif status_code == "M":
            changed.append((path, "EXISTING_MODIFY"))
        elif status_code == "D":
            deleted.append(path)
        else:
            raise RuntimeError(f"unsupported git diff status {status_code!r} for {path}")
    return changed, deleted


def _zip_write_bytes(
    archive: zipfile.ZipFile,
    name: str,
    body: bytes,
    *,
    executable: bool = False,
) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    mode = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
    info.external_attr = mode
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, body)


def build(args: argparse.Namespace) -> tuple[Path, str]:
    repo = args.repo.resolve()
    release = args.release_commit.lower()
    baseline = args.expected_baseline_commit.lower()
    for label, value in (("release_commit", release), ("expected_baseline_commit", baseline)):
        if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError(f"{label} must be a full lowercase Git SHA")

    remote = _run(repo, "ls-remote", "origin", RELEASE_REF).split()
    if not remote or remote[0] != release:
        observed = remote[0] if remote else "NONE"
        raise RuntimeError(
            f"remote main mismatch: expected release {release}, got {observed}"
        )
    _run(repo, "cat-file", "-e", f"{release}^{{commit}}")
    _run(repo, "cat-file", "-e", f"{baseline}^{{commit}}")
    tree = _run(repo, "rev-parse", f"{release}^{{tree}}")

    changed, deleted = _changed_paths(repo, baseline, release)
    installer_path = _safe(args.installer_source_path)
    if installer_path not in {path for path, _ in changed}:
        # The installer may be unchanged relative to baseline, but it must exist in release.
        _run(repo, "cat-file", "-e", f"{release}:{installer_path}")

    payload: dict[str, bytes] = {}
    changed_manifest: list[dict[str, str]] = []
    for path, kind in changed:
        body = _git_bytes(repo, release, path)
        payload[path] = body
        changed_manifest.append(
            {"path": path, "kind": kind, "sha256": _sha256(body)}
        )

    installer = _git_bytes(repo, release, installer_path)
    payload_sha = _payload_digest(payload)
    created_at = datetime.now(UTC).replace(microsecond=0).isoformat()

    manifest = {
        "manifest_version": 1,
        "patch_id": args.patch_id,
        "patch_version": args.patch_version,
        "build": args.build,
        "created_at": created_at,
        "source_repository": SOURCE_REPOSITORY,
        "release_ref": RELEASE_REF,
        "release_commit": release,
        "release_tree_sha": tree,
        "expected_baseline_commit": baseline,
        "baseline_policy": "EXACT_COMMIT",
        "prerequisites": args.prerequisite
        or [
            "GitHub main independently verified",
            "mainnet gate disarmed",
            "no active real Strategy execution permission",
            "no open/pending Exchange mutation",
        ],
        "changed_files": changed_manifest,
        "deleted_files": deleted,
        "does_change": args.does_change or ["exact files listed in changed_files"],
        "does_not_change": args.does_not_change
        or [
            "does not arm mainnet",
            "does not activate Strategy",
            "does not create Exchange mutation",
        ],
        "required_services": args.required_service,
        "restart_services": args.restart_service,
        "prechecks": args.precheck,
        "targeted_tests": args.targeted_test,
        "payload_sha256": payload_sha,
        "installer_source_path": installer_path,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    readme = (
        "CRIPTA verified server release\n"
        f"release_commit={release}\n"
        f"expected_baseline_commit={baseline}\n"
        "Source authority: GitHub PH1119057/cripta main.\n"
    ).encode()

    sums: dict[str, bytes] = {
        "MANIFEST.json": manifest_bytes,
        "install.sh": installer,
        "README_RU.md": readme,
    }
    sums.update({f"payload/{path}": body for path, body in payload.items()})
    sha_lines = "".join(
        f"{_sha256(body)}  {name}\n" for name, body in sorted(sums.items())
    ).encode()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        _zip_write_bytes(archive, "MANIFEST.json", manifest_bytes)
        _zip_write_bytes(archive, "install.sh", installer, executable=True)
        _zip_write_bytes(archive, "README_RU.md", readme)
        _zip_write_bytes(archive, "SHA256SUMS.txt", sha_lines)
        for path, body in sorted(payload.items()):
            executable = bool(
                int(_run(repo, "ls-tree", release, path).split()[0], 8) & 0o111
            )
            _zip_write_bytes(
                archive,
                f"payload/{path}",
                body,
                executable=executable,
            )
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"ZIP CRC failure: {bad}")
        roots = {name.split("/", 1)[0] for name in archive.namelist()}
        if roots - {"MANIFEST.json", "install.sh", "SHA256SUMS.txt", "README_RU.md", "payload"}:
            raise RuntimeError("unexpected ZIP root structure")

    zip_sha = _sha256(output.read_bytes())
    companion = output.with_name(output.name + ".sha256")
    companion.write_text(f"{zip_sha}  {output.name}\n", encoding="ascii")
    return output, zip_sha


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--release-commit", required=True)
    p.add_argument("--expected-baseline-commit", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--patch-id", default="slot-lifecycle-canon")
    p.add_argument("--patch-version", default="V1")
    p.add_argument("--build", default="BUILD_01")
    p.add_argument("--installer-source-path", default=DEFAULT_INSTALLER)
    p.add_argument("--prerequisite", action="append", default=[])
    p.add_argument("--does-change", action="append", default=[])
    p.add_argument("--does-not-change", action="append", default=[])
    p.add_argument("--required-service", action="append", default=[])
    p.add_argument("--restart-service", action="append", default=[])
    p.add_argument("--precheck", action="append", default=[])
    p.add_argument("--targeted-test", action="append", default=[])
    return p


def main() -> int:
    args = parser().parse_args()
    output, digest = build(args)
    print(f"ZIP={output}")
    print(f"ZIP_SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

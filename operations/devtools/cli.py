from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

DEFAULT_ROOT = Path(os.environ.get("CRIPTA_SOURCE_ROOT", "/srv/cripta/source_checkout"))
DEFAULT_STATE = Path(os.environ.get("CRIPTA_OPS_ROOT", "/srv/cripta-share/operations"))
DEFAULT_GIT_DIR = Path(os.environ.get("CRIPTA_GIT_DIR", "/srv/cripta-share/git-mirror.git"))
SENSITIVE = {
    "ENTRY": ("entry",), "EXIT": ("exit",), "RISK": ("risk",),
    "EXECUTION": ("execution", "bybit_live"), "MAYAK": ("mayak",),
    "DISPATCHER": ("dispatcher",), "SUPERVISOR": ("supervisor",),
    "OPERATIONS": ("operations", "scripts/"), "DOCS": ("docs/", "agents.md"),
    "TESTS": ("tests/",), "RESEARCH": ("research",),
}
TRADING_CLASSES = {"ENTRY", "EXIT", "RISK", "EXECUTION", "SUPERVISOR"}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def classify(paths: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {key: [] for key in SENSITIVE}
    for raw in paths:
        normalized = raw.replace("\\", "/").lower()
        for group, needles in SENSITIVE.items():
            if any(needle in normalized for needle in needles):
                result[group].append(raw)
    return {key: values for key, values in result.items() if values}


@dataclass
class Artifacts:
    command: str
    root: Path = DEFAULT_STATE

    def __post_init__(self) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.directory = self.root / self.command / stamp
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log = self.directory / "full.log"
        self.machine = self.directory / "result.json"
        self.summary = self.directory / "summary.txt"

    def write(self, result: dict[str, Any], lines: list[str]) -> int:
        atomic_json(self.machine, result)
        text = "\n".join(lines) + "\n"
        self.summary.write_text(text, encoding="utf-8")
        print(text, end="")
        print(f"artifacts={self.directory}")
        return 0 if result.get("status") == "PASS" else 1


def run(
    command: list[str], cwd: Path, log: Path, env: dict[str, str] | None = None
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, check=False, env=env
    )
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"$ {' '.join(command)}\n{completed.stdout}{completed.stderr}\n")
    return {"command": command, "returncode": completed.returncode,
            "seconds": round(time.monotonic() - started, 3)}


def git_head(root: Path) -> str | None:
    marker = root / "PROJECT_GIT_HEAD.txt"
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip().splitlines()[0]
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True,
                            capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def git_dirty(root: Path) -> bool | None:
    git_directory = root / ".git"
    command = ["git", "status", "--porcelain"]
    if not git_directory.exists():
        return None
    result = subprocess.run(command, cwd=root, text=True,
                            capture_output=True, check=False)
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def state_command(args: argparse.Namespace) -> int:
    art = Artifacts("state", args.ops_root)
    services: dict[str, str] = {}
    for service in args.services:
        completed = subprocess.run(["systemctl", "is-active", service], text=True,
                                   capture_output=True, check=False)
        services[service] = completed.stdout.strip() or "unknown"
    state_result: dict[str, Any] = {
        "status": "PASS", "observed_at": now(), "root": str(args.root),
        "git_head": git_head(args.root), "dirty": git_dirty(args.root),
        "disk": shutil.disk_usage(args.root)._asdict(), "services": services,
    }
    return art.write(state_result, ["CRIPTA STATE: PASS",
                              f"git_head={state_result['git_head']}",
                              f"dirty={state_result['dirty']}",
                              "services=" + ", ".join(f"{k}:{v}" for k, v in services.items())])


def gate_steps(root: Path, python: str) -> list[list[str]]:
    return [
        [python, "-m", "compileall", "-q", "src", "production/src", "operations"],
        [python, "-m", "ruff", "check", "operations/devtools", "tests/test_devtools.py"],
        [python, "-m", "pytest", "-q", "tests/test_devtools.py"],
        [python, "-m", "pytest", "-q", "tests", "--ignore=tests/test_strategy_dispatcher.py",
         "--ignore=tests/test_strategy_dispatcher_runtime.py"],
    ]


def execute_gate(root: Path, python: str, log: Path) -> list[dict[str, Any]]:
    steps = []
    source_environment = os.environ.copy()
    source_environment["PYTHONPATH"] = str(root / "src")
    for command in gate_steps(root, python):
        step = run(command, root, log, source_environment)
        steps.append(step)
        if step["returncode"]:
            return steps
    dispatcher = root / "production/src/bybit_workbench/strategy_dispatcher"
    if dispatcher.exists():
        temporary = Path(tempfile.mkdtemp(prefix="cripta-dispatcher-gate-"))
        package = temporary / "src/bybit_workbench"
        shutil.copytree(root / "src/bybit_workbench", package)
        shutil.copytree(dispatcher, package / "strategy_dispatcher")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(temporary / "src")
        command = [python, "-m", "pytest", "-q", "tests/test_strategy_dispatcher.py",
                   "tests/test_strategy_dispatcher_runtime.py",
                   "tests/test_strategy_dispatcher_passive_runtime.py"]
        steps.append(run(command, root, log, environment))
    return steps


def gate_command(args: argparse.Namespace) -> int:
    art = Artifacts("gate", args.ops_root)
    steps = execute_gate(args.root, args.python, art.log)
    status = "PASS" if steps and all(step["returncode"] == 0 for step in steps) else "FAIL"
    result = {"status": status, "observed_at": now(), "root": str(args.root), "steps": steps}
    failed = next((" ".join(x["command"]) for x in steps if x["returncode"]), "none")
    return art.write(result, [f"CRIPTA GATE: {status}", f"steps={len(steps)}",
                              f"failed={failed}"])


def changed_paths(root: Path, baseline: str) -> list[str]:
    command = ["git", "diff", "--name-only", baseline, "--"]
    if not (root / ".git").exists() and DEFAULT_GIT_DIR.exists():
        installed_head = git_head(root)
        if not installed_head:
            raise RuntimeError("installed git head is unavailable")
        command = ["git", f"--git-dir={DEFAULT_GIT_DIR}", "diff", "--name-only",
                   baseline, installed_head, "--"]
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return [line for line in result.stdout.splitlines() if line]


def diff_command(args: argparse.Namespace) -> int:
    art = Artifacts("diff-report", args.ops_root)
    try:
        paths = changed_paths(args.root, args.baseline)
        groups = classify(paths)
        status, error = "PASS", None
    except RuntimeError as exc:
        paths, groups, status, error = [], {}, "FAIL", str(exc)
    trading = sorted(set(groups) & TRADING_CLASSES)
    result = {"status": status, "baseline": args.baseline, "paths": paths,
              "classes": groups, "trading_sensitive": trading, "error": error}
    return art.write(result, [f"CRIPTA DIFF: {status}", f"files={len(paths)}",
                              f"classes={','.join(groups) or 'NONE'}",
                              f"trading_sensitive={','.join(trading) or 'NONE'}"])


def docs_audit_command(args: argparse.Namespace) -> int:
    art = Artifacts("docs-audit", args.ops_root)
    findings = []
    docs = args.root / "docs"
    for path in docs.rglob("*") if docs.exists() else []:
        if not path.is_file():
            continue
        low = path.name.lower()
        if low in {"manifest.json", "readme.txt"}:
            findings.append(
                {"kind": "transport_metadata", "path": str(path.relative_to(args.root))}
            )
        if any(word in low for word in ("resume", "next_task", "handoff")):
            findings.append(
                {"kind": "possible_stale_handoff", "path": str(path.relative_to(args.root))}
            )
    agents_path = args.root / "AGENTS.md"
    agents = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    for reference in ("PROJECT_ARCHITECTURE_RU.md", "DOCUMENT_AUTHORITY_RU.md"):
        if reference not in agents:
            findings.append({"kind": "agents_reference_missing", "path": reference})
    result = {"status": "PASS" if not findings else "WARN", "findings": findings,
              "observed_at": now()}
    # WARN is a successful audit execution, not a tool failure.
    result["status_code"] = result["status"]
    result["status"] = "PASS"
    return art.write(result, [f"CRIPTA DOCS AUDIT: {result['status_code']}",
                              f"findings={len(findings)}"])


def safe_member(name: str) -> str:
    value = name.replace("\\", "/")
    if value.startswith("/") or ".." in Path(value).parts:
        raise ValueError(f"unsafe archive path: {name}")
    return value


def load_patch(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    with zipfile.ZipFile(path) as archive:
        names = [safe_member(name) for name in archive.namelist()]
        if "MANIFEST.json" not in names:
            raise ValueError("MANIFEST.json missing")
        manifest = json.loads(archive.read("MANIFEST.json"))
        payload = {name[8:]: archive.read(name) for name in names
                   if name.startswith("payload/") and not name.endswith("/")}
    files = manifest.get("files", [])
    expected = {item["path"]: item for item in files}
    if set(payload) != set(expected):
        raise ValueError("payload and manifest file list differ")
    for name, body in payload.items():
        if hashlib.sha256(body).hexdigest() != expected[name].get("sha256"):
            raise ValueError(f"bad SHA256: {name}")
    return manifest, payload


def baseline_check(root: Path, manifest: dict[str, Any]) -> list[str]:
    errors = []
    baseline = manifest.get("baseline", {})
    policy = baseline.get("policy", "EXACT_COMMIT")
    head = git_head(root)
    allowed = baseline.get("commits", [baseline.get("commit")])
    if policy in {"EXACT_COMMIT", "ALLOWED_COMMITS"} and head not in allowed:
        errors.append(f"wrong baseline: current={head}")
    dirty = git_dirty(root)
    if dirty:
        errors.append("dirty tree")
    for item in manifest.get("files", []):
        before = item.get("before_sha256")
        target = root / safe_member(item["path"])
        if before and (not target.exists() or sha256(target) != before):
            errors.append(f"before hash mismatch: {item['path']}")
    if dirty is None and not all(item.get("before_sha256") or not (root / item["path"]).exists()
                                 for item in manifest.get("files", [])):
        errors.append("no git metadata: before_sha256 required for existing files")
    return errors


def overlay(root: Path, payload: dict[str, bytes]) -> Path:
    parent = Path(tempfile.mkdtemp(prefix="cripta-patch-"))
    destination = parent / "overlay"
    ignored = shutil.ignore_patterns(".git", "__pycache__", ".venv")
    shutil.copytree(root, destination, ignore=ignored)
    for name, body in payload.items():
        target = destination / safe_member(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return destination


def patch_report(args: argparse.Namespace, action: str) -> int:
    art = Artifacts("patch", args.ops_root)
    try:
        manifest, payload = load_patch(args.patch)
        errors = baseline_check(args.root, manifest)
        classes = classify(list(payload))
        trading = sorted(set(classes) & TRADING_CLASSES)
        if action == "inspect":
            errors = []
        overlay_root = None
        gate = None
        if action in {"precheck", "install"} and not errors:
            overlay_root = overlay(args.root, payload)
            gate_steps_result = execute_gate(overlay_root, args.python, art.log)
            gate = "PASS" if all(x["returncode"] == 0 for x in gate_steps_result) else "FAIL"
            if gate != "PASS":
                errors.append("overlay gate failed")
        install_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_" + manifest.get("id", "patch")
        if action == "install" and not errors:
            backup = args.ops_root / "patch-backups" / install_id
            backup.mkdir(parents=True, exist_ok=False)
            metadata = {"id": install_id, "root": str(args.root), "files": [],
                        "manifest": manifest, "installed_at": now()}
            for name, body in payload.items():
                target, saved = args.root / name, backup / "files" / name
                existed = target.exists()
                if existed:
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, saved)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(target.suffix + ".patch-tmp")
                temporary.write_bytes(body)
                os.replace(temporary, target)
                metadata["files"].append({"path": name, "existed": existed})
            atomic_json(backup / "install.json", metadata)
        status = "PASS" if not errors else "FAIL"
        result = {"status": status, "action": action, "patch": str(args.patch),
                  "patch_id": manifest.get("id"), "files": sorted(payload), "classes": classes,
                  "trading_sensitive": trading, "overlay_gate": gate, "errors": errors}
    except Exception as exc:
        result = {"status": "FAIL", "action": action, "patch": str(args.patch),
                  "errors": [f"{type(exc).__name__}: {exc}"]}
    trading_result = cast(list[str], result.get("trading_sensitive", []))
    error_result = cast(list[str], result.get("errors", []))
    return art.write(result, [f"CRIPTA PATCH {action.upper()}: {result['status']}",
                              f"overlay_gate={result.get('overlay_gate')}",
                              "trading_sensitive="
                              + (",".join(trading_result) or "NONE"),
                              f"errors={'; '.join(error_result) or 'none'}"])


def rollback_command(args: argparse.Namespace) -> int:
    art = Artifacts("patch-rollback", args.ops_root)
    backup = args.ops_root / "patch-backups" / args.install_id
    try:
        metadata = json.loads((backup / "install.json").read_text(encoding="utf-8"))
        root = Path(metadata["root"])
        for item in metadata["files"]:
            target, saved = root / item["path"], backup / "files" / item["path"]
            if item["existed"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(saved, target)
            elif target.exists():
                target.unlink()
        status, error = "PASS", None
    except Exception as exc:
        status, error = "FAIL", f"{type(exc).__name__}: {exc}"
    return art.write({"status": status, "install_id": args.install_id, "error": error},
                     [f"CRIPTA PATCH ROLLBACK: {status}", f"error={error or 'none'}"])


def patch_status_command(args: argparse.Namespace) -> int:
    art = Artifacts("patch-status", args.ops_root)
    path = args.ops_root / "patch-backups" / args.install_id / "install.json"
    if path.exists():
        metadata = json.loads(path.read_text(encoding="utf-8"))
        result = {"status": "PASS", "install_id": args.install_id, "install": metadata}
        lines = ["CRIPTA PATCH STATUS: PASS", f"install_id={args.install_id}"]
    else:
        result = {"status": "FAIL", "install_id": args.install_id,
                  "error": "install_id not found"}
        lines = ["CRIPTA PATCH STATUS: FAIL", "error=install_id not found"]
    return art.write(result, lines)


def job_command(args: argparse.Namespace, kind: str) -> int:
    art = Artifacts(kind, args.ops_root)
    job_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + f"_{args.target}"
    job_dir = args.ops_root / kind / "jobs" / job_id
    result = {"status": "PASS", "job_id": job_id, "kind": kind, "target": args.target,
              "state": "CREATED", "started_at": now(), "hours": getattr(args, "hours", None),
              "trading_effect": "NONE", "heartbeat": None, "result": None}
    atomic_json(job_dir / "status.json", result)
    return art.write(result, [f"CRIPTA {kind.upper()}: CREATED", f"job_id={job_id}",
                              "trading_effect=NONE"])


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    value.add_argument("--ops-root", type=Path, default=DEFAULT_STATE)
    value.add_argument("--python", default=sys.executable)
    sub = value.add_subparsers(dest="command", required=True)
    state = sub.add_parser("state")
    state.add_argument("--services", nargs="*", default=["cripta-dashboard.service",
                                                          "cripta-mayak-v2.service"])
    sub.add_parser("gate")
    diff = sub.add_parser("diff-report")
    diff.add_argument("baseline")
    sub.add_parser("docs-audit")
    patch = sub.add_parser("patch")
    patch.add_argument("action", choices=["inspect", "precheck", "install"])
    patch.add_argument("patch", type=Path)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("install_id")
    status = sub.add_parser("status")
    status.add_argument("install_id")
    soak = sub.add_parser("soak")
    soak.add_argument("target")
    soak.add_argument("--hours", type=float, required=True)
    proof = sub.add_parser("field-proof")
    proof.add_argument("target")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "state":
        return state_command(args)
    if args.command == "gate":
        return gate_command(args)
    if args.command == "diff-report":
        return diff_command(args)
    if args.command == "docs-audit":
        return docs_audit_command(args)
    if args.command == "patch":
        return patch_report(args, args.action)
    if args.command == "rollback":
        return rollback_command(args)
    if args.command == "status":
        return patch_status_command(args)
    if args.command in {"soak", "field-proof"}:
        return job_command(args, args.command)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
OUTPUT = REPORTS / "root_package_audit_20260824"


@dataclass(frozen=True)
class Package:
    name: str
    family: str
    bytes: int
    modified_ns: int
    sha256: str
    zip_ok: bool
    extracted_dir: bool
    has_fix_marker: bool
    report_evidence: tuple[str, ...]
    declared_report_roots: tuple[str, ...]
    complete_report_roots: tuple[str, ...]
    current_payload_files: int
    current_payload_matches: int
    current_payload_exact: bool
    payload_lineage: str
    disposition: str


def family_for(stem: str) -> str:
    upper = stem.upper()
    match = re.match(r"^(EO\d+|P\d+[A-Z]?|SE\d+|ZS\d+)", upper)
    if match:
        return match.group(1)
    for prefix in (
        "ENTRY_BOT_LIVE_SCANNER_P48",
        "ENTRY_BOT_P48",
        "ENTRY_V1_FULL_PANEL_PATCH",
        "ENTRY_P31_RUNTIME",
        "ENTRY_RUNTIME",
        "GUI_CONNECTION",
        "APP_RUNTIME_SNAPSHOT",
        "APP_2DAY_LOGS",
    ):
        if upper.startswith(prefix):
            return prefix
    return re.split(r"_V\d|_\d+", upper, maxsplit=1)[0]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def report_index() -> dict[str, tuple[str, ...]]:
    index: dict[str, list[str]] = {}
    if not REPORTS.is_dir():
        return {}
    for marker in REPORTS.rglob("*"):
        if not marker.is_file() or marker.name.lower() not in {
            "run_complete.json",
            "summary.json",
            "provenance.json",
            "summary_ru.md",
        }:
            continue
        text = str(marker.relative_to(ROOT)).upper()
        for code in set(re.findall(r"(?:EO\d+|P\d+[A-Z]?|SE\d+|ZS\d+)", text)):
            index.setdefault(code, []).append(str(marker.relative_to(ROOT)))
    return {key: tuple(sorted(values)) for key, values in index.items()}


def package_contract(
    package_dir: Path,
) -> tuple[tuple[str, ...], tuple[str, ...], int, int, str]:
    declared: set[str] = set()
    pattern = re.compile(r"reports[\\/]([A-Za-z0-9_.-]+)", re.IGNORECASE)
    if package_dir.is_dir():
        for path in package_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {
                ".py", ".ps1", ".sh", ".md", ".txt", ".json"
            }:
                continue
            try:
                text = path.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue
            declared.update(match.group(1) for match in pattern.finditer(text))

    complete: list[str] = []
    completion_names = {
        "run_complete.json", "summary.json", "provenance.json", "summary_ru.md"
    }
    for root_name in sorted(declared):
        report_root = REPORTS / root_name
        if report_root.is_dir() and any(
            path.is_file() and path.name.lower() in completion_names
            for path in report_root.rglob("*")
        ):
            complete.append(root_name)

    total = 0
    matches = 0
    payload_paths: list[str] = []
    payload = package_dir / "payload"
    if payload.is_dir():
        for area in ("src", "tests", "scripts"):
            source_root = payload / area
            target_root = ROOT / area
            if not source_root.is_dir():
                continue
            for source in source_root.rglob("*"):
                if not source.is_file():
                    continue
                total += 1
                payload_paths.append(f"{area}/{source.relative_to(source_root).as_posix()}")
                target = target_root / source.relative_to(source_root)
                if target.is_file() and digest(source) == digest(target):
                    matches += 1
    lineage_source = "\n".join(sorted(payload_paths)) or package_dir.name
    lineage = hashlib.sha256(lineage_source.encode("utf-8")).hexdigest()[:16]
    return tuple(sorted(declared)), tuple(complete), total, matches, lineage


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    evidence = report_index()
    provisional: list[dict[str, object]] = []
    for path in sorted(ROOT.glob("*.zip"), key=lambda item: item.name.upper()):
        family = family_for(path.stem)
        try:
            with zipfile.ZipFile(path) as archive:
                zip_ok = archive.testzip() is None
        except (OSError, zipfile.BadZipFile):
            zip_ok = False
        provisional.append(
            {
                "path": path,
                "family": family,
                "zip_ok": zip_ok,
                "extracted_dir": (ROOT / path.stem).is_dir(),
                "has_fix_marker": bool(
                    re.search(r"FIX|HOTFIX|SAFE|RESUME|CLEAN|WORKING", path.stem.upper())
                ),
                "report_evidence": evidence.get(family, ()),
                "contract": package_contract(ROOT / path.stem),
            }
        )

    newest: dict[tuple[str, str], Path] = {}
    for row in provisional:
        path = row["path"]
        assert isinstance(path, Path)
        family = str(row["family"])
        lineage = str(row["contract"][4])
        key = (family, lineage)
        current = newest.get(key)
        if current is None or (path.stat().st_mtime_ns, path.name) > (
            current.stat().st_mtime_ns,
            current.name,
        ):
            newest[key] = path

    packages: list[Package] = []
    for row in provisional:
        path = row["path"]
        assert isinstance(path, Path)
        family = str(row["family"])
        zip_ok = bool(row["zip_ok"])
        reports = tuple(row["report_evidence"])
        declared, complete, payload_total, payload_matches, lineage = row["contract"]
        payload_exact = payload_total > 0 and payload_total == payload_matches
        latest_in_lineage = path == newest[(family, lineage)]
        if not zip_ok:
            disposition = "quarantine_corrupt"
        elif complete and payload_exact and latest_in_lineage:
            disposition = "keep_verified_report_and_current_code"
        elif complete and payload_exact:
            disposition = "quarantine_duplicate_current_payload"
        elif complete:
            disposition = "ran_but_superseded_or_shared_report"
        elif payload_exact and latest_in_lineage:
            disposition = "keep_current_code_without_report_contract"
        elif payload_exact:
            disposition = "quarantine_duplicate_current_payload"
        elif latest_in_lineage:
            disposition = "review_latest_without_complete_report"
        else:
            disposition = "quarantine_superseded_candidate"
        packages.append(
            Package(
                name=path.name,
                family=family,
                bytes=path.stat().st_size,
                modified_ns=path.stat().st_mtime_ns,
                sha256=digest(path),
                zip_ok=zip_ok,
                extracted_dir=bool(row["extracted_dir"]),
                has_fix_marker=bool(row["has_fix_marker"]),
                report_evidence=reports,
                declared_report_roots=declared,
                complete_report_roots=complete,
                current_payload_files=payload_total,
                current_payload_matches=payload_matches,
                current_payload_exact=payload_exact,
                payload_lineage=lineage,
                disposition=disposition,
            )
        )

    with (OUTPUT / "packages.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "name",
                "family",
                "bytes",
                "modified_ns",
                "sha256",
                "zip_ok",
                "extracted_dir",
                "has_fix_marker",
                "report_evidence",
                "declared_report_roots",
                "complete_report_roots",
                "current_payload_files",
                "current_payload_matches",
                "current_payload_exact",
                "payload_lineage",
                "disposition",
            ],
        )
        writer.writeheader()
        for item in packages:
            row = asdict(item)
            row["report_evidence"] = " | ".join(item.report_evidence)
            row["declared_report_roots"] = " | ".join(item.declared_report_roots)
            row["complete_report_roots"] = " | ".join(item.complete_report_roots)
            writer.writerow(row)

    counts: dict[str, int] = {}
    for item in packages:
        counts[item.disposition] = counts.get(item.disposition, 0) + 1
    payload = {
        "root": str(ROOT),
        "packages": len(packages),
        "families": len({item.family for item in packages}),
        "counts": counts,
        "destructive_actions_performed": False,
        "warning": (
            "Disposition is a review plan, not proof. Nothing may be deleted until "
            "the final candidate reproduces its report and tests."
        ),
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, cast

APP_ROOT = Path(os.environ.get("CRIPTA_APP_ROOT", "/srv/cripta"))
CODE_ROOT = Path(os.environ.get("CRIPTA_CODE_ROOT", APP_ROOT / "source_checkout"))
REPORT_ROOT = Path(os.environ.get("CRIPTA_REPORT_ROOT", "/srv/cripta-share/reports"))
STAGING_ROOT = Path(os.environ.get("CRIPTA_ARCHIVE_STAGING", "/srv/cripta-share/.archive_jobs"))
JOB_ROOT = Path(os.environ.get("CRIPTA_ARCHIVE_JOB_ROOT", "/var/lib/cripta/archive_jobs"))
DB_DSN = os.environ.get("CRIPTA_DB_DSN", "dbname=cripta user=cripta host=/var/run/postgresql")
ARCHIVE_VERSION = "2.1"
PROFILES = {"CODE", "ANALYSIS_FULL", "ANALYSIS_FULL_WITH_RESEARCH", "FULL_RECOVERY", "RESEARCH"}
PERIODS = {"3d": 3, "10d": 10, "all": None}
STAGES = (
    "PREPARE",
    "CODE",
    "REPORTS",
    "STATISTICS",
    "POSTGRESQL",
    "RESEARCH",
    "LOGS",
    "INDEX",
    "FINALIZE",
    "SMOKE",
    "DONE",
)
_STATE_LOCK = threading.RLock()
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "test_gate_venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "cache",
    "staging",
    "backup",
    "backups",
    "releases",
    "old_releases",
    "incoming",
    "reports",
    "research_runs",
    "datasets",
}
EXCLUDED_SUFFIXES = {".zip", ".dump", ".sqlite", ".db", ".pyc"}
SERVICE_NAMES = (
    "cripta-dashboard.service",
    "cripta-private-runtime.service",
    "cripta-mayak-v2.service",
    "cripta-strategy-dispatcher.service",
    "cripta-position-supervisor.service",
    "postgresql.service",
)


@dataclass(frozen=True)
class TableExport:
    table: str
    path: str
    time_candidates: tuple[str, ...]


TABLE_EXPORTS = (
    TableExport("runtime.executions", "exchange/executions.jsonl", ("exec_time_ms", "received_at")),
    TableExport("runtime.private_events", "exchange/private_events.jsonl", ("received_at",)),
    TableExport(
        "runtime.trade_commands",
        "execution/commands.jsonl",
        ("requested_at_epoch_ms", "created_at"),
    ),
    TableExport(
        "runtime.connection_events",
        "system_quality/connection_events.jsonl",
        ("occurred_at", "created_at"),
    ),
    TableExport(
        "runtime.reconciliation_runs",
        "execution/reconciliation_runs.jsonl",
        ("created_at", "started_at"),
    ),
    TableExport("runtime.hot_orders", "execution/hot_orders.jsonl", ("updated_at", "created_at")),
    TableExport(
        "runtime.hot_positions", "execution/hot_positions.jsonl", ("updated_at", "created_at")
    ),
    TableExport("runtime.wallet_latest", "risk/wallet_latest.jsonl", ("updated_at", "created_at")),
    TableExport(
        "runtime.trade_settings", "risk/trade_settings.jsonl", ("updated_at", "created_at")
    ),
    TableExport(
        "runtime.trade_settings_history",
        "risk/trade_settings_history.jsonl",
        ("changed_at", "created_at"),
    ),
    TableExport("runtime.entry_decisions", "entry/decisions.jsonl", ("created_at", "decided_at")),
    TableExport("monitoring.opportunities", "strategy/signals.jsonl", ("updated_at", "created_at")),
    TableExport(
        "monitoring.opportunity_events",
        "strategy/signal_events.jsonl",
        ("created_at", "observed_at"),
    ),
    TableExport("mayak_v2.snapshots", "mayak/snapshots.jsonl", ("observed_at", "created_at")),
    TableExport("mayak_v2.coin_minutes", "mayak/coin_minutes.jsonl", ("minute_at", "observed_at")),
    TableExport("mayak_v2.events", "mayak/events.jsonl", ("event_at", "created_at")),
    TableExport("mayak_v2.state_events", "mayak/state_events.jsonl", ("observed_at", "created_at")),
    TableExport(
        "mayak_v2.observation_journal",
        "mayak/observation_journal.jsonl",
        ("observed_at", "created_at"),
    ),
    TableExport("mayak_v2.liquidations", "mayak/liquidations.jsonl", ("observed_at", "created_at")),
    TableExport(
        "position_supervisor.snapshots", "supervisor/snapshots.jsonl", ("observed_at", "created_at")
    ),
    TableExport(
        "position_supervisor.transitions",
        "supervisor/transitions.jsonl",
        ("observed_at", "created_at"),
    ),
    TableExport("strategy_dispatcher.runs", "dispatcher/runs.jsonl", ("observed_at", "created_at")),
    TableExport(
        "strategy_dispatcher.assessments",
        "dispatcher/assessments.jsonl",
        ("observed_at", "created_at"),
    ),
    TableExport(
        "research_context.event_links", "analytics/event_links.jsonl", ("event_at", "created_at")
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    os.replace(temporary, path)


def _job_path(job_id: str) -> Path:
    if not job_id or any(character not in "0123456789abcdef-" for character in job_id):
        raise ValueError("некорректный идентификатор задания")
    return JOB_ROOT / f"{job_id}.json"


def read_job(job_id: str) -> dict[str, Any]:
    path = _job_path(job_id)
    if not path.is_file():
        raise FileNotFoundError("задание архива не найдено")
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _STATE_LOCK:
        state = read_job(job_id)
        state.update(changes)
        state["heartbeat_at_utc"] = datetime.now(UTC).isoformat()
        _json_write(_job_path(job_id), state)
        return state


def start_job(profile: str = "ANALYSIS_FULL", period: str = "3d") -> dict[str, Any]:
    profile, period = profile.upper(), period.lower()
    if profile not in PROFILES:
        raise ValueError("неизвестный профиль архива")
    if period not in PERIODS:
        raise ValueError("период должен быть 3d, 10d или all")
    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    job_id = str(uuid.uuid4())
    cutoff = datetime.now(UTC).replace(microsecond=0)
    state = {
        "job_id": job_id,
        "archive_version": ARCHIVE_VERSION,
        "profile": profile,
        "period": period,
        "status": "QUEUED",
        "stage": "PREPARE",
        "percent": 0,
        "created_at_utc": cutoff.isoformat(),
        "bundle_cutoff_time_utc": cutoff.isoformat(),
        "elapsed_seconds": 0,
        "eta_seconds": None,
        "output": None,
        "error": None,
    }
    _json_write(_job_path(job_id), state)
    threading.Thread(
        target=_run_job, args=(job_id,), daemon=True, name=f"archive-v2-{job_id[:8]}"
    ).start()
    return state


def _set_stage(job_id: str, stage: str, started: float, percent: int) -> None:
    elapsed = round(time.monotonic() - started, 1)
    eta = round(elapsed * (100 - percent) / percent, 1) if percent else None
    _update_job(
        job_id,
        status="RUNNING",
        stage=stage,
        percent=percent,
        elapsed_seconds=elapsed,
        eta_seconds=eta,
    )


def _excluded(relative: Path) -> bool:
    lowered = [part.lower() for part in relative.parts]
    return (
        any(part in EXCLUDED_PARTS or "venv" in part for part in lowered)
        or relative.suffix.lower() in EXCLUDED_SUFFIXES
        or relative.name.startswith(".env")
        or relative.name in {"rc.sh", "secrets.json"}
    )


def _code_files() -> Iterable[tuple[Path, str]]:
    root = CODE_ROOT if CODE_ROOT.is_dir() else APP_ROOT
    if not root.is_dir():
        raise RuntimeError(f"исходное дерево не найдено: {root}")
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root)
            if not _excluded(relative):
                yield path, relative.as_posix()


def _zip_files(output: Path, files: Iterable[tuple[Path, str]]) -> dict[str, Any]:
    count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for source, name in files:
            archive.write(source, name)
            count += 1
    return {"file_count": count, "bytes": output.stat().st_size, "sha256": _sha256(output)}


def _git(*command: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(CODE_ROOT), *command], capture_output=True, text=True, check=False
    )
    if completed.returncode == 0:
        return completed.stdout.strip()
    if command == ("rev-parse", "HEAD"):
        deployed_head = CODE_ROOT / "PROJECT_GIT_HEAD.txt"
        if deployed_head.is_file():
            return deployed_head.read_text(encoding="utf-8").strip()
    return None


def _component(path: Path, purpose: str, status: str = "READY", **extra: Any) -> dict[str, Any]:
    row = {
        "name": path.name,
        "purpose": purpose,
        "status": status,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    row.update(extra)
    return row


def _copy_period_files(
    source_root: Path, target: Path, cutoff: datetime, since: datetime | None
) -> dict[str, Any]:
    files: list[tuple[Path, str]] = []
    if source_root.is_dir():
        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or _excluded(path.relative_to(source_root)):
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if modified <= cutoff and (since is None or modified >= since):
                files.append((path, path.relative_to(source_root).as_posix()))
    return _zip_files(target, files)


def _columns(connection: Any, table: str) -> list[tuple[str, str]]:
    schema, name = table.split(".", 1)
    rows = connection.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (schema, name),
    ).fetchall()
    return cast(list[tuple[str, str]], rows)


def _statistics(output: Path, cutoff: datetime, since: datetime | None) -> dict[str, Any]:
    import psycopg

    manifest: dict[str, Any] = {"cutoff_utc": cutoff.isoformat(), "tables": []}
    with (
        psycopg.connect(DB_DSN) as connection,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive,
    ):
        for table_index, spec in enumerate(TABLE_EXPORTS):
            columns = _columns(connection, spec.table)
            if not columns:
                continue
            types = dict(columns)
            time_column = next((name for name in spec.time_candidates if name in types), None)
            where: str = ""
            parameters: list[Any] = []
            if time_column:
                if types[time_column] in {"bigint", "integer", "numeric"}:
                    where = f' WHERE "{time_column}" <= %s'
                    parameters.append(int(cutoff.timestamp() * 1000))
                    if since:
                        where += f' AND "{time_column}" >= %s'
                        parameters.append(int(since.timestamp() * 1000))
                else:
                    where = f' WHERE "{time_column}" <= %s'
                    parameters.append(cutoff)
                    if since:
                        where += f' AND "{time_column}" >= %s'
                        parameters.append(since)
            query = f"SELECT to_jsonb(t) FROM {spec.table} t{where} ORDER BY " + (
                f'"{time_column}"' if time_column else "1"
            )
            row_count = 0
            minimum_time: Any = None
            maximum_time: Any = None
            digest = hashlib.sha256()
            with (
                connection.cursor(name=f"archive_v2_{table_index}") as cursor,
                archive.open(spec.path, "w", force_zip64=True) as destination,
            ):
                cursor.itersize = 500
                cursor.execute(query, parameters)
                for result in cursor:
                    row = result[0]
                    payload = (json.dumps(row, ensure_ascii=False, default=str) + "\n").encode()
                    destination.write(payload)
                    digest.update(payload)
                    row_count += 1
                    if time_column:
                        observed = row.get(time_column)
                        if row_count == 1:
                            minimum_time = observed
                        maximum_time = observed
            manifest["tables"].append(
                {
                    "table": spec.table,
                    "path": spec.path,
                    "row_count": row_count,
                    "time_column": time_column,
                    "min_time": minimum_time,
                    "max_time": maximum_time,
                    "sha256": digest.hexdigest(),
                    "columns": [{"name": n, "type": t} for n, t in columns],
                }
            )
        archive.writestr(
            "STATISTICS_MANIFEST.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        )
    return {
        "tables": len(manifest["tables"]),
        "rows": sum(row["row_count"] for row in manifest["tables"]),
    }


def _postgres_dump(output: Path, cutoff: datetime) -> dict[str, Any]:
    started = datetime.now(UTC)
    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(output),
        "cripta",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError("pg_dump завершился ошибкой: " + completed.stderr[-1000:])
    return {
        "database": "cripta",
        "format": "custom",
        "no_owner": True,
        "no_privileges": True,
        "bundle_cutoff_time_utc": cutoff.isoformat(),
        "dump_started_at_utc": started.isoformat(),
        "dump_finished_at_utc": datetime.now(UTC).isoformat(),
        "bytes": output.stat().st_size,
        "sha256": _sha256(output),
    }


def _logs(output: Path, cutoff: datetime, since: datetime | None) -> dict[str, Any]:
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for service in SERVICE_NAMES:
            command = ["journalctl", "-u", service, "--no-pager", "--until", cutoff.isoformat()]
            if since:
                command += ["--since", since.isoformat()]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0 or "No journal files were opened" in result.stderr:
                raise RuntimeError(f"journalctl недоступен для {service}: {result.stderr.strip()}")
            archive.writestr(
                service + ".log",
                result.stdout + ("\nSTDERR:\n" + result.stderr if result.stderr else ""),
            )
    return {"services": len(SERVICE_NAMES)}


def _service_states() -> dict[str, str]:
    states = {}
    for service in SERVICE_NAMES:
        result = subprocess.run(
            ["systemctl", "is-active", service], capture_output=True, text=True, check=False
        )
        states[service] = result.stdout.strip() or "unknown"
    return states


def _build(job_id: str) -> Path:
    state = read_job(job_id)
    profile, period = state["profile"], state["period"]
    cutoff = datetime.fromisoformat(state["bundle_cutoff_time_utc"])
    period_days = PERIODS[period]
    since = None if period_days is None else cutoff - timedelta(days=period_days)
    started = time.monotonic()
    work = STAGING_ROOT / job_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    components: list[dict[str, Any]] = []
    _set_stage(job_id, "CODE", started, 8)
    code = work / "01_CODE.zip"
    code_meta = _zip_files(code, _code_files())
    components.append(
        _component(
            code,
            "Канонический исходный код, конфигурация, документация и полное дерево тестов",
            **code_meta,
        )
    )
    if profile in {"ANALYSIS_FULL", "ANALYSIS_FULL_WITH_RESEARCH", "RESEARCH"}:
        _set_stage(job_id, "REPORTS", started, 20)
        reports = work / f"02_REPORTS_{period}.zip"
        report_meta = _copy_period_files(REPORT_ROOT, reports, cutoff, since)
        components.append(
            _component(
                reports,
                "Отчёты выбранного периода",
                period_start_utc=since,
                period_end_utc=cutoff,
                **report_meta,
            )
        )
        _set_stage(job_id, "STATISTICS", started, 34)
        statistics = work / f"03_STATISTICS_{period}.zip"
        stats_meta = _statistics(statistics, cutoff, since)
        components.append(
            _component(
                statistics,
                "Компактные причинные выгрузки PostgreSQL",
                period_start_utc=since,
                period_end_utc=cutoff,
                **stats_meta,
            )
        )
    if profile != "CODE":
        _set_stage(job_id, "POSTGRESQL", started, 50)
        dump = work / "04_POSTGRESQL_FULL.dump"
        dump_manifest = _postgres_dump(dump, cutoff)
        dump_manifest_path = work / "04_POSTGRESQL_MANIFEST.json"
        _json_write(dump_manifest_path, dump_manifest)
        components.append(_component(dump, "Полный восстанавливаемый снимок PostgreSQL"))
        components.append(_component(dump_manifest_path, "Манифест снимка PostgreSQL"))
    if profile in {"ANALYSIS_FULL_WITH_RESEARCH", "RESEARCH"}:
        _set_stage(job_id, "RESEARCH", started, 64)
        research = work / "05_RESEARCH.zip"
        research_meta = _copy_period_files(APP_ROOT / "research_runs", research, cutoff, since)
        components.append(
            _component(
                research,
                "Выбранные исследовательские материалы",
                period_start_utc=since,
                period_end_utc=cutoff,
                **research_meta,
            )
        )
    if profile in {"ANALYSIS_FULL", "ANALYSIS_FULL_WITH_RESEARCH", "FULL_RECOVERY", "RESEARCH"}:
        _set_stage(job_id, "LOGS", started, 74)
        logs = work / f"06_LOGS_{period}.zip"
        log_meta = _logs(logs, cutoff, since)
        components.append(
            _component(
                logs,
                "Журналы служб выбранного периода",
                period_start_utc=since,
                period_end_utc=cutoff,
                **log_meta,
            )
        )
    _set_stage(job_id, "INDEX", started, 82)
    git_head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current") or "main"
    dirty_output = _git("status", "--porcelain")
    dirty = None if dirty_output is None else bool(dirty_output)
    index = {
        "bundle_id": job_id,
        "archive_version": ARCHIVE_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "bundle_cutoff_time_utc": cutoff.isoformat(),
        "profile": profile,
        "period": period,
        "period_start_utc": since,
        "git_head": git_head,
        "branch": branch,
        "dirty": dirty,
        "canonical_code_source": str(CODE_ROOT if CODE_ROOT.is_dir() else APP_ROOT),
        "installed_source_fingerprint": code_meta["sha256"],
        "service_states": _service_states(),
        "builder": {"module": "operations.dashboard.archive_v2", "version": ARCHIVE_VERSION},
        "components": components,
        "warning": "Профиль all может быть большим и выполняться значительно дольше."
        if period == "all"
        else None,
    }
    index_path = work / "00_INDEX.json"
    _json_write(index_path, index)
    timestamp = cutoff.strftime("%Y%m%d_%H%M%S")
    temporary_bundle = work / f"CRIPTA_SNAPSHOT_{timestamp}.zip"
    with zipfile.ZipFile(temporary_bundle, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.write(index_path, index_path.name)
        for component in components:
            archive.write(work / component["name"], component["name"])
    _set_stage(job_id, "SMOKE", started, 92)
    verify_bundle(temporary_bundle, run_code_tests=True)
    _set_stage(job_id, "FINALIZE", started, 97)
    final = REPORT_ROOT / temporary_bundle.name
    publishing = final.with_suffix(".zip.partial")
    shutil.copy2(temporary_bundle, publishing)
    os.replace(publishing, final)
    return final


def _run_job(job_id: str) -> None:
    started = time.monotonic()
    stopped = threading.Event()

    def heartbeat() -> None:
        while not stopped.wait(25):
            _update_job(job_id, elapsed_seconds=round(time.monotonic() - started, 1))

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        final = _build(job_id)
        _update_job(
            job_id,
            status="DONE",
            stage="DONE",
            percent=100,
            elapsed_seconds=round(time.monotonic() - started, 1),
            eta_seconds=0,
            output={
                "file": final.name,
                "path": str(final),
                "size": final.stat().st_size,
                "sha256": _sha256(final),
                "url": "/reports/" + final.name,
            },
        )
    except Exception as exc:  # job boundary must persist the exact failure
        _update_job(
            job_id,
            status="FAILED",
            error=f"{type(exc).__name__}: {exc}",
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
    finally:
        stopped.set()
        heartbeat_thread.join(timeout=1)


def verify_bundle(path: Path, run_code_tests: bool = False) -> dict[str, Any]:
    with zipfile.ZipFile(path) as bundle:
        names = set(bundle.namelist())
        if "00_INDEX.json" not in names or "01_CODE.zip" not in names:
            raise RuntimeError("архив не содержит индекс или компонент исходного кода")
        index = json.loads(bundle.read("00_INDEX.json"))
        expected = {"00_INDEX.json", "01_CODE.zip"}
        if index.get("profile") == "ANALYSIS_FULL":
            period = index.get("period")
            expected.update(
                {
                    f"02_REPORTS_{period}.zip",
                    f"03_STATISTICS_{period}.zip",
                    "04_POSTGRESQL_FULL.dump",
                    "04_POSTGRESQL_MANIFEST.json",
                    f"06_LOGS_{period}.zip",
                }
            )
        missing = expected - names
        if missing:
            raise RuntimeError("неполная раскладка профиля: " + ", ".join(sorted(missing)))
        cutoff = datetime.fromisoformat(index["bundle_cutoff_time_utc"])
        for component in index["components"]:
            name = component["name"]
            if name not in names:
                raise RuntimeError(f"компонент отсутствует: {name}")
            payload = bundle.read(name)
            if (
                len(payload) != component["bytes"]
                or hashlib.sha256(payload).hexdigest() != component["sha256"]
            ):
                raise RuntimeError(f"контрольная сумма компонента не совпадает: {name}")
            if name.startswith("03_STATISTICS"):
                with zipfile.ZipFile(__import__("io").BytesIO(payload)) as stats:
                    manifest = json.loads(stats.read("STATISTICS_MANIFEST.json"))
                    for table in manifest["tables"]:
                        lines = [line for line in stats.read(table["path"]).splitlines() if line]
                        if len(lines) != table["row_count"]:
                            raise RuntimeError(f"число строк не совпадает: {table['table']}")
                        for line in lines:
                            row = json.loads(line)
                            time_column = table.get("time_column")
                            if time_column and row.get(time_column) is not None:
                                raw_time = row[time_column]
                                observed = (
                                    datetime.fromtimestamp(float(raw_time) / 1000, UTC)
                                    if isinstance(raw_time, (int, float))
                                    else datetime.fromisoformat(
                                        str(raw_time).replace("Z", "+00:00")
                                    )
                                )
                                if observed > cutoff:
                                    raise RuntimeError(
                                        f"строка позже общего cutoff: {table['table']}"
                                    )
        code_payload = bundle.read("01_CODE.zip")
        with zipfile.ZipFile(__import__("io").BytesIO(code_payload)) as code:
            code_names = code.namelist()
            forbidden = [name for name in code_names if _excluded(Path(PurePosixPath(name)))]
            if forbidden:
                raise RuntimeError(
                    "в кодовом компоненте запрещённые пути: " + ", ".join(forbidden[:10])
                )
            if any(name.startswith("source_checkout/") for name in code_names):
                raise RuntimeError("код содержит дублирующий префикс source_checkout")
            if not any(name.startswith("tests/") for name in code_names):
                raise RuntimeError("код не содержит тестового дерева")
    if run_code_tests:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="cripta-v2-smoke-") as temporary:
            with zipfile.ZipFile(path) as bundle:
                code_file = Path(temporary) / "01_CODE.zip"
                code_file.write_bytes(bundle.read("01_CODE.zip"))
            root = Path(temporary) / "code"
            with zipfile.ZipFile(code_file) as code:
                code.extractall(root)
            fixtures = root / "test_data" / "fixtures"
            if fixtures.is_dir():
                shutil.copytree(fixtures, root / "reports", dirs_exist_ok=True)
            python = os.environ.get("CRIPTA_TEST_PYTHON", "/srv/cripta/test_gate_venv/bin/python")
            if not Path(python).is_file():
                import sys

                python = sys.executable
            compile_result = subprocess.run(
                [python, "-m", "compileall", "-q", "src", "operations", "production"],
                cwd=root,
                check=False,
            )
            if compile_result.returncode:
                raise RuntimeError("py_compile из распакованного архива не прошёл")
            pytest_result = subprocess.run(
                [python, "-m", "pytest", "-q", "tests/test_archive_v2.py"], cwd=root, check=False
            )
            if pytest_result.returncode:
                raise RuntimeError("целевые тесты Archive V2 из распакованного архива не прошли")
            full_result = subprocess.run(
                [
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "tests",
                    "--ignore=tests/test_strategy_dispatcher.py",
                    "--ignore=tests/test_strategy_dispatcher_runtime.py",
                ],
                cwd=root,
                env={**os.environ, "PYTHONPATH": str(root / "src")},
                check=False,
            )
            if full_result.returncode:
                raise RuntimeError("полный pytest из распакованного архива не прошёл")
            dispatcher_overlay = Path(temporary) / "dispatcher_overlay"
            shutil.copytree(root / "src", dispatcher_overlay)
            shutil.copytree(
                root / "production" / "src" / "bybit_workbench" / "strategy_dispatcher",
                dispatcher_overlay / "bybit_workbench" / "strategy_dispatcher",
                dirs_exist_ok=True,
            )
            dispatcher_result = subprocess.run(
                [
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_strategy_dispatcher.py",
                    "tests/test_strategy_dispatcher_runtime.py",
                ],
                cwd=root,
                env={**os.environ, "PYTHONPATH": str(dispatcher_overlay)},
                check=False,
            )
            if dispatcher_result.returncode:
                raise RuntimeError("тесты Диспетчера из распакованного архива не прошли")
        dump_name = next(
            (row["name"] for row in index["components"] if row["name"].endswith(".dump")), None
        )
        if dump_name:
            import tempfile

            with tempfile.TemporaryDirectory(prefix="cripta-v2-dump-") as temporary:
                dump = Path(temporary) / "database.dump"
                with zipfile.ZipFile(path) as bundle:
                    dump.write_bytes(bundle.read(dump_name))
                result = subprocess.run(
                    ["pg_restore", "--list", str(dump)], capture_output=True, check=False
                )
                if result.returncode:
                    raise RuntimeError("pg_restore --list не прошёл")
    return cast(dict[str, Any], index)

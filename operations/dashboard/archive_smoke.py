from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REQUIRED = (
    "исходники/config/strategy_dispatcher/MAYAK_HANDOFF_SCHEMA_V1.json",
    "исходники/config/strategy_dispatcher/PROFILE_SCHEMA_V1.json",
    "исходники/config/strategy_dispatcher/PROFILE_TEMPLATE_RU.json",
    "исходники/config/strategy_dispatcher/vocabulary_v1.json",
    "исходники/config/strategy_dispatcher/profiles/reference_breakout.json",
    "исходники/config/strategy_dispatcher/profiles/reference_conservative_calm.json",
    "исходники/config/strategy_dispatcher/profiles/reference_exhaustion_bounce.json",
    "исходники/docs/PROJECT_ARCHITECTURE_RU.md",
    "исходники/docs/MAYAK_ARCHITECTURE_PRINCIPLES_RU.md",
    "исходники/docs/STRATEGY_DISPATCHER_ARCHITECTURE_RU.md",
    "торговая_база/mayak_v2.observation_journal.jsonl",
    "postgresql/DATABASE_MANIFEST.json",
    "PROJECT_GIT_HEAD.txt",
    "PROJECT_TREE_STATE.json",
    "МАНИФЕСТ.json",
)


def verify_archive(path: Path, python: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        missing = [name for name in REQUIRED if name not in names]
        if missing:
            raise RuntimeError("в архиве отсутствуют обязательные файлы: " + ", ".join(missing))
        manifest = json.loads(archive.read("МАНИФЕСТ.json"))
        file_rows = manifest.get("файлы")
        if not isinstance(file_rows, list) or not file_rows:
            raise RuntimeError("главный манифест не содержит перечня файлов")
        for row in file_rows:
            name = str(row.get("путь") or "")
            if name not in names:
                raise RuntimeError(f"файл из манифеста отсутствует в ZIP: {name}")
            payload = archive.read(name)
            if len(payload) != int(row.get("размер", -1)):
                raise RuntimeError(f"размер файла не совпадает с манифестом: {name}")
            if hashlib.sha256(payload).hexdigest() != str(row.get("sha256")):
                raise RuntimeError(f"SHA-256 файла не совпадает с манифестом: {name}")
        with tempfile.TemporaryDirectory(prefix="cripta-archive-smoke-") as temporary:
            root = Path(temporary)
            archive.extractall(root)
            source = root / "исходники"
            checkout = source / "source_checkout"
            if not checkout.is_dir():
                raise RuntimeError("в архиве отсутствует полный source_checkout текущего commit")
            test_files = sorted((checkout / "tests").glob("test_*.py"))
            if len(test_files) < 100:
                raise RuntimeError(
                    f"активное тестовое дерево неполно: найдено {len(test_files)} файлов"
                )
            fixtures = checkout / "test_data" / "fixtures"
            if not fixtures.is_dir():
                raise RuntimeError("в архиве отсутствуют причинные данные для активных тестов")
            shutil.copytree(fixtures, checkout / "reports", dirs_exist_ok=True)
            main_environment = dict(os.environ)
            main_environment["PYTHONPATH"] = str(checkout / "src")
            main_command = [
                str(python),
                "-m",
                "pytest",
                "-q",
                str(checkout / "tests"),
                "--ignore=" + str(checkout / "tests" / "test_strategy_dispatcher.py"),
                "--ignore=" + str(checkout / "tests" / "test_strategy_dispatcher_runtime.py"),
            ]
            main_completed = subprocess.run(
                main_command, env=main_environment, cwd=checkout, check=False
            )
            if main_completed.returncode != 0:
                raise RuntimeError("основной test gate из распакованного архива не прошёл")
            dispatcher_overlay = root / "dispatcher_test_overlay"
            shutil.copytree(checkout / "src", dispatcher_overlay)
            shutil.copytree(
                checkout / "production" / "src" / "bybit_workbench" / "strategy_dispatcher",
                dispatcher_overlay / "bybit_workbench" / "strategy_dispatcher",
            )
            dispatcher_environment = dict(os.environ)
            dispatcher_environment["PYTHONPATH"] = str(dispatcher_overlay)
            dispatcher_command = [
                str(python),
                "-m",
                "pytest",
                "-q",
                str(checkout / "tests" / "test_strategy_dispatcher.py"),
                str(checkout / "tests" / "test_strategy_dispatcher_runtime.py"),
            ]
            dispatcher_completed = subprocess.run(
                dispatcher_command,
                env=dispatcher_environment,
                cwd=checkout,
                check=False,
            )
            if dispatcher_completed.returncode != 0:
                raise RuntimeError("test gate Диспетчера из распакованного архива не прошёл")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Проверка воспроизводимости диагностического архива проекта"
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    arguments = parser.parse_args()
    verify_archive(arguments.archive, arguments.python)
    print("ARCHIVE_SMOKE=PASS")


if __name__ == "__main__":
    main()

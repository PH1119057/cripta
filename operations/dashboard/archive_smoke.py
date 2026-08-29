from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REQUIRED = (
    "исходники/config/strategy_dispatcher/MAYAK_HANDOFF_SCHEMA_V1.json",
    "исходники/config/strategy_dispatcher/PROFILE_SCHEMA_V1.json",
    "исходники/docs/PROJECT_ARCHITECTURE_RU.md",
    "исходники/docs/MAYAK_ARCHITECTURE_PRINCIPLES_RU.md",
    "исходники/docs/STRATEGY_DISPATCHER_ARCHITECTURE_RU.md",
    "торговая_база/mayak_v2.observation_journal.jsonl",
    "postgresql/DATABASE_MANIFEST.json",
    "PROJECT_GIT_HEAD.txt",
    "PROJECT_TREE_STATE.json",
)


def verify_archive(path: Path, python: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        missing = [name for name in REQUIRED if name not in names]
        if missing:
            raise RuntimeError("в архиве отсутствуют обязательные файлы: " + ", ".join(missing))
        with tempfile.TemporaryDirectory(prefix="cripta-archive-smoke-") as temporary:
            root = Path(temporary)
            archive.extractall(root)
            source = root / "исходники"
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(source / "production" / "src")
            command = [
                str(python),
                "-m",
                "pytest",
                "-q",
                str(source / "tests" / "test_strategy_dispatcher.py"),
                str(source / "tests" / "test_strategy_dispatcher_runtime.py"),
            ]
            completed = subprocess.run(command, env=environment, cwd=root, check=False)
            if completed.returncode != 0:
                raise RuntimeError("целевые тесты Диспетчера из распакованного архива не прошли")


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

"""No-I/O research contract test for independent P90L90 strategy replay."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_exact_strategy_coin_partition_and_second48_only():
    # Other historical research tests replace scripts.research in
    # sys.modules during pytest collection; import in a clean process.
    root = Path(__file__).resolve().parents[2]
    process = subprocess.run(
        [sys.executable, "-c", (
            "import json; from scripts.research import "
            "p90l90_strategy_split_second48_v1 as r; "
            "print(json.dumps({'groups':r.GROUPS,'scenarios':r.SCENARIOS}))"
        )],
        cwd=root, check=True, capture_output=True, text=True, timeout=20,
    )
    obj = json.loads(process.stdout)
    assert obj["groups"] == [
        ["MAIN_LTC_INJ", ["LTCUSDT", "INJUSDT"]],
        ["SUB_DOT_ARB_APT", ["DOTUSDT", "ARBUSDT", "APTUSDT"]],
    ]
    assert obj["scenarios"] == {
        "BASELINE": [0, None], "SECOND_48H": [2, 48]
    }
    symbols = [symbol for _, group in obj["groups"] for symbol in group]
    assert len(symbols) == len(set(symbols)) == 5


def test_original_replay_globals_restored_even_on_failure():
    script = """
import json
from pathlib import Path
from scripts.research import p90l90_strategy_split_second48_v1 as m
original_symbols = m.replay.SYMBOLS
original_scenarios = m.replay.SCENARIOS

def success(period, output, smoke):
    assert m.replay.SYMBOLS == ("LTCUSDT", "INJUSDT")
    assert m.replay.SCENARIOS == {"BASELINE": (0, None), "SECOND_48H": (2, 48)}
    return {"status": "SMOKE_PASS"}

m.replay.run = success
got = m._run_reference_group(
    symbols=("LTCUSDT", "INJUSDT"),
    output=Path("/data/cripta/research/runs/never-written-by-fixture"),
    smoke_hours=1,
)
assert got == {"status": "SMOKE_PASS"}
assert m.replay.SYMBOLS == original_symbols
assert m.replay.SCENARIOS == original_scenarios

def fail(period, output, smoke):
    raise RuntimeError("fixture-failure")

m.replay.run = fail
try:
    m._run_reference_group(
        symbols=("DOTUSDT",),
        output=Path("/data/cripta/research/runs/never-written-by-fixture"),
        smoke_hours=1,
    )
except RuntimeError as ex:
    assert str(ex) == "fixture-failure"
else:
    assert False
assert m.replay.SYMBOLS == original_symbols
assert m.replay.SCENARIOS == original_scenarios
print(json.dumps({"ok": True}))
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root, capture_output=True, text=True, check=True, timeout=20,
    )
    assert json.loads(result.stdout) == {"ok": True}


def test_absolute_path_entrypoint_imports_from_foreign_working_dir():
    root = Path(__file__).resolve().parents[2]
    entrypoint = root / "scripts/research/p90l90_strategy_split_second48_v1.py"
    run = subprocess.run(
        [sys.executable, str(entrypoint), "--help"],
        cwd=root.parent, capture_output=True, text=True,
        check=True, timeout=20,
    )
    assert "--smoke-hours" in run.stdout
    assert "--suffix" in run.stdout

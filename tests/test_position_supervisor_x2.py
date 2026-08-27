from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from bybit_workbench.research.position_supervisor_x2 import MANDATORY_POST_FILL, run


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_x2_preflight_fails_closed_without_post_fill_layers(tmp_path: Path) -> None:
    cohort = tmp_path / "cohort.csv"
    write_csv(
        cohort,
        [
            {
                "symbol": "ETHUSDT",
                "direction": "Long",
                "fill_at": "2026-01-01T00:00:00+00:00",
                "fill_price": "100",
            }
        ],
    )
    values = {name: None for name in MANDATORY_POST_FILL}
    summary = run(
        argparse.Namespace(
            cohort=cohort, output_dir=tmp_path / "out", symbol="ETHUSDT", fraction=1.0, **values
        )
    )
    assert summary["status"] == "ЗАБЛОКИРОВАНО"
    assert summary["blocked_trades"] == 1


def test_x2_preflight_accepts_only_causal_post_fill_events(tmp_path: Path) -> None:
    cohort = tmp_path / "cohort.csv"
    trade = {
        "symbol": "ETHUSDT",
        "direction": "Long",
        "fill_at": "2026-01-01T00:00:00+00:00",
        "fill_price": "100",
    }
    write_csv(cohort, [trade])
    paths = {}
    for name in MANDATORY_POST_FILL:
        path = tmp_path / f"{name}.csv"
        write_csv(path, [{**trade, "observed_at": "2026-01-01T00:01:00+00:00"}])
        paths[name] = path
    summary = run(
        argparse.Namespace(
            cohort=cohort, output_dir=tmp_path / "out", symbol="ETHUSDT", fraction=1.0, **paths
        )
    )
    assert summary["status"] == "ГОТОВО"
    assert (
        json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))["ready_trades"]
        == 1
    )

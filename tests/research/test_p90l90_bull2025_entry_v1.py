"""Old P90L90 2026 causal Entry -> 2025 period port identity tests."""
from __future__ import annotations

import ast
import copy
import csv
import gzip
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext
from pathlib import Path
from statistics import median

D = Decimal
REPO = Path(__file__).resolve().parents[2]
OLD = REPO / "scripts/research/p90l90_entry_90d_v2.py"
NEW = REPO / "scripts/research/p90l90_bull2025_entry_v1.py"


def _named(tree: ast.AST, name: str) -> ast.FunctionDef:
    funcs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(funcs) == 1
    return funcs[0]


def _tick_loop(fn: ast.FunctionDef) -> ast.For:
    loops = [
        node for node in fn.body
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name)
        and node.target.id == "path" and isinstance(node.iter, ast.Name)
        and node.iter.id == "paths"
    ]
    assert len(loops) == 1
    return loops[0]


class NormalizePeriodNames(ast.NodeTransformer):
    def visit_Name(self, node: ast.Name) -> ast.Name:  # noqa: N802
        if node.id == "START_TS":
            node.id = "start_ts"
        elif node.id == "END_TS":
            node.id = "end_ts"
        return node


def test_old_native_trade_entry_loop_exact_ast_unchanged() -> None:
    original = ast.parse(OLD.read_text(encoding="utf8"))
    current = ast.parse(NEW.read_text(encoding="utf8"))
    o = NormalizePeriodNames().visit(copy.deepcopy(_tick_loop(_named(original, "process"))))
    n = _tick_loop(_named(current, "process"))
    assert ast.dump(o, include_attributes=False) == ast.dump(n, include_attributes=False)


def test_frozen_market_window_cohort_source_gates() -> None:
    source = NEW.read_text(encoding="utf8")
    compile(source, str(NEW), "exec")
    assert "2025-04-01T00:00:00+00:00" in source
    assert "2025-10-01T00:00:00+00:00" in source
    assert '"2025-03-22", "2025-09-30"' in source
    assert "SYMBOLS = tuple(old.SYMBOLS)" in source
    old_core = OLD.read_text(encoding="utf8")
    assert all(symbol in old_core for symbol in (
        "LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT"
    ))
    assert (
        'sha(SOURCE / ORIGINAL_REL) != acquisition["source_snapshot_sha256"][ORIGINAL_REL]'
        in source
    )
    assert "EXACT_2026_CANDIDATE_LEDGER_PARITY_PASS" in source
    assert "QC_PASS_2025_ENTRY_CANDIDATE_ONLY_NO_EXITS" in source
    assert "if os.geteuid() != 107" in source
    assert "if not q.is_file()" in source
    assert "if manifest[\"events_sha256\"] != reference[\"events_sha256\"]" in source
    assert str(NEW.relative_to(REPO)) == "scripts/research/p90l90_bull2025_entry_v1.py"


def test_period_aware_prev7d_frozen_width_is_caused_by_prior_days(
    tmp_path: Path,
) -> None:
    """Exercise exactly the period parameterized loader on 8 causal UTC days."""
    source = NEW.read_text(encoding="utf8")
    fn = _named(ast.parse(source), "load_frozen_period")
    snippet = ast.Module(body=[fn], type_ignores=[])
    getcontext().prec = 50
    def utc_day(ts: int):
        return datetime.fromtimestamp(ts, UTC).date()
    namespace = {
        "D": D, "gzip": gzip, "csv": csv, "defaultdict": defaultdict,
        "timedelta": timedelta, "median": median, "utc_day": utc_day,
        "Path": Path, "datetime": datetime
    }
    exec(compile(snippet, "<pure source-extracted loader>", "exec"), namespace)
    start = datetime(2025, 4, 1, tzinfo=UTC)
    end = datetime(2025, 4, 2, tzinfo=UTC)
    first = start - timedelta(days=7)
    low, high, lower, upper = D(100), D(110), D(102), D(108)
    width = (upper - lower) / lower * D(100)
    path = tmp_path / "states.csv.gz"
    with gzip.open(path, "wt", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "symbol", "state_effective_ts", "source_bar_end_ts", "range_low",
            "range_high", "lower_inner", "upper_inner", "working_width_pct_lower",
        ])
        writer.writeheader()
        for i in range(8 * 288):
            ts = int(first.timestamp()) + i * 300
            writer.writerow({
                "symbol": "LTCUSDT", "state_effective_ts": ts,
                "source_bar_end_ts": ts, "range_low": str(low),
                "range_high": str(high), "lower_inner": str(lower),
                "upper_inner": str(upper), "working_width_pct_lower": str(width),
            })
    states, fixed, prev7, meta = namespace["load_frozen_period"](path, start, end)
    assert len(states) == 288
    assert meta["states"] == 288
    assert abs(fixed - width) <= D("1e-46")
    assert set(prev7) == {start.date()}
    assert abs(prev7[start.date()] - width) <= D("1e-46")
    assert meta["prior_complete_days"] == 7


def test_no_future_money_claims_and_original_H9_helpers_only() -> None:
    source = NEW.read_text(encoding="utf8")
    assert "RollingClosedBars = old.RollingClosedBars" in source
    assert "geometry_candidates = old.geometry_candidates" in source
    assert "crossing = old.crossing" in source
    assert "source_paths = old.source_paths" in source
    assert "P90L90_ENTRY_BULL2025_V1" in source
    assert "No fills, no exits, no financial outcome" in source

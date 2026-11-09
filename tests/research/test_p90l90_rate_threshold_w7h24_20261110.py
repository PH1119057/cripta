"""Scope, threshold boundary, causal ordering and immutable 2.5% parity checks."""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts/research/p90l90_rate_threshold_w7h24_20261110_v1.py"
)


def source() -> str:
    return SOURCE_PATH.read_text(encoding="utf-8")


def test_compile_and_scoped_output() -> None:
    code = source()
    compile(code, str(SOURCE_PATH), "exec")
    assert "RESEARCH = \"P90L90_RATE_THRESHOLD_W7H24_V1\"" in code
    assert "p90l90_rate_threshold_w7h24_20261110_v1" in code
    assert "assert os.geteuid() == 107" in code
    assert "assert not OUT.exists() and not OUT.is_symlink()" in code
    assert "OUT.mkdir(mode=0o2770)" in code
    assert "BASELINE = RESULTS /" in code
    assert "PRIOR_RATE25 = RESULTS /" in code


def test_exact_six_thresholds_one_window_one_duration() -> None:
    code = source()
    assert "THRESHOLDS_BPS = (150, 200, 250, 300, 350, 400)" in code
    assert "WINDOW_DAYS = (7,)" in code
    assert "PAUSE_HOURS = (24,)" in code
    assert "MIN_CLOSES = 20" in code
    assert 'f"{direction}_B{threshold_bps}_W7_H24"' in code
    assert "signal_deques = {side:deque() for side in SIDES}" in code


def test_2p5_threshold_exact_at_boundary() -> None:
    tree = ast.parse(source())
    comparisons = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Compare)
        and ast.unparse(n) == "stopped * 10000 < len(q) * threshold_bps"
    ]
    assert len(comparisons) == 1
    code = compile(ast.Expression(comparisons[0]), str(SOURCE_PATH), "eval")
    env = {"q": [(0, 1)] + [(0, 0)] * 39, "stopped": 1}
    # Code continues when false, so exactly 2.5% is an active signal.
    assert eval(code, {}, {**env, "threshold_bps": 250}) is False
    assert eval(code, {}, {**env, "threshold_bps": 200}) is False
    assert eval(code, {}, {**env, "threshold_bps": 300}) is True
    # 1/20=5% qualifies all six thresholds once mature and eligible.
    assert all(20 * bps <= 10000 for bps in (150, 200, 250, 300, 350, 400))


def test_causal_shadow_maturity_and_same_direction_only() -> None:
    code = source()
    assert "while q and q[0][0] <= t-7*86400:" in code
    assert "if hard and t>=START_TS+7*86400 and len(q)>=MIN_CLOSES:" in code
    assert code.index("shadow.handle(symbol, t, seq, px") < code.index(
        "signal_events[(book_name,threshold_bps)].append(info)"
    ) < code.index("controlled.handle(symbol,t,seq,px,direction_events,state)")
    assert 'case=f"{book_name}_B{threshold_bps}_W7_H24"' in code
    assert 'controlled.pause_until=max(previous_end,t+24*3600)' in code
    assert "close_PORTFOLIO_FLATTEN" not in code


def test_exact_previous_2p5_cash_ledger_and_signals_parity() -> None:
    code = source()
    assert 'case=f"{side}_B250_W7_H24"' in code
    assert 'old_case=f"{side}_W7_H24"' in code
    assert 'assert books[case].trades == [json.loads(x) for x in old_raw.splitlines()]' in code
    assert 'assert reports[case]["counts"] == prior_rate25["scenario_counts"][old_case]' in code
    assert 'assert len(new_signals)==len(prior_signals)' in code
    assert 'if k!="threshold_pct"} == previous' in code
    assert 'prior_rate25["cash_models"][old_case][mode][field]' in code
    assert "OUT.mkdir(mode=0o2770)" in code
    assert code.index("OLD_RATE25_PARITY_PASS") < code.index("OUT.mkdir(mode=0o2770)")


def test_complete_native_and_independent_baseline_parity() -> None:
    code = source()
    assert 'assert n == expected_eval == EXPECTED_TICKS' in code
    assert 'assert books["CONTROL"].trades == reference' in code
    assert 'assert books[side].trades == [json.loads(z) for z in raw.splitlines()]' in code
    assert 'assert reports["CONTROL"]["money"] == expected_control["money"]' in code
    assert 'D(books_money[side][mode][metric]) == D(' in code
    assert '"source_snapshot_sha256": engine_hashes' in code
    assert "source_sha256.txt" in code
    assert "run_manifest.json" in code


def test_no_unsafe_write_or_runtime_mutation() -> None:
    code = source()
    assert "shutil.copyfile(path, snapshot / path.name)" in code
    assert "PRIOR_RATE25/" in code
    assert "BASELINE/" in code
    assert "os.unlink" not in code
    assert "shutil.rmtree" not in code
    assert "os.chmod" not in code
    assert "subprocess.run(" in code
    assert '"rev-parse", "HEAD"' in code

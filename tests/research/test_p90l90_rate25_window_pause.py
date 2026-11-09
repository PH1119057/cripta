"""Static causal, capital, and scope contract for P90L90 2.5% research."""
from __future__ import annotations

import ast
from pathlib import Path

RUNNER = (
    Path(__file__).resolve().parents[2]
    / "scripts/research/p90l90_rate25_window_pause_20261109_v1.py"
)


def source() -> str:
    return RUNNER.read_text(encoding="utf-8")


def test_compile_and_scope() -> None:
    code = source()
    compile(ast.parse(code), str(RUNNER), "exec")
    assert 'PERIOD = ARGS.period' in code
    assert '"research": "P90L90_RATE25_CAUSAL_SHADOW_AUTOPAUSE_V1"' not in code
    assert 'RESEARCH = "P90L90_RATE25_CAUSAL_SHADOW_AUTOPAUSE_V1"' in code
    assert 'OUT = RESULTS / "p90l90_rate25_7_10_30_pause3_6_24_20261109_v1"' in code
    assert 'assert os.geteuid() == 107' in code
    assert 'assert not OUT.exists() and not OUT.is_symlink()' in code
    assert "SOURCE_HEAD" not in code or "source mirror identity mismatch" in code


def test_causal_threshold_and_mature_lookback() -> None:
    code = source()
    assert "WINDOW_DAYS = (7, 10, 30)" in code
    assert "PAUSE_HOURS = (3, 6, 24)" in code
    assert "MIN_CLOSES = 20" in code
    assert "RATE_STOP_DENOMINATOR = 40" in code
    assert "if not hard or t < START_TS+window*86400 or len(q)<MIN_CLOSES" in code
    assert "while q and q[0][0] <= t-window*86400:" in code
    assert "if stopped*RATE_STOP_DENOMINATOR < len(q):" in code
    assert 'if shadow.count["closed"] != prev_closed:' in code
    assert 'assert float(close["exit_ts"]) == t' in code
    assert code.index('shadow.handle(symbol, t, seq, px') < code.index(
        'controlled.handle('
    )


def test_no_flatten_and_automatic_resume() -> None:
    code = source()
    assert "old.EntryEmbargo(0,None)" in code
    assert 'controlled.pause_until=max(previous_end,t+hours*3600)' in code
    assert 'controlled.count["signal_triggers"]+=1' in code
    assert "not books[case].stops and not books[case].pending" in code
    assert 'if t < self.pause_until' not in code  # inherited, never overridden
    assert 'close_PORTFOLIO_FLATTEN' not in code
    assert "WINDOW_DAYS if w not in AVAILABLE" in code


def test_data_and_money_parity() -> None:
    code = source()
    assert 'assert books["CONTROL"].trades == reference' in code
    assert 'assert reports["CONTROL"]["money"] == expected_control["money"]' in code
    assert 'assert n == expected_eval == EXPECTED_TICKS' in code
    assert "assert books[side].trades == [json.loads(z) for z in raw.splitlines()]" in code
    assert '"fixed", "compound_capped"' in code
    assert 'cash(book_name, "compound_capped", bps)' in code
    assert 'D(books_money[side][mode][metric]) == D(' in code
    assert "OUT.mkdir(mode=0o2770)" in code
    assert "source_snapshot" in code
    assert '"run_manifest.json"' in code
    assert '"input_provenance.json"' in code
    assert '"logs"' in code


def test_no_undefined_baseline_path_during_postprocessing() -> None:
    """Regression: no NameError after 43,124,572 valid replay ticks.

    All three uses (result read, side-ledger parity, provenance) must
    resolve to the one output-independent immutable baseline folder.
    """
    code = source()
    module = ast.parse(code)
    uses = [
        node for node in ast.walk(module)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in {"BASE", "BASELINE"}
    ]
    assert uses and all(node.id == "BASELINE" for node in uses)
    assert code.count('BASELINE/"result.json"') == 2
    assert 'leaf=BASELINE/("trade_ledger_"+side.lower()+".jsonl")' in code
    assert 'BASELINE = RESULTS / "p90l90_side_nopause_compare_20261109_v1"' in code


def test_baseline_checks_precede_immutable_output_create() -> None:
    code = source()
    assert code.index('baseline = json.loads((BASELINE/"result.json")') < (
        code.index("OUT.mkdir(mode=0o2770)")
    )
    assert code.index('assert books[side].trades == [json.loads(z)') < (
        code.index("OUT.mkdir(mode=0o2770)")
    )


def test_zero_shadow_signal_does_not_raise_or_fake_trigger() -> None:
    """90d SHORT 30d window has no trigger; Counter omits zero keys."""
    tree = ast.parse(source())
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assert)
        and "signal_triggers" in ast.unparse(node.test)
    ]
    assert len(matches) == 1
    check = compile(ast.Expression(matches[0].test), str(RUNNER), "eval")
    env = {"side": "SHORT_ONLY", "window": 30}
    env["controlled"] = {"counts": {}}  # Counter serialized without zero entry
    env["signal_events"] = {("SHORT_ONLY", 30): []}
    assert eval(check, {}, env) is True
    env["signal_events"][("SHORT_ONLY", 30)] = [{"ts": "example"}]
    assert eval(check, {}, env) is False
    env["controlled"]["counts"]["signal_triggers"] = 1
    assert eval(check, {}, env) is True


def test_undefined_zero_key_never_used_directly_in_qc() -> None:
    code = source()
    assert 'controlled["counts"].get("signal_triggers", 0)' in code
    assert 'controlled["counts"]["signal_triggers"]' not in code

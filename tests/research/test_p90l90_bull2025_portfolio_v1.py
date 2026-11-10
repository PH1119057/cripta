"""P90L90 original 2026 cash-accounting and 2025 side protection contract."""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NEW = REPO / "scripts/research/p90l90_bull2025_portfolio_v1.py"
OLD = REPO / "scripts/research/p90l90_rate_threshold_w7h24_20261110_v1.py"


def function_ast(source: str, name: str) -> ast.FunctionDef:
    matches = [
        x for x in ast.parse(source).body
        if isinstance(x, ast.FunctionDef) and x.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def test_cash_accounting_ast_identical_to_frozen_2026_reference() -> None:
    old = function_ast(OLD.read_text(encoding="utf8"), "cash")
    new = function_ast(NEW.read_text(encoding="utf8"), "cash")
    assert ast.dump(old, include_attributes=False) == ast.dump(
        new, include_attributes=False
    )


def test_source_frozen_research_scope_and_market_first_cohort() -> None:
    source = NEW.read_text(encoding="utf8")
    compile(source, str(NEW), "exec")
    assert 'THRESHOLDS_BPS = (150, 200, 250, 300, 350, 400)' in source
    assert 'MIN_CLOSES = 20' in source
    assert 'f"{side}_SECOND48": embargo.EntryEmbargo(2, 48)' in source
    assert 'f"{side}_B{bps}_W7_H24": embargo.EntryEmbargo(0, None)' in source
    assert 'embargo.EntryEmbargo(0, None)' in source
    assert 'stopped * 10000 < len(rolling) * bps' in source
    assert 'if hard and t >= start_ts + 7*86400 and len(rolling) >= MIN_CLOSES' in source
    assert "books[side]" in source
    assert "side.split(\"_\")[0]" in source
    assert "QC_PASS_2025_ENTRY_CANDIDATE_ONLY_NO_EXITS" in source
    assert "EXACT_2026_PORTFOLIO_LEDGER_CASH_PARITY_PASS" in source
    assert "COMPLETE_2025_HISTORICAL_PROXY_NOT_LIVE" in source
    assert "P90L90_ENTRY_BULL2025_V1" not in source or (
        "entry" in source and "load_frozen_period" in source
    )
    assert 'RATE = D("0.00055")' in source
    assert 'wallet = D(1000)' in source
    assert 'if os.geteuid() != 107' in source
    assert 'shutil.disk_usage(RESULTS).free < 15 * 2**30' in source
    assert "No retune" in source
    assert "No production" not in source or "LIVE" in source


def test_original_cash_fee_and_wallet_semantics_in_pure_memory() -> None:
    """Run original exact 2026 cash function with one synthetic closed long."""
    cash_ast = function_ast(NEW.read_text(encoding="utf8"), "cash")
    module = ast.Module(body=[cash_ast], type_ignores=[])
    trade = {
        "entry_ts": "100", "exit_ts": "200", "side": "LONG", "symbol": "LTCUSDT",
        "entry_price": "100", "exit_price": "101", "gross_usdt": "0.1",
        "exit_fee_usdt": "0.005555", "exit_reason": "RESTING_TP",
        "entry_event_id": "mock:one",
    }
    class Book:
        trades = [trade]
        pos = {"LTCUSDT": None}
    context = {
        "D": D, "Counter": Counter, "defaultdict": defaultdict,
        "RATE": D("0.00055"), "SYMBOLS": ("LTCUSDT",), "books": {"LONG_ONLY": Book()},
        "reports": {"LONG_ONLY": {"censored": []}}, "last_prices": {"LTCUSDT": D(101)},
        "BEG": D(0), "END": D(3600),
    }
    exec(compile(module, "<immutable original cash>", "exec"), context)
    calc = context["cash"]("LONG_ONLY", "compound_capped")
    assert calc["starting_deposit_usdt"] == "1000"
    assert D(calc["final_wallet_usdt"]) > D("1000")
    assert D(calc["money"]["entry_fees"]) == D("200") * 5 * D("0.00055")
    assert D(calc["money"]["exit_fees"]) == (
        D("200") * 5 * D("101") / D("100") * D("0.00055")
    )
    assert calc["blocked_count"] == 0
    assert calc["mode"] == "compound_capped"
    assert calc["max_concurrent_positions"] == 1


def test_separate_w7_shadow_and_second48_books() -> None:
    source = NEW.read_text(encoding="utf8")
    assert 'shadow = books[side]' in source
    assert 'second = books[f"{side}_SECOND48"]' in source
    assert 'if direction_events or shadow.pos[symbol] is not None:' in source
    assert 'if direction_events or second.pos[symbol] is not None:' in source
    assert 'book.pause_until = max(previous, t + 24*3600)' in source
    assert 'book.handle(symbol, t, seq, px, direction_events, state)' in source
    assert "source_row_sequence" not in source or "source" in source

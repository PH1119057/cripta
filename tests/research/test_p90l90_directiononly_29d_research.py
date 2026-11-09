"""Guard independent 29d P90L90 native replay study before any data-disk writes."""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "scripts/research/p90l90_directiononly_29d_20261109_v1.py"
)


def test_directiononly_29d_compiles() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert ast.parse(source)
    compile(source, str(SOURCE), "exec", dont_inherit=True)


def test_three_books_and_direction_filter_before_admission() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert '"CONTROL": old.EntryEmbargo(2, 48)' in source
    assert '"LONG_ONLY": old.EntryEmbargo(2, 48)' in source
    assert '"SHORT_ONLY": old.EntryEmbargo(2, 48)' in source
    assert 'if e["side"] == "LONG"' in source
    assert 'if e["side"] == "SHORT"' in source
    assert 'books[book_name].handle(symbol, t, seq, px, direction_events, state)' in source
    assert (
        source.index('bys ide'.replace(" ", "") + ' = {')
        < source.index('books[book_name].handle')
    )


def test_exact_control_qc_and_historical_root() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert 'entry_only_embargo_29d_20261026_v1' in source
    assert 'if row["portfolio_scenario"] == "SECOND_48H":' in source
    assert 'assert books["CONTROL"].trades == reference' in source
    assert 'assert n == expected_eval == 43124572' in source
    assert 'assert all(x["side"] == expected for x in rows)' in source
    assert 'p90l90_directiononly_29d_20261109_v1' in source
    assert 'assert not OUT.exists() and not OUT.is_symlink()' in source
    assert source.index('assert books["CONTROL"].trades == reference') < (
        source.index('OUT.mkdir(mode=0o2770)')
    )


def test_per_side_cash_accounting_and_smoke_no_writes() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert 'def cash(book_name:str,mode:str,' in source
    assert 'old.entry29.START_TS' in source
    assert 'old.entry29.END_TS' in source
    assert 'D("0.00055")' in source
    assert '"compound_capped"' in source
    assert 'if ARGS.smoke_hours:' in source
    assert source.index('if ARGS.smoke_hours:') < source.index('OUT.mkdir(mode=0o2770)')

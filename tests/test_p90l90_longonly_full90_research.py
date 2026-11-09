"""Structural preflight for the research-only LONG admission replay.

No historical files or production runtime are read by this test.
The native bounded smoke and exact 692-trade control parity remain
mandatory before a COMPLETE research finding.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "scripts/research/p90l90_longonly_full90_20261109_v1.py"


def test_longonly_replay_source_compiles() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    compile(source, str(SOURCE), "exec", dont_inherit=True)
    tree = ast.parse(source)
    assert tree is not None


def test_longonly_replay_has_exact_direction_and_baseline_isolation() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert 'long=tuple(e for e in events if e["side"]=="LONG")' in source
    assert (
        'books={"CONTROL":old.EntryEmbargo(2,48),"LONG_ONLY":old.EntryEmbargo(2,48)}'
        in source
    )
    assert 'books["CONTROL"].handle(symbol,t,seq,px,events,state)' in source
    assert 'books["LONG_ONLY"].handle(symbol,t,seq,px,long,state)' in source
    assert (
        source.index('long=tuple(e for e in events if e["side"]=="LONG")')
        < source.index('books["LONG_ONLY"].handle')
    )
    assert 'CONTROL TRADE-BY-TRADE PARITY FAILED' in source
    assert 'control count parity' in source
    assert '"short_only_counts"' not in source


def test_longonly_output_is_isolated_and_writes_after_qc() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert 'p90l90_longonly_full90_20261109_v1' in source
    assert 'OUT.parent.resolve()==(ROOT/"results").resolve()' in source
    assert 'assert os.geteuid()==107' in source
    assert 'smoke-hours' in source
    assert 'if SMOKE_HOURS:' in source
    assert (
        source.index('assert books["CONTROL"].trades==reference')
        < source.index('OUT.mkdir(mode=0o2770)')
    )
    assert (
        source.index('OUT.mkdir(mode=0o2770)')
        < source.index('(OUT/"result.json").write_text')
    )
    assert '"fee_model":"Conservative taker/taker' in source
    assert 'capital_admission_warning' in source

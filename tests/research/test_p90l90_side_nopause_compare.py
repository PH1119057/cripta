"""Guard causal paired NO_PAUSE 90d/29d replay; no raw data access in CI."""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "scripts/research/p90l90_side_nopause_compare_20261109_v1.py"
)


def source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_compilation_and_both_periods() -> None:
    text = source()
    assert ast.parse(text)
    compile(text, str(SOURCE), "exec", dont_inherit=True)
    assert 'choices=("90d", "29d")' in text
    assert "EXPECTED_TICKS = 68988768 if PERIOD" in text
    assert "EXPECTED_CLOSED = 730 if PERIOD" in text


def test_disable_only_embargo_and_keep_independent_books() -> None:
    text = source()
    assert '"CONTROL": old.EntryEmbargo(0, None)' in text
    assert '"LONG_ONLY": old.EntryEmbargo(0, None)' in text
    assert '"SHORT_ONLY": old.EntryEmbargo(0, None)' in text
    assert "EntryEmbargo(2, 48)" not in text
    assert 'if e["side"] == "LONG"' in text
    assert 'if e["side"] == "SHORT"' in text
    assert text.index("bys ide".replace(" ", "") + " = {") < text.index(
        "books[book_name].handle"
    )


def test_exact_baseline_and_read_only_smoke() -> None:
    text = source()
    assert 'if row["portfolio_scenario"] == "BASELINE":' in text
    assert 'assert books["CONTROL"].trades == reference' in text
    assert 'assert reports["CONTROL"]["money"] == expected_control["money"]' in text
    assert "assert n == expected_eval == EXPECTED_TICKS" in text
    assert "if ARGS.smoke_hours:" in text
    assert text.index("assert books") < text.index("OUT.mkdir(mode=0o2770)")
    assert 'assert os.geteuid() == 107' in text
    assert 'assert not OUT.exists() and not OUT.is_symlink()' in text


def test_fee_models_and_artifact_integrity() -> None:
    text = source()
    assert 'for mode in ("fixed", "compound_capped"):' in text
    assert 'cash(book_name, mode)' in text
    assert 'RATE=D("0.00055")' in text
    assert '"capital_admission_warning"' in text
    assert 'source_snapshot_sha256' in text
    assert '"ledger_sha256"' in text

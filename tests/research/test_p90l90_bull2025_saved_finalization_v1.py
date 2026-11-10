"""Pure 2025 final checkpoint rounding and independent cash audit regression."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from scripts.research import p90l90_bull2025_finalize_saved_v1 as fin
from scripts.research import p90l90_bull2025_saved_independent_qc_v1 as qc
from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as chunk


def new_book():
    return fin.old.Book("LONG_ONLY", "NO_PAUSE", "compound_capped", [])


def test_exact_reserved_book_unchanged():
    book = new_book()
    evidence = fin.normalize_final_reserve(book)
    assert book.reserved == D(0)
    assert evidence["reserve_roundoff_usdt"] == "0"
    assert evidence["in_memory_adjusted"] is False


def test_real_2025_decimal_dust_only_normalized_in_memory():
    book = new_book()
    book.reserved = D("-5.3E-47")
    book.wallet = D("71.431565921052")
    book.margin["LTCUSDT"] = D("0")
    frozen_original = chunk.encode(chunk.freeze_books({"b": book}))
    evidence = fin.normalize_final_reserve(book)
    assert evidence["reserve_roundoff_usdt"] == "-5.3E-47"
    assert evidence["in_memory_adjusted"] is True
    assert book.reserved == D(0)
    assert book.wallet == D("71.431565921052")
    assert frozen_original["b"]["reserved"] == {"$decimal": "-5.3E-47"}


@pytest.mark.parametrize("gap", ["1E-12", "-1E-12", "0.01", "-0.01"])
def test_material_capital_gap_rejected(gap):
    book = new_book()
    book.reserved = D(gap)
    with pytest.raises(ValueError, match="material reserved capital mismatch"):
        fin.normalize_final_reserve(book)


def test_negative_wallet_not_silently_finalized():
    book = new_book()
    book.wallet = D("-1")
    with pytest.raises(ValueError, match="negative modeled wallet"):
        fin.normalize_final_reserve(book)


def original_empty_book():
    book = new_book()
    saved = chunk.freeze_books({"sample": book})["sample"]
    dust = fin.normalize_final_reserve(book)
    result = book.finish({}, fin.old.END_TS)
    return saved, dust, result


def test_independent_full_cash_checks_empty_cash_book():
    saved, dust, report = original_empty_book()
    row = qc.strict_case(
        "NO_PAUSE_compound_capped", saved, report, [], dust, {},
    )
    assert row["closed_trades"] == 0
    assert row["wallet_end_usdt_after_closed_trade_fees"] == "1000"
    assert row["wallet_change_after_fees_usdt"] == "0"
    assert row["censored_open_positions"] == 0


def test_independent_rejects_misreported_fee_or_wallet():
    saved, dust, report = original_empty_book()
    report["wallet_usdt"] = "1000.01"
    with pytest.raises(ValueError, match="independent cash/fees/coverage"):
        qc.strict_case("bad-wallet", saved, report, [], dust, {})


def test_independent_rejects_wrong_rounding_evidence():
    saved, dust, report = original_empty_book()
    dust["reserve_roundoff_usdt"] = "0.01"
    with pytest.raises(ValueError, match="reserve roundoff evidence mismatch"):
        qc.strict_case("bad-roundoff", saved, report, [], dust, {})


def test_independent_rejects_bad_single_trade_net():
    saved, dust, report = original_empty_book()
    rows = [{
        "gross_usdt": "1", "entry_fee_usdt": "0.1",
        "exit_fee_usdt": "0.1", "net_usdt": "1", "side": "LONG",
    }]
    with pytest.raises(ValueError, match="invalid gross/fee/net single row"):
        qc.strict_case("bad-ledger", saved, report, rows, dust, {})


def test_evaluation_scope_unchanged():
    assert fin.SIDE == "LONG_ONLY" and qc.SIDE == "LONG_ONLY"
    assert fin.EXPECTED_ROWS_ALL == 204446371
    assert chunk.TOTAL_DAYS == 183
    assert len(fin.PINNED) == len(chunk.PINNED) + 1
    assert fin.RESEARCH != qc.RESEARCH
    assert qc.RESEARCH == "P90L90_BULL2025_SAVED_INDEPENDENT_QC_V1"
    assert fin.old.Q == D("1E-12")
    assert fin.old.START_WALLET == D("1000")
    assert fin.old.SYMBOLS == (
        "LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT",
    )

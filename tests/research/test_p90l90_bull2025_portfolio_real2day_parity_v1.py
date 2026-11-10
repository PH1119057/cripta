"""Independent real-day parity checker financial guard unit tests."""
from decimal import Decimal as D

import pytest

from scripts.research import p90l90_bull2025_portfolio_real2day_parity_v1 as parity


def make_book():
    return parity.old.Book("LONG_ONLY", "NO_PAUSE", "fixed", [])


def test_empty_reference_account_balances():
    book = make_book()
    parity.assert_finances(book, "empty")
    assert book.wallet == D("1000")
    assert book.reserved == D(0)


def test_reject_unexplained_wallet_credit():
    book = make_book()
    book.wallet += D("1")
    with pytest.raises(ValueError, match="fee/wallet/reserved"):
        parity.assert_finances(book, "altered wallet")


def test_reject_bad_closed_trade_fee_arithmetic():
    book = make_book()
    book.trades.append({
        "gross_usdt": "1", "entry_fee_usdt": "0.10",
        "exit_fee_usdt": "0.10", "net_usdt": "0.90", "side": "LONG",
    })
    with pytest.raises(ValueError, match="closed trade row fee"):
        parity.assert_finances(book, "altered fees")


def test_16_case_snapshot_contract():
    assert len(parity.old.SCENARIOS) * len(parity.old.CASH_MODES) == 16
    assert parity.DAYS == (1, 2)
    assert parity.SIDE == "LONG_ONLY"
    assert "trades" in parity.chunk.BOOK_KEYS
    assert "wallet" in parity.chunk.BOOK_KEYS
    assert "last_stops" in parity.chunk.BOOK_KEYS
    assert "signal_index" in parity.chunk.BOOK_KEYS

"""Current signed Bybit inventory does not imply Strategy ownership."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.position_supervisor.exchange_attribution import (
    matches_reconciled_strategy_cycle,
)

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
HTML = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")

ACCOUNT = "ta-test-main"
SYMBOL = "LTCUSDT"
STRATEGY = "p90l90_legacy_main_ltc_inj"
# Exact row shape: account, symbol, index, side, strategy, entry, qty, fee.
OWNER = (ACCOUNT, SYMBOL, 0, "Buy", STRATEGY, "100.25", "0.12", "0.006")


def match(
    owners: list[tuple[object, ...]],
    quantity: object = "0.12",
    avg: object = "100.25",
) -> bool:
    return matches_reconciled_strategy_cycle(
        owners=owners,
        expected_strategy=STRATEGY,
        current_quantity=quantity,
        current_avg_fill=avg,
    )


def test_unique_reconciled_exact_current_cycle_is_displayable() -> None:
    assert match([OWNER])


@pytest.mark.parametrize(
    ("owners", "quantity", "price"),
    [
        ([], "0.12", "100.25"),
        ([OWNER, OWNER], "0.12", "100.25"),
        ([(ACCOUNT, SYMBOL, 0, "Buy", "other", "100.25", "0.12", "0")],
         "0.12", "100.25"),
        ([OWNER], "0.06", "100.25"),  # manually reduced / partially closed
        ([OWNER], "0.12", "102.00"),  # new manual cycle at same symbol
        ([OWNER], None, "100.25"),
        ([OWNER], "NaN", "100.25"),
        ([OWNER], Decimal("Infinity"), "100.25"),
        ([OWNER], "0", "100.25"),
        ([OWNER], "0.12", "-1"),
    ],
)
def test_manual_unmatched_and_invalid_positions_never_adopt_strategy(
    owners: list[tuple[object, ...]], quantity: object, price: object,
) -> None:
    assert not match(owners, quantity, price)


def test_dashboard_uses_current_exchange_rows_and_exact_owner_filter() -> None:
    assert "FROM runtime.hot_positions" in APP
    assert "o.close_link_status='OPEN'" in APP
    assert "o.strategy_version=%s" in APP
    assert "o.identity_status='PROVEN'" in APP
    assert "matches_reconciled_strategy_cycle(" in APP
    assert 'state = "EXTERNAL"' in APP
    assert '"position_attribution": (' in APP
    assert '"EXCHANGE_UNATTRIBUTED"' in APP
    assert "p90_external_symbols.add(symbol)" in APP
    assert "p90_positions_by_symbol.setdefault(symbol, []).append(row)" in APP


def test_unowned_physical_position_never_gets_strategy_pnl_or_exit() -> None:
    section = HTML.split("function renderP90RailPositions(", 1)[1].split(
        "let livePollTimer=", 1
    )[0]
    external = section.split("if(external){", 1)[1].split(
        "if(geometryStale){", 1
    )[0]
    assert "BYBIT: ВНЕ STRATEGY" in external
    assert "Strategy owner is NOT proven" in external
    assert "return " in external
    assert "net_pnl_to_close" not in external
    assert "tradeCommand(" not in external


def test_historical_unresolved_close_is_not_claimed_terminal() -> None:
    reconciliation = (
        ROOT / "operations/connectivity/private_runtime.py"
    ).read_text(encoding="utf-8")
    assert "close_link_status='UNRESOLVED_EXACT_LINK'" in reconciliation
    assert "resolve_exchange_position_close" in reconciliation
    assert "release_position_capital_reservation(" in reconciliation

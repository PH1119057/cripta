"""#470 R11: exact BTC/ETH MAYAK-only source overlay, no trading fan-out."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bybit_workbench.market_data.source_required_feed_manifest import (
    SourceManifestInvalid,
)
from operations.monitoring.public_market_source_union_capture import (
    parse_explicit_source_manifest,
)

_FIVE = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")
_REFERENCES = ("BTCUSDT", "ETHUSDT")
_MANIFEST = Path("operations/monitoring/r11_seven_public_feeds.json")


def _rows() -> list[dict[str, object]]:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def test_exact_five_trading_and_two_nonfinancial_mayak_references() -> None:
    rows = _rows()
    assert len(rows) == 138
    trade = [row for row in rows if row["symbol"] in _FIVE]
    ref = [row for row in rows if row["symbol"] in _REFERENCES]
    assert len(trade) == 120 and len(ref) == 18
    assert {row["consumer_id"] for row in ref} == {"MAYAK_REFERENCE"}
    assert all(row["required"] is False for row in ref)
    assert not any(
        row["symbol"] in _REFERENCES and row["consumer_id"] in (
            "ENTRY_P90", "EXIT_P90", "EXIT_SHADOW",
            "EXECUTION_R1", "EXECUTION_P90", "POSITION",
        )
        for row in rows
    )
    union = parse_explicit_source_manifest(
        json.dumps(rows), native_symbols=_FIVE,
        mayak_reference_symbols=_REFERENCES,
    )
    assert union.consumer_requirements == 138
    assert len(union.websocket_topics) == 57
    assert len(union.rest_inputs) == 21
    assert len(union.websocket_batches("SPOT")) == 2
    assert all(len(part) <= 10 for part in union.websocket_batches("SPOT"))
    assert len(union.websocket_batches("LINEAR")) == 2
    assert all(len(part) <= 30 for part in union.websocket_batches("LINEAR"))


def test_no_explicit_owner_flag_never_enables_btc_eth() -> None:
    with pytest.raises(
        SourceManifestInvalid, match="SOURCE_OUTSIDE_APPROVED_UNIVERSE",
    ):
        parse_explicit_source_manifest(
            json.dumps(_rows()), native_symbols=_FIVE,
        )


@pytest.mark.parametrize("change", [
    "new_symbol", "new_trading_consumer", "reference_required",
    "missing_trade", "duplicate_trade", "new_kline",
])
def test_nonfinancial_reference_contract_fail_closed(change: str) -> None:
    rows = _rows()
    ref_index = next(i for i, r in enumerate(rows) if r["symbol"] == "BTCUSDT")
    if change == "new_symbol":
        rows[ref_index]["symbol"] = "SOLUSDT"
    elif change == "new_trading_consumer":
        rows[ref_index]["consumer_id"] = "ENTRY_P90"
    elif change == "reference_required":
        rows[ref_index]["required"] = True
    elif change == "missing_trade":
        del rows[ref_index]
    elif change == "duplicate_trade":
        rows.append(dict(rows[ref_index]))
    elif change == "new_kline":
        rows[ref_index]["dataset"] = "CLOSED_KLINE"
        rows[ref_index]["timeframe"] = "5"
    with pytest.raises(SourceManifestInvalid):
        parse_explicit_source_manifest(
            json.dumps(rows), native_symbols=_FIVE,
            mayak_reference_symbols=_REFERENCES,
        )


def test_one_existing_native_owner_and_no_new_financial_symbol_default() -> None:
    source = Path("operations/monitoring/native_public_trade_source.py").read_text(
        encoding="utf-8",
    )
    union = Path(
        "operations/monitoring/public_market_source_union_capture.py"
    ).read_text(encoding="utf-8")
    unit = Path(
        "operations/systemd/cripta-native-public-trade-source.service"
    ).read_text(encoding="utf-8")
    assert 'os.environ.get("CRIPTA_R11_MAYAK_ANCHORS", "0")' in source
    assert "CRIPTA_R11_MAYAK_ANCHORS=1" not in unit
    assert 'native_symbols=symbols' in source
    assert 'mayak_reference_symbols=(' in source
    assert "r11_anchor_symbols" in union
    assert "native._persist_frame(" in union
    assert 'symbols=tuple((*p90_symbols, *r11_anchor_symbols))' in union
    assert "CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES=1" not in unit

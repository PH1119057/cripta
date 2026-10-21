"""No DB/runtime dependency: verify the reviewed research-ledger projection."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from operations.dashboard.coin_research_catalog import (
    CATALOG_PATH,
    read_catalog,
    read_symbol_records,
)


def test_catalog_has_30_independently_balanced_records() -> None:
    raw = read_catalog()
    assert raw["record_count"] == 30
    assert raw["storage_approval"] == "OWNER_APPROVED_GITHUB_MAIN_20261021"
    assert {
        r["instrument"]["symbol"] for r in raw["records"]
    } == {"LTCUSDT", "APTUSDT", "DOTUSDT", "ARBUSDT", "INJUSDT"}
    assert all(
        r["quality"]["publication_qc"] == "MODEL_QC_PASS_CANDIDATE_RECORD"
        and r["quality"]["lookahead"] == "YES"
        and r["quality"]["oos"] == "NOT_VERIFIED"
        and r["instrument"]["instrument_id"] == (
            "inst-bybit-linear-" + r["instrument"]["symbol"].lower()
        )
        for r in raw["records"]
    )
    for sym in ("LTCUSDT", "APTUSDT", "DOTUSDT", "ARBUSDT", "INJUSDT"):
        assert read_symbol_records(sym)["record_count"] == 6


def test_zero_result_must_not_be_guessed_from_invalid_data(tmp_path: Path) -> None:
    src = read_catalog()
    bad = copy.deepcopy(src)
    bad["records"][0]["economics"]["realized_net_usdt"] = 0
    dest = tmp_path / "bad.json"
    dest.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="PNL_BALANCE"):
        read_catalog(dest)


def test_duplicate_and_censored_trade_broken_rejected(tmp_path: Path) -> None:
    source = read_catalog()
    dest = tmp_path / "bad.json"
    dup = copy.deepcopy(source)
    dup["records"][1]["record_id"] = dup["records"][0]["record_id"]
    dest.write_text(json.dumps(dup))
    with pytest.raises(ValueError, match="IDEMPOTENCY"):
        read_catalog(dest)
    cnt = copy.deepcopy(source)
    cnt["records"][0]["counts"]["open_censored"] += 1
    dest.write_text(json.dumps(cnt))
    with pytest.raises(ValueError, match="COUNT_BALANCE"):
        read_catalog(dest)


def test_no_synthetic_instrument_identity_or_file_fallback(tmp_path: Path) -> None:
    source = read_catalog()
    dest = tmp_path / "bad.json"
    bad = copy.deepcopy(source)
    bad["records"][0]["instrument"]["instrument_id"] = "WRONG"
    dest.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        read_catalog(dest)
    with pytest.raises(ValueError, match="ABSENT_OR_TOO_LARGE"):
        read_catalog(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="INVALID_SYMBOL"):
        read_symbol_records("../APTUSDT")
    assert CATALOG_PATH.is_file()

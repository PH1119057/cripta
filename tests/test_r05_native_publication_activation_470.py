"""R05: one native writer publishes immutable PG references, without LIVE.

Pure source/unit contract checks only; physical cutover requires protected CI,
one controlled native release, post-cutover SQL and independent safety audit.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.market_data.market_publication import (
    MarketPublicationUnavailable,
    publish_market_source_second,
)

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "operations/systemd/cripta-native-public-trade-source.service"
NATIVE = ROOT / "operations/monitoring/native_public_trade_source.py"
UNION = ROOT / "operations/monitoring/public_market_source_union_capture.py"
PUBLISHER = ROOT / "src/bybit_workbench/market_data/market_publication.py"
CONTROLLER = ROOT / "operations/infrastructure/cripta-release-controller"
SYMBOLS = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")


def test_r05_activates_publication_inside_one_existing_native_producer() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    env = dict(
        line.removeprefix("Environment=").split("=", 1)
        for line in unit.splitlines()
        if line.startswith("Environment=")
    )
    assert unit.count("ExecStart=") == 1
    assert unit.count("User=cripta") == 1
    assert env["CRIPTA_NATIVE_PUBLIC_TRADE_SYMBOLS"] == ",".join(SYMBOLS)
    assert env["CRIPTA_SOURCE_CLOSED_KLINES"] == "0"
    assert env["CRIPTA_SOURCE_UNION_CAPTURE"] == "1"
    assert env["CRIPTA_SOURCE_PUBLICATION"] == "1"
    assert env["CRIPTA_SOURCE_KLINE_INTERVALS"] == "1,5,15"
    assert env["CRIPTA_SOURCE_UNION_MANIFEST_FILE"] == (
        "/etc/cripta/market-source/required_public_feeds.json"
    )
    assert "EnvironmentFile=/etc/cripta/release.env" in unit
    assert "ProtectSystem=strict" in unit
    assert "NoNewPrivileges=true" in unit
    assert "cripta-mayak-v2.service" not in unit
    assert "Requires=postgresql.service" in unit


def test_r05_selects_native_gap_release_and_keeps_consumers_inert() -> None:
    native = NATIVE.read_text(encoding="utf-8")
    capture = UNION.read_text(encoding="utf-8")
    controller = CONTROLLER.read_text(encoding="utf-8")
    publisher = PUBLISHER.read_text(encoding="utf-8")
    assert '"operations/systemd/cripta-native-public-trade-source.service"' in controller
    assert "NATIVE_CUTOVER_PURPOSE" in controller
    assert "publish_seconds=_source_feature_enabled(_PUBLICATION_FLAG)" in native
    assert "if publish_seconds:" in capture
    assert "publish_market_source_second(" in capture
    assert "current_second != last_published_second" in capture
    assert "ON CONFLICT (market_source_id,source_session_id,as_of_second)" in publisher
    assert "DO NOTHING RETURNING publication_id" in publisher
    assert "WATERMARKS_ONLY" in publisher
    assert "CRIPTA_SOURCE_PUBLICATION=1" in UNIT.read_text(encoding="utf-8")
    assert "Strategy" not in UNIT.read_text(encoding="utf-8").split("ExecStart=")[-1]


@pytest.mark.parametrize(
    ("session", "release", "symbols", "at", "reason"),
    [
        ("x" * 32, "a" * 40, SYMBOLS, datetime.now(UTC), "EPOCH_INVALID"),
        ("a" * 32, "x" * 40, SYMBOLS, datetime.now(UTC), "RELEASE_INVALID"),
        ("a" * 32, "a" * 40, ("APTUSDT", "APTUSDT"), datetime.now(UTC), "SYMBOLS_INVALID"),
        ("a" * 32, "a" * 40, SYMBOLS, datetime(2026, 11, 11), "TIME_NOT_UTC"),
    ],
)
def test_r05_rejects_invalid_publication_before_any_db_write(
    session: str,
    release: str,
    symbols: tuple[str, ...],
    at: datetime,
    reason: str,
) -> None:
    with pytest.raises(MarketPublicationUnavailable, match=reason):
        publish_market_source_second(
            object(),
            source_session_id=session,
            producer_release_commit=release,
            symbols=symbols,
            observed_at=at,
        )

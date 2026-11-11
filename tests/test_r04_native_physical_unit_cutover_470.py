"""R04 unit activation is an explicit native-producer ABI cutover, not R05.

The tests are pure: no network, PostgreSQL, systemd or Exchange mutations.
A GitHub/source merge alone never starts a service.
"""
from __future__ import annotations

import json
from pathlib import Path

from bybit_workbench.market_data.source_required_feed_manifest import (
    SourceManifestInvalid,
)
from operations.monitoring.public_market_source_union_capture import (
    parse_explicit_source_manifest,
    r03_closed_only_feed_union,
)

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "operations/systemd/cripta-native-public-trade-source.service"
MANIFEST = ROOT / "operations/monitoring/required_public_feeds.json"
P90 = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")


def _environment() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in UNIT.read_text(encoding="utf-8").splitlines():
        if line.startswith("Environment="):
            key, sep, value = line.partition("=")[2].partition("=")
            assert sep and key not in result
            result[key] = value
    return result


def test_r04_activates_one_native_union_not_two_competing_sources() -> None:
    source = UNIT.read_text(encoding="utf-8")
    env = _environment()
    assert env["CRIPTA_NATIVE_PUBLIC_TRADE_SYMBOLS"] == ",".join(P90)
    assert env["CRIPTA_SOURCE_CLOSED_KLINES"] == "0"
    assert env["CRIPTA_SOURCE_UNION_CAPTURE"] == "1"
    assert env["CRIPTA_SOURCE_PUBLICATION"] == "0"
    assert env["CRIPTA_SOURCE_KLINE_INTERVALS"] == "1,5,15"
    assert env["CRIPTA_SOURCE_UNION_MANIFEST_FILE"] == (
        "/etc/cripta/market-source/required_public_feeds.json"
    )
    assert "User=cripta" in source
    assert "Group=cripta" in source
    assert "EnvironmentFile=/etc/cripta/release.env" in source
    assert "ProtectSystem=strict" in source
    assert "ReadWritePaths=/var/lib/cripta/native_public_trade_source" in source
    assert "Restart=on-failure" in source


def test_r04_full_manifest_is_five_symbols_and_only_spot_optional() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(manifest, list) and len(manifest) == 120
    assert {x["symbol"] for x in manifest} == set(P90)
    union = parse_explicit_source_manifest(
        MANIFEST.read_text(encoding="utf-8"), native_symbols=P90,
    )
    spot = [x for x in union.websocket_topics if x.product == "SPOT"]
    linear = [x for x in union.websocket_topics if x.product == "LINEAR"]
    assert len(spot) + len(linear) == 45
    assert len(union.rest_inputs) == 15
    assert all(not x.required for x in spot)
    assert {x.dataset_topic for x in linear if x.dataset_topic.startswith(
        "publicTrade."
    )} == {f"publicTrade.{symbol}" for symbol in P90}
    assert {x.dataset_topic for x in linear if x.dataset_topic.startswith(
        "kline."
    )} >= {
        f"kline.{period}.{symbol}"
        for period in ("1", "5", "15") for symbol in P90
    }


def test_r04_unit_does_not_activate_r05_or_second_mayak_writer() -> None:
    text = UNIT.read_text(encoding="utf-8")
    assert "CRIPTA_SOURCE_PUBLICATION=0" in text
    assert "cripta-mayak-v2.service" not in text
    assert "Requires=postgresql.service" in text
    assert "StateDirectory=cripta/native_public_trade_source" in text


def test_r04_unit_must_use_native_source_cutover_release_not_full() -> None:
    controller = (
        ROOT / "operations/infrastructure/cripta-release-controller"
    ).read_text(encoding="utf-8")
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert '"operations/systemd/cripta-native-public-trade-source.service"' in controller
    assert "NATIVE_CUTOVER_PURPOSE" in controller
    assert "NATIVE_SOURCE_OLD_SESSION_NOT_EXPLICIT_GAP" in installer
    assert "NATIVE_SOURCE_CUTOVER_AUTH_OR_OLD_PROCESS_INVALID" in installer
    assert "NATIVE_SOURCE_EXACT_CODE_CUTOVER=PASS" in installer


def test_r03_closed_only_and_r04_union_are_separate_feed_plans() -> None:
    from operations.monitoring.native_public_trade_source import (
        NativeSourceInputUnverified,
        _source_feature_enabled,
    )

    assert _source_feature_enabled("0") is False
    assert _source_feature_enabled("1") is True
    union = parse_explicit_source_manifest(
        MANIFEST.read_text(encoding="utf-8"), native_symbols=P90,
    )
    r03 = r03_closed_only_feed_union(union, native_symbols=P90)
    assert len(r03.websocket_batches("SPOT")) == 0
    assert len(r03.rest_inputs) == 0
    assert len(union.websocket_batches("SPOT")) > 0
    assert len(union.rest_inputs) > 0

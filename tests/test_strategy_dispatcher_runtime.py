from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

import bybit_workbench

PRODUCTION_PACKAGE = (
    Path(__file__).parents[1] / "production" / "src" / "bybit_workbench"
)
production_path = str(PRODUCTION_PACKAGE)
if production_path not in bybit_workbench.__path__:
    bybit_workbench.__path__.append(production_path)
dispatcher = importlib.import_module("bybit_workbench.strategy_dispatcher")
profile_io = importlib.import_module(
    "bybit_workbench.strategy_dispatcher.profile_io"
)
replay = importlib.import_module("bybit_workbench.strategy_dispatcher.replay")

DispatcherDataQuality = dispatcher.DispatcherDataQuality
FeatureStatus = dispatcher.FeatureStatus
FileDispatcherContextProvider = dispatcher.FileDispatcherContextProvider
MayakSnapshotAdapter = dispatcher.MayakSnapshotAdapter
PassiveDispatcherService = dispatcher.PassiveDispatcherService
load_profile_directory = profile_io.load_profile_directory
load_profile_file = profile_io.load_profile_file
replay_jsonl = replay.replay_jsonl


def canonical_payload() -> dict[str, object]:
    return {
        "snapshot_id": "m-1",
        "observed_at": "2026-08-29T13:31:25+00:00",
        "engine_version": "mayak-test",
        "architecture_version": "1.0",
        "data_quality": "HIGH",
        "dispatcher_features": {
            "market.direction": {
                "value": "DOWN",
                "status": "VALID",
                "confidence": 0.9,
                "observed_at": "2026-08-29T13:31:24+00:00",
            },
            "market.volatility": {
                "value": "NORMAL",
                "status": "VALID",
                "confidence": 0.8,
            },
            "liquidation.phase": {
                "value": None,
                "status": "NO_DATA",
                "confidence": 1.0,
            },
            "strategy.secret_feature": {"value": "NO", "status": "VALID"},
        },
        "signals": {"long": 99},
        "positions": {"count": 88},
    }


def legacy_payload() -> dict[str, object]:
    return {
        "observed_at": "2026-08-29T13:31:25+00:00",
        "engine_version": "mayak-v2.1",
        "state": "спокойный рынок",
        "confidence": 0.91,
        "price_breadth": {
            "up_share": 0.15,
            "down_share": 0.75,
            "median_return_pct": -0.28,
        },
        "money_breadth": {
            "spot_sales_share": 0.65,
            "derivatives_sales_share": 0.72,
            "buyer_liquidity_withdrawal_share": 0.60,
        },
        "direction_synchronization": {"agreement": 0.85},
        "coins": {
            "AAA": {"ticker": {"open_interest_change_5m_pct": 0.2}},
            "BBB": {"ticker": {"open_interest_change_5m_pct": 0.1}},
        },
        "btc": {"linear": {"return_pct": -0.2}},
        "eth": {"linear": {"return_pct": -0.1}},
        "signals": {"count_30m": 20},
        "positions": {"count": 10, "correlated_risk": True},
    }


def write_enabled_profile(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "profile_id": "test.profile",
                "version": "1",
                "display_name_ru": "Тест",
                "description_ru": "Тестовый профиль",
                "rules": [
                    {
                        "feature_id": "data.quality",
                        "mode": "REQUIRED",
                        "operator": "ONE_OF",
                        "expected": ["HIGH"],
                    },
                    {
                        "feature_id": "market.direction",
                        "mode": "PREFERRED",
                        "operator": "ONE_OF",
                        "expected": ["DOWN"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_canonical_adapter_copies_only_vocabulary_features() -> None:
    snapshot = MayakSnapshotAdapter().adapt(canonical_payload())
    assert snapshot.data_quality is DispatcherDataQuality.HIGH
    assert snapshot.features["market.direction"].value == "DOWN"
    assert snapshot.features["liquidation.phase"].usable is False
    assert "strategy.secret_feature" not in snapshot.features
    assert "signals" not in snapshot.features
    assert "positions" not in snapshot.features


def test_canonical_adapter_preserves_feature_specific_quality_metadata() -> None:
    payload = canonical_payload()
    direction = payload["dispatcher_features"]["market.direction"]
    direction["feature_confidence"] = 0.2
    direction["confidence"] = 0.2
    direction["transport_confidence"] = 1.0
    direction["coverage"] = {"valid": 4, "total": 20}
    feature = MayakSnapshotAdapter().adapt(payload).features["market.direction"]
    assert feature.confidence == 0.2
    assert feature.transport_confidence == 1.0
    assert feature.coverage_valid == 4
    assert feature.coverage_total == 20


def test_strategy_and_position_context_cannot_change_canonical_snapshot() -> None:
    first = canonical_payload()
    second = canonical_payload()
    second["signals"] = {"short": 1000}
    second["positions"] = {"count": 0}
    left = MayakSnapshotAdapter().adapt(first)
    right = MayakSnapshotAdapter().adapt(second)
    assert left == right


def test_legacy_bridge_is_capped_at_low_quality_and_does_not_invent_missing_layers() -> None:
    snapshot = MayakSnapshotAdapter().adapt(legacy_payload())
    assert snapshot.data_quality is DispatcherDataQuality.LOW
    assert snapshot.provenance["adapter_mode"] == "legacy_bridge"
    assert snapshot.features["market.direction"].value == "STRONG_DOWN"
    assert "liquidation.phase" not in snapshot.features
    assert "event.context" not in snapshot.features


def test_legacy_strategy_and_position_context_cannot_change_snapshot() -> None:
    first = legacy_payload()
    second = legacy_payload()
    second["signals"] = {"anything": "different"}
    second["positions"] = {"anything": "different"}
    assert MayakSnapshotAdapter().adapt(first) == MayakSnapshotAdapter().adapt(second)


def test_profile_loader_requires_explicit_enable_for_live_directory(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    write_enabled_profile(profile_path)
    loaded = load_profile_directory(tmp_path, require_enabled=True)
    assert len(loaded) == 1
    raw = json.loads(profile_path.read_text(encoding="utf-8"))
    raw["enabled"] = False
    profile_path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_profile_directory(tmp_path, require_enabled=True) == ()
    assert load_profile_file(profile_path, require_enabled=False) is not None


def test_passive_service_persists_status_and_deduplicates_same_snapshot(tmp_path: Path) -> None:
    mayak = tmp_path / "mayak.json"
    mayak.write_text(json.dumps(canonical_payload()), encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    write_enabled_profile(profiles / "p.json")
    state = tmp_path / "state"
    service = PassiveDispatcherService(
        mayak_status_path=mayak,
        profile_dir=profiles,
        state_root=state,
    )
    first = service.run_once()
    second = service.run_once()
    assert first["trading_effect"] == "NONE"
    assert first["profile_count"] == 5
    assert second["profile_count"] == 5
    research = [row for row in first["assessments"] if row["status"] == "RESEARCH_REQUIRED"]
    assert {row["profile_id"] for row in research} == {
        "M3_V1_LONG_ENTRY",
        "M3_V1_SHORT_ENTRY",
        "M3_V1_LONG_HOLD",
        "M3_V1_SHORT_HOLD",
    }
    assert all(row["snapshot_id"] == "m-1" for row in research)
    assert all(row["trading_effect"] == "NONE" for row in research)
    lines = (state / "assessments.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    status = json.loads((state / "status.json").read_text(encoding="utf-8"))
    assert status["snapshot"]["snapshot_id"] == "m-1"


def test_reference_profiles_are_disabled_by_default() -> None:
    root = Path(__file__).parents[1] / "config" / "strategy_dispatcher" / "profiles"
    enabled = load_profile_directory(root, require_enabled=True)
    assert {item.profile_id for item in enabled} == {
        "M3_V1_LONG_ENTRY", "M3_V1_SHORT_ENTRY",
        "M3_V1_LONG_HOLD", "M3_V1_SHORT_HOLD",
    }
    assert all(item.version == "1.0.0-owner-live" for item in enabled)
    assert len(tuple(root.glob("*.json"))) >= 3


def test_provider_is_read_only_lookup(tmp_path: Path) -> None:
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps(
            {
                "service_version": "s1",
                "snapshot": {
                    "snapshot_id": "m1",
                    "observed_at": "2026-08-29T13:31:25+00:00",
                    "data_quality": "HIGH",
                    "mayak_version": "m",
                    "architecture_version": "1",
                },
                "assessments": [
                    {"profile_id": "p", "profile_version": "1", "status": "GOOD_MATCH"}
                ],
                "trading_effect": "NONE",
            }
        ),
        encoding="utf-8",
    )
    provider = FileDispatcherContextProvider(status)
    context = provider.current("p", "1")
    assert context is not None
    assert context["snapshot"]["snapshot_id"] == "m1"
    assert context["assessment"]["status"] == "GOOD_MATCH"
    assert context["trading_effect"] == "NONE"
    assert provider.current("missing", "1") is None


def test_replay_is_offline_and_deterministic(tmp_path: Path) -> None:
    input_path = tmp_path / "mayak.jsonl"
    input_path.write_text(
        json.dumps(canonical_payload()) + "\n" + json.dumps(canonical_payload()) + "\n",
        encoding="utf-8",
    )
    profile_path = tmp_path / "profile.json"
    write_enabled_profile(profile_path)
    output = tmp_path / "out.jsonl"
    summary = replay_jsonl(input_path=input_path, profile_path=profile_path, output_path=output)
    assert summary["processed"] == 2
    assert summary["invalid_rows"] == 0
    rows = output.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    assert rows[0] == rows[1]


def test_dispatcher_package_has_no_network_database_or_trading_imports() -> None:
    package = (
        Path(__file__).parents[1]
        / "production"
        / "src"
        / "bybit_workbench"
        / "strategy_dispatcher"
    )
    forbidden = (
        "execution",
        "risk",
        "position_supervisor",
        "strategies",
        "private_runtime",
        "psycopg",
        "requests",
        "urllib",
        "websocket",
        "pybit",
    )
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not any(word in name.lower() for name in imports for word in forbidden), path


def test_systemd_template_is_passive_and_not_an_installed_unit() -> None:
    unit = (
        Path(__file__).parents[1]
        / "operations"
        / "strategy_dispatcher"
        / "cripta-strategy-dispatcher.service"
    )
    text = unit.read_text(encoding="utf-8")
    assert "ReadOnlyPaths=/var/lib/cripta/mayak_v2" in text
    assert "ReadWritePaths=/var/lib/cripta/strategy_dispatcher" in text
    assert "private-runtime" not in text.lower()
    assert "execution" not in text.lower()
    assert "api.bybit" not in text.lower()
    assert "credential" not in text.lower()
    assert "loadcredential" not in text.lower()


def test_invalid_canonical_timestamp_fails_closed() -> None:
    payload = canonical_payload()
    payload["observed_at"] = "not-a-time"
    with pytest.raises(ValueError, match="observed_at"):
        MayakSnapshotAdapter().adapt(payload)


def test_canonical_adapter_rejects_unknown_category_value() -> None:
    payload = canonical_payload()
    payload["dispatcher_features"]["market.direction"]["value"] = "SIDEWAYS_MAGIC"  # type: ignore[index]
    with pytest.raises(ValueError, match="invalid canonical value"):
        MayakSnapshotAdapter().adapt(payload)


def test_adapter_accepts_persisted_database_snapshot_wrapper() -> None:
    wrapped = {
        "id": 42,
        "observed_at": "2026-08-29 13:31:25+00:00",
        "engine_version": "mayak-v2.1",
        "confidence": 1.0,
        "payload": legacy_payload(),
    }
    snapshot = MayakSnapshotAdapter().adapt(wrapped)
    assert snapshot.snapshot_id == "db-42"
    assert snapshot.data_quality is DispatcherDataQuality.LOW
    assert snapshot.features["market.direction"].value == "STRONG_DOWN"


def test_service_restart_does_not_duplicate_last_snapshot(tmp_path: Path) -> None:
    mayak = tmp_path / "mayak.json"
    mayak.write_text(json.dumps(canonical_payload()), encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    state = tmp_path / "state"
    first = PassiveDispatcherService(
        mayak_status_path=mayak, profile_dir=profiles, state_root=state
    )
    first.run_once()
    second = PassiveDispatcherService(
        mayak_status_path=mayak, profile_dir=profiles, state_root=state
    )
    second.run_once()
    assert len((state / "assessments.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_service_builds_russian_portal_view(tmp_path: Path) -> None:
    mayak = tmp_path / "mayak.json"
    mayak.write_text(json.dumps(canonical_payload()), encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    write_enabled_profile(profiles / "p.json")
    service = PassiveDispatcherService(
        mayak_status_path=mayak,
        profile_dir=profiles,
        state_root=tmp_path / "state",
    )
    envelope = service.run_once()
    view = envelope["view_ru"]
    assert view["качество_данных"] == "высокое"
    assert view["торговое_влияние"] == "отсутствует"
    assert view["профили"][0]["статус"] in {
        "отлично подходит",
        "подходит",
        "подходит частично",
        "скорее не подходит",
        "не подходит",
        "недостаточно данных",
    }


def test_machine_vocabulary_matches_python_contract() -> None:
    from bybit_workbench.strategy_dispatcher.vocabulary import V1_FEATURES

    path = Path(__file__).parents[1] / "config" / "strategy_dispatcher" / "vocabulary_v1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert [item["feature_id"] for item in raw["features"]] == [
        item.feature_id for item in V1_FEATURES
    ]


def test_handoff_schema_explicitly_excludes_strategy_and_position_fields() -> None:
    path = (
        Path(__file__).parents[1]
        / "config"
        / "strategy_dispatcher"
        / "MAYAK_HANDOFF_SCHEMA_V1.json"
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["additionalProperties"] is False
    properties = raw["properties"]
    assert "signals" not in properties
    assert "positions" not in properties
    assert "pnl" not in properties
    assert "dispatcher_features" in properties


def test_no_data_feature_keeps_missing_observation_time() -> None:
    payload = canonical_payload()
    payload["dispatcher_features"]["market.direction"] = {
        "status": "NO_DATA",
        "confidence": 0.0,
        "observed_at": None,
    }
    snapshot = MayakSnapshotAdapter().adapt(payload)
    feature = snapshot.features["market.direction"]
    assert feature.status is FeatureStatus.NO_DATA
    assert feature.observed_at is None


def test_nested_dispatcher_handoff_isolated_from_outer_mayak_fields() -> None:
    handoff = canonical_payload()
    handoff.pop("signals", None)
    handoff.pop("positions", None)
    outer = {
        "observed_at": "1999-01-01T00:00:00+00:00",
        "signals": {"long": 999},
        "positions": {"count": 999},
        "pnl": 999,
        "dispatcher_handoff": handoff,
    }
    snapshot = MayakSnapshotAdapter().adapt(outer)
    assert snapshot.snapshot_id == "m-1"
    assert snapshot.observed_at.isoformat().startswith("2026-08-29")
    assert "signals" not in snapshot.features
    assert "positions" not in snapshot.features

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.contracts import (
    CandidateCooldown,
    FrozenPolicy,
    StrategyCard,
    TouchPolicy,
    TradeDirection,
)
from bybit_workbench.universal_entry.dashboard_control import (
    StaleActivationState,
    StrategyDashboardStore,
    assemble_strategy_catalog,
    build_new_strategy_version,
    card_to_editable,
)
from bybit_workbench.universal_entry.fingerprint import canonical_json

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)
APP = ROOT / "operations/dashboard/app.py"
HTML = ROOT / "operations/dashboard/index.html"
UNIT = ROOT / "operations/systemd/cripta-dashboard.service"


def make_card(
    strategy_id: str = "strategy-a",
    version: str = "1.0",
    direction: TradeDirection = TradeDirection.LONG,
    *,
    threshold: str = "1.0",
) -> StrategyCard:
    return StrategyCard.build(
        strategy_id=strategy_id,
        strategy_version=version,
        name=f"{strategy_id} {version}",
        description="test strategy",
        scope=FrozenPolicy.from_mapping({"kind": "symbols", "symbols": ["UNIUSDT"]}),
        symbols=("UNIUSDT",),
        direction_policy=(direction,),
        entry_policy=FrozenPolicy.from_mapping(
            {
                "entry_plan_version": "entry-1",
                "predicate": {
                    "op": "COMPARE",
                    "params": {"path": "fact.x", "comparator": "GTE", "value": threshold},
                },
                "watch_policy": {"enabled": False},
            }
        ),
        exit_policy=FrozenPolicy.from_mapping({"exit_plan_version": "exit-1", "mode": "TEST"}),
        capital_policy=FrozenPolicy.from_mapping({"require_capacity": False}),
        protection_policy=FrozenPolicy.from_mapping({"enabled": False}),
        lifecycle_policy=FrozenPolicy.from_mapping(
            {"post_signal_outcome_policy": {"enabled": False}}
        ),
        touch_policy=TouchPolicy(candidate_cooldown=CandidateCooldown(enabled=False)),
        approved_at=NOW,
        approved_source="u6-test",
    )


def card_row(card: StrategyCard) -> dict[str, object]:
    return {
        "strategy_id": card.strategy_id,
        "strategy_version": card.strategy_version,
        "strategy_config_fingerprint": card.strategy_config_fingerprint,
        "name": card.name,
        "description": card.description,
        "card_json": json.loads(canonical_json(card)),
        "approved_at": card.approved_at,
        "approved_source": card.approved_source,
        "created_at": NOW,
    }


def activation_row(card: StrategyCard, activation_id: str, enabled: bool) -> dict[str, object]:
    return {
        "activation_id": activation_id,
        "strategy_id": card.strategy_id,
        "strategy_version": card.strategy_version,
        "strategy_config_fingerprint": card.strategy_config_fingerprint,
        "enabled": enabled,
        "enabled_at": NOW,
        "disabled_at": None if enabled else NOW,
        "scope": {},
        "operator": "owner",
        "source": "u6-test",
        "change_reason": "test",
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_dashboard_control_import_does_not_eagerly_require_trading_dependencies() -> None:
    code = "\n".join(
        [
            "import builtins",
            "real_import = builtins.__import__",
            "def guarded(name, *args, **kwargs):",
            "    if name == 'pydantic' or name.startswith('pydantic.'):",
            "        raise ModuleNotFoundError('blocked for U6 import-boundary test')",
            "    return real_import(name, *args, **kwargs)",
            "builtins.__import__ = guarded",
            "from bybit_workbench.universal_entry.dashboard_control import StrategyDashboardStore",
            "print(StrategyDashboardStore.__name__)",
        ]
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "StrategyDashboardStore"


def test_lazy_universal_entry_public_engine_exports_remain_available() -> None:
    from bybit_workbench.universal_entry import ActivePlanRegistry, UniversalEntryEngine

    assert UniversalEntryEngine.__name__ == "UniversalEntryEngine"
    assert ActivePlanRegistry.__name__ == "ActivePlanRegistry"


def test_u6_contract_is_frozen_before_dashboard_source() -> None:
    text = (ROOT / "docs/UNIVERSAL_STRATEGY_ENTRY_IMPLEMENTATION_RU.md").read_text(encoding="utf-8")
    assert "**Версия:** 1.3" in text
    assert "78e5e90753a3dffb2b61177174a94dc8ea4eea54" in text
    assert "## 21. U6 Strategy dashboard control/read-model contract" in text
    assert "Activation = NOT SET" in text
    assert "STALE_ACTIVATION_STATE" in text
    assert "НЕ создаёт StrategyActivation" in text


def test_one_strategy_catalog_renders_exact_identity_and_missing_states() -> None:
    card = make_card()
    catalog = assemble_strategy_catalog([card_row(card)], [], [], [], [])
    assert len(catalog) == 1
    item = catalog[0]
    assert item["strategy_id"] == card.strategy_id
    assert item["strategy_version"] == card.strategy_version
    assert item["strategy_config_fingerprint"] == card.strategy_config_fingerprint
    assert item["activation_state"] == "NOT SET"
    assert item["activations"] == []
    assert item["entry_plan_fingerprints"] == []
    assert item["exit_plan_fingerprints"] == []
    assert item["sections"]["capital_leverage"] == {"require_capacity": False}
    assert 0 not in item["sections"].values()


def test_five_strategies_remain_independent_without_winner_or_priority() -> None:
    cards = [make_card(f"strategy-{i}") for i in range(5)]
    catalog = assemble_strategy_catalog([card_row(card) for card in cards], [], [], [], [])
    assert [item["strategy_id"] for item in catalog] == [f"strategy-{i}" for i in range(5)]
    encoded = json.dumps(catalog, default=str).lower()
    assert "winner" not in encoded
    assert "priority" not in encoded
    assert "primary" not in encoded


def test_contradictory_long_short_strategies_render_independently() -> None:
    long_card = make_card("same-market-long", direction=TradeDirection.LONG)
    short_card = make_card("same-market-short", direction=TradeDirection.SHORT)
    catalog = assemble_strategy_catalog([card_row(long_card), card_row(short_card)], [], [], [], [])
    assert len(catalog) == 2
    assert catalog[0]["direction_policy"] == ["LONG"]
    assert catalog[1]["direction_policy"] == ["SHORT"]


def test_exact_plan_fingerprints_are_kept_distinct_from_strategy_fingerprint() -> None:
    card = make_card()
    entry_fp = "e" * 64
    exit_fp = "x" * 64
    catalog = assemble_strategy_catalog(
        [card_row(card)],
        [],
        [
            {
                "strategy_id": card.strategy_id,
                "strategy_version": card.strategy_version,
                "strategy_config_fingerprint": card.strategy_config_fingerprint,
                "entry_plan_fingerprint": entry_fp,
                "entry_plan_version": "entry-1",
            }
        ],
        [
            {
                "strategy_id": card.strategy_id,
                "strategy_version": card.strategy_version,
                "strategy_config_fingerprint": card.strategy_config_fingerprint,
                "exit_plan_fingerprint": exit_fp,
                "exit_plan_version": "exit-1",
            }
        ],
        [],
    )
    item = catalog[0]
    assert item["strategy_config_fingerprint"] == card.strategy_config_fingerprint
    assert item["entry_plan_fingerprints"] == [entry_fp]
    assert item["exit_plan_fingerprints"] == [exit_fp]
    assert len({item["strategy_config_fingerprint"], entry_fp, exit_fp}) == 3


def test_build_new_version_is_immutable_and_policy_change_changes_fingerprint() -> None:
    base = make_card()
    editable = card_to_editable(base)
    editable["strategy_version"] = "2.0"
    entry = dict(editable["entry_policy"])
    predicate = dict(entry["predicate"])
    params = dict(predicate["params"])
    params["value"] = "2.0"
    predicate["params"] = params
    entry["predicate"] = predicate
    editable["entry_policy"] = entry
    created = build_new_strategy_version(
        base,
        editable,
        approved_at=NOW + timedelta(minutes=1),
        approved_source="dashboard:owner",
    )
    assert created.strategy_id == base.strategy_id
    assert created.strategy_version == "2.0"
    assert created.strategy_config_fingerprint != base.strategy_config_fingerprint
    assert base.strategy_version == "1.0"
    assert card_to_editable(base)["entry_policy"]["predicate"]["params"]["value"] == "1.0"


def test_new_version_rejects_string_boolean_instead_of_coercing_it() -> None:
    base = make_card()
    editable = card_to_editable(base)
    editable["strategy_version"] = "2.0"
    touch = dict(editable["touch_policy"])
    touch["require_exit_from_zone"] = "false"
    editable["touch_policy"] = touch
    with pytest.raises(ValueError, match="must be boolean"):
        build_new_strategy_version(
            base,
            editable,
            approved_at=NOW,
            approved_source="dashboard:owner",
        )


def test_new_version_cannot_reuse_same_version_or_change_strategy_id() -> None:
    base = make_card()
    editable = card_to_editable(base)
    with pytest.raises(ValueError, match="new strategy_version"):
        build_new_strategy_version(
            base, editable, approved_at=NOW, approved_source="dashboard:owner"
        )
    editable["strategy_version"] = "2.0"
    editable["strategy_id"] = "different"
    with pytest.raises(ValueError, match="strategy_id"):
        build_new_strategy_version(
            base, editable, approved_at=NOW, approved_source="dashboard:owner"
        )


@dataclass
class Cursor:
    rows: list[tuple[object, ...]]
    rowcount: int = 1

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        rows = list(self.rows)
        self.rows.clear()
        return rows


class Tx:
    def __enter__(self) -> object:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeConnection:
    def __init__(self, activation: dict[str, object] | None = None) -> None:
        self.activation = activation
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.activation_events = 0

    def transaction(self) -> Tx:
        return Tx()

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> Cursor:
        self.statements.append((statement, parameters))
        if "FROM strategy_entry.strategy_activations" in statement and "FOR UPDATE" in statement:
            if self.activation is None:
                return Cursor([], 0)
            a = self.activation
            return Cursor(
                [
                    (
                        a["activation_id"],
                        a["strategy_id"],
                        a["strategy_version"],
                        a["strategy_config_fingerprint"],
                        a["enabled"],
                        a["enabled_at"],
                        a["disabled_at"],
                        a["operator"],
                        a["source"],
                        a["updated_at"],
                    )
                ]
            )
        if statement.lstrip().startswith("UPDATE strategy_entry.strategy_activations"):
            assert self.activation is not None
            self.activation["enabled"] = parameters[0]
            self.activation["updated_at"] = parameters[2]
            self.activation_events += 1
            return Cursor([(parameters[2],)], 1)
        return Cursor([], 1)


def test_activation_toggle_changes_only_activation_and_is_journal_compatible() -> None:
    card = make_card()
    activation = activation_row(card, "act-1", False)
    connection = FakeConnection(activation)
    store = StrategyDashboardStore(connection)
    result = store.set_activation_enabled_cas(
        activation_id="act-1",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        expected_enabled=False,
        expected_updated_at=NOW,
        enabled=True,
        changed_at=NOW + timedelta(seconds=1),
        operator="owner",
        source="dashboard-u6",
        reason="owner toggle",
    )
    assert result["status"] == "UPDATED"
    sql = "\n".join(statement for statement, _ in connection.statements).lower()
    assert "update strategy_entry.strategy_activations" in sql
    assert "update strategy_entry.strategy_cards" not in sql
    assert "entry_plans" not in sql
    assert "exit_plans" not in sql
    assert connection.activation_events == 1


def test_activation_noop_is_safe_and_does_not_write_false_journal_event() -> None:
    card = make_card()
    activation = activation_row(card, "act-1", True)
    connection = FakeConnection(activation)
    store = StrategyDashboardStore(connection)
    result = store.set_activation_enabled_cas(
        activation_id="act-1",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        expected_enabled=True,
        expected_updated_at=NOW,
        enabled=True,
        changed_at=NOW + timedelta(seconds=1),
        operator="owner",
        source="dashboard-u6",
        reason="retry",
    )
    assert result["status"] == "NO_CHANGE"
    assert connection.activation_events == 0
    assert not any(stmt.lstrip().startswith("UPDATE") for stmt, _ in connection.statements)


def test_stale_activation_toggle_is_rejected_fail_honest() -> None:
    card = make_card()
    activation = activation_row(card, "act-1", False)
    activation["updated_at"] = NOW + timedelta(seconds=5)
    connection = FakeConnection(activation)
    store = StrategyDashboardStore(connection)
    with pytest.raises(StaleActivationState):
        store.set_activation_enabled_cas(
            activation_id="act-1",
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            expected_enabled=False,
            expected_updated_at=NOW,
            enabled=True,
            changed_at=NOW + timedelta(seconds=6),
            operator="owner",
            source="dashboard-u6",
            reason="stale",
        )
    assert connection.activation_events == 0
    assert not any(stmt.lstrip().startswith("UPDATE") for stmt, _ in connection.statements)


def test_dashboard_strategy_ui_contains_required_sections_and_full_fingerprint_labels() -> None:
    html = HTML.read_text(encoding="utf-8")
    for label in (
        "General",
        "Entry",
        "Touch",
        "Lifecycle / cooldown / reset",
        "Geometry / sensors",
        "Capital / leverage",
        "Protection",
        "Exit",
        "MAYAK usage",
        "Dispatcher usage",
        "strategy_config_fingerprint",
        "entry_plan_fingerprint",
        "exit_plan_fingerprint",
    ):
        assert label in html
    assert "NOT SET" in html
    assert "READ ONLY" in html


def test_strategy_api_has_no_execution_mainnet_or_selector_path() -> None:
    app = APP.read_text(encoding="utf-8")
    start = app.index("# U6_STRATEGY_API_BEGIN")
    end = app.index("# U6_STRATEGY_API_END")
    scope = app[start:end].lower()
    for forbidden in (
        "runtime.trade_commands",
        "executionrequest",
        "exchange",
        "mainnet",
        "re-arm",
        "select_best_strategy",
        "winner",
        "priority",
        "allocator",
    ):
        assert forbidden not in scope
    assert "/api/strategies" in scope
    assert "/api/strategies/version" in scope
    assert "/api/strategies/activation" in scope


def test_dashboard_unit_uses_installed_u6_source_not_mutable_checkout() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "CRIPTA_UNIVERSAL_ENTRY_SOURCE=/srv/cripta/dashboard/universal_entry_source/src" in unit
    assert "PYTHONPATH=/srv/cripta/dashboard/universal_entry_source/src" in unit
    assert "/srv/cripta/source_checkout/src" not in unit


def test_u5_shadow_and_legacy_paths_are_not_controlled_by_u6_strategy_api_source() -> None:
    source = (ROOT / "src/bybit_workbench/universal_entry/dashboard_control.py").read_text(
        encoding="utf-8"
    )
    lowered = source.lower()
    assert "universal_entry_shadow.service" not in lowered
    assert "entry_shadow_scanner.service" not in lowered
    assert "runtime.trade_commands" not in lowered
    assert "runtime.executions" not in lowered

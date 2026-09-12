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
    StrategyRuntimeNotReady,
    assemble_strategy_catalog,
    build_new_strategy_version,
    card_from_editable,
    card_to_editable,
    strategy_authoring_template,
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
    assert "**Source baseline U6:** 78e5e90753a3dffb2b61177174a94dc8ea4eea54" in text
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
    def __init__(
        self,
        activation: dict[str, object] | None = None,
        card: StrategyCard | None = None,
    ) -> None:
        self.activation = activation
        self.card = card
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.activation_events = 0

    def transaction(self) -> Tx:
        return Tx()

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> Cursor:
        self.statements.append((statement, parameters))
        if (
            "FROM strategy_entry.strategy_cards" in statement
            and "WHERE strategy_id=%s" in statement
        ):
            if self.card is None:
                return Cursor([], 0)
            return Cursor(
                [
                    (
                        json.loads(canonical_json(self.card)),
                        self.card.approved_at,
                        self.card.approved_source,
                    )
                ]
            )
        if (
            "SELECT activation_id,enabled,updated_at" in statement
            and "FROM strategy_entry.strategy_activations" in statement
        ):
            if self.activation is None:
                return Cursor([], 0)
            return Cursor(
                [
                    (
                        self.activation["activation_id"],
                        self.activation["enabled"],
                        self.activation["updated_at"],
                    )
                ]
            )
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


def test_strategy_authoring_context_catalog_is_exact_current_34_groups() -> None:
    from bybit_workbench.universal_entry.dashboard_control import strategy_context_feature_catalog

    rows = strategy_context_feature_catalog()
    assert len(rows) == 34
    assert sum(item["scope"] == "GLOBAL" for item in rows) == 19
    assert sum(item["scope"] == "COIN" for item in rows) == 15
    ids = {item["feature_id"] for item in rows}
    assert len(ids) == 34
    for required in (
        "money.pressure",
        "market.synchronization",
        "positioning.price_oi_state",
        "money.spot",
        "money.derivatives",
        "positioning.open_interest",
        "liquidity.derivatives",
        "relative_strength",
    ):
        assert required in ids


def test_new_strategy_ui_version_requires_exactly_one_direction() -> None:
    base = make_card()
    editable = card_to_editable(base)
    editable["strategy_version"] = "2.0"
    editable["direction_policy"] = ["LONG", "SHORT"]
    with pytest.raises(ValueError, match="exactly one direction"):
        build_new_strategy_version(
            base, editable, approved_at=NOW, approved_source="dashboard:owner"
        )
    editable["direction_policy"] = ["SHORT"]
    created = build_new_strategy_version(
        base, editable, approved_at=NOW, approved_source="dashboard:owner"
    )
    assert created.direction_policy == (TradeDirection.SHORT,)


def test_context_condition_requires_explicit_fail_honest_data_semantics() -> None:
    base = make_card()
    editable = card_to_editable(base)
    editable["strategy_version"] = "2.0"
    entry = dict(editable["entry_policy"])
    entry["context_feature_policy"] = [
        {
            "feature_id": "money.pressure",
            "scope": "GLOBAL",
            "mode": "CONDITION",
            "condition": {"operator": "EQ", "value": "STRONG_BUY"},
        }
    ]
    editable["entry_policy"] = entry
    with pytest.raises(ValueError, match="max_age_seconds"):
        build_new_strategy_version(
            base, editable, approved_at=NOW, approved_source="dashboard:owner"
        )
    entry["context_feature_policy"][0].update(
        {
            "max_age_seconds": 30,
            "min_quality": "MEDIUM",
            "on_missing": "REJECT_SIGNAL",
            "on_stale": "REJECT_SIGNAL",
            "on_partial": "REJECT_SIGNAL",
        }
    )
    created = build_new_strategy_version(
        base, editable, approved_at=NOW, approved_source="dashboard:owner"
    )
    assert created.strategy_version == "2.0"


def test_enabled_hedge_requires_explicit_trigger_size_and_leverage() -> None:
    base = make_card()
    editable = card_to_editable(base)
    editable["strategy_version"] = "2.0"
    lifecycle = dict(editable["lifecycle_policy"])
    lifecycle["hedge_policy"] = {"enabled": True}
    editable["lifecycle_policy"] = lifecycle
    with pytest.raises(ValueError, match="hedge_policy.trigger|hedge trigger reference"):
        build_new_strategy_version(
            base, editable, approved_at=NOW, approved_source="dashboard:owner"
        )
    lifecycle["hedge_policy"] = {
        "enabled": True,
        "opposite_direction": True,
        "trigger": {"reference": "PRIMARY_ENTRY", "offset_pct_signed": "0.50"},
        "capital": {"size_percent_of_primary": "100", "leverage": 1},
        "stop_loss": {"enabled": True, "percent": "1.00"},
        "take_profit": {"enabled": True, "percent": "0.60"},
        "trailing": {"enabled": False},
    }
    created = build_new_strategy_version(
        base, editable, approved_at=NOW, approved_source="dashboard:owner"
    )
    catalog = assemble_strategy_catalog([card_row(created)], [], [], [], [])
    assert catalog[0]["sections"]["hedge"]["enabled"] is True


def test_strategy_authoring_ui_has_entry_exit_hedge_and_friendly_strategy_name() -> None:
    html = HTML.read_text(encoding="utf-8")
    for label in (
        "Вход",
        "Выход",
        "Хедж",
        "Понятное название стратегии",
        "Смещение от рассчитанного Entry, %",
        "Большая зона · 5m свечей",
        "Большая зона · 15m свечей",
        "Локальное окно, минут",
        "180 = 3 часа",
        "Hard stop",
        "Take profit",
        "Fee-aware",
        "Trailing",
        "Глубина/смещение от primary Entry, %",
        "Размер хеджа, % основной позиции",
        "context_feature_catalog",
        "strategy-name-chip",
    ):
        assert label.lower() in html.lower()


def test_strategy_api_exposes_objective_context_catalog_without_trading_rights() -> None:
    app = APP.read_text(encoding="utf-8")
    start = app.index("# U6_STRATEGY_API_BEGIN")
    end = app.index("# U6_STRATEGY_API_END")
    scope = app[start:end]
    assert "strategy_context_feature_catalog()" in scope
    assert '"context_modes": ["OFF", "OBSERVE", "CONDITION", "RANKING"]' in scope
    lowered = scope.lower()
    for forbidden in ("runtime.trade_commands", "place_order", "mainnet", "exchange"):
        assert forbidden not in lowered


def test_trade_cards_use_exact_strategy_identity_for_friendly_name() -> None:
    app = APP.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    assert "LEFT JOIN strategy_entry.strategy_cards c" in app
    assert "c.strategy_id=o.strategy_id AND c.strategy_version=o.strategy_version" in app
    assert '"strategy_name": None if ownership is None else ownership["strategy_name"]' in app
    assert '"strategy_name": strategy_name_by_identity.get' in app
    assert "strategy-name-chip" in html
    assert "p.strategy_name" in html
    assert "card.strategy_name" in html


def test_strategy_authoring_template_is_inert_and_has_no_legacy_reset_numbers() -> None:
    template = strategy_authoring_template()
    assert template["symbols"] == []
    assert template["direction_policy"] == []
    entry = template["entry_policy"]
    assert entry["watch_policy"] == {"enabled": False}
    assert template["touch_policy"]["candidate_cooldown"]["enabled"] is False
    assert template["lifecycle_policy"]["post_signal_outcome_policy"] == {"enabled": False}
    encoded = json.dumps(template, sort_keys=True)
    for legacy_number in ('"30"', '"60"', '"10.0"', '"3.0"'):
        assert legacy_number not in encoded


def test_new_strategy_requires_exact_nonempty_symbol_scope() -> None:
    template = strategy_authoring_template()
    template["strategy_id"] = "strategy-new"
    template["strategy_version"] = "1.0"
    template["name"] = "LONG fast coins"
    template["direction_policy"] = ["LONG"]
    with pytest.raises(ValueError, match="at least one symbol"):
        card_from_editable(template, approved_at=NOW, approved_source="dashboard:owner")
    template["symbols"] = ["UNIUSDT", "LINKUSDT"]
    template["scope"] = {"kind": "symbols", "symbols": ["UNIUSDT"]}
    with pytest.raises(ValueError, match="scope symbols must exactly match"):
        card_from_editable(template, approved_at=NOW, approved_source="dashboard:owner")
    template["scope"] = {"kind": "symbols", "symbols": ["UNIUSDT", "LINKUSDT"]}
    created = card_from_editable(template, approved_at=NOW, approved_source="dashboard:owner")
    assert created.symbols == ("LINKUSDT", "UNIUSDT")


def test_legacy_v1_card_can_reproduce_without_relaxing_new_authoring_rules() -> None:
    from bybit_workbench.universal_entry.v1_compat import load_v1_compatibility_bundle

    legacy = load_v1_compatibility_bundle(ROOT).card
    editable = card_to_editable(legacy)
    reproduced = card_from_editable(
        editable,
        approved_at=legacy.approved_at,
        approved_source=legacy.approved_source,
        validate_authoring=False,
    )
    assert reproduced.strategy_config_fingerprint == legacy.strategy_config_fingerprint
    with pytest.raises(ValueError, match="exactly one direction"):
        card_from_editable(
            editable,
            approved_at=NOW,
            approved_source="dashboard:owner",
        )


def test_new_authoring_validates_explicit_shock_and_failure_embargo() -> None:
    template = strategy_authoring_template()
    template.update(
        {
            "strategy_id": "strategy-reset",
            "strategy_version": "1.0",
            "name": "LONG reset test",
            "symbols": ["UNIUSDT"],
            "scope": {"kind": "symbols", "symbols": ["UNIUSDT"]},
            "direction_policy": ["LONG"],
        }
    )
    watch = {
        "enabled": True,
        "candidate_timeframe_minutes": 5,
        "required_closed_timeframes": ["5", "15"],
        "events": {
            "bar_open": "BAR_OPEN",
            "candle_closed": "CANDLE_CLOSED",
            "open_interest": "OPEN_INTEREST",
            "trade": "PUBLIC_TRADE",
        },
        "geometry": {},
        "hourly_swing": {"enabled": False},
        "direction_rules": {"LONG": {"entry_zone_field": "support_top", "touch_comparator": "LTE"}},
        "direction_precedence": ["LONG"],
        "candidate_lifecycle": {"clear_on_touch": True},
        "flow": {"enabled": False},
        "oi": {"enabled": False},
        "derived_event_kind": "TOUCH",
    }
    template["entry_policy"]["watch_policy"] = watch
    geometry = watch["geometry"]
    geometry.update(
        {
            "operator": "RANGE_ATR_CONFLUENCE",
            "timeframes": ["5", "15"],
            "primary_timeframe": "5",
            "confirming_timeframe": "15",
            "lookback_by_timeframe": {"5": 36, "15": 12},
            "atr_period": 20,
            "zone_half_width_atr": "0.5",
            "confluence_max_gap_percent": "0.25",
            "shock_reset_policy": {
                "enabled": True,
                "detection_mode": "RANGE_PERCENT",
                "maturity_minutes": 45,
                "threshold_percent_by_timeframe": {"5": "2.5", "15": "4.0"},
            },
        }
    )
    watch["enabled"] = True
    watch["hourly_swing"] = {
        "enabled": True,
        "operator": "ROLLING_RANGE_PERCENT",
        "timeframe": "5",
        "window_bars": 12,
        "threshold_percent": "8.0",
        "comparator": "GTE",
    }
    template["lifecycle_policy"]["post_signal_outcome_policy"] = {
        "enabled": True,
        "observation_event_kind": "PUBLIC_TRADE",
        "reference_value_path": "fact.entry_price",
        "observation_value_path": "fact.price",
        "metric": "DIRECTIONAL_PERCENT_CHANGE",
        "favorable_threshold": "0.60",
        "adverse_threshold": "-1.20",
        "horizon": "240",
        "horizon_unit": "minutes",
        "resolution_semantics": "FIRST_THRESHOLD",
        "favorable_resulting_entry_state": "CLEAR",
        "adverse_resulting_entry_state": "EMBARGO",
        "optional_embargo": {
            "enabled": True,
            "on_resolution": "ADVERSE",
            "duration": "90",
            "unit": "minutes",
            "scope": "PER_SYMBOL",
            "anchor": "fact.observed_at",
        },
    }
    created = card_from_editable(template, approved_at=NOW, approved_source="dashboard:owner")
    stored = card_to_editable(created)
    assert (
        stored["entry_policy"]["watch_policy"]["geometry"]["shock_reset_policy"]["detection_mode"]
        == "RANGE_PERCENT"
    )
    assert stored["lifecycle_policy"]["post_signal_outcome_policy"]["adverse_threshold"] == "-1.20"


def test_strategy_create_store_inserts_only_immutable_card() -> None:
    template = strategy_authoring_template()
    template.update(
        {
            "strategy_id": "strategy-new",
            "strategy_version": "1.0",
            "name": "SHORT group",
            "symbols": ["UNIUSDT"],
            "scope": {"kind": "symbols", "symbols": ["UNIUSDT"]},
            "direction_policy": ["SHORT"],
        }
    )
    connection = FakeConnection()
    created = StrategyDashboardStore(connection).create_strategy(
        payload=template,
        approved_at=NOW,
        operator="owner",
    )
    assert created.strategy_id == "strategy-new"
    sql = "\n".join(statement for statement, _ in connection.statements).lower()
    assert "insert into strategy_entry.strategy_cards" in sql
    assert "strategy_activations" not in sql
    assert "entry_plans" not in sql
    assert "exit_plans" not in sql


def test_strategy_dashboard_exposes_prominent_create_symbols_and_reset_controls() -> None:
    html = HTML.read_text(encoding="utf-8")
    for label in (
        "＋ Создать новую Strategy",
        "Монеты этой Strategy",
        "Сброс старой зоны после резкого движения",
        "ATR/True Range × среднее",
        "True Range в % цены",
        "Rolling swing gate",
        "Повторный candidate после касания / сигнала / попытки",
        "Неудачный StrategySignal → запрет нового входа",
        "Entry V1: после любого TOUCH",
        "+0,50%",
        "−1,00%",
        "/api/strategies/create",
    ):
        assert label in html


def test_strategy_api_create_path_has_no_activation_or_execution_side_effect() -> None:
    app = APP.read_text(encoding="utf-8")
    start = app.index("# U6_STRATEGY_API_BEGIN")
    end = app.index("# U6_STRATEGY_API_END")
    scope = app[start:end].lower()
    assert 'u6_strategy_create_path = "/api/strategies/create"' in scope
    assert "strategy_authoring_template()" in scope
    assert '"symbol_catalog"' in scope
    for forbidden in ("runtime.trade_commands", "runtime.executions", "place_order", "mainnet"):
        assert forbidden not in scope


def _runtime_ready_owner_card() -> StrategyCard:
    from bybit_workbench.universal_entry.dashboard_control import (
        card_from_editable,
        strategy_authoring_template,
    )

    raw = strategy_authoring_template()
    raw.update(
        {
            "strategy_id": "strategy-runtime-ready",
            "strategy_version": "1.0",
            "name": "Runtime ready LONG",
            "symbols": ["UNIUSDT"],
            "scope": {"kind": "symbols", "symbols": ["UNIUSDT"]},
            "direction_policy": ["LONG"],
        }
    )
    raw["entry_policy"]["watch_policy"] = {
        "enabled": True,
        "candidate_timeframe_minutes": 5,
        "required_closed_timeframes": ["5", "15"],
        "events": {
            "bar_open": "BAR_OPEN",
            "candle_closed": "CANDLE_CLOSED",
            "open_interest": "OPEN_INTEREST",
            "trade": "PUBLIC_TRADE",
        },
        "geometry": {
            "operator": "RANGE_ATR_CONFLUENCE",
            "timeframes": ["5", "15"],
            "primary_timeframe": "5",
            "confirming_timeframe": "15",
            "lookback_by_timeframe": {"5": 36, "15": 12},
            "atr_period": 20,
            "zone_half_width_atr": "0.5",
            "confluence_max_gap_percent": "0.25",
            "shock_reset_policy": {"enabled": False},
        },
        "hourly_swing": {"enabled": False},
        "direction_rules": {"LONG": {"entry_zone_field": "support_top", "touch_comparator": "LTE"}},
        "direction_precedence": ["LONG"],
        "candidate_lifecycle": {"clear_on_touch": True},
        "flow": {"enabled": False},
        "oi": {"enabled": False},
        "derived_event_kind": "TOUCH",
    }
    raw["entry_policy"]["execution_policy"] = {
        "order_type": "MARKET",
        "reference_value_path": "fact.entry_price",
        "max_request_age_seconds": 30,
    }
    raw["capital_policy"] = {
        "require_capacity": True,
        "requested_amount": "10",
        "amount_currency": "USDT",
        "leverage": 1,
        "capacity_max_age_seconds": 15,
        "capacity_min_quality": "MEDIUM",
    }
    raw["exit_policy"].update(
        {
            "hard_stop": {"enabled": True, "percent": "1.00"},
            "take_profit": {"enabled": True, "percent": "1.10"},
            "break_even": {"enabled": False},
            "trailing": {"enabled": False},
            "local_zone_exit": {"enabled": False},
            "time_exit": {"enabled": False},
        }
    )
    raw["protection_policy"] = {
        "initial_protection": {
            "stop_loss_enabled": True,
            "stop_loss_pct": "1.00",
            "take_profit_enabled": True,
            "take_profit_pct": "1.10",
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
        }
    }
    return card_from_editable(raw, approved_at=NOW, approved_source="dashboard:owner")


def test_strategy_runtime_readiness_separates_policy_execution_and_observer() -> None:
    from bybit_workbench.universal_entry.readiness import assess_strategy_runtime_readiness

    card = _runtime_ready_owner_card()
    without_observer = assess_strategy_runtime_readiness(card, observer_ready=False)
    assert without_observer.policy_ready is True
    assert without_observer.execution_ready is True
    assert without_observer.observer_ready is False
    assert without_observer.active_ready is False
    assert [item.code for item in without_observer.reasons] == [
        "MULTI_STRATEGY_OBSERVER_NOT_INSTALLED"
    ]
    complete = assess_strategy_runtime_readiness(card, observer_ready=True)
    assert complete.active_ready is True
    assert complete.reasons == ()


def test_every_known_stored_only_policy_blocks_activation_instead_of_silent_ignore() -> None:
    from bybit_workbench.universal_entry.dashboard_control import (
        card_from_editable,
        card_to_editable,
    )
    from bybit_workbench.universal_entry.readiness import assess_strategy_runtime_readiness

    base = _runtime_ready_owner_card()
    raw = card_to_editable(base)
    raw["strategy_version"] = "2.0"
    raw["entry_policy"]["entry_reference_policy"] = {
        "enabled": True,
        "reference": "CALCULATED_ENTRY",
        "offset_pct_signed": "-0.20",
    }
    raw["entry_policy"]["local_entry_policy"] = {
        "enabled": True,
        "window_minutes": 180,
        "lookback_by_timeframe": {"5": 36, "15": 12},
        "use_1m": False,
        "require_5m_15m_confluence": True,
        "require_macro_relation": True,
    }
    raw["entry_policy"]["context_feature_policy"] = [
        {"feature_id": "money.pressure", "scope": "GLOBAL", "mode": "OBSERVE"}
    ]
    raw["exit_policy"]["trailing"] = {
        "enabled": True,
        "activation_profit_pct": "0.50",
        "distance_pct": "0.30",
    }
    raw["lifecycle_policy"]["hedge_policy"] = {
        "enabled": True,
        "opposite_direction": True,
        "trigger": {"reference": "PRIMARY_ENTRY", "offset_pct_signed": "0.50"},
        "capital": {"size_percent_of_primary": "100", "leverage": 1},
        "stop_loss": {"enabled": False},
        "take_profit": {"enabled": False},
        "trailing": {"enabled": False},
    }
    card = card_from_editable(raw, approved_at=NOW, approved_source="dashboard:owner")
    readiness = assess_strategy_runtime_readiness(card, observer_ready=True)
    codes = {item.code for item in readiness.reasons}
    assert {
        "ENTRY_REFERENCE_OFFSET_NOT_CONSUMED",
        "LOCAL_ENTRY_POLICY_NOT_IMPLEMENTED",
        "ENTRY_CONTEXT_FEATURE_POLICY_NOT_COMPILED",
        "EXIT_TRAILING_NOT_WIRED",
        "HEDGE_POLICY_NOT_IMPLEMENTED",
    } <= codes
    assert readiness.active_ready is False


def test_first_activation_atomically_materializes_exact_entry_and_exit_plans() -> None:
    card = _runtime_ready_owner_card()
    connection = FakeConnection(card=card)
    result = StrategyDashboardStore(connection).activate_exact_strategy(
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        changed_at=NOW,
        operator="owner",
        source="dashboard-strategy-control",
        reason="owner enabled Strategy",
        observer_ready=True,
    )
    assert result["status"] == "CREATED_ENABLED"
    sql = "\n".join(statement for statement, _ in connection.statements).lower()
    assert "insert into strategy_entry.strategy_activations" in sql
    assert "insert into strategy_entry.entry_plans" in sql
    assert "insert into strategy_entry.exit_plans" in sql
    assert "runtime.trade_commands" not in sql


def test_first_activation_is_blocked_before_db_mutation_without_observer() -> None:
    card = _runtime_ready_owner_card()
    connection = FakeConnection(card=card)
    with pytest.raises(StrategyRuntimeNotReady) as caught:
        StrategyDashboardStore(connection).activate_exact_strategy(
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            changed_at=NOW,
            operator="owner",
            source="dashboard-strategy-control",
            reason="owner enabled Strategy",
            observer_ready=False,
        )
    assert caught.value.readiness.active_ready is False
    writes = [
        statement
        for statement, _ in connection.statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []


def test_strategy_ui_has_active_toggle_readiness_and_explicit_limit_execution_offset() -> None:
    html = HTML.read_text(encoding="utf-8")
    for token in (
        "Сделать активной",
        "Сделать неактивной",
        "Runtime readiness",
        "STRATEGY_RUNTIME_NOT_READY",
        "Execution offset для LIMIT, %",
        "document.getElementById('strategyNewEditor')",
        "Архив Entry V1 · остановлен",
    ):
        assert token in html


def test_strategy_api_keeps_multi_strategy_observer_fail_closed_until_installed() -> None:
    app = APP.read_text(encoding="utf-8")
    scope = app[app.index("# U6_STRATEGY_API_BEGIN") : app.index("# U6_STRATEGY_API_END")]
    assert "U6_MULTI_STRATEGY_OBSERVER_READY = False" in scope
    assert "activate_exact_strategy(" in scope
    assert "enforce_readiness=True" in scope
    assert '"STRATEGY_RUNTIME_NOT_READY"' in scope

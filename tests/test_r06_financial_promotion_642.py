"""Issue #642: R06 SOURCE_ONLY -> Universal Entry financial promotion regressions."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.market_identity import (
    bybit_instrument_id,
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
)
from bybit_workbench.universal_entry import r06_financial_promotion as promotion
from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
    StrategyExecutionBindingRef,
    TradeDirection,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.r07_publication_provenance import (
    R07PublicationProvenanceUnavailable,
    require_r07_publication_provenance,
)
from operations.monitoring import r06_pg_only_entry


EVENT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
SESSION = "a" * 32
PRODUCER = "b" * 40
CONSUMER = "c" * 40


class Result:
    def __init__(self, *, one=None, many=(), rowcount=1):
        self._one = one
        self._many = tuple(many)
        self.rowcount = rowcount

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


def _fact() -> MarketFactEnvelope:
    return MarketFactEnvelope(
        fact_id="watch-p90l90-test",
        event_kind="P90L90_RAW_H9_PREV7D_CROSSING",
        symbol="LTCUSDT",
        observed_at=EVENT,
        event_at=EVENT,
        received_at=EVENT + timedelta(milliseconds=50),
        source_refs=("bybit:publicTrade:LTCUSDT:native-100",),
        attributes=FrozenPolicy.from_mapping(
            {
                "entry_price": "100",
                "price": "100",
                "previous_tick_price": "101",
                "raw_entry_boundary": "100",
                "depth_pct": "1",
                "state_effective_at": EVENT.isoformat(),
                "p90l90": {
                    "candidate_price": "100",
                    "opposite_raw_l53_target": "101",
                    "state_effective_at": EVENT.isoformat(),
                    "depth_pct": "1",
                },
                "modeled_exchange_fill": False,
                "trade_fact_id": "trade-native-100",
            }
        ),
        direction=TradeDirection.LONG,
    )


def _bundle():
    return SimpleNamespace(
        card=SimpleNamespace(strategy_id=MAIN_ID, strategy_version=VERSION),
        activation=SimpleNamespace(activation_id="activation-main"),
    )


def _binding():
    return StrategyExecutionBindingRef(
        strategy_execution_binding_id="binding-main",
        strategy_activation_id="activation-main",
        execution_leg="PRIMARY",
        trading_account_id="ta-main",
    )


def test_candidate_envelope_roundtrip_is_exact_and_not_financial():
    fact = _fact()
    encoded = promotion.encode_candidate_fact(fact)
    import json

    restored = promotion._restore_candidate_fact(json.loads(encoded))
    assert restored == fact
    assert "EntryDecision" not in encoded
    assert "EntryExecutionRequest" not in encoded


def test_disarmed_baseline_tails_r06_without_financial_objects(monkeypatch):
    queries: list[str] = []

    class Connection:
        def execute(self, sql, params=()):
            queries.append(sql)
            if "SELECT enabled FROM control.execution_gates" in sql:
                # Consumer uses mapping rows; prearm uses tuple rows.
                return Result(one={"enabled": False})
            if "INSERT INTO strategy_entry.p90_r06_financial_promotion_checkpoints" in sql:
                return Result()
            raise AssertionError(sql)

    monkeypatch.setattr(
        promotion,
        "_route_bundle",
        lambda *_a, **_kw: (_bundle(), _binding()),
    )
    monkeypatch.setattr(
        promotion,
        "_r06_checkpoint",
        lambda *_a, **_kw: (SESSION, 100, 2, 45),
    )
    outcome = promotion.sync_disarmed_baseline(
        Connection(),
        trading_account_id="ta-main",
        account_connector_id="connector-main",
        loaded_release_commit=CONSUMER,
        now=EVENT,
    )
    assert outcome == "BASELINE_SYNCED"
    rendered = "\n".join(queries)
    for forbidden in (
        "strategy_signals",
        "strategy_attempts",
        "entry_decisions",
        "execution_requests",
        "exchange_position_slot_claims",
        "capital_reservations",
    ):
        assert forbidden not in rendered


def test_stale_r06_backlog_is_skipped_to_current_verified_boundary(monkeypatch):
    updates: list[tuple[object, ...]] = []
    stale_at = EVENT - timedelta(seconds=6)

    class Connection:
        def execute(self, sql, params=()):
            if "SELECT enabled FROM control.execution_gates" in sql:
                return Result(one={"enabled": True})
            if "FROM strategy_entry.p90_r06_financial_promotion_checkpoints" in sql:
                return Result(
                    one=(
                        "activation-main",
                        "binding-main",
                        SESSION,
                        90,
                        1,
                        40,
                        CONSUMER,
                        "DISARMED_BASELINE",
                    )
                )
            if "FROM strategy_entry.p90_r06_publication_observations" in sql:
                return Result(
                    many=(
                        (
                            41,
                            SESSION,
                            91,
                            0,
                            "native-old",
                            "sig-old",
                            "LTCUSDT",
                            "LONG",
                            stale_at,
                            {},
                        ),
                    )
                )
            if "UPDATE strategy_entry.p90_r06_financial_promotion_checkpoints" in sql:
                updates.append(tuple(params))
                return Result(rowcount=1)
            raise AssertionError(sql)

    monkeypatch.setattr(
        promotion,
        "_route_bundle",
        lambda *_a, **_kw: (_bundle(), _binding()),
    )
    monkeypatch.setattr(
        promotion,
        "_r06_checkpoint",
        lambda *_a, **_kw: (SESSION, 100, 2, 45),
    )
    monkeypatch.setattr(
        promotion,
        "account_live_arm_ready",
        lambda *_a, **_kw: pytest.fail("stale backlog must not reach owner arm"),
    )
    result = promotion.promote_next(
        Connection(),
        trading_account_id="ta-main",
        account_connector_id="connector-main",
        loaded_release_commit=CONSUMER,
        now=EVENT,
    )
    assert result == "STALE_BACKLOG_SKIPPED"
    assert len(updates) == 1
    # Cursor jumps to the current verified R06 boundary, never replays backlog.
    assert updates[0][1:4] == (100, 2, 45)


def _r07_proof(*, published_after: timedelta) -> dict[str, object]:
    return {
        "publication_id": 45,
        "source_session_id": SESSION,
        "frame_number": 100,
        "frame_index": 2,
        "native_exec_id": "native-100",
        "event_at": EVENT,
        "symbol": "LTCUSDT",
        "direction": "LONG",
        "market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
        "publication_session_id": SESSION,
        "producer_release_commit": PRODUCER,
        "published_at": EVENT + published_after,
        "as_of_second": EVENT,
        "publication_status": "WATERMARKS_ONLY",
        "publication_native_watermarks": {
            "LTCUSDT": {
                "instrument_id": bybit_instrument_id("linear", "LTCUSDT"),
                "frame_number": 100,
                "frame_index": 2,
                "exchange_sequence": 123456,
                "exec_id": "native-100",
            }
        },
        "source_session_state": "ACTIVE",
        "session_producer_release_commit": PRODUCER,
        "event_market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
        "instrument_id": bybit_instrument_id("linear", "LTCUSDT"),
        "external_symbol": "LTCUSDT",
        "exec_id": "native-100",
        "original_exchange_sequence": 123456,
        "original_event_at": EVENT,
        "accepted_at": EVENT + timedelta(milliseconds=100),
        "checkpoint_frame_number": 101,
        "checkpoint_frame_index": 0,
        "last_publication_id": 46,
        "bootstrap_publication_id": 40,
        "entry_plan_fingerprint": "e" * 64,
        "exit_plan_fingerprint": "f" * 64,
        "checkpoint_producer_release_commit": PRODUCER,
        "consumer_release_commit": CONSUMER,
    }


class R07Connection:
    def __init__(self, proof: dict[str, object]):
        self.proof = proof

    def execute(self, _sql, _params=()):
        return Result(many=(self.proof,))


def _r07_request() -> dict[str, object]:
    # P90 request TTL remains anchored to the original market event.
    return {
        "strategy_id": MAIN_ID,
        "strategy_version": VERSION,
        "signal_id": "signal-100",
        "symbol": "LTCUSDT",
        "direction": "LONG",
        "requested_at": EVENT,
        "entry_plan_fingerprint": "e" * 64,
        "exit_plan_fingerprint": "f" * 64,
    }


def test_r07_accepts_publication_committed_after_native_event_within_five_seconds():
    assert require_r07_publication_provenance(
        R07Connection(_r07_proof(published_after=timedelta(seconds=1))),
        _r07_request(),
    ) == 45


def test_r07_rejects_publication_delay_beyond_signal_freshness():
    with pytest.raises(
        R07PublicationProvenanceUnavailable,
        match="TIME_OR_FRESHNESS_MISMATCH",
    ):
        require_r07_publication_provenance(
            R07Connection(_r07_proof(published_after=timedelta(seconds=6))),
            _r07_request(),
        )


def test_r06_no_exposure_guard_is_bootstrap_only_not_process_restart_gate():
    source = inspect.getsource(r06_pg_only_entry.run_pg_only)
    assert "bootstrap_requires_safety = not _has_durable_decision_checkpoint(writer)" in source
    assert "if not first_cursor_accepted and bootstrap_requires_safety:" in source
    assert "if not bootstrap_requires_safety:" in source
    assert source.index("if not checkpoints:") < source.index(
        "if not bootstrap_requires_safety:"
    )


ROOT = Path(__file__).resolve().parents[1]


def test_r06_persists_candidate_payload_but_stays_no_order():
    source = inspect.getsource(r06_pg_only_entry._observe_one)
    assert "candidate_fact" in source
    assert "encode_candidate_fact(signal_fact)" in source
    assert "item.decision is not None" in source
    assert "item.execution_request is not None" in source
    assert "R06_FINANCIAL_OR_CROSS_PLAN_EFFECT_FORBIDDEN" in source


def test_consumer_existing_requests_keep_priority_over_r06_promotion():
    source = (
        ROOT / "operations/connectivity/universal_entry_consumer.py"
    ).read_text(encoding="utf-8")
    next_request = source.index("row = _next_request(connection, route=route)")
    promotion_call = source.index("promotion_result = promote_next_r06_financial_signal(")
    diagnostic_call = source.index("diagnostic_result = materialize_requested_run(")
    assert next_request < promotion_call < diagnostic_call
    assert "sync_r06_financial_disarmed_baseline(" in source


def test_prearm_accepts_exact_r06_source_only_and_requires_financial_baseline():
    source = (
        ROOT / "src/bybit_workbench/p90l90_real_prearm_evidence.py"
    ).read_text(encoding="utf-8")
    assert '"R06_PG_NO_ORDER_SOURCE_ONLY"' in source
    assert "sync_disarmed_baseline(" in source
    assert "require_disarmed_baseline(" in source
    assert '"financial_promotion_baselines"' in source


def test_migration_keeps_runtime_role_non_ddl_and_installer_owns_apply():
    migration = (
        ROOT / "operations/sql/20261116_r12_r06_financial_promotion.sql"
    ).read_text(encoding="utf-8")
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS candidate_fact jsonb" in migration
    assert "p90_r06_financial_promotion_checkpoints" in migration
    assert "GRANT SELECT,INSERT,UPDATE" in migration
    assert "REVOKE DELETE,TRUNCATE" in migration
    assert "GRANT ALL" not in migration
    assert "GRANT CREATE" not in migration
    assert "20261116_r12_r06_financial_promotion.sql" in installer


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"enabled": False}, False),
        ({"enabled": True}, True),
        ({"enabled": 0}, False),
        ({"enabled": 1}, True),
        ((False,), False),
        ((True,), True),
        ((0,), False),
        ((1,), True),
    ],
)
def test_gate_row_shape_is_consistent_for_consumer_and_prearm(row, expected):
    class Connection:
        def execute(self, _sql, _params=()):
            return Result(one=row)

    assert promotion._gate_enabled(Connection()) is expected


def test_execution_permission_must_predate_candidate_event():
    bundle = SimpleNamespace(
        card=SimpleNamespace(
            strategy_id=MAIN_ID,
            strategy_version=VERSION,
            strategy_config_fingerprint="f" * 64,
        )
    )

    class Connection:
        def __init__(self, enabled_at):
            self.enabled_at = enabled_at

        def execute(self, _sql, _params=()):
            return Result(
                many=({"enabled": True, "enabled_at": self.enabled_at},)
            )

    assert promotion._execution_permission_ready(
        Connection(EVENT - timedelta(seconds=1)),
        bundle=bundle,
        event_at=EVENT,
    )
    assert not promotion._execution_permission_ready(
        Connection(EVENT + timedelta(milliseconds=1)),
        bundle=bundle,
        event_at=EVENT,
    )


def test_mapping_row_adapter_preserves_runtime_loader_column_order():
    class Inner:
        def fetchall(self):
            return [{"first": "a", "second": "b"}]

    class Connection:
        def execute(self, _sql, _params=()):
            return Inner()

    rows = promotion._tuple_rows(Connection()).execute("SELECT 1").fetchall()
    assert rows == [("a", "b")]


def test_non_p90_account_is_rejected_before_registry_materialization(monkeypatch):
    class Result:
        def fetchall(self):
            return []

    class Connection:
        def execute(self, _sql, _params=()):
            return Result()

    monkeypatch.setattr(
        promotion,
        "load_active_strategy_bundles",
        lambda *_args, **_kwargs: pytest.fail("non-P90 route must not load registry"),
    )
    assert promotion._route_bundle(
        Connection(), trading_account_id="ta-generic", now=EVENT
    ) is None

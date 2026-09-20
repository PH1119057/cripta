from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.counterfactual import (
    AnalystCounterfactualStore,
    CounterfactualCandidate,
    CounterfactualEconomicsStatus,
    CounterfactualOutcome,
    CounterfactualOutcomeStatus,
)
from bybit_workbench.universal_entry.contracts import (
    EntryDecisionCode,
    FrozenPolicy,
    TradeDirection,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)
NOW = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)


def _seed_candidate_lineage(
    connection: psycopg.Connection,
    prefix: str,
) -> CounterfactualCandidate:
    strategy_id = f"{prefix}-strategy"
    version = "1"
    strategy_fp = f"{prefix}-strategy-fp"
    activation_id = f"{prefix}-activation"
    entry_fp = f"{prefix}-entry-fp"
    exit_fp = f"{prefix}-exit-fp"
    signal_id = f"{prefix}-signal"
    attempt_id = f"{prefix}-attempt"
    decision_id = f"{prefix}-decision"
    symbol = "P8" + prefix.replace("-", "").upper()[-8:] + "USDT"

    connection.execute(
        """INSERT INTO strategy_entry.strategy_cards(
               strategy_id,strategy_version,strategy_config_fingerprint,
               name,description,card_json,approved_at,approved_source
           ) VALUES(%s,%s,%s,'p8','p8',%s::jsonb,%s,'test')""",
        (
            strategy_id,
            version,
            strategy_fp,
            json.dumps(
                {
                    "strategy_id": strategy_id,
                    "strategy_version": version,
                    "strategy_config_fingerprint": strategy_fp,
                }
            ),
            NOW,
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.strategy_activations(
               activation_id,strategy_id,strategy_version,
               strategy_config_fingerprint,enabled,enabled_at,disabled_at,
               scope,operator,source,change_reason
           ) VALUES(%s,%s,%s,%s,true,%s,NULL,'{}'::jsonb,'test','test','')""",
        (activation_id, strategy_id, version, strategy_fp, NOW),
    )
    connection.execute(
        """INSERT INTO strategy_entry.entry_plans(
               entry_plan_fingerprint,strategy_id,strategy_version,
               strategy_config_fingerprint,entry_plan_version,plan_json
           ) VALUES(%s,%s,%s,%s,'1',%s::jsonb)""",
        (
            entry_fp,
            strategy_id,
            version,
            strategy_fp,
            json.dumps(
                {
                    "strategy_id": strategy_id,
                    "strategy_version": version,
                    "strategy_config_fingerprint": strategy_fp,
                    "entry_plan_fingerprint": entry_fp,
                    "capital_policy": {
                        "requested_amount": "20",
                        "amount_currency": "USDT",
                    },
                }
            ),
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.exit_plans(
               exit_plan_fingerprint,strategy_id,strategy_version,
               strategy_config_fingerprint,exit_plan_version,plan_json
           ) VALUES(%s,%s,%s,%s,'1',%s::jsonb)""",
        (
            exit_fp,
            strategy_id,
            version,
            strategy_fp,
            json.dumps(
                {
                    "strategy_id": strategy_id,
                    "strategy_version": version,
                    "strategy_config_fingerprint": strategy_fp,
                    "exit_plan_fingerprint": exit_fp,
                    "exit_policy": {},
                    "protection_policy": {},
                }
            ),
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.strategy_signals(
               signal_id,strategy_id,strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,strategy_activation_id,symbol,direction,
               detected_at,fact_id,source_refs,payload
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,'LONG',%s,%s,%s::jsonb,'{}'::jsonb)""",
        (
            signal_id,
            strategy_id,
            version,
            strategy_fp,
            entry_fp,
            activation_id,
            symbol,
            NOW,
            f"{prefix}-fact",
            json.dumps([f"p8:{prefix}"]),
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.strategy_attempts(
               strategy_attempt_id,signal_id,strategy_id,strategy_version,
               strategy_config_fingerprint,entry_plan_fingerprint,
               strategy_activation_id,created_at,payload
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb)""",
        (
            attempt_id,
            signal_id,
            strategy_id,
            version,
            strategy_fp,
            entry_fp,
            activation_id,
            NOW,
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.entry_decisions(
               entry_decision_id,strategy_attempt_id,signal_id,decision_code,
               reason,decided_at,capacity_snapshot_id,payload
           ) VALUES(
               %s,%s,%s,'INSUFFICIENT_AVAILABLE_FUNDS',
               'p8 disposable insufficient capital',%s,%s,'{}'::jsonb
           )""",
        (
            decision_id,
            attempt_id,
            signal_id,
            NOW,
            f"{prefix}-capacity",
        ),
    )
    connection.commit()

    return CounterfactualCandidate(
        counterfactual_id=f"{prefix}-counterfactual",
        strategy_activation_id=activation_id,
        strategy_id=strategy_id,
        strategy_version=version,
        strategy_config_fingerprint=strategy_fp,
        entry_plan_fingerprint=entry_fp,
        exit_plan_fingerprint=exit_fp,
        signal_id=signal_id,
        strategy_attempt_id=attempt_id,
        entry_decision_id=decision_id,
        symbol=symbol,
        direction=TradeDirection.LONG,
        decided_at=NOW,
        captured_at=NOW,
        requested_amount=Decimal("20"),
        amount_currency="USDT",
        capacity_snapshot_id=f"{prefix}-capacity",
        reported_available_amount=Decimal("5"),
        decision_code=EntryDecisionCode.INSUFFICIENT_AVAILABLE_FUNDS,
        decision_reason="p8 disposable insufficient capital",
        evidence=FrozenPolicy.from_mapping({"source": "p8-disposable"}),
    )


def test_counterfactual_candidate_has_no_real_execution_side_effects() -> None:
    assert DSN is not None
    prefix = "p8-candidate-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        candidate = _seed_candidate_lineage(connection, prefix)
        store = AnalystCounterfactualStore(connection)
        store.record_candidate(candidate)
        store.record_candidate(candidate)
        connection.commit()

        row = connection.execute(
            """SELECT strategy_activation_id,strategy_id,strategy_version,
                      strategy_config_fingerprint,entry_plan_fingerprint,
                      exit_plan_fingerprint,signal_id,strategy_attempt_id,
                      entry_decision_id,decision_code,requested_amount,
                      reported_available_amount
                 FROM analytics.counterfactual_candidates
                WHERE counterfactual_id=%s""",
            (candidate.counterfactual_id,),
        ).fetchone()
        assert row is not None
        assert row["strategy_activation_id"] == candidate.strategy_activation_id
        assert row["strategy_id"] == candidate.strategy_id
        assert row["strategy_version"] == candidate.strategy_version
        assert row["strategy_config_fingerprint"] == candidate.strategy_config_fingerprint
        assert row["entry_plan_fingerprint"] == candidate.entry_plan_fingerprint
        assert row["exit_plan_fingerprint"] == candidate.exit_plan_fingerprint
        assert row["signal_id"] == candidate.signal_id
        assert row["strategy_attempt_id"] == candidate.strategy_attempt_id
        assert row["entry_decision_id"] == candidate.entry_decision_id
        assert row["decision_code"] == "INSUFFICIENT_AVAILABLE_FUNDS"
        assert row["requested_amount"] == Decimal("20")
        assert row["reported_available_amount"] == Decimal("5")

        reservation = connection.execute(
            """SELECT count(*) AS count FROM runtime.capital_reservations
                WHERE strategy_attempt_id=%s""",
            (candidate.strategy_attempt_id,),
        ).fetchone()
        request = connection.execute(
            """SELECT count(*) AS count FROM strategy_entry.execution_requests
                WHERE strategy_attempt_id=%s""",
            (candidate.strategy_attempt_id,),
        ).fetchone()
        position = connection.execute(
            """SELECT count(*) AS count FROM runtime.position_ownership
                WHERE signal_id=%s""",
            (candidate.signal_id,),
        ).fetchone()
        command = connection.execute(
            """SELECT count(*) AS count FROM runtime.trade_commands
                WHERE payload_json LIKE %s""",
            (f"%{candidate.signal_id}%",),
        ).fetchone()
        assert reservation == {"count": 0}
        assert request == {"count": 0}
        assert position == {"count": 0}
        assert command == {"count": 0}


def test_counterfactual_outcome_is_isolated_from_actual_economics() -> None:
    assert DSN is not None
    prefix = "p8-outcome-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        candidate = _seed_candidate_lineage(connection, prefix)
        store = AnalystCounterfactualStore(connection)
        store.record_candidate(candidate)
        actual_before = connection.execute(
            "SELECT count(*) AS count FROM runtime.position_exit_attribution"
        ).fetchone()
        outcome = CounterfactualOutcome.build(
            counterfactual_id=candidate.counterfactual_id,
            status=CounterfactualOutcomeStatus.CLOSED,
            evaluated_at=NOW + timedelta(hours=1),
            opened_at=NOW + timedelta(minutes=1),
            entry_price=Decimal("10"),
            closed_at=NOW + timedelta(minutes=40),
            exit_price=Decimal("10.2"),
            exit_reason="EXACT_REPLAY_EXIT_PLAN",
            gross_pnl=Decimal("0.4"),
            fees=Decimal("0.02"),
            funding=None,
            slippage=Decimal("0.01"),
            net_pnl_after_fees=Decimal("0.38"),
            economics_status=CounterfactualEconomicsStatus.PARTIAL_NO_FUNDING,
            evidence=FrozenPolicy.from_mapping({"source": "p8-replay"}),
        )
        store.record_outcome(outcome)
        store.record_outcome(outcome)
        connection.commit()

        stored = connection.execute(
            """SELECT status,gross_pnl,fees,funding,slippage,
                      net_pnl_after_fees,economics_status
                 FROM analytics.counterfactual_outcomes
                WHERE outcome_id=%s""",
            (outcome.outcome_id,),
        ).fetchone()
        actual_after = connection.execute(
            "SELECT count(*) AS count FROM runtime.position_exit_attribution"
        ).fetchone()
        assert stored == {
            "status": "CLOSED",
            "gross_pnl": Decimal("0.4"),
            "fees": Decimal("0.02"),
            "funding": None,
            "slippage": Decimal("0.01"),
            "net_pnl_after_fees": Decimal("0.38"),
            "economics_status": "PARTIAL_NO_FUNDING",
        }
        assert actual_after == actual_before


def test_counterfactual_tables_are_append_only_for_runtime_role() -> None:
    assert DSN is not None
    prefix = "p8-immutable-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        candidate = _seed_candidate_lineage(connection, prefix)
        AnalystCounterfactualStore(connection).record_candidate(candidate)
        connection.commit()

        with pytest.raises(psycopg.Error):
            connection.execute(
                """UPDATE analytics.counterfactual_candidates
                      SET decision_reason='tampered'
                    WHERE counterfactual_id=%s""",
                (candidate.counterfactual_id,),
            )
        connection.rollback()

        with pytest.raises(psycopg.Error):
            connection.execute(
                """DELETE FROM analytics.counterfactual_candidates
                    WHERE counterfactual_id=%s""",
                (candidate.counterfactual_id,),
            )
        connection.rollback()

        row = connection.execute(
            """SELECT decision_reason FROM analytics.counterfactual_candidates
                WHERE counterfactual_id=%s""",
            (candidate.counterfactual_id,),
        ).fetchone()
        assert row == {"decision_reason": candidate.decision_reason}


def test_counterfactual_schema_has_no_execution_or_reservation_identity_columns() -> None:
    assert DSN is not None
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT column_name
                 FROM information_schema.columns
                WHERE table_schema='analytics'
                  AND table_name='counterfactual_candidates'"""
        ).fetchall()
        columns = {str(row["column_name"]) for row in rows}
        for forbidden in (
            "execution_request_id",
            "capital_reservation_id",
            "command_id",
            "exchange_order_id",
            "strategy_position_id",
        ):
            assert forbidden not in columns

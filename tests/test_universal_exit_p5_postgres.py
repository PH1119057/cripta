from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.universal_entry.contracts import FrozenPolicy
from bybit_workbench.universal_exit.contracts import (
    ExitEvaluationStatus,
    ExitObservation,
)
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.loader import (
    load_exit_binding,
    prior_once_rule_ids,
)
from bybit_workbench.universal_exit.storage import PostgresExitShadowStore

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)
NOW = datetime(2026, 9, 18, 19, 0, tzinfo=UTC)


def _rule(*, repeat_policy: str = "EACH_MATCH") -> dict[str, object]:
    return {
        "rule_id": "close-on-profit",
        "priority": 10,
        "repeat_policy": repeat_policy,
        "required_fact_paths": ["fact.pnl_pct"],
        "predicate": {
            "op": "COMPARE",
            "params": {
                "path": "fact.pnl_pct",
                "comparator": "GTE",
                "value": "1.0",
            },
        },
        "action": {
            "kind": "CLOSE",
            "mutation": {"quantity": "ALL", "reason": "explicit-test-rule"},
        },
    }


def _seed(
    connection: psycopg.Connection,
    prefix: str,
    *,
    rules: list[dict[str, object]] | None,
) -> dict[str, str]:
    strategy_id = f"{prefix}-strategy"
    version = "1"
    strategy_fp = f"{prefix}-strategy-fp"
    activation_id = f"{prefix}-activation"
    entry_fp = f"{prefix}-entry-fp"
    exit_fp = f"{prefix}-exit-fp"
    signal_id = f"{prefix}-signal"
    attempt_id = f"{prefix}-attempt"
    entry_decision_id = f"{prefix}-entry-decision"
    request_id = f"{prefix}-entry-request"
    position_id = f"{prefix}-position"
    command_id = f"{prefix}-entry-command"
    symbol = "P5" + prefix.replace("-", "").upper()[-8:] + "USDT"

    exit_policy: dict[str, object] = {"exit_plan_version": "1"}
    if rules is not None:
        exit_policy["rules"] = rules
        exit_policy["conflict_policy"] = {
            "mode": "PRIORITY",
            "priority_order": "HIGHER_WINS",
            "tie_break": "FAIL_CLOSED",
        }
    exit_plan_json = {
        "strategy_id": strategy_id,
        "strategy_version": version,
        "strategy_config_fingerprint": strategy_fp,
        "exit_plan_version": "1",
        "exit_plan_fingerprint": exit_fp,
        "exit_policy": exit_policy,
        "protection_policy": {},
    }

    connection.execute(
        """INSERT INTO strategy_entry.strategy_cards(
               strategy_id,strategy_version,strategy_config_fingerprint,
               name,description,card_json,approved_at,approved_source
           ) VALUES(%s,%s,%s,'p5','p5',%s::jsonb,%s,'test')""",
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
            json.dumps({"entry_plan_fingerprint": entry_fp}),
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
            json.dumps(exit_plan_json),
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.strategy_signals(
               signal_id,strategy_id,strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,strategy_activation_id,symbol,direction,
               detected_at,fact_id,source_refs,payload
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,'LONG',%s,%s,'[]'::jsonb,'{}'::jsonb)""",
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
               reason,decided_at,payload
           ) VALUES(%s,%s,%s,'ACCEPTED','test',%s,'{}'::jsonb)""",
        (entry_decision_id, attempt_id, signal_id, NOW),
    )
    connection.execute(
        """INSERT INTO strategy_entry.execution_requests(
               execution_request_id,strategy_attempt_id,entry_decision_id,signal_id,
               strategy_id,strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,symbol,direction,requested_at,payload,
               exit_plan_fingerprint
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'LONG',%s,'{}'::jsonb,%s)""",
        (
            request_id,
            attempt_id,
            entry_decision_id,
            signal_id,
            strategy_id,
            version,
            strategy_fp,
            entry_fp,
            symbol,
            NOW,
            exit_fp,
        ),
    )
    connection.execute(
        """INSERT INTO runtime.position_ownership(
               position_id,trade_id,bot_instance_id,strategy_id,strategy_version,
               signal_id,entry_command_id,symbol,side,actual_avg_fill,actual_qty,
               fill_at,exchange_order_ids,client_order_ids,execution_ids,state,
               exchange_position_key,position_idx,account_ref,
               strategy_config_fingerprint,strategy_activation_id,
               strategy_attempt_id,entry_decision_id,entry_execution_request_id,
               entry_plan_fingerprint,exit_plan_fingerprint
           ) VALUES(%s,%s,'universal-entry',%s,%s,%s,%s,%s,'Buy',10,2,%s,
                    '[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'OPEN',%s,0,
                    'BYBIT:UNIFIED',%s,%s,%s,%s,%s,%s,%s)""",
        (
            position_id,
            f"{prefix}-trade",
            strategy_id,
            version,
            signal_id,
            command_id,
            symbol,
            NOW,
            f"BYBIT:UNIFIED:LINEAR:USDT:{symbol}:0",
            strategy_fp,
            activation_id,
            attempt_id,
            entry_decision_id,
            request_id,
            entry_fp,
            exit_fp,
        ),
    )
    connection.commit()
    return {
        "position_id": position_id,
        "exit_fp": exit_fp,
        "symbol": symbol,
    }


def _observation(ids: dict[str, str], suffix: str = "1") -> ExitObservation:
    return ExitObservation(
        observation_id=f"{ids['position_id']}-obs-{suffix}",
        strategy_position_id=ids["position_id"],
        symbol=ids["symbol"],
        event_at=NOW,
        observed_at=NOW,
        received_at=NOW,
        event_kind="PRICE",
        attributes=FrozenPolicy.from_mapping({"pnl_pct": "1.2"}),
        source_refs=(f"ticker:{suffix}",),
    )


def test_exact_loader_evaluates_and_persists_shadow_without_execution_path() -> None:
    assert DSN is not None
    prefix = "p5-" + uuid4().hex[:10]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix, rules=[_rule()])
        position, plan = load_exit_binding(
            connection,
            strategy_position_id=ids["position_id"],
        )
        result = UniversalExitEngine().evaluate(
            position,
            plan,
            _observation(ids),
        )
        assert result.status is ExitEvaluationStatus.DECISION_CREATED
        assert result.decision is not None

        PostgresExitShadowStore(connection).record(
            result,
            position=position,
            plan=plan,
        )
        connection.commit()

        observation_count = connection.execute(
            """SELECT count(*) FROM strategy_exit.exit_observations
                WHERE strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        decision_count = connection.execute(
            """SELECT count(*) FROM strategy_exit.exit_decisions
                WHERE strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        evaluation_count = connection.execute(
            """SELECT count(*) FROM strategy_exit.shadow_evaluations
                WHERE strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        request_count = connection.execute(
            """SELECT count(*) FROM strategy_exit.execution_requests
                WHERE strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        trade_command_count = connection.execute(
            "SELECT count(*) FROM runtime.trade_commands WHERE command_type='exit'"
        ).fetchone()
        assert observation_count == {"count": 1}
        assert decision_count == {"count": 1}
        assert evaluation_count == {"count": 1}
        assert request_count == {"count": 0}
        assert trade_command_count == {"count": 0}


def test_shadow_persistence_is_idempotent_for_same_observation() -> None:
    assert DSN is not None
    prefix = "p5-idem-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix, rules=[_rule()])
        position, plan = load_exit_binding(
            connection,
            strategy_position_id=ids["position_id"],
        )
        event = _observation(ids)
        result = UniversalExitEngine().evaluate(position, plan, event)
        store = PostgresExitShadowStore(connection)
        store.record(result, position=position, plan=plan)
        store.record(result, position=position, plan=plan)
        connection.commit()

        row = connection.execute(
            """SELECT
                 (SELECT count(*) FROM strategy_exit.exit_observations
                   WHERE strategy_position_id=%s) AS observations,
                 (SELECT count(*) FROM strategy_exit.exit_decisions
                   WHERE strategy_position_id=%s) AS decisions,
                 (SELECT count(*) FROM strategy_exit.shadow_evaluations
                   WHERE strategy_position_id=%s) AS evaluations""",
            (ids["position_id"], ids["position_id"], ids["position_id"]),
        ).fetchone()
        assert row == {"observations": 1, "decisions": 1, "evaluations": 1}


def test_once_per_position_reads_durable_prior_decision() -> None:
    assert DSN is not None
    prefix = "p5-once-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(
            connection,
            prefix,
            rules=[_rule(repeat_policy="ONCE_PER_POSITION")],
        )
        position, plan = load_exit_binding(
            connection,
            strategy_position_id=ids["position_id"],
        )
        first = UniversalExitEngine().evaluate(position, plan, _observation(ids, "1"))
        assert first.status is ExitEvaluationStatus.DECISION_CREATED
        PostgresExitShadowStore(connection).record(first, position=position, plan=plan)
        connection.commit()

        prior = prior_once_rule_ids(
            connection,
            strategy_position_id=ids["position_id"],
            exit_plan_fingerprint=ids["exit_fp"],
        )
        assert prior == frozenset({"close-on-profit"})
        second = UniversalExitEngine().evaluate(
            position,
            plan,
            _observation(ids, "2"),
            prior_once_rule_ids=prior,
        )
        assert second.status is ExitEvaluationStatus.NO_MATCH
        assert second.decision is None


def test_compatibility_plan_without_rules_persists_no_decision() -> None:
    assert DSN is not None
    prefix = "p5-compat-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix, rules=None)
        position, plan = load_exit_binding(
            connection,
            strategy_position_id=ids["position_id"],
        )
        result = UniversalExitEngine().evaluate(position, plan, _observation(ids))
        assert result.status is ExitEvaluationStatus.NO_EXECUTABLE_EXIT_RULES
        PostgresExitShadowStore(connection).record(
            result,
            position=position,
            plan=plan,
        )
        connection.commit()

        decision_count = connection.execute(
            """SELECT count(*) FROM strategy_exit.exit_decisions
                WHERE strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        eval_row = connection.execute(
            """SELECT status,exit_decision_id FROM strategy_exit.shadow_evaluations
                WHERE strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        assert decision_count == {"count": 0}
        assert eval_row == {
            "status": "NO_EXECUTABLE_EXIT_RULES",
            "exit_decision_id": None,
        }

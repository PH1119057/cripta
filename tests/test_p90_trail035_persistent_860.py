"""#860: persistent native Bybit TRAIL035 across 30-60 minute restarts.

No real PostgreSQL, Bybit, credential, service or ARM action. The existing
2026-11-19 OPEN INJUSDT lacks an opt-in marker and is explicitly excluded.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, timedelta
from decimal import Decimal

import pytest

from test_p90l90_real_observation import AT, _scope

from bybit_workbench.market_data.r10_execution_publication import (
    R10ExecutionQuote,
)
from bybit_workbench.universal_exit.contracts import (
    ExitActionKind,
    ExitEvaluationStatus,
)
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.p90l90_real_trail_reconcile import (
    P90TrailRecoveryUnverified,
    build_p90_trail_recovery_observation,
)
from bybit_workbench.universal_exit.p90l90_real_v3 import (
    P90_TRAIL_RECOVERY_POLICY,
)


def proof(*, short=False, sub=False, hours=1, ltp=None, trailing="0"):
    position, plan, _, _, private, _ = _scope(short=short, sub=sub)
    now = AT + timedelta(hours=hours)
    seen = now.replace(microsecond=0)
    if ltp is None:
        ltp = "98" if short else "102"
    private = replace(
        private,
        observed_at=now - timedelta(milliseconds=100),
        source_ref="bybit:signed-position-list:exact-account",
        position={
            **private.position,
            "markPrice": str(ltp),
            "trailingStop": str(trailing),
        },
    )
    quote = R10ExecutionQuote(
        symbol=position.symbol,
        last_price=Decimal(str(ltp)),
        bid_price=Decimal(str(ltp)) - Decimal("0.01"),
        ask_price=Decimal(str(ltp)) + Decimal("0.01"),
        publication_id=1234,
        source_session_id="a" * 32,
        producer_release_commit="b" * 40,
        as_of_second=seen,
        field_times=(seen, seen, seen),
    )
    return position, plan, private, quote, now


@pytest.mark.parametrize(("short", "sub", "hours", "ltp", "expected"), (
    (False, False, 0.5, "102", {"distance": "0.35", "tpsl_mode": "Full"}),
    (False, False, 1, "100.2", {
        "distance": "0.35", "active_price": "100.50", "tpsl_mode": "Full",
    }),
    (True, True, 0.5, "98", {"distance": "0.35", "tpsl_mode": "Full"}),
    (True, True, 1, "99.8", {
        "distance": "0.35", "active_price": "99.50", "tpsl_mode": "Full",
    }),
))
def test_old_window_is_not_a_terminal_gate_for_new_opted_in_position(
    short, sub, hours, ltp, expected,
):
    position, plan, private, quote, now = proof(
        short=short, sub=sub, hours=hours, ltp=ltp,
    )
    obs = build_p90_trail_recovery_observation(
        position=position, private=private, quote=quote,
        eligibility=P90_TRAIL_RECOVERY_POLICY,
        expected_source_session_id=quote.source_session_id,
        received_at=now,
    )
    assert obs.event_kind == "P90L90_REAL_TRAIL_RECOVERY"
    assert "durable_best_favorable_price" not in obs.attributes.to_dict()["p90l90_real"]
    result = UniversalExitEngine().evaluate(position, plan, obs)
    assert result.status is ExitEvaluationStatus.DECISION_CREATED
    assert result.decision is not None
    assert result.decision.action_kind is ExitActionKind.SET_TRAILING
    assert result.decision.requested_mutation.to_dict() == expected
    assert result.decision.rule_id == "P90L90_REAL_BYBIT_NATIVE_TRAIL035_RECONCILE"


def test_recovered_or_already_armed_venue_trail_does_not_reissue() -> None:
    position, plan, private, quote, now = proof(trailing="0.35")
    observation = build_p90_trail_recovery_observation(
        position=position, private=private, quote=quote,
        eligibility=P90_TRAIL_RECOVERY_POLICY,
        expected_source_session_id=quote.source_session_id,
        received_at=now,
    )
    evaluation = UniversalExitEngine().evaluate(position, plan, observation)
    assert evaluation.status is ExitEvaluationStatus.NO_MATCH
    assert evaluation.decision is None


@pytest.mark.parametrize("defect", (
    "pre-release-unmarked-current-INJ",
    "unknown-account",
    "stale-original-ltp",
    "stale-private",
    "pending-mutation",
    "signed-qty-drift",
    "source-session-drift",
    "protection-missing",
    "marketable-protection",
))
def test_recovery_fails_closed_without_exact_present_time_evidence(defect) -> None:
    position, _plan, private, quote, now = proof()
    marker = P90_TRAIL_RECOVERY_POLICY
    source_id = quote.source_session_id
    if defect == "pre-release-unmarked-current-INJ":
        marker = None
    elif defect == "unknown-account":
        private = replace(private, trading_account_id="ta-foreign")
    elif defect == "stale-original-ltp":
        quote = replace(quote, field_times=(
            now - timedelta(seconds=10),
            quote.field_times[1], quote.field_times[2],
        ))
    elif defect == "stale-private":
        private = replace(private, observed_at=now - timedelta(seconds=10))
    elif defect == "pending-mutation":
        private = replace(private, pending_exit_mutation=True)
    elif defect == "signed-qty-drift":
        private = replace(private, position={
            **private.position, "size": "0.2",
        })
    elif defect == "source-session-drift":
        source_id = "f" * 32
    elif defect == "protection-missing":
        private = replace(private, position={
            **private.position, "stopLoss": "0",
        })
    else:
        private = replace(private, position={
            **private.position, "takeProfit": "101",
            "markPrice": "102",
        })
    with pytest.raises(P90TrailRecoveryUnverified):
        build_p90_trail_recovery_observation(
            position=position, private=private, quote=quote,
            eligibility=marker, expected_source_session_id=source_id,
            received_at=now,
        )


def test_old_firstfill_terminal_verdict_remains_durable_not_a_recovery_decision():
    from pathlib import Path
    source = (
        Path(__file__).resolve().parents[1]
        / "operations/connectivity/p90_real_independent_exit.py"
    ).read_text()
    assert "_FIRST_FILL_WINDOW = timedelta(seconds=15)" in source
    assert "P90_REAL_EXIT_FIRST_FILL_ORIGINAL_SOURCE_WINDOW_EXPIRED" in source
    # #860 uses a distinct current-source event, not forged POSTFILL.
    from bybit_workbench.universal_exit.p90l90_real_v3 import (
        project_p90l90_real_exit,
    )
    assert callable(project_p90l90_real_exit)


def test_runtime_rollout_writes_marker_only_at_new_confirmed_protected_fill():
    from pathlib import Path
    base = Path(__file__).resolve().parents[1]
    original = (
        base / "operations/connectivity/private_runtime.py"
    ).read_text()
    assert '"trailing_recovery_policy": P90_TRAIL_RECOVERY_POLICY' in original
    query = (
        base / "operations/connectivity/p90_real_persistent_trail.py"
    ).read_text()
    assert "initial_protection_evidence->>'trailing_recovery_policy'=%s" in query
    assert "AND p.state='OPEN'" in query
    assert "p90_real_exit_no_pending(" in query

"""#865 deterministic candidate-only P90 source/Exit/Exchange safety preflight.

No production DB, Bybit, credentials, network, systemd, ARM, or real orders.
C1 SIMULATED verification only; this suite NEVER certifies signed Exchange E2E.
"""
from __future__ import annotations

import ast
import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.market_data.r10_execution_publication import R10ExecutionQuote
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import StrategyActivation, TradeDirection
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import real_v3_cards
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_exit.contracts import ExitActionKind, ExitEvaluationStatus
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.p90l90_exchange_safety import (
    verify_p90_real_exchange_mutation,
)
from bybit_workbench.universal_exit.p90l90_real_observation import (
    ScopedPrivatePositionEvidence,
)
from bybit_workbench.universal_exit.p90l90_real_trail_reconcile import (
    P90TrailRecoveryUnverified,
    build_p90_trail_recovery_observation,
)
from bybit_workbench.universal_exit.p90l90_real_v3 import P90_TRAIL_RECOVERY_POLICY
from operations.connectivity.p90_real_independent_exit import (
    P90IndependentExitBlocked,
    _validate_first_fill_age,
)
from operations.connectivity.p90_real_persistent_trail import (
    _fresh_matching_private_protection,
)

ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 19, 11, 0, tzinfo=UTC)
D = Decimal


def _case(*, short: bool = False, already_armed: bool = False):
    policy = StrategyHardStopEmbargoPolicy(
        enabled=True, hardstop_count=2, rolling_hours=48,
        new_entry_pause_hours=48, close_existing_positions=False,
        domain="STRATEGY_VERSION",
    )
    cards = real_v3_cards(
        approved_at=AT, policy_by_strategy_id={MAIN_ID: policy, SUB_ID: policy},
    )
    card = cards[1] if short else cards[0]
    activation = StrategyActivation(
        activation_id="causal-865-test", strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True, enabled_at=AT,
    )
    _, exit_plan = materialize_plans(card, activation)
    account_id = "ta-sub" if short else "ta-main"
    symbol = "DOTUSDT" if short else "LTCUSDT"
    direction = TradeDirection.SHORT if short else TradeDirection.LONG
    position = StrategyPosition(
        strategy_position_id="position-865-simulated",
        account_ref="BYBIT:UNIFIED",
        exchange_position_key=exchange_position_key(account_id, symbol, 0),
        position_idx=0,
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        strategy_activation_id=activation.activation_id,
        signal_id="signal-865", strategy_attempt_id="attempt-865",
        entry_decision_id="decision-865",
        entry_execution_request_id="request-865",
        entry_plan_fingerprint="entry-fingerprint-865",
        exit_plan_fingerprint=exit_plan.exit_plan_fingerprint,
        entry_command_id="entry-command-865", symbol=symbol,
        direction=direction, actual_avg_fill=D("100"), actual_qty=D("0.1"),
        fill_at=AT, initial_protection_confirmed_at=AT+timedelta(milliseconds=1),
        strategy_execution_binding_id="binding-865",
        execution_leg="PRIMARY", trading_account_id=account_id,
        identity_status="PROVEN",
    )
    now = AT+timedelta(hours=1)
    ltp = D("98") if short else D("102")
    signed = {
        "symbol": symbol, "side": "Sell" if short else "Buy",
        "positionIdx": 0, "avgPrice": "100", "size": "0.1",
        "stopLoss": "110" if short else "90",
        "takeProfit": "90" if short else "110",
        "markPrice": str(ltp),
        "trailingStop": "0.35" if already_armed else "0",
    }
    private = ScopedPrivatePositionEvidence(
        trading_account_id=account_id,
        strategy_execution_binding_id="binding-865",
        connector_id="connector-sub" if short else "connector-main",
        signed_provider_uid="12345678",
        generation_id="signed-generation-865",
        exchange_position_key=position.exchange_position_key,
        symbol=symbol, position_idx=0,
        observed_at=now-timedelta(milliseconds=100),
        source_ref="bybit:signed-position-list:exact-account",
        position=signed, tick_size=D("0.01"),
        pending_exit_mutation=False,
    )
    quote = R10ExecutionQuote(
        symbol=symbol, last_price=ltp,
        bid_price=ltp-D("0.01"), ask_price=ltp+D("0.01"),
        publication_id=865, source_session_id="a"*32,
        producer_release_commit="b"*40,
        as_of_second=now, field_times=(now, now, now),
    )
    return position, exit_plan, private, quote, now


def _observe(position, private, quote, now, *, eligibility=P90_TRAIL_RECOVERY_POLICY):
    return build_p90_trail_recovery_observation(
        position=position, private=private, quote=quote,
        eligibility=eligibility,
        expected_source_session_id=quote.source_session_id,
        received_at=now,
    )


class CandidateGraphTests(unittest.TestCase):
    """C0: candidate code, not documentation or installed/current code."""

    def test_all_cross_owner_rails_exist_and_are_parseable(self):
        paths = (
            "operations/monitoring/native_public_trade_source.py",
            "operations/connectivity/universal_entry_consumer.py",
            "operations/connectivity/private_runtime.py",
            "operations/connectivity/p90_real_independent_exit.py",
            "operations/connectivity/p90_real_persistent_trail.py",
            "operations/connectivity/p90_r06_exit_owner.py",
            "operations/connectivity/universal_exit_consumer.py",
            "src/bybit_workbench/universal_exit/p90l90_real_exit_execution.py",
            "src/bybit_workbench/universal_exit/p90l90_real_trail_reconcile.py",
            "src/bybit_workbench/market_data/r10_execution_publication.py",
            "src/bybit_workbench/p90l90_real_prearm_evidence.py",
            "src/bybit_workbench/release_arm_safety.py",
        )
        for relative in paths:
            with self.subTest(owner=relative):
                file = ROOT / relative
                self.assertTrue(file.is_file())
                ast.parse(file.read_text(encoding="utf-8"), filename=str(file))

    def test_exit_consumer_orders_three_owners_and_does_not_post(self):
        source = (ROOT/"operations/connectivity/universal_exit_consumer.py").read_text()
        self.assertLess(
            source.index("run_p90_exit_once("),
            source.index("run_p90_persistent_trail_once("),
        )
        self.assertLess(
            source.index("run_p90_persistent_trail_once("),
            source.index("run_p90_r06_exit_once("),
        )
        self.assertIn("P90_R06_FIRSTFILL_OWNER_BLOCKED", source)
        self.assertNotIn("/v5/position/trading-stop", source)

    def test_gateway_and_guard_consume_exit_proof(self):
        prearm = (ROOT/"src/bybit_workbench/p90l90_real_prearm_evidence.py").read_text()
        guard = (ROOT/"src/bybit_workbench/release_arm_safety.py").read_text()
        self.assertIn("P90_R06_EXIT_OWNER_READY_FLAT", prearm)
        self.assertIn("exit_owner_ready", guard)
        self.assertIn("REAL_ENTRY_RUNTIME_NOT_READY", guard)


    def test_installer_packages_and_verifies_candidate_tests_before_cutover(self):
        installer = (
            ROOT / "operations/infrastructure/install_verified_release.sh"
        ).read_text(encoding="utf-8")
        for test_path in (
            "tests/test_p90_causal_release_preflight_865.py",
            "tests/test_p90_causal_pg16_865.py",
        ):
            self.assertIn(test_path, installer)
        self.assertIn("runtime_test_paths=()", installer)
        self.assertIn("runtime_verify_test_paths=()", installer)
        self.assertIn("runtime_test_paths+=(", installer)
        self.assertIn('runtime_verify_test_paths+=(--path "$test_path")', installer)
        self.assertIn("runtime_test_paths[@]", installer)
        self.assertIn("runtime_verify_test_paths[@]", installer)
        self.assertLess(
            installer.index('git -C "$SOURCE" archive --format=tar'),
            installer.index("RELEASE_RUNTIME_GIT_BLOB_VERIFY=PASS"),
        )
        gate = installer.index("P90_CANDIDATE_CAUSAL_PREFLIGHT_FAILED")
        self.assertLess(gate, installer.index("services_stopped=1"))
        self.assertLess(gate, installer.index("pg_dump -Fc -d cripta"))
        self.assertLess(
            gate,
            installer.index('mv -Tf "$next_runtime" "$RUNTIME_ROOT/current"'),
        )
        self.assertIn(
            '"$runtime_release/operations/infrastructure/'
            'p90_causal_release_preflight.py"',
            installer,
        )
        self.assertIn(
            'PYTHONPATH="$runtime_release:$runtime_release/src"', installer,
        )
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", installer)



    def test_offline_candidate_rehearsal_preserves_live_ownership(self):
        builder_path = (
            ROOT / "operations/infrastructure/p90_causal_candidate_archive_865.py"
        )
        builder = builder_path.read_text(encoding="utf-8")
        verifier = (
            ROOT / "operations/infrastructure/verify_release_candidate_git.py"
        )
        self.assertTrue(verifier.is_file())
        self.assertIn(
            '"/data/cripta/research/testenvs/p90_causal_candidate_865"',
            builder,
        )
        self.assertIn('"/data/cripta/research/worktrees/p90_causal_c2_865_20261119"', builder)
        self.assertIn("RELEASE_CANDIDATE_GIT_VERIFY=PASS", builder)
        self.assertLess(
            builder.index("_verify_archive(candidate, sha)"),
            builder.index("_run_candidate(candidate, sha)"),
        )
        self.assertIn('"--require-hashes"', builder)
        self.assertIn('"--staged-candidate"', builder)
        self.assertIn("CANDIDATE_ALREADY_EXISTS_RECONCILE", builder)
        self.assertNotIn("systemctl", builder)
        self.assertNotIn("pg_dump", builder)
        self.assertNotIn("trading-stop", builder)
        installer = (
            ROOT / "operations/infrastructure/install_verified_release.sh"
        ).read_text(encoding="utf-8")
        self.assertNotIn("--staged-candidate", installer)



class CausalSimulatedTests(unittest.TestCase):
    """C1: real plan/observation/engine/safety functions, fake venue only."""

    def test_firstfill_window_can_expire_but_opted_in_recovery_reaches_decision(self):
        for short in (False, True):
            with self.subTest(short=short):
                position, plan, private, quote, now = _case(short=short)
                with self.assertRaises(P90IndependentExitBlocked):
                    _validate_first_fill_age(
                        fill_at=position.fill_at,
                        protected_at=position.initial_protection_confirmed_at,
                        now=now,
                    )
                observation = _observe(position, private, quote, now)
                self.assertEqual(observation.event_kind, "P90L90_REAL_TRAIL_RECOVERY")
                evaluated = UniversalExitEngine().evaluate(position, plan, observation)
                self.assertIs(evaluated.status, ExitEvaluationStatus.DECISION_CREATED)
                self.assertIs(evaluated.decision.action_kind, ExitActionKind.SET_TRAILING)
                mutation = evaluated.decision.requested_mutation.to_dict()
                self.assertEqual(mutation["distance"], "0.35")
                self.assertEqual(mutation["tpsl_mode"], "Full")
                # Test the actual private exchange pre-send safety contract,
                # never call a real API or assume an ACK is a signed readback.
                payload = {
                    "strategy_id": position.strategy_id,
                    "strategy_version": position.strategy_version,
                    "trading_account_id": position.trading_account_id,
                    "strategy_execution_binding_id":
                        position.strategy_execution_binding_id,
                    "identity_status": "PROVEN",
                    "direction": position.direction.value,
                    "exchange_position_key": position.exchange_position_key,
                    "trail_recovery_policy": P90_TRAIL_RECOVERY_POLICY,
                }
                verify_p90_real_exchange_mutation(
                    payload=payload, trading_account_id=position.trading_account_id,
                    symbol=position.symbol, position=dict(private.position),
                    action_kind=ExitActionKind.SET_TRAILING,
                    mutation=mutation, tick=D("0.01"),
                )
                # A modeled Exchange ACK does not prove actual trailing:
                # the independently signed post-readback is still zero.
                self.assertEqual(D(private.position["trailingStop"]), D("0"))

    def test_already_armed_native_trailing_does_not_duplicate(self):
        position, plan, private, quote, now = _case(already_armed=True)
        result = UniversalExitEngine().evaluate(
            position, plan, _observe(position, private, quote, now),
        )
        self.assertIs(result.status, ExitEvaluationStatus.NO_MATCH)
        self.assertIsNone(result.decision)

    def test_stale_or_wrong_private_source_and_unsafe_position_fail_closed(self):
        for defect in (
            "wrong_account", "wrong_connector_scope", "qty", "missing_stop",
            "stale_quote", "stale_private", "pending", "foreign_session",
            "unmarked_position",
        ):
            with self.subTest(defect=defect):
                position, _plan, private, quote, now = _case(short=True)
                marker = P90_TRAIL_RECOVERY_POLICY
                session = quote.source_session_id
                if defect == "wrong_account":
                    private = replace(private, trading_account_id="ta-foreign")
                elif defect == "wrong_connector_scope":
                    private = replace(
                        private, strategy_execution_binding_id="wrong-binding",
                    )
                elif defect == "qty":
                    private = replace(private, position={
                        **private.position, "size": "0.2",
                    })
                elif defect == "missing_stop":
                    private = replace(private, position={
                        **private.position, "stopLoss": "0",
                    })
                elif defect == "stale_quote":
                    quote = replace(quote, as_of_second=now-timedelta(seconds=6))
                elif defect == "stale_private":
                    private = replace(
                        private, observed_at=now-timedelta(seconds=8),
                    )
                elif defect == "pending":
                    private = replace(private, pending_exit_mutation=True)
                elif defect == "foreign_session":
                    session = "f"*32
                else:
                    marker = None
                with self.assertRaises(P90TrailRecoveryUnverified):
                    build_p90_trail_recovery_observation(
                        position=position, private=private, quote=quote,
                        eligibility=marker, expected_source_session_id=session,
                        received_at=now,
                    )

    def test_gateway_exact_ready_flat_is_not_confused_with_recovery_failures(self):
        from operations.connectivity import universal_exit_consumer as consumer

        def project(result: str, state: str, error: str | None = None):
            writer = MagicMock()
            with (
                patch.object(consumer, "STATUS_PATH", writer),
                patch.object(consumer, "P90_R06_OWNER", "ENABLED"),
                patch.object(consumer, "CONSUMER_ARM", "DISABLED"),
                patch.object(consumer, "LOADED_COMMIT", "a" * 40),
            ):
                consumer._p90_status(state=state, result=result, error=error)
            self.assertTrue(
                writer.with_suffix.return_value.write_text.called,
            )
            data = writer.with_suffix.return_value.write_text.call_args.args[0]
            return json.loads(data)

        flat = "P90_R06_EXIT_OWNER_READY_FLAT"
        benign = "P90_TRAIL_RECOVERY_NO_ELIGIBLE_POSITION"
        healthy = project("RECOVERY=" + benign + "|" + flat, flat)
        self.assertEqual(healthy["result"], flat)
        self.assertEqual(healthy["recovery_result"], benign)
        self.assertEqual(healthy["state"], flat)
        self.assertIsNone(healthy["error"])

        blocked = "RECOVERY=P90_TRAIL_RECOVERY_BLOCKED:CURRENT_PROOF_STALE|" + flat
        unsafe = project(blocked, flat)
        self.assertEqual(unsafe["result"], blocked)
        self.assertNotIn("recovery_result", unsafe)
        self.assertNotEqual(unsafe["result"], flat)
        crashed = project("BLOCKED", "P90_R06_EXIT_OWNER_BLOCKED",
                          error="P90_PRIVATE_IPC_UNVERIFIED")
        self.assertEqual(crashed["state"], "P90_R06_EXIT_OWNER_BLOCKED")
        self.assertNotEqual(crashed["result"], flat)

    def test_signed_private_rotation_is_not_protection_drift(self):
        _, _, private, _, now = _case()
        rotated = replace(private, generation_id="new-signed-generation")
        self.assertTrue(_fresh_matching_private_protection(
            private, rotated, checked_at=now,
        ))
        bad = replace(rotated, position={
            **rotated.position, "takeProfit": "0",
        })
        self.assertFalse(_fresh_matching_private_protection(
            private, bad, checked_at=now,
        ))


if __name__ == "__main__":
    unittest.main()

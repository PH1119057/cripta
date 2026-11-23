"""#849 regression-first tests: credential-local signed MAIN/SUB Exit protection.

No network, database, systemd, Exchange or filesystem mutation.
Synthetic proofs NEVER grant release or financial authorization.
"""
from __future__ import annotations

import copy
import sys
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_signed_preflight import (  # noqa: E402
    _check_signed_exit_account_pair,
    _check_signed_exit_plan_recovery,
    collect_current_signed_exit_preflight_once,
)

NOW = datetime(2026, 11, 22, 3, 0, 0, tzinfo=UTC)
SOURCE = "bybit:signed-recovery-linear-usdt:exact-account"


def fixture() -> tuple[object, object]:
    def route(kind: str, ident: str, uid: str) -> object:
        return SimpleNamespace(
            account_kind=kind,
            trading_account_id="ta-" + ident * 32,
            account_connector_id="connector-" + kind.lower(),
            registered_exchange_uid=uid,
        )

    def proof(ident: str, uid: str, positions: tuple[dict, ...] = ()) -> object:
        return SimpleNamespace(
            trading_account_id="ta-" + ident * 32,
            signed_uid=uid,
            source_ref=SOURCE,
            observed_at=NOW - timedelta(seconds=3),
            positions=positions,
            open_orders=0,
            pending_commands=0,
        )

    routes = SimpleNamespace(
        main=route("MAIN", "a", "123456789"),
        sub=route("SUB", "b", "987654321"),
    )
    proofs = (proof("a", "123456789"), proof("b", "987654321"))
    return routes, proofs


def open_position() -> dict[str, object]:
    return {
        "symbol": "LTCUSDT",
        "side": "Buy",
        "positionIdx": 0,
        "size": "1.1",
        "avgPrice": "100",
        "stopLoss": "98",
        "takeProfit": "103",
        "trailingStop": "0",
        "markPrice": "101",
    }


class SignedExitPreflightTests(unittest.TestCase):
    def run_check(self, routes: object, proofs: object) -> dict[str, object]:
        return _check_signed_exit_account_pair(
            routes=routes, proofs=proofs, observed_at=NOW,
        )

    def test_exact_signed_two_account_flat_readback_is_still_no_permit(self) -> None:
        routes, proofs = fixture()
        report = self.run_check(routes, proofs)
        self.assertEqual(report["state"], "SIGNED_MAIN_SUB_GET_CONSISTENCY_PASS")
        self.assertEqual(report["component_id"], "EXIT")
        self.assertEqual(report["account_position_counts"], {"MAIN": 0, "SUB": 0})
        self.assertEqual(report["account_open_orders"], {"MAIN": 0, "SUB": 0})
        self.assertEqual(report["source_ref"], SOURCE)
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["financial_mutation_authorized"])
        self.assertFalse(report["runtime_behavior_verified"])
        self.assertFalse(report["rollback_ready"])
        self.assertFalse(report["source_compatibility_verified"])
        self.assertFalse(report["actual_host_attested"])

    def test_live_open_main_position_does_not_block_signed_read(self) -> None:
        routes, proofs = fixture()
        main, sub = proofs
        main.positions = (open_position(),)
        report = self.run_check(routes, (main, sub))
        self.assertEqual(report["account_position_counts"], {"MAIN": 1, "SUB": 0})
        self.assertFalse(report["release_authorized"])

    def test_fail_closed_wrong_route_account_uid_or_duplicate(self) -> None:
        for kind, key, value in (
            ("main", "account_kind", "SUB"),
            ("main", "trading_account_id", "ta-" + "c" * 32),
            ("sub", "account_connector_id", ""),
            ("main", "registered_exchange_uid", "not-uid"),
            ("sub", "registered_exchange_uid", "123456789"),
            ("sub", "trading_account_id", "ta-" + "a" * 32),
            ("sub", "account_connector_id", "connector-main"),
        ):
            routes, proofs = fixture()
            setattr(getattr(routes, kind), key, value)
            with self.subTest(kind=kind, key=key), self.assertRaises(ValueError):
                self.run_check(routes, proofs)

    def test_fail_closed_wrong_proof_identity_source_or_commands(self) -> None:
        for key, value in (
            ("trading_account_id", "ta-" + "c" * 32),
            ("signed_uid", "9"),
            ("source_ref", "bybit:unsigned"),
            ("open_orders", 1),
            ("pending_commands", 1),
            ("open_orders", False),
            ("positions", "unknown"),
        ):
            routes, proofs = fixture()
            main, sub = proofs
            setattr(main, key, value)
            with self.subTest(field=key, value=str(value)[:30]), self.assertRaises(ValueError):
                self.run_check(routes, (main, sub))

    def test_fail_closed_stale_future_naive_or_missing_timestamp(self) -> None:
        for value in (
            NOW - timedelta(seconds=15, microseconds=1),
            NOW + timedelta(microseconds=1),
            NOW.replace(tzinfo=None),
            None,
        ):
            routes, proofs = fixture()
            proofs[0].observed_at = value
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                self.run_check(routes, proofs)

    def test_fail_closed_missing_or_extra_account_proof(self) -> None:
        routes, proofs = fixture()
        for value in (
            (),
            proofs[:1],
            proofs + (proofs[0],),
            {"MAIN": proofs[0], "SUB": proofs[1]},
            None,
        ):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                self.run_check(routes, value)

    def test_fail_closed_position_without_protection_or_malformed_numbers(self) -> None:
        for key, value in (
            ("stopLoss", ""),
            ("takeProfit", "0"),
            ("size", "0"),
            ("avgPrice", "-1"),
            ("markPrice", "nan"),
            ("trailingStop", "-1"),
            ("side", "None"),
            ("positionIdx", 1),
            ("symbol", ""),
        ):
            routes, proofs = fixture()
            row = open_position()
            row[key] = value
            proofs[0].positions = (row,)
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.run_check(routes, proofs)

    def test_fail_closed_duplicate_position_or_untrusted_shape(self) -> None:
        routes, proofs = fixture()
        row = open_position()
        proofs[0].positions = (row, copy.deepcopy(row))
        with self.assertRaises(ValueError):
            self.run_check(routes, proofs)
        proofs[0].positions = ({"symbol": "LTCUSDT"},)
        with self.assertRaises(ValueError):
            self.run_check(routes, proofs)

    def test_signed_main_open_position_requires_same_durable_exit_plan(self) -> None:
        routes, proofs = fixture()
        proofs[0].positions = (open_position(),)
        position = SimpleNamespace(
            trading_account_id=routes.main.trading_account_id,
            symbol="LTCUSDT", identity_status="PROVEN",
            position_idx=0, initial_protection_confirmed_at=NOW,
            exit_plan_fingerprint="exit-fp", strategy_activation_id="activation",
            direction=SimpleNamespace(value="LONG"), actual_qty=Decimal("1.1"),
        )
        plan = SimpleNamespace(
            exit_plan_fingerprint="exit-fp", strategy_activation_id="activation",
        )
        result = _check_signed_exit_plan_recovery(
            routes=routes, proofs=proofs, bindings=((position, plan),),
        )
        self.assertEqual(
            result["state"], "SIGNED_POSITION_EXIT_PLAN_DB_ALIGNMENT_PASS",
        )
        self.assertEqual(result["account_position_counts"], {"MAIN": 1, "SUB": 0})
        for flag in ("release_authorized", "financial_mutation_authorized",
                     "rollback_ready", "runtime_behavior_verified"):
            self.assertFalse(result[flag])

        for name, update in (
            ("no_binding", lambda p, q: ()),
            ("wrong_account", lambda p, q: ((SimpleNamespace(
                **{**vars(p), "trading_account_id": routes.sub.trading_account_id}
            ), q),)),
            ("wrong_qty", lambda p, q: ((SimpleNamespace(
                **{**vars(p), "actual_qty": Decimal("1.2")}
            ), q),)),
            ("wrong_plan", lambda p, q: ((p, SimpleNamespace(
                exit_plan_fingerprint="forged",
                strategy_activation_id="activation",
            )),)),
            ("missing_protection", lambda p, q: ((SimpleNamespace(
                **{**vars(p), "initial_protection_confirmed_at": None}
            ), q),)),
            ("not_proven", lambda p, q: ((SimpleNamespace(
                **{**vars(p), "identity_status": "LEGACY_UNKNOWN"}
            ), q),)),
            ("wrong_direction", lambda p, q: ((SimpleNamespace(
                **{**vars(p), "direction": SimpleNamespace(value="SHORT")}
            ), q),)),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _check_signed_exit_plan_recovery(
                    routes=routes, proofs=proofs, bindings=update(position, plan),
                )

    def test_signed_flat_account_rejects_orphan_db_and_signed_position(self) -> None:
        routes, proofs = fixture()
        empty = _check_signed_exit_plan_recovery(
            routes=routes, proofs=proofs, bindings=(),
        )
        self.assertEqual(empty["account_position_counts"], {"MAIN": 0, "SUB": 0})
        proofs[1].positions = (open_position(),)
        with self.assertRaisesRegex(ValueError, "SIGNED_DB_POSITION_SET_MISMATCH"):
            _check_signed_exit_plan_recovery(
                routes=routes, proofs=proofs, bindings=(),
            )

    @unittest.skipUnless(sys.platform == "linux", "requires Linux Unix pwd")
    def test_noarg_host_owner_rejects_non_cripta_actor(self) -> None:
        self.assertEqual(collect_current_signed_exit_preflight_once.__code__.co_argcount, 0)
        with (
            patch("component_release_exit_signed_preflight.os.geteuid", return_value=0),
            patch("pwd.getpwnam", return_value=SimpleNamespace(pw_uid=107)),
            self.assertRaisesRegex(ValueError, "ACTOR_NOT_CRIPTA"),
        ):
            collect_current_signed_exit_preflight_once()

    @unittest.skipUnless(sys.platform == "linux", "requires Linux Unix pwd")
    def test_absent_named_service_actor_fails_closed_in_ci(self) -> None:
        with (
            patch("pwd.getpwnam", side_effect=KeyError("cripta")),
            self.assertRaisesRegex(ValueError, "ACTOR_NOT_CRIPTA"),
        ):
            collect_current_signed_exit_preflight_once()

    def test_non_linux_platform_denied_before_unix_actor_lookup(self) -> None:
        with (
            patch("component_release_exit_signed_preflight.sys.platform", "win32"),
            self.assertRaisesRegex(ValueError, "ACTOR_NOT_CRIPTA"),
        ):
            collect_current_signed_exit_preflight_once()

    def test_wrong_proof_type_and_route_result_fail_closed(self) -> None:
        routes, proofs = fixture()
        with self.assertRaises(ValueError):
            self.run_check({"MAIN": routes.main, "SUB": routes.sub}, proofs)
        with self.assertRaises(ValueError):
            self.run_check(routes, (proofs[0], object()))


if __name__ == "__main__":
    unittest.main()

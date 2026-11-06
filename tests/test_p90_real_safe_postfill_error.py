"""Only sanitized source tokens may be persisted by SUB Execution."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py"


class ExchangeMutationBarrier(RuntimeError):
    pass


class PreMutationSafetyBlock(RuntimeError):
    pass


def formatter(sub=True):
    assert SOURCE.is_file(), "Actual checkout required"
    fn = next(n for n in ast.parse(SOURCE.read_text(encoding="utf-8")).body
              if isinstance(n, ast.FunctionDef) and n.name == "_private_worker_safe_error")
    scope = {
        "_is_sub_private_worker": lambda: sub,
        "ExchangeMutationBarrier": ExchangeMutationBarrier,
        "PreMutationSafetyBlock": PreMutationSafetyBlock,
    }
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        fn], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), scope)
    return scope["_private_worker_safe_error"]


@pytest.mark.parametrize("code", (
    "P90_REAL_POSTFILL_SOURCE_TRANSPORT_UNAVAILABLE",
    "P90_REAL_POSTFILL_SIGNED_PRIVATE_ACCOUNT_DRIFT",
    "P90_REAL_MARKET_SOURCE_NATIVE_WINDOW_INCOMPLETE",
))
def test_safe_postfill_reason_survives_sub_error_redaction(code):
    reason = "P90_REAL_FIRST_FILL_NATIVE_EXIT_HANDOFF_UNVERIFIED:" + code
    assert formatter()(ExchangeMutationBarrier(reason)) == "ExchangeMutationBarrier:" + reason


@pytest.mark.parametrize("unsafe", (
    "P90_REAL_MARKET_SOURCE_NATIVE_WINDOW_INCOMPLETE:SECRET",
    "P90_REAL_MARKET_SOURCE_NATIVE_WINDOW_INCOMPLETE /var/run/private",
    "bybit raw response",
    "P90_REAL_MARKET_SOURCE_" + "A"*150,
))
def test_arbitrary_details_stay_hidden(unsafe):
    assert formatter()(ExchangeMutationBarrier(
        "P90_REAL_FIRST_FILL_NATIVE_EXIT_HANDOFF_UNVERIFIED:" + unsafe
    )) == "ExchangeMutationBarrier"


def test_unrelated_sub_barrier_stays_redacted():
    assert formatter()(ExchangeMutationBarrier("OTHER_FAILURE")) == "ExchangeMutationBarrier"


def test_existing_owner_safety_classification_unchanged():
    assert formatter()(PreMutationSafetyBlock("OWNER_FULL_CONTOUR_UNVERIFIED")) == (
        "PreMutationSafetyBlock:OWNER_FULL_CONTOUR_UNVERIFIED"
    )


def test_main_non_sub_error_policy_unchanged():
    assert formatter(sub=False)(ExchangeMutationBarrier("TEST")) == "ExchangeMutationBarrier: TEST"

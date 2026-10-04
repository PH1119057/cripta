from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bybit_workbench.live_arm_readiness import (
    REQUIRED_LIVE_ARM_CHECKS,
    LiveArmContext,
    evaluate_live_arm,
    scope_for_check,
)

NOW = datetime(2026, 10, 4, 17, 20, tzinfo=UTC)
RELEASE = "a" * 40


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, statement, parameters=()):
        return _Cursor(self._rows)


def _rows(context: LiveArmContext):
    result = []
    for code in REQUIRED_LIVE_ARM_CHECKS:
        scope_type, scope_key = scope_for_check(code, context)
        status = (
            "OWNER_WAIVED_FOR_R1_MICRO_LIVE"
            if code == "CRITICAL_FAULT_DELIVERY"
            else "PASS"
        )
        result.append(
            {
                "check_code": code,
                "scope_type": scope_type,
                "scope_key": scope_key,
                "status": status,
                "checked_at": NOW,
                "valid_until": NOW + timedelta(minutes=10),
                "release_commit": RELEASE,
                "source": "test",
                "evidence": {},
            }
        )
    return result


def test_r1_micro_live_accepts_only_scoped_owner_webhook_waiver() -> None:
    context = LiveArmContext(
        strategy_id="r1_aptusdt",
        strategy_version="1.0-micro-live",
        strategy_config_fingerprint="fp",
        strategy_activation_id="activation",
        symbol="APTUSDT",
        release_commit=RELEASE,
    )
    decision = evaluate_live_arm(_Connection(_rows(context)), context=context, now=NOW)
    assert decision.ready
    waived = next(c for c in decision.checks if c.code == "CRITICAL_FAULT_DELIVERY")
    assert waived.status == "OWNER_WAIVED_FOR_R1_MICRO_LIVE"
    assert waived.waived
    assert waived.passed


def test_webhook_waiver_does_not_unlock_non_r1_strategy() -> None:
    context = LiveArmContext(
        strategy_id="other_strategy",
        strategy_version="1",
        strategy_config_fingerprint="fp",
        strategy_activation_id="activation",
        symbol="BTCUSDT",
        release_commit=RELEASE,
    )
    decision = evaluate_live_arm(_Connection(_rows(context)), context=context, now=NOW)
    assert not decision.ready
    assert "CRITICAL_FAULT_DELIVERY" in decision.failed_codes

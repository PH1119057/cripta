"""P90 REAL missed TRAIL035 restart recovery regression (#401)."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.p90l90_real_open_restart import (
    P90RealOpenRestartUnverified,
    _verify_trailing,
)
from bybit_workbench.universal_entry.p90l90_real_restart_recovery import (
    _recover_missing_trail035_from_current_native,
)
from bybit_workbench.universal_exit.contracts import (
    ExitActionKind,
    ExitEvaluationStatus,
)
from bybit_workbench.universal_exit.p90l90_real_mfe import (
    P90RealMfeUnverified,
)


def _position() -> SimpleNamespace:
    return SimpleNamespace(
        strategy_position_id="POS-" + "a" * 32,
        actual_avg_fill=Decimal("100"),
    )


def _evidence(trailing: str) -> SimpleNamespace:
    return SimpleNamespace(
        tick_size=Decimal("0.01"),
        position={"trailingStop": trailing},
    )


def test_restart_inventory_remains_strict_by_default() -> None:
    with pytest.raises(
        P90RealOpenRestartUnverified,
        match="P90_REAL_RESTART_SIGNED_TRAIL035_NOT_ARMED",
    ):
        _verify_trailing(_position(), _evidence("0"))


def test_source_replay_may_classify_exact_zero_trailing_without_arming() -> None:
    assert _verify_trailing(
        _position(), _evidence("0"), allow_missing=True,
    ) is False
    assert _verify_trailing(
        _position(), _evidence("0.35"), allow_missing=True,
    ) is True
    with pytest.raises(
        P90RealOpenRestartUnverified,
        match="P90_REAL_RESTART_SIGNED_TRAIL035_NOT_ARMED",
    ):
        _verify_trailing(
            _position(), _evidence("0.34"), allow_missing=True,
        )


def _item(*, armed: bool) -> SimpleNamespace:
    return SimpleNamespace(
        trail035_armed=armed,
        position=_position(),
    )


def test_missing_trail_recovery_queues_only_exact_set_trailing() -> None:
    evaluation = SimpleNamespace(
        status=ExitEvaluationStatus.DECISION_CREATED,
        decision=SimpleNamespace(action_kind=ExitActionKind.SET_TRAILING),
    )
    calls: list[tuple[object, object]] = []
    ports = SimpleNamespace(
        on_native_trade=lambda fact, cursor: (
            calls.append((fact, cursor)) or (evaluation,)
        )
    )
    state = _recover_missing_trail035_from_current_native(
        item=_item(armed=False),
        fact="real-native-fact",
        cursor="real-native-cursor",
        exit_ports=ports,
    )
    assert state == "QUEUED"
    assert calls == [("real-native-fact", "real-native-cursor")]


def test_missing_trail_recovery_preserves_late_arm_as_structural_block() -> None:
    def late(_fact, _cursor):
        raise P90RealMfeUnverified(
            "P90_REAL_EXIT_POLICY_DENIED:"
            "P90_REAL_NATIVE_TRAILING_LATE_ARM_UNVERIFIED"
        )

    state = _recover_missing_trail035_from_current_native(
        item=_item(armed=False),
        fact="real-native-fact",
        cursor="real-native-cursor",
        exit_ports=SimpleNamespace(on_native_trade=late),
    )
    assert state == "LATE_ARM_BLOCKED"


def test_missing_trail_recovery_never_hides_other_source_or_execution_failure() -> None:
    def broken(_fact, _cursor):
        raise P90RealMfeUnverified("P90_REAL_LIVE_SOURCE_NATIVE_EVENT_STALE")

    with pytest.raises(
        P90RealMfeUnverified,
        match="P90_REAL_LIVE_SOURCE_NATIVE_EVENT_STALE",
    ):
        _recover_missing_trail035_from_current_native(
            item=_item(armed=False),
            fact="real-native-fact",
            cursor="real-native-cursor",
            exit_ports=SimpleNamespace(on_native_trade=broken),
        )


def test_already_armed_position_never_requeues_trailing() -> None:
    calls: list[str] = []
    state = _recover_missing_trail035_from_current_native(
        item=_item(armed=True),
        fact="unused",
        cursor="unused",
        exit_ports=SimpleNamespace(
            on_native_trade=lambda *_: calls.append("unexpected")
        ),
    )
    assert state == "ARMED"
    assert calls == []

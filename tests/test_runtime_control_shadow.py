from __future__ import annotations

from pathlib import Path

import pytest

from bybit_workbench.runtime_control_shadow import (
    LegacyP90Snapshot,
    ShadowClassification,
    evaluate_p90_shadow,
)


def snapshot(**overrides: object) -> LegacyP90Snapshot:
    values: dict[str, object] = {
        "gate_open": False,
        "gate_reason": "",
        "owner_scope_exact": False,
        "strategy_authorized_exact": False,
        "release_scope_exact": True,
        "observer_ready": True,
        "consumers_ready": True,
        "hard_safety_block": False,
        "p90_staging": False,
        "non_p90_control_scope": False,
    }
    values.update(overrides)
    return LegacyP90Snapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (
            snapshot(),
            ShadowClassification.PARITY_BLOCK,
        ),
        (
            snapshot(
                gate_open=True,
                owner_scope_exact=True,
                strategy_authorized_exact=True,
            ),
            ShadowClassification.PARITY_ALLOW,
        ),
        (
            snapshot(
                gate_open=False,
                owner_scope_exact=True,
                strategy_authorized_exact=True,
            ),
            ShadowClassification.LEGACY_STUCK_AFTER_RECOVERY,
        ),
        (
            snapshot(
                gate_open=True,
                owner_scope_exact=True,
                strategy_authorized_exact=True,
                observer_ready=False,
            ),
            ShadowClassification.LEGACY_OPEN_WHILE_CORE_BLOCKS,
        ),
    ],
)
def test_shadow_classifies_terminal_states(
    item: LegacyP90Snapshot,
    expected: ShadowClassification,
) -> None:
    assert evaluate_p90_shadow(item).classification is expected


def test_transient_technical_block_is_normal_parity_block() -> None:
    result = evaluate_p90_shadow(
        snapshot(
            gate_open=False,
            owner_scope_exact=True,
            strategy_authorized_exact=True,
            observer_ready=False,
        )
    )
    assert result.classification is ShadowClassification.PARITY_BLOCK
    assert result.core_state.owner_authorized is True
    assert result.core_effective_new_entry is False


def test_recovery_without_legacy_gate_reopen_is_the_target_stuck_state() -> None:
    result = evaluate_p90_shadow(
        snapshot(
            gate_open=False,
            owner_scope_exact=True,
            strategy_authorized_exact=True,
            observer_ready=True,
            consumers_ready=True,
        )
    )
    assert result.classification is ShadowClassification.LEGACY_STUCK_AFTER_RECOVERY
    assert result.core_state.owner_authorized is True
    assert result.core_effective_new_entry is True
    assert result.legacy_effective_new_entry is False


def test_hard_safety_block_never_classifies_as_recovered() -> None:
    result = evaluate_p90_shadow(
        snapshot(
            gate_open=False,
            owner_scope_exact=True,
            strategy_authorized_exact=True,
            hard_safety_block=True,
        )
    )
    assert result.classification is ShadowClassification.PARITY_BLOCK
    assert result.core_effective_new_entry is False


def test_p90_staging_is_excluded_from_parity() -> None:
    result = evaluate_p90_shadow(
        snapshot(
            owner_scope_exact=True,
            strategy_authorized_exact=True,
            p90_staging=True,
        )
    )
    assert result.classification is ShadowClassification.TRANSITIONAL_P90_STAGING


def test_non_p90_scope_is_excluded_from_p90_parity() -> None:
    result = evaluate_p90_shadow(
        snapshot(
            gate_open=True,
            non_p90_control_scope=True,
        )
    )
    assert result.classification is ShadowClassification.NON_P90_CONTROL_SCOPE


def test_probe_is_structurally_read_only() -> None:
    path = Path("operations/monitoring/stable_control_shadow_probe.py")
    source = path.read_text(encoding="utf-8")
    upper = source.upper()

    assert "SET TRANSACTION READ ONLY" in upper
    for forbidden in (
        "INSERT INTO",
        "UPDATE CONTROL.",
        "UPDATE STRATEGY_ENTRY.",
        "UPDATE RUNTIME.",
        "DELETE FROM",
        "FOR UPDATE",
        "CREATE TABLE",
        "ALTER TABLE",
        "DROP TABLE",
        "SYSTEMCTL",
        "SUBPROCESS",
        "PYBIT",
        "REQUESTS.",
    ):
        assert forbidden not in upper

    for forbidden_write in (
        ".WRITE_TEXT(",
        ".WRITE_BYTES(",
        ".MKDIR(",
        ".UNLINK(",
        ".TOUCH(",
    ):
        assert forbidden_write not in upper


def test_shadow_has_no_existing_production_wiring() -> None:
    allowed = {
        Path("src/bybit_workbench/runtime_control_shadow.py"),
        Path("operations/monitoring/stable_control_shadow_probe.py"),
    }
    offenders: list[str] = []
    for root in (Path("src/bybit_workbench"), Path("operations")):
        for path in root.rglob("*.py"):
            if path in allowed:
                continue
            if "runtime_control_shadow" in path.read_text(encoding="utf-8"):
                offenders.append(path.as_posix())

    assert offenders == []

from datetime import UTC, datetime, timedelta

from bybit_workbench.live_exit_policy import EarlyLossContext, early_loss_eligible

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


def context(**overrides: object) -> EarlyLossContext:
    values = {
        "hold_status": "INCOMPATIBLE",
        "supervisor_state": "BROKEN",
        "structural_event": "protective_clean_break_against",
        "hold_observed_at": NOW - timedelta(seconds=5),
        "structure_observed_at": NOW - timedelta(seconds=10),
        "decided_at": NOW,
    }
    values.update(overrides)
    return EarlyLossContext(**values)  # type: ignore[arg-type]


def test_warning_incompatible_never_closes() -> None:
    assert not early_loss_eligible(context(supervisor_state="WARNING"))


def test_unknown_structure_never_closes() -> None:
    assert not early_loss_eligible(context(structural_event=None))


def test_causal_clean_break_and_incompatible_is_eligible() -> None:
    assert early_loss_eligible(context())


def test_future_or_stale_context_is_rejected() -> None:
    assert not early_loss_eligible(context(structure_observed_at=NOW + timedelta(seconds=1)))
    assert not early_loss_eligible(context(hold_observed_at=NOW - timedelta(seconds=91)))

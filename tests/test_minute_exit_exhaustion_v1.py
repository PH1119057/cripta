from bybit_workbench.research.minute_exit_exhaustion_v1 import (
    ExitObservation,
    decide_exit,
)


def observation(**changes: bool) -> ExitObservation:
    values = dict(
        direction="Long",
        current_profit_pct=0.35,
        maximum_profit_pct=0.50,
        local_barrier_reached=True,
        continuation_failed=True,
        minute_structure_broken=False,
        aggressive_flow_against=False,
        opposing_book_restored=False,
    )
    values.update(changes)
    return ExitObservation(**values)  # type: ignore[arg-type]


def test_one_orderbook_wall_is_not_an_exit() -> None:
    result = decide_exit(observation(opposing_book_restored=True))
    assert result.action == "наблюдать_внимательно"


def test_structure_break_confirms_exit() -> None:
    result = decide_exit(observation(minute_structure_broken=True))
    assert result.action == "выйти"


def test_flow_and_book_together_confirm_exit() -> None:
    result = decide_exit(
        observation(aggressive_flow_against=True, opposing_book_restored=True)
    )
    assert result.action == "выйти"

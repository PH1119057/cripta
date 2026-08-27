from bybit_workbench.research.minute_entry_wait_v1 import PriceZone, decide_entry


def test_long_waits_for_deeper_support() -> None:
    result = decide_entry(
        direction="Long",
        entry_price=100.0,
        current_price=99.8,
        minute_zone=PriceZone(99.35, 99.55),
        five_minute_zone=PriceZone(99.40, 99.60),
    )
    assert result.action == "ждать"
    assert result.proposed_price == 99.55


def test_long_enters_when_deeper_zone_is_reached() -> None:
    result = decide_entry(
        direction="Long",
        entry_price=100.0,
        current_price=99.5,
        minute_zone=PriceZone(99.35, 99.55),
        five_minute_zone=PriceZone(99.40, 99.60),
    )
    assert result.action == "войти"


def test_mismatched_zones_do_not_authorize_entry() -> None:
    result = decide_entry(
        direction="Short",
        entry_price=100.0,
        current_price=100.2,
        minute_zone=PriceZone(100.5, 100.6),
        five_minute_zone=PriceZone(100.8, 100.9),
    )
    assert result.action == "нет_согласованной_зоны"

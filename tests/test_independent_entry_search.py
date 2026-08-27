from bybit_workbench.research.independent_entry_search import Bar, PriceIndex


def test_price_index_finds_first_threshold_crossing() -> None:
    bars = [
        Bar(float(index), 10.0, high, low, 10.0, 1.0, 0.0)
        for index, (high, low) in enumerate([(10.0, 9.0), (11.0, 8.0), (12.0, 7.0), (13.0, 6.0)])
    ]
    index = PriceIndex(bars)
    assert index.first_ge(0, 12.0) == 2
    assert index.first_ge(3, 12.0) == 3
    assert index.first_le(1, 7.0) == 2
    assert index.first_le(3, 5.0) is None

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_trade_gate_routes_exact_r1_cohort_to_r1_micro_live_endpoint() -> None:
    source = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "function exactR1MicroLiveCohort()" in source
    for strategy_id in (
        "r1_aptusdt",
        "r1_injusdt",
        "r1_dotusdt",
        "r1_ltcusdt",
        "r1_arbusdt",
    ):
        assert strategy_id in source
    assert "endpoint=r1?'/api/r1/micro-live':'/api/live/gate'" in source
    assert "String(item.strategy_version||'')==='1.1-micro-live'" in source

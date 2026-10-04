from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_r1_micro_live_uses_smallint_global_gate_contract() -> None:
    source = (ROOT / "src/bybit_workbench/r1_micro_live_control.py").read_text(
        encoding="utf-8"
    )
    assert "SET enabled=1," in source
    assert "SET enabled=0," in source
    assert "SET enabled=true," not in source
    assert "SET enabled=false," not in source
    assert "VALUES(%s,'mainnet',false,true,true" in source

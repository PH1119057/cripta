from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "operations/connectivity/private_runtime.py"


def test_manual_smoke_reads_position_from_its_own_exchange_response():
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("def execute_command(")
    end = source.index("\ndef ", start + 1)
    command = source[start:end]
    assert 'positions, _ = api_get("/v5/position/list"' in command
    assert '((positions.get("result") or {}).get("list") or [])' in command
    assert "p for p in position_rows" not in command

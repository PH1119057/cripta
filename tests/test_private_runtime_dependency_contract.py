from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISPATCHER_UNIT = ROOT / "operations" / "dispatcher_v2" / "cripta-dispatcher-v2.service"


def _unit_directive(text: str, key: str) -> str:
    prefix = f"{key}="
    values = [line[len(prefix):].strip() for line in text.splitlines() if line.startswith(prefix)]
    assert len(values) == 1, f"expected exactly one {key}= directive"
    return values[0]


def test_dispatcher_does_not_start_private_runtime() -> None:
    unit = DISPATCHER_UNIT.read_text(encoding="utf-8")
    wants = _unit_directive(unit, "Wants").split()
    after = _unit_directive(unit, "After").split()

    assert "cripta-private-runtime.service" not in wants
    # Ordering is allowed if private runtime is independently activated.
    assert "cripta-private-runtime.service" in after
    assert "cripta-mayak-v2.service" in wants
    assert "postgresql.service" in wants

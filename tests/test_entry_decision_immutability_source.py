from pathlib import Path


SOURCE = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")


def test_original_entry_decision_is_immutable() -> None:
    function = SOURCE.split("def record_entry_decision", 1)[1].split("def atomic_status", 1)[0]
    assert "ON CONFLICT(signal_id) DO NOTHING" in function
    assert "DO UPDATE SET" not in function
    assert "DUPLICATE_RUNTIME_OBSERVATION" in function


def test_completed_command_is_not_described_as_fill() -> None:
    correlator = Path("operations/monitoring/causal_context_correlator.py").read_text(
        encoding="utf-8"
    )
    assert "'processing_state',state" in correlator
    assert "'exchange_filled',EXISTS" in correlator

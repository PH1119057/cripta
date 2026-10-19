from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bybit_workbench.universal_entry import r1_independent_parity as parity
from operations.dashboard import app

ROOT = Path(__file__).resolve().parents[1]


def test_r1_independent_replay_proves_all_five_strategies_and_both_directions() -> None:
    proof = parity.run_r1_independent_decision_parity()
    assert proof["status"] == "PASS"
    assert proof["cases"] == 10
    assert proof["directions"] == ["LONG", "SHORT"]
    assert len(proof["symbols"]) == 5
    assert proof["real_exchange_mutation"] is False


def test_replay_detects_paper_real_adapter_semantic_drift(monkeypatch) -> None:
    original = parity.prepare_runtime_entry_command

    def altered_real_command(*args, **kwargs):
        command = original(*args, **kwargs)
        return replace(command, payload={**command.payload, "side": "WRONG"})

    monkeypatch.setattr(parity, "prepare_runtime_entry_command", altered_real_command)
    with pytest.raises(parity.IndependentParityFailure, match="adapter mismatch side"):
        parity.run_r1_independent_decision_parity()


def test_prearm_rejects_incomplete_or_forged_parity_result(monkeypatch) -> None:
    monkeypatch.setattr(
        parity,
        "run_r1_independent_decision_parity",
        lambda: {"status": "PASS", "cases": 0, "real_exchange_mutation": False},
    )
    with pytest.raises(ValueError, match="proof incomplete"):
        app._r1_paper_real_parity_attestation(
            "a" * 40, source_root=ROOT, runtime_root=ROOT,
        )


def test_prearm_propagates_independent_replay_failure(monkeypatch) -> None:
    def failed_replay():
        raise parity.IndependentParityFailure("deliberate semantic drift")

    monkeypatch.setattr(parity, "run_r1_independent_decision_parity", failed_replay)
    with pytest.raises(ValueError, match="independent replay failed"):
        app._r1_paper_real_parity_attestation(
            "a" * 40, source_root=ROOT, runtime_root=ROOT,
        )

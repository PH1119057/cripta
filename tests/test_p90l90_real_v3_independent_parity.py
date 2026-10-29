from bybit_workbench.p90l90_real_v3_independent_parity import (
    run_p90l90_real_v3_independent_decision_parity,
)


def test_p90l90_real_v3_independent_paper_real_parity() -> None:
    proof = run_p90l90_real_v3_independent_decision_parity()
    assert proof["status"] == "PASS"
    assert proof["proof_version"] == "P90L90_REAL_V3_INDEPENDENT_PAPER_REAL_POLICY_PARITY_V1"
    assert proof["cases"] == 10
    assert proof["symbols"] == ["APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT"]
    assert proof["directions"] == ["LONG", "SHORT"]
    assert proof["old_paper_activation"] is False
    assert proof["real_exchange_mutation"] is False
    assert proof["database_mutation"] is False

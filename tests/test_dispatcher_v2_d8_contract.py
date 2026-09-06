from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_d8_contract_is_observed_only_and_clean_v2() -> None:
    body = (ROOT / "docs/DISPATCHER_V2_D8_OBSERVED_CONTEXT_RU.md").read_text(encoding="utf-8")
    assert "DISPATCHER_V2_OBSERVED_CONTEXT = YES" in body
    assert "DISPATCHER_V2_CONSUMED_CONTEXT = NO" in body
    assert "TRADING_EFFECT = NONE" in body
    assert "research_context.dispatcher_v2_event_links" in body
    assert "symbol + ближайшее время" in body
    assert "CoinMarketRating" in body


def test_d8_is_registered_and_closed_with_runtime_evidence() -> None:
    authority = (ROOT / "docs/DOCUMENT_AUTHORITY_RU.md").read_text(encoding="utf-8")
    current_map = (ROOT / "docs/CURRENT_PROJECT_MAP_RU.md").read_text(encoding="utf-8")
    results = (ROOT / "docs/DISPATCHER_V2_D8_STAGE_RESULTS_RU.md").read_text(encoding="utf-8")
    assert "DISPATCHER_V2_D8_OBSERVED_CONTEXT_RU.md" in authority
    assert "DISPATCHER_V2_D8_STAGE_RESULTS_RU.md" in authority
    assert "D8 завершён" in current_map
    assert "D8_RUNTIME_EVIDENCE       = PASS" in results
    assert "D8_TRADING_EFFECT         = NONE" in results
    assert "D8_CONSUMED_CONTEXT       = NO" in results

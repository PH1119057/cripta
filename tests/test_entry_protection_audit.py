from datetime import UTC, datetime, timedelta
from operations.monitoring.entry_protection_audit import classify_protection, summarize

def test_proof_states_distinct():
    assert classify_protection("UNKNOWN") == "PROOF_UNAVAILABLE"
    assert classify_protection("NOT_VERIFIED") == "PROTECTION_NOT_CONFIRMED"
    assert classify_protection("VERIFIED") == "CONFIRMED_AT_OBSERVATION"

def test_window_overlap_and_unresolved():
    now = datetime(2026, 10, 14, tzinfo=UTC)
    since = now - timedelta(hours=1)
    earlier = since - timedelta(minutes=5)
    resolved = since + timedelta(minutes=5)
    faults = [("a", "POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION", "POS1", earlier, resolved, {"protection_state": "UNKNOWN", "reason": "stale"}),
              ("b", "POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION", "POS2", since, None, {"protection_state": "NOT_VERIFIED"})]
    data = summarize([("OPERATIONAL_SAFETY_BLOCKED", "NOT_ARMED")], faults, since=since, now=now)
    assert data["entry_counts"]["OPERATIONAL_SAFETY_BLOCKED"] == 1
    assert data["protection_proof"]["PROOF_UNAVAILABLE"]["duration_seconds"] == 300
    assert data["protection_proof"]["PROTECTION_NOT_CONFIRMED"]["duration_seconds"] == 3600
    assert data["protection_proof"]["PROTECTION_NOT_CONFIRMED"]["still_open"] == 1

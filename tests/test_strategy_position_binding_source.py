from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
CONSUMER = (ROOT / "operations/connectivity/universal_entry_consumer.py").read_text(
    encoding="utf-8"
)


def test_private_runtime_uses_exact_universal_position_binding() -> None:
    assert "load_universal_entry_lineage(" in PRIVATE
    assert "persist_universal_strategy_position(" in PRIVATE
    assert "Universal Entry fill lost durable request lineage" in PRIVATE
    assert "universal_lineage is not None" in PRIVATE


def test_private_runtime_releases_bound_capital_only_from_position_close_path() -> None:
    assert PRIVATE.count("release_position_capital_reservation(") == 2
    assert "entry_execution_request_id" in PRIVATE
    assert "close_link_status='UNRESOLVED_EXACT_LINK'" in PRIVATE
    assert "close_link_status='EXACT'" in PRIVATE


def test_universal_consumer_blocks_physical_slot_before_command_publish() -> None:
    conflict_pos = CONSUMER.index("conflict = _exchange_position_slot_conflict")
    publish_pos = CONSUMER.index("_publish_command(connection, prepared")
    assert conflict_pos < publish_pos
    assert "EXCHANGE_POSITION_OWNERSHIP_CONFLICT" in CONSUMER
    assert "_release_pre_exchange_reservation(" in CONSUMER
    assert "PENDING_ENTRY_COMMAND" in CONSUMER
    assert "PENDING_ENTRY_ORDER" in CONSUMER
    assert "EXCHANGE_POSITION:" in CONSUMER
    assert "OWNED_POSITION:" in CONSUMER

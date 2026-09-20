from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = (ROOT / "operations/connectivity/universal_entry_consumer.py").read_text(
    encoding="utf-8"
)
PRIVATE = (ROOT / "operations/connectivity/private_runtime.py").read_text(
    encoding="utf-8"
)


def test_expired_reservations_are_swept_before_mainnet_gate_check() -> None:
    sweep = CONSUMER.index("_release_expired_reserved_requests(connection, now=current)")
    gate = CONSUMER.index("if not _execution_gate_enabled(connection):")
    assert sweep < gate
    assert "REQUEST_EXPIRED_PRE_DISPATCH" in CONSUMER


def test_pre_exchange_blocks_finalize_atomic_admission_not_capital_alone() -> None:
    assert "PRE_EXCHANGE_BLOCK:{exc.code.value}" in CONSUMER
    assert "PRE_EXCHANGE_BLOCK:STRUCTURAL_ERROR" in CONSUMER
    assert "EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN" in CONSUMER
    assert "EXCHANGE_POSITION_MODE_MISMATCH" in CONSUMER
    assert "_release_pre_exchange_admission(" in CONSUMER
    assert "_mark_admission_reconciliation_required(" in CONSUMER
    assert "_release_pre_exchange_reservation(" not in CONSUMER


def test_private_runtime_marks_order_ack_before_post_ack_processing() -> None:
    create = PRIVATE.index('result=api_post("/v5/order/create"')
    pending = PRIVATE.index("mark_entry_order_acknowledged(", create)
    market_followup = PRIVATE.index("if offset == 0:", pending)
    assert create < pending < market_followup


def test_private_runtime_ambiguous_mutation_marks_reservation_before_reconcile() -> None:
    barrier = PRIVATE.index("def handle_exchange_mutation_barrier(")
    mark = PRIVATE.index("finalize_failed_entry_command_reservation(", barrier)
    reconcile = PRIVATE.index('reconcile(connection, key, secret, "ambiguous_exchange_mutation")')
    assert barrier < mark < reconcile
    assert "mutation_ambiguous=True" in PRIVATE[mark:reconcile]


def test_limit_ttl_has_no_hidden_30_second_default() -> None:
    cancel = PRIVATE.index("def cancel_expired_entry_limits(")
    worker = PRIVATE.index("def command_worker_loop(", cancel)
    block = PRIVATE[cancel:worker]
    assert 'entry_limit_ttl_seconds") or 30' not in block
    assert "limit Entry is missing Strategy-owned entry_limit_ttl_seconds" in block
    assert "resolve_cancelled_entry_reservation_after_reconcile(" in block


def test_worker_releases_only_pre_ack_failures_and_escalates_post_ack() -> None:
    worker = PRIVATE.index("def command_worker_loop(")
    block = PRIVATE[worker:]
    assert "mutation_ambiguous=False" in block
    assert 'reservation_state == "RECONCILIATION_REQUIRED"' in block
    assert "post-ack Entry failure requires reconciliation" in block

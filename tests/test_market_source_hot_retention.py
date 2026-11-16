"""Scope tests for current MarketSource hot retention (#642)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "operations/monitoring/market_source_hot_retention.py"
SPEC = importlib.util.spec_from_file_location("market_source_hot_retention", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _state(session: str, release: str) -> bytes:
    payload = json.dumps(
        {
            "checkpoint": {
                "source_session_id": session,
                "release_commit": release,
                "optional_cursors": [
                    {
                        "product_category": "SPOT",
                        "source_session_id": session,
                        "transport_epoch_id": "1" * 32,
                        "next_cursor": 123,
                    },
                    {
                        "product_category": "LINEAR",
                        "source_session_id": session,
                        "transport_epoch_id": "2" * 32,
                        "next_cursor": 456,
                    },
                ],
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(payload).hexdigest().encode()
    return MODULE._STATE_MAGIC + digest + b"\n" + zlib.compress(payload)


def test_exact_short_hot_scope_and_never_native_causal_tables():
    assert MODULE.SOURCE_RETENTION_MINUTES == 15
    assert MODULE.DEFAULT_BATCH_LIMIT == 10_000
    assert MODULE.MAX_BATCH_LIMIT >= MODULE.DEFAULT_BATCH_LIMIT
    text = SOURCE.read_text()
    for table in (
        "analytics.market_publications",
        "analytics.public_market_source_seconds",
        "analytics.public_forming_minute_observations",
        "analytics.public_market_rest_observations",
        "analytics.public_source_original_event_frames",
    ):
        assert table in text
    for protected in (
        "analytics.native_public_trade_events",
        "analytics.native_public_trade_source_events",
        "analytics.closed_candles",
        "analytics.market_minutes",
        "runtime.hot_positions",
        "runtime.hot_orders",
        "runtime.executions",
    ):
        assert f"DELETE FROM {protected}" not in text
        assert f"TRUNCATE TABLE {protected}" not in text
    assert "VACUUM (FULL" not in text
    assert "VACUUM (ANALYZE)" in text


def test_mayak_cursor_decoder_requires_exact_digest_and_two_products():
    session = "a" * 32
    release = "b" * 40
    cursors = MODULE._decode_optional_cursors(
        _state(session, release),
        expected_session=session,
        expected_release=release,
    )
    assert cursors == {
        "SPOT": ("1" * 32, 123),
        "LINEAR": ("2" * 32, 456),
    }
    broken = bytearray(_state(session, release))
    broken[-1] ^= 1
    try:
        MODULE._decode_optional_cursors(
            bytes(broken),
            expected_session=session,
            expected_release=release,
        )
    except Exception as exc:
        assert "DECODE_INVALID" in str(exc) or "DIGEST_INVALID" in str(exc)
    else:
        raise AssertionError("corrupt MAYAK state accepted")


def test_raw_predicate_keeps_unconsumed_tail_and_prunes_consumed_prefix():
    active = ("a" * 32, "b" * 40)
    cutoff = object()
    predicate, params = MODULE._raw_delete_predicate(
        active,
        ("c" * 32, "d" * 40, {
            "SPOT": ("1" * 32, 10),
            "LINEAR": ("2" * 32, 20),
        }),
        cutoff,
    )
    assert "r.committed_at < %s" not in predicate
    assert "source_frame_number < %s" not in predicate
    assert params == ["a" * 32]

    predicate, params = MODULE._raw_delete_predicate(
        active,
        ("a" * 32, "b" * 40, {
            "SPOT": ("1" * 32, 10),
            "LINEAR": ("2" * 32, 20),
        }),
        cutoff,
    )
    assert "r.committed_at < %s" not in predicate
    assert "source_frame_number < %s" in predicate
    assert params[0] == "a" * 32
    assert params[-1] == 20


def test_systemd_contract_starts_retention_with_source():
    source = (
        ROOT / "operations/systemd/cripta-native-public-trade-source.service"
    ).read_text()
    service = (
        ROOT / "operations/systemd/cripta-market-hot-retention.service"
    ).read_text()
    timer = (
        ROOT / "operations/systemd/cripta-market-hot-retention.timer"
    ).read_text()
    assert "cripta-market-hot-retention.timer" in source
    assert "cripta-market-source-hot-retention.py --apply --batch-limit 50000" in service
    assert "User=postgres" in service
    assert "PartOf=cripta-native-public-trade-source.service" in timer
    assert "OnUnitActiveSec=60s" in timer

def test_native_cutover_accepts_source_coupled_retention_runtime_state():
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text()
    assert '"$native_code_changed" == "1"' in installer
    assert '"cripta-market-hot-retention.timer"' in installer
    assert (
        "source-coupled market retention timer inactive after native cutover"
        in installer
    )
    assert '"cripta-market-hot-retention.service"' in installer
    assert (
        "source-coupled market retention service failed after native cutover"
        in installer
    )
    assert "systemctl is-failed --quiet" in installer

def test_native_cutover_fresh_cursor_proof_counts_only_trading_panel():
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text()
    assert "c.external_symbol IN (" in installer
    for symbol in ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT"):
        assert f"'{symbol}'" in installer
    assert '"$source_cursor_count" == "5"' in installer

def test_native_cutover_waits_for_transitional_source_and_records_exact_failure():
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text()
    assert (
        "systemctl show cripta-native-public-trade-source.service "
        "--value --property=ActiveState"
    ) in installer
    assert "activating|reloading)" in installer
    assert (
        "native MarketSource writer not active during release proof:"
        in installer
    )
    assert "LAST_FAILED_RELEASE" in installer
    assert "last_error_line" in installer
    assert "last_error_command" in installer
    assert "trap 'record_err" in installer
    assert 'rm -f "$STATE_ROOT/LAST_FAILED_RELEASE"' in installer

def test_native_cutover_retries_transient_status_wrapper_failure():
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text()
    assert 'if ! source_session="$(runuser -u cripta -- python3 -c' in installer
    assert 'if ! source_commit_seen="$(runuser -u cripta -- python3 -c' in installer
    assert 'source_session=""' in installer
    assert 'source_commit_seen=""' in installer
    assert "for _attempt in {1..60}; do" in installer
    assert "independent native source five-symbol fresh cursor proof missing" in installer

def test_native_cutover_inline_status_python_compiles():
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text()
    prefixes = (
        'if ! source_session="$(runuser -u cripta -- python3 -c \'',
        'if ! source_commit_seen="$(runuser -u cripta -- python3 -c \'',
    )
    snippets = []
    for prefix in prefixes:
        start = installer.index(prefix) + len(prefix)
        end = installer.index(
            "\n  ' /var/lib/cripta/native_public_trade_source/status.json)",
            start,
        )
        snippet = installer[start:end]
        assert snippet.startswith("\nimport json,sys\ntry:\n")
        snippets.append(snippet.lstrip("\n"))
    assert len(snippets) == 2
    for index, snippet in enumerate(snippets):
        compile(snippet, f"native-status-inline-{index}", "exec")


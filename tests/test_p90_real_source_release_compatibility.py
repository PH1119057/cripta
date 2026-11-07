"""Cross-release recovery must retain origin; no fabricated compatibility."""
from __future__ import annotations

import copy
import hashlib
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry import p90l90_real_source_compatibility as compat
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalEntry
from bybit_workbench.universal_entry.p90l90_causal_transport_join import P90L90CausalTransportJoin
from bybit_workbench.universal_entry.p90l90_entry_restart_checkpoint import entry_restart_snapshot
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.p90l90_real_open_restart import (
    inspect_p90_real_open_restart_epoch,
)

ORIGIN = "a" * 40
LOADED = "b" * 40
EPOCH = "p90-real-epoch-" + "c" * 40
AT = datetime(2026, 11, 6, 0, 0, tzinfo=UTC)
SYMBOLS = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")


def bundles():
    return tuple(SimpleNamespace(
        card=SimpleNamespace(strategy_id=name, strategy_version=VERSION,
                             strategy_config_fingerprint="config-" + name),
        activation=SimpleNamespace(enabled=True, strategy_id=name, activation_id="act-" + name),
        entry_plan=SimpleNamespace(strategy_activation_id="act-" + name,
                                   entry_plan_fingerprint="entry-" + name),
        exit_plan=SimpleNamespace(exit_plan_fingerprint="exit-" + name),
    ) for name in (MAIN_ID, SUB_ID))


def checkpoints():
    rows = []
    for symbol in SYMBOLS:
        join = P90L90CausalTransportJoin(symbol=symbol, closed_5m_at=AT, closed_15m_at=AT,
                                       last_emitted_trade_at=AT,
                                       last_emitted_trade_sequence=100,
                                       last_emitted_exec_ids={"exec-" + symbol})
        entry = P90L90CausalEntry(previous_at=AT, previous_price=Decimal("100"))
        rows.append((symbol, join.snapshot(), entry_restart_snapshot(entry), ORIGIN, 3, ORIGIN))
    return rows


class SourceDB:
    def __init__(self, *, count=1):
        self.epoch = (ORIGIN, "ACTIVE")
        self.checkpoints = checkpoints()
        self.attestations = []
        self.count = count
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if "position_id,state,strategy_id,symbol,trading_account_id" in sql:
            rows = [("pos-" + str(i), "OPEN", MAIN_ID, "LTCUSDT", "account-" + str(i))
                    for i in range(self.count)]
        elif "FROM strategy_entry.p90l90_native_sequence_exec_ids" in sql:
            # Every fixture checkpoint is a complete legacy V1 exact-ID set.
            # The new sidecar must be empty until a locked writer migrates it;
            # compatibility is SELECT-only and never bootstraps a journal.
            assert "GROUP BY exchange_sequence" in sql
            return SimpleNamespace(fetchall=lambda: [])
        elif "count(*)" in sql:
            return SimpleNamespace(fetchone=lambda: (0,))
        elif "p90l90_real_source_release_compatibility" in sql:
            rows = self.attestations
        elif "FROM strategy_entry.p90l90_real_source_epochs" in sql:
            if "SELECT epoch_id,source_commit" in sql:
                rows = [(EPOCH, self.epoch[0])]
            else:
                return SimpleNamespace(fetchone=lambda: self.epoch)
        elif "p90l90_real_causal_checkpoints" in sql:
            if "generation,writer_release_commit" in sql:
                rows = self.checkpoints
            else:
                rows = [(r[0], r[1], r[3]) for r in self.checkpoints]
        else:
            raise AssertionError(sql)
        return SimpleNamespace(fetchall=lambda: rows)


def attest(db):
    evidence = compat.checkpoint_evidence(db, epoch_id=EPOCH, origin=ORIGIN,
                                          approved=frozenset({ORIGIN}))
    db.attestations = [(LOADED, ORIGIN, compat.CONTRACT_VERSION,
                        compat.consumer_contract_digest(), compat.plan_pair_digest(bundles()),
                        fingerprint(evidence), evidence)]


@pytest.mark.parametrize("count", [1, 2, 5, 60])
def test_actual_startup_selector_accepts_registered_release_without_relabel(count):
    db = SourceDB(count=count)
    attest(db)
    before = copy.deepcopy(db.checkpoints)
    assert inspect_p90_real_open_restart_epoch(
        db, source_commit=LOADED, bundles=bundles(),
    ) == EPOCH
    identity = compat.inspect_real_source_identity(
        db, epoch_id=EPOCH, loaded_commit=LOADED, bundles=bundles(),
    )
    assert identity.origin_commit == ORIGIN and identity.loaded_commit == LOADED
    assert identity.approved_producers == {ORIGIN, LOADED}
    assert db.checkpoints == before and db.epoch == (ORIGIN, "ACTIVE")
    assert all(sql.lstrip().startswith("SELECT") for sql in db.sql)


def test_new_release_is_not_approved_by_valid_sha_or_ancestry_alone():
    db = SourceDB()
    with pytest.raises(compat.P90RealSourceCompatibilityUnverified,
                       match="RELEASE_NOT_REGISTERED"):
        inspect_p90_real_open_restart_epoch(db, source_commit=LOADED, bundles=bundles())


def test_reviewed_historical_producer_keeps_own_digest_without_relabelling():
    db = SourceDB()
    attest(db)
    old = list(copy.deepcopy(db.attestations[0]))
    old[0], old[3] = next(iter(compat._REVIEWED_HISTORICAL_CONSUMERS.items()))
    db.attestations.insert(0, tuple(old))
    before = copy.deepcopy(db.attestations)
    identity = compat.inspect_real_source_identity(
        db, epoch_id=EPOCH, loaded_commit=LOADED, bundles=bundles(),
    )
    assert old[0] in identity.approved_producers
    assert db.attestations == before
    assert all(sql.lstrip().startswith("SELECT") for sql in db.sql)


@pytest.mark.parametrize("defect", ["unknown-release", "wrong-digest", "loaded-release"])
def test_historical_allowlist_cannot_approve_unreviewed_or_loaded_code(defect):
    db = SourceDB()
    attest(db)
    old = list(copy.deepcopy(db.attestations[0]))
    old[0], old[3] = next(iter(compat._REVIEWED_HISTORICAL_CONSUMERS.items()))
    if defect == "unknown-release":
        old[0] = "d" * 40
    elif defect == "wrong-digest":
        old[3] = "0" * 64
    else:
        # Old code cannot certify the executable that is actually loaded.
        db.attestations = []
    db.attestations.insert(0, tuple(old))
    with pytest.raises(compat.P90RealSourceCompatibilityUnverified):
        compat.inspect_real_source_identity(
            db, epoch_id=EPOCH,
            loaded_commit=old[0] if defect == "loaded-release" else LOADED,
            bundles=bundles(),
        )


@pytest.mark.parametrize("bad", [
    "origin", "release", "contract", "consumer", "plans", "digest", "snapshot",
    "retired", "missing-symbol", "mixed-origin", "unknown-writer", "invalid-generation",
])
def test_incomplete_or_incompatible_attestation_never_grants_restart(bad):
    db = SourceDB()
    attest(db)
    row = list(db.attestations[0])
    indices = {"origin": 1, "release": 0, "contract": 2, "consumer": 3,
               "plans": 4, "digest": 5}
    if bad in indices:
        row[indices[bad]] = "0" * (64 if bad in {"consumer", "plans", "digest"} else 40)
    elif bad == "snapshot":
        row[6][0]["generation"] += 1
    elif bad == "retired":
        db.epoch = (ORIGIN, "RETIRED")
    elif bad == "missing-symbol":
        db.checkpoints.pop()
    else:
        r = list(db.checkpoints[0])
        r[{"mixed-origin": 3, "unknown-writer": 5, "invalid-generation": 4}[bad]] = (
            -1 if bad == "invalid-generation" else "f" * 40
        )
        db.checkpoints[0] = tuple(r)
    db.attestations[0] = tuple(row)
    with pytest.raises(compat.P90RealSourceCompatibilityUnverified):
        compat.inspect_real_source_identity(db, epoch_id=EPOCH,
                                            loaded_commit=LOADED, bundles=bundles())


def test_same_release_keeps_existing_no_registration_contract():
    identity = compat.inspect_real_source_identity(SourceDB(), epoch_id=EPOCH,
                                                    loaded_commit=ORIGIN, bundles=())
    assert identity.origin_commit == ORIGIN
    assert identity.approved_producers == {ORIGIN}


@pytest.mark.parametrize("field,value", [("source_commit", "f" * 40),
                                        ("writer_release_commit", "f" * 40),
                                        ("generation", -1), ("checkpoint", {})])
def test_self_consistent_checksum_cannot_approve_foreign_or_corrupt_checkpoint(field, value):
    db = SourceDB()
    attest(db)
    row = list(db.attestations[0])
    row[6][0][field] = value
    row[5] = fingerprint(row[6])
    db.attestations[0] = tuple(row)
    with pytest.raises(compat.P90RealSourceCompatibilityUnverified):
        compat.inspect_real_source_identity(db, epoch_id=EPOCH,
                                            loaded_commit=LOADED, bundles=bundles())


def test_current_checkpoint_writer_may_advance_only_to_registered_producer():
    db = SourceDB()
    attest(db)
    db.checkpoints = [(*r[:4], r[4] + 1, LOADED) for r in db.checkpoints]
    assert compat.inspect_real_source_identity(
        db, epoch_id=EPOCH, loaded_commit=LOADED, bundles=bundles(),
    ).origin_commit == ORIGIN


def test_contract_digest_detects_changed_consumer_bytes(tmp_path):
    for folder in ("universal_entry", "universal_exit"):
        p = tmp_path / "src/bybit_workbench" / folder / "algorithm.py"
        p.parent.mkdir(parents=True)
        p.write_text("original")
    for name in ("strategy_position.py", "exchange_position_identity.py",
                 "domain/models.py", "market_data/shared_closed_candles.py"):
        p = tmp_path / "src/bybit_workbench" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("original")
    before = compat.consumer_contract_digest(tmp_path)
    (tmp_path / "src/bybit_workbench/universal_exit/algorithm.py").write_text("changed")
    assert compat.consumer_contract_digest(tmp_path) != before


def test_v2_reviewed_bugfix_delta_is_exact_and_hash_bound(tmp_path, monkeypatch):
    package = "src/bybit_workbench"
    origin = tmp_path / ("a" * 40)
    current = tmp_path / ("b" * 40)
    for root in (origin, current):
        for folder in ("universal_entry", "universal_exit"):
            p = root / package / folder / "algorithm.py"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("original")
        for name in (
            "strategy_position.py", "exchange_position_identity.py",
            "domain/models.py", "market_data/shared_closed_candles.py",
        ):
            p = root / package / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("original")
    changed = current / package / "universal_exit/algorithm.py"
    changed.write_text("reviewed-bugfix")
    monkeypatch.setattr(
        compat,
        "REVIEWED_BUGFIX_CONSUMER_SHA256",
        {
            "universal_exit/algorithm.py":
                hashlib.sha256(b"reviewed-bugfix").hexdigest(),
        },
    )
    compat.verify_reviewed_bugfix_delta(
        current_root=current, origin_root=origin,
    )

    changed.write_text("different-bytes")
    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="REVIEWED_BUGFIX_CONTENT_DRIFT",
    ):
        compat.verify_reviewed_bugfix_delta(
            current_root=current, origin_root=origin,
        )

    changed.write_text("reviewed-bugfix")
    (current / package / "universal_entry/algorithm.py").write_text(
        "unreviewed-extra-change"
    )
    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="UNREVIEWED_CONSUMER_DELTA",
    ):
        compat.verify_reviewed_bugfix_delta(
            current_root=current, origin_root=origin,
        )


def test_registration_refuses_without_explicit_actor_transaction_and_off_gate():
    with pytest.raises(compat.P90RealSourceCompatibilityUnverified,
                       match="REGISTRATION_TRANSACTION_REQUIRED"):
        compat.register_real_source_compatibility(
            SourceDB(), epoch_id=EPOCH, loaded_commit=LOADED, bundles=bundles(),
            current_root=__import__("pathlib").Path(LOADED),
            origin_root=__import__("pathlib").Path(ORIGIN),
        )

def test_reviewed_delta_can_use_exact_local_git_origin_without_retained_release(
    tmp_path, monkeypatch,
):
    repo = tmp_path / "source_checkout"
    package = repo / "src/bybit_workbench"
    for folder in ("universal_entry", "universal_exit"):
        p = package / folder / "algorithm.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("original", encoding="utf-8")
    for name in (
        "strategy_position.py",
        "exchange_position_identity.py",
        "domain/models.py",
        "market_data/shared_closed_candles.py",
    ):
        p = package / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("original", encoding="utf-8")

    subprocess.run(("git", "-C", str(repo), "init"), check=True, capture_output=True)
    subprocess.run(("git", "-C", str(repo), "add", "."), check=True, capture_output=True)
    subprocess.run(
        (
            "git", "-C", str(repo),
            "-c", "user.name=Cripta Test",
            "-c", "user.email=cripta-test@example.invalid",
            "commit", "-m", "origin",
        ),
        check=True,
        capture_output=True,
    )
    origin = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    current = tmp_path / ("b" * 40)
    for folder in ("universal_entry", "universal_exit"):
        p = current / "src/bybit_workbench" / folder / "algorithm.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("original", encoding="utf-8")
    for name in (
        "strategy_position.py",
        "exchange_position_identity.py",
        "domain/models.py",
        "market_data/shared_closed_candles.py",
    ):
        p = current / "src/bybit_workbench" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("original", encoding="utf-8")
    changed = current / "src/bybit_workbench/universal_exit/algorithm.py"
    changed.write_text("reviewed-bugfix", encoding="utf-8")
    monkeypatch.setattr(
        compat,
        "REVIEWED_BUGFIX_CONSUMER_SHA256",
        {
            "universal_exit/algorithm.py":
                hashlib.sha256(b"reviewed-bugfix").hexdigest(),
        },
    )

    compat.verify_reviewed_bugfix_delta_from_git(
        current_root=current,
        source_checkout=repo,
        origin_commit=origin,
    )

    changed.write_text("unreviewed-content", encoding="utf-8")
    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="REVIEWED_BUGFIX_CONTENT_DRIFT",
    ):
        compat.verify_reviewed_bugfix_delta_from_git(
            current_root=current,
            source_checkout=repo,
            origin_commit=origin,
        )

    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="ORIGIN_GIT_COMMIT",
    ):
        compat.verify_reviewed_bugfix_delta_from_git(
            current_root=current,
            source_checkout=repo,
            origin_commit="f" * 40,
        )



# Regression for the exact 2026-11-07 production fault:
# old valid attestation rows from two executable release families were
# incorrectly compared to the currently loaded consumer digest.
@pytest.mark.parametrize(("release", "digest"), [
    ("3682292450a6f6e2f49f67e30e473ab035a0e27d",
     "e455220878f19f5d1fd530abdb98b7d379a67fbe5a911f378747b8d1301979a9"),
    ("aa65991b7e704fdbd87b8f3ca158add94cb76d53",
     "e455220878f19f5d1fd530abdb98b7d379a67fbe5a911f378747b8d1301979a9"),
    ("1fc88f96f110337bfe575c4caaba48b5cdbaa134",
     "e455220878f19f5d1fd530abdb98b7d379a67fbe5a911f378747b8d1301979a9"),
    ("0044d4eabd89b762641e7468e56161d707b07ea5",
     "e455220878f19f5d1fd530abdb98b7d379a67fbe5a911f378747b8d1301979a9"),
    ("741984632f5b613fcc2df84a21dbd1ac5e4fd3f9",
     "71458d52d543ff91350d51999bbefcfa0b8e7fd1a8b7feee4e9c9edbb6367c8e"),
    ("ef2ac6460bf5bd75f56fe7b6a112b3852fec271d",
     "71458d52d543ff91350d51999bbefcfa0b8e7fd1a8b7feee4e9c9edbb6367c8e"),
])
def test_exact_historical_compatibility_rows_not_relabelled_as_loaded(
    release, digest,
):
    assert compat._REVIEWED_HISTORICAL_CONSUMERS[release] == digest
    db = SourceDB()
    attest(db)  # Explicitly registered current release, no inferred permission.
    historic = list(copy.deepcopy(db.attestations[0]))
    historic[0], historic[3] = release, digest
    db.attestations.insert(0, tuple(historic))
    saved = copy.deepcopy(db.attestations)
    verified = compat.inspect_real_source_identity(
        db, epoch_id=EPOCH, loaded_commit=LOADED, bundles=bundles(),
    )
    assert release in verified.approved_producers
    assert db.attestations == saved
    # Unregistered executable must not become compatible merely because a
    # genuine historical row exists.
    with pytest.raises(compat.P90RealSourceCompatibilityUnverified,
                       match="RELEASE_NOT_REGISTERED"):
        compat.inspect_real_source_identity(
            db, epoch_id=EPOCH, loaded_commit="9" * 40, bundles=bundles(),
        )
    historic[3] = "0" * 64
    db.attestations[0] = tuple(historic)
    with pytest.raises(compat.P90RealSourceCompatibilityUnverified,
                       match="ATTESTATION_CORRUPT_OR_INCOMPATIBLE"):
        compat.inspect_real_source_identity(
            db, epoch_id=EPOCH, loaded_commit=LOADED, bundles=bundles(),
        )


def test_reviewed_current_ws_reconnect_delta_is_hash_bound():
    # The historical origin is immutable; this one extra source module was
    # separately reviewed. Its current exact bytes must remain pinned.
    relative = "src/bybit_workbench/universal_entry/trade_mirror.py"
    # Runtime releases use immutable Git blob bytes. Windows checkout may
    # convert LF to CRLF and therefore cannot be used for content attestation.
    source_bytes = subprocess.check_output(["git", "show", f"HEAD:{relative}"])
    assert hashlib.sha256(source_bytes).hexdigest() == (
        compat.REVIEWED_BUGFIX_CONSUMER_SHA256["universal_entry/trade_mirror.py"]
    )


@pytest.mark.parametrize(("relative", "expected"), [
    ("universal_entry/p90l90_real_bounded_native_restart.py",
     "165bed9a2a64e45b62c02077aac2937d31e9b27221e4881aaf2efdf4f71601bc"),
    ("universal_entry/p90l90_real_restart_recovery.py",
     "6ed2cd670e83dd429cb8b293843dc0c482fc823ca8636937db8bf62646af2431"),
])
def test_owner_reviewed_recovery_delta_is_immutable_git_blob(
    relative, expected,
):
    # Git source bytes, not Windows CRLF-transformed checkout bytes.
    blob = subprocess.check_output(
        ["git", "show", f"HEAD:src/bybit_workbench/{relative}"],
    )
    assert hashlib.sha256(blob).hexdigest() == expected
    if relative.endswith("/p90l90_real_restart_recovery.py"):
        # This original source-recovery adapter was explicitly excluded from
        # the causal consumer contract inventory BEFORE this owner change.
        # Keep that ownership boundary; the release still pins its Git bytes.
        assert "p90l90_real_restart_recovery.py" in compat._ADAPTERS
        assert relative not in compat.REVIEWED_BUGFIX_CONSUMER_SHA256
    else:
        assert compat.REVIEWED_BUGFIX_CONSUMER_SHA256[relative] == expected

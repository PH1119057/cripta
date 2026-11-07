"""Reviewed REAL data-origin / executable-release compatibility, never an ARM.

The Observer only reads attestations. Registration is a separate operator
operation, preserving every existing epoch/checkpoint and the native-gap gates.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fingerprint import canonical_json, fingerprint
from .p90l90_causal_transport_join import P90L90CausalTransportJoin
from .p90l90_legacy_strategy import MAIN_ID, SUB_ID
from .p90l90_native_sequence_id_journal import P90NativeSequenceIdJournal
from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION

CONTRACT_VERSION = "P90_REAL_SOURCE_RELEASE_COMPATIBILITY_V2"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_EPOCH = re.compile(r"p90-real-epoch-[0-9a-f]{40}\Z")
_SYMBOLS = frozenset().union(*ALLOWED_SYMBOLS.values())
_TABLE = "strategy_entry.p90l90_real_source_release_compatibility"
# Exact historical producers reviewed for the Entry -> Exit ownership fix.
# Their evidence remains bound to their own code, not rewritten to current code.
# This NEVER approves the loaded executable using an older producer's digest.
_REVIEWED_HISTORICAL_CONSUMERS = dict.fromkeys((
    "3682292450a6f6e2f49f67e30e473ab035a0e27d",
    "aa65991b7e704fdbd87b8f3ca158add94cb76d53",
    "1fc88f96f110337bfe575c4caaba48b5cdbaa134",
    "0044d4eabd89b762641e7468e56161d707b07ea5",
), "e455220878f19f5d1fd530abdb98b7d379a67fbe5a911f378747b8d1301979a9")

# Exact Git-verified consumer inventories from the subsequent deployed releases.
# The durable compatibility rows are evidence about THOSE historical releases;
# they must not be compared against the newly loaded release's consumer digest.
# Verified using all 93 inventory files at the immutable Git SHAs, not copied
# from PostgreSQL or inferred from ancestry. Existing rows are never rewritten.
_REVIEWED_HISTORICAL_CONSUMERS.update({
    "741984632f5b613fcc2df84a21dbd1ac5e4fd3f9":
        "71458d52d543ff91350d51999bbefcfa0b8e7fd1a8b7feee4e9c9edbb6367c8e",
    "ef2ac6460bf5bd75f56fe7b6a112b3852fec271d":
        "71458d52d543ff91350d51999bbefcfa0b8e7fd1a8b7feee4e9c9edbb6367c8e",
})

# These wiring adapters are reviewed in the release diff. All Strategy,
# Entry/Exit algorithms, codecs, contracts, account and private-proof modules
# below remain byte-identical to the retained origin release.
_ADAPTERS = frozenset({
    "p90l90_real_source_compatibility.py",
    "p90l90_real_open_restart.py",
    "p90l90_real_restart_recovery.py",
})

# Reviewed bugfix delta from the retained origin 08344a12... into the
# integrated stabilization release.  These are consumer/transport fixes, not
# Strategy/EntryPlan/ExitPlan policy changes.  Registration accepts exactly
# this delta and exact bytes; any other consumer change remains fail-closed.
REVIEWED_BUGFIX_CONSUMER_SHA256 = {
    "universal_entry/p90l90_atomic_paper_checkpoint.py":
        "737fdd8020c255b8aee7616c4dac2050fc415cd25c40db574f41ded332d9e5d0",
    "universal_entry/p90l90_atomic_real_epoch_checkpoint.py":
        "5836e301977d6f84131f583fe067d5b766fb96bfcd4ab6508826c53a8cad280c",
    "universal_entry/p90l90_causal_transport_join.py":
        "7905ac5ac1bbb581ad0ebca17d32adedbe10abc756ec6e493d182b1ad5efbb3b",
    "universal_entry/p90l90_causal_transport_store.py":
        "5fe47593b7672ced88980e664c6c098541392988c6cfb1c53e7381f34d7fa5b0",
    "universal_entry/p90l90_native_sequence_id_journal.py":
        "d04dbd48d07f66bfd33d999886cba9a00a443903d7034c3ff8b25523447fefec",
    "universal_entry/p90l90_observer_paper_consumer.py":
        "788c4b964cbb88d114882f6ff9e55f1e32b7c3468352c36f824132d7fd155e53",
    "universal_entry/p90l90_real_bounded_native_restart.py":
        "ade34d3ec160b249286ec9946edb7c312ccc359a89f8a5f96efc05b630b735ad",
    "universal_entry/p90l90_real_observer_runtime.py":
        "8d93f93b29446dab448e172a36ea2d74260ec6526022d92785c42e70e762cee9",
    "universal_entry/p90l90_real_shared_candle_ledger.py":
        "67baca3d0e8e73be61859a053052c4691f108a1adb98f8aa8dccc8069a6e1401",
    "universal_entry/p90l90_shared_candle_bridge.py":
        "01bfb15924c9fcae07b70568e79327d5426e015f5879c8fc8bf7dd4431f0ba2f",
    "universal_exit/p90l90_real_exit_port.py":
        "3a245cba87891a53b05c81ec86bc9caa11d7b93893743717a73aed40dc0ed506",
    "universal_exit/p90l90_real_mfe.py":
        "53bd13487b70c632a724caeebd257fcd04dd9c92a993f9e0072f5dd2750be4ed",
    "universal_exit/p90l90_real_mfe_ledger.py":
        "e85fb4c788ffa1a178a4b887e3e06593871e267573fc87b4ad221424bad26fab",
    # Reviewed WS reconnect diagnostic: gap counters are scoped to the
    # NEW transport session; the old durable native cursor is not recovered
    # or forgiven. This exact delta is tested independently in trade_mirror.
    "universal_entry/trade_mirror.py":
        "228a92991c4ab3bd31506bd40bfc55e9a30a237cdda4f23921005586808d4c1a",
    "universal_exit/p90l90_real_runtime_ports.py":
        "e471b82d1e6e84e105cb13c7094974799dfae145e9af1b68f389d08f93a57970",
}


class P90RealSourceCompatibilityUnverified(RuntimeError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise P90RealSourceCompatibilityUnverified("P90_REAL_SOURCE_COMPATIBILITY_" + reason)


@dataclass(frozen=True, slots=True)
class P90RealSourceIdentity:
    epoch_id: str
    origin_commit: str
    loaded_commit: str
    approved_producers: frozenset[str]


def _consumer_inventory(root: Path) -> tuple[Path, dict[str, Path]]:
    package = root / "src/bybit_workbench"
    paths: list[Path] = []
    for directory in ("universal_entry", "universal_exit"):
        found = sorted((package / directory).glob("*.py"))
        _require(bool(found), "CONSUMER_FILES_MISSING")
        paths.extend(p for p in found if p.name not in _ADAPTERS)
    for name in ("strategy_position.py", "exchange_position_identity.py",
                 "domain/models.py", "market_data/shared_closed_candles.py"):
        paths.append(package / name)
    inventory: dict[str, Path] = {}
    for item in paths:
        _require(item.is_file() and not item.is_symlink(), "CONSUMER_FILE_UNVERIFIED")
        relative = item.relative_to(package).as_posix()
        _require(relative not in inventory, "CONSUMER_FILE_DUPLICATE")
        inventory[relative] = item
    return package, inventory


def consumer_contract_digest(root: Path | None = None) -> str:
    """Exact loaded-consumer bytes, independently bound to contract V2."""
    root = root or Path(__file__).resolve().parents[3]
    _package, inventory = _consumer_inventory(root)
    digest = hashlib.sha256(CONTRACT_VERSION.encode())
    for relative in sorted(inventory):
        digest.update(relative.encode() + b"\0")
        digest.update(hashlib.sha256(inventory[relative].read_bytes()).digest())
    return digest.hexdigest()


def verify_reviewed_bugfix_delta(*, current_root: Path, origin_root: Path) -> None:
    """Allow only the reviewed stabilization delta; no ancestry shortcut."""
    _current_package, current = _consumer_inventory(current_root)
    _origin_package, origin = _consumer_inventory(origin_root)
    changed = {
        relative
        for relative in set(current) | set(origin)
        if relative not in current
        or relative not in origin
        or current[relative].read_bytes() != origin[relative].read_bytes()
    }
    _require(
        changed == set(REVIEWED_BUGFIX_CONSUMER_SHA256),
        "UNREVIEWED_CONSUMER_DELTA",
    )
    for relative, expected in REVIEWED_BUGFIX_CONSUMER_SHA256.items():
        item = current.get(relative)
        if item is None:
            raise P90RealSourceCompatibilityUnverified(
                "P90_REAL_SOURCE_COMPATIBILITY_REVIEWED_BUGFIX_FILE_MISSING"
            )
        actual = hashlib.sha256(item.read_bytes()).hexdigest()
        _require(actual == expected, "REVIEWED_BUGFIX_CONTENT_DRIFT")


def _git_bytes(source_checkout: Path, *args: str) -> bytes:
    _require(
        source_checkout.is_dir()
        and (source_checkout / ".git").is_dir()
        and not source_checkout.is_symlink(),
        "ORIGIN_GIT_REPOSITORY_INVALID",
    )
    result = subprocess.run(
        ("git", "-C", str(source_checkout), *args),
        capture_output=True,
        check=False,
    )
    _require(result.returncode == 0, "ORIGIN_GIT_READ_FAILED")
    return result.stdout


def _consumer_inventory_from_git(
    source_checkout: Path, origin_commit: str,
) -> dict[str, bytes]:
    """Read exact historical consumer bytes from local Git; never fetch/network."""
    _require(_SHA.fullmatch(origin_commit) is not None, "ORIGIN_GIT_COMMIT_INVALID")
    try:
        commit_probe = subprocess.run(
            ("git", "-C", str(source_checkout), "cat-file", "-t", origin_commit),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise P90RealSourceCompatibilityUnverified(
            "P90_REAL_SOURCE_COMPATIBILITY_ORIGIN_GIT_READ_FAILED"
        ) from exc
    _require(commit_probe.returncode == 0, "ORIGIN_GIT_COMMIT_MISSING")
    _require(commit_probe.stdout.strip() == b"commit", "ORIGIN_GIT_COMMIT_INVALID")
    package_prefix = "src/bybit_workbench/"
    shared = {
        "strategy_position.py",
        "exchange_position_identity.py",
        "domain/models.py",
        "market_data/shared_closed_candles.py",
    }
    raw = _git_bytes(
        source_checkout,
        "ls-tree",
        "-r",
        "--name-only",
        origin_commit,
        "--",
        package_prefix + "universal_entry",
        package_prefix + "universal_exit",
        *(package_prefix + name for name in sorted(shared)),
    )
    inventory: dict[str, bytes] = {}
    for full in raw.decode("utf-8").splitlines():
        if not full.startswith(package_prefix):
            continue
        relative = full[len(package_prefix):]
        path = Path(relative)
        include = (
            (
                path.parent.as_posix() in {"universal_entry", "universal_exit"}
                and path.suffix == ".py"
                and path.name not in _ADAPTERS
            )
            or relative in shared
        )
        if not include:
            continue
        _require(relative not in inventory, "CONSUMER_FILE_DUPLICATE")
        inventory[relative] = _git_bytes(
            source_checkout, "show", f"{origin_commit}:{full}"
        )
    _require(
        any(name.startswith("universal_entry/") for name in inventory)
        and any(name.startswith("universal_exit/") for name in inventory)
        and shared.issubset(inventory),
        "ORIGIN_GIT_CONSUMER_FILES_MISSING",
    )
    return inventory


def verify_reviewed_bugfix_delta_from_git(
    *, current_root: Path, source_checkout: Path, origin_commit: str,
) -> None:
    """Compare current consumer bytes to the exact active-epoch Git commit."""
    _current_package, current = _consumer_inventory(current_root)
    origin = _consumer_inventory_from_git(source_checkout, origin_commit)
    changed = {
        relative
        for relative in set(current) | set(origin)
        if relative not in current
        or relative not in origin
        or current[relative].read_bytes() != origin[relative]
    }
    _require(
        changed == set(REVIEWED_BUGFIX_CONSUMER_SHA256),
        "UNREVIEWED_CONSUMER_DELTA",
    )
    for relative, expected in REVIEWED_BUGFIX_CONSUMER_SHA256.items():
        item = current.get(relative)
        if item is None:
            raise P90RealSourceCompatibilityUnverified(
                "P90_REAL_SOURCE_COMPATIBILITY_REVIEWED_BUGFIX_FILE_MISSING"
            )
        _require(
            hashlib.sha256(item.read_bytes()).hexdigest() == expected,
            "REVIEWED_BUGFIX_CONTENT_DRIFT",
        )


def plan_pair_digest(bundles: tuple[object, ...]) -> str:
    rows = []
    for raw in bundles:
        b: Any = raw
        _require(b.card.strategy_id in (MAIN_ID, SUB_ID)
                 and b.card.strategy_version == VERSION
                 and b.activation.enabled
                 and b.activation.strategy_id == b.card.strategy_id
                 and b.entry_plan.strategy_activation_id == b.activation.activation_id,
                 "PLAN_PAIR_UNVERIFIED")
        rows.append({
            "strategy_id": b.card.strategy_id,
            "strategy_version": b.card.strategy_version,
            "activation_id": b.activation.activation_id,
            "config": b.card.strategy_config_fingerprint,
            "entry": b.entry_plan.entry_plan_fingerprint,
            "exit": b.exit_plan.exit_plan_fingerprint,
        })
    _require(len(rows) == 2 and {r["strategy_id"] for r in rows} == {MAIN_ID, SUB_ID},
             "EXACT_TWO_PLANS_REQUIRED")
    return fingerprint(sorted(rows, key=lambda r: r["strategy_id"]))


def checkpoint_evidence(connection: Any, *, epoch_id: str, origin: str,
                        approved: frozenset[str]) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT symbol,checkpoint_json,entry_checkpoint_json,source_commit,
                  generation,writer_release_commit
             FROM strategy_entry.p90l90_real_causal_checkpoints
            WHERE epoch_id=%s ORDER BY symbol""", (epoch_id,),
    ).fetchall()
    _require(len(rows) == 5 and {r[0] for r in rows} == _SYMBOLS,
             "EXACT_FIVE_CHECKPOINTS_REQUIRED")
    result = []
    for symbol, snapshot, entry, commit, generation, writer in rows:
        _require(commit == origin and (writer or origin) in approved
                 and type(generation) is int and generation >= 0
                 and isinstance(entry, dict), "CHECKPOINT_PROVENANCE_UNVERIFIED")
        join = P90L90CausalTransportJoin.restore(snapshot)
        P90NativeSequenceIdJournal(
            connection, mode="REAL", source_epoch_id=epoch_id,
            source_commit=origin, symbol=symbol,
        ).verify(join)
        _require(join.symbol == symbol, "CHECKPOINT_SYMBOL_DRIFT")
        result.append({"symbol": symbol, "checkpoint": snapshot, "entry": entry,
                       "source_commit": commit, "generation": generation,
                       "writer_release_commit": writer})
    return result


def inspect_real_source_identity(connection: Any, *, epoch_id: str,
                                 loaded_commit: str,
                                 bundles: tuple[object, ...]) -> P90RealSourceIdentity:
    """SELECT-only; native replay, private inventory and ARM remain separate."""
    _require(_SHA.fullmatch(loaded_commit) is not None
             and _EPOCH.fullmatch(epoch_id) is not None, "IDENTITY_INVALID")
    row = connection.execute(
        """SELECT source_commit,status FROM strategy_entry.p90l90_real_source_epochs
            WHERE epoch_id=%s""", (epoch_id,),
    ).fetchone()
    _require(row is not None and row[1] == "ACTIVE"
             and isinstance(row[0], str) and _SHA.fullmatch(row[0]) is not None,
             "EPOCH_NOT_ACTIVE")
    origin = row[0]
    if origin == loaded_commit:
        return P90RealSourceIdentity(epoch_id, origin, loaded_commit, frozenset({origin}))
    rows = connection.execute(
        f"""SELECT loaded_commit,origin_commit,contract_version,consumer_digest,
                   plan_pair_digest,checkpoint_digest,checkpoint_evidence
              FROM {_TABLE} WHERE epoch_id=%s ORDER BY loaded_commit""", (epoch_id,),
    ).fetchall()
    contract = consumer_contract_digest()
    plans = plan_pair_digest(bundles)
    approved = {origin}
    recorded_producers = {origin, *(r[0] for r in rows)}
    for release, saved_origin, version, code, pair, checksum, evidence in rows:
        _require(isinstance(release, str) and _SHA.fullmatch(release) is not None
                 and saved_origin == origin and version == CONTRACT_VERSION
                 and (
                     code == contract
                     or (release != loaded_commit
                         and _REVIEWED_HISTORICAL_CONSUMERS.get(release) == code)
                 ) and pair == plans
                 and isinstance(checksum, str) and _DIGEST.fullmatch(checksum) is not None
                 and isinstance(evidence, list) and len(evidence) == 5
                 and {x.get("symbol") for x in evidence if isinstance(x, dict)} == _SYMBOLS
                 and fingerprint(evidence) == checksum,
                 "ATTESTATION_CORRUPT_OR_INCOMPATIBLE")
        for item in evidence:
            _require(isinstance(item, dict)
                     and set(item) == {"symbol", "checkpoint", "entry", "source_commit",
                                       "generation", "writer_release_commit"}
                     and item["source_commit"] == origin
                     and (item["writer_release_commit"] or origin) in recorded_producers
                     and type(item["generation"]) is int and item["generation"] >= 0
                     and isinstance(item["entry"], dict)
                     and item["entry"].get("schema") == "P90L90_CAUSAL_ENTRY_RESTART_V1",
                     "ATTESTATION_CHECKPOINT_IDENTITY_INVALID")
            try:
                saved_join = P90L90CausalTransportJoin.restore(item["checkpoint"])
            except (ValueError, RuntimeError, TypeError, KeyError):
                raise P90RealSourceCompatibilityUnverified(
                    "P90_REAL_SOURCE_COMPATIBILITY_ATTESTATION_CHECKPOINT_CORRUPT"
                ) from None
            _require(saved_join.symbol == item["symbol"] and not saved_join.pending,
                     "ATTESTATION_CHECKPOINT_SOURCE_GAP")
        approved.add(release)
    _require(loaded_commit in approved, "RELEASE_NOT_REGISTERED")
    checkpoint_evidence(connection, epoch_id=epoch_id, origin=origin,
                        approved=frozenset(approved))
    return P90RealSourceIdentity(epoch_id, origin, loaded_commit, frozenset(approved))


def register_real_source_compatibility(
    connection: Any,
    *,
    epoch_id: str,
    loaded_commit: str,
    bundles: tuple[object, ...],
    current_root: Path,
    origin_root: Path | None = None,
    origin_git_checkout: Path | None = None,
) -> None:
    """Bounded preparation INSERT; Observer remains a read-only consumer."""
    _require(_SHA.fullmatch(loaded_commit) is not None
             and _EPOCH.fullmatch(epoch_id) is not None, "IDENTITY_INVALID")
    _require(getattr(getattr(getattr(connection, "info", None),
                             "transaction_status", None), "name", None) == "INTRANS",
             "REGISTRATION_TRANSACTION_REQUIRED")
    _require(connection.execute("SHOW transaction_isolation").fetchone() == ("serializable",),
             "REGISTRATION_SERIALIZABLE_REQUIRED")
    _require(current_root.resolve().name == loaded_commit, "CURRENT_RELEASE_PATH_INVALID")
    _require(connection.execute("SELECT session_user,current_user").fetchone()
             == ("cripta", "cripta"), "DB_ACTOR_INVALID")
    _require(connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'",
    ).fetchone() == (0,), "ENTRY_GATE_NOT_OFF")
    row = connection.execute(
        """SELECT source_commit,status FROM strategy_entry.p90l90_real_source_epochs
            WHERE epoch_id=%s FOR SHARE""", (epoch_id,),
    ).fetchone()
    _require(row is not None and row[1] == "ACTIVE"
             and isinstance(row[0], str) and _SHA.fullmatch(row[0]) is not None,
             "EPOCH_NOT_ACTIVE")
    origin = row[0]
    _require(
        (current_root / "INSTALLED_COMMIT").read_text().strip() == loaded_commit,
        "RETAINED_RELEASE_MARKER_INVALID",
    )
    _require(
        (origin_root is None) != (origin_git_checkout is None),
        "EXACT_ONE_ORIGIN_EVIDENCE_REQUIRED",
    )
    contract = consumer_contract_digest(current_root)
    if origin_root is not None:
        _require(
            origin_root.resolve().name == origin,
            "ORIGIN_RELEASE_PATH_INVALID",
        )
        _require(
            (origin_root / "INSTALLED_COMMIT").read_text().strip() == origin,
            "RETAINED_RELEASE_MARKER_INVALID",
        )
        verify_reviewed_bugfix_delta(
            current_root=current_root,
            origin_root=origin_root,
        )
    else:
        assert origin_git_checkout is not None
        verify_reviewed_bugfix_delta_from_git(
            current_root=current_root,
            source_checkout=origin_git_checkout,
            origin_commit=origin,
        )
    plans = plan_pair_digest(bundles)
    prior = connection.execute(
        f"SELECT loaded_commit FROM {_TABLE} WHERE epoch_id=%s", (epoch_id,),
    ).fetchall()
    approved = frozenset({origin, *(r[0] for r in prior)})
    evidence = checkpoint_evidence(connection, epoch_id=epoch_id,
                                   origin=origin, approved=approved)
    # Independently reconstruct native+Entry state against canonical candles.
    from .p90l90_atomic_real_epoch_checkpoint import P90L90AtomicRealEpochCheckpointStore
    from .p90l90_durable_paper_processor import P90L90DurablePaperProcessor
    from .p90l90_real_shared_candle_ledger import P90RealSharedCandleLedger

    ledger = P90RealSharedCandleLedger(connection, epoch_id=epoch_id, source_commit=origin)
    store = P90L90AtomicRealEpochCheckpointStore(connection, epoch_id=epoch_id,
                                               source_commit=origin)
    processor = P90L90DurablePaperProcessor(
        connection, source_commit=origin, atomic_checkpoint_store=store,
        bar_ledger=ledger, on_native_exit=lambda *_: None, on_closed_candle=lambda *_: None,
    )
    for symbol in sorted(_SYMBOLS):
        processor.recover(symbol=symbol)
    connection.execute(
        f"""INSERT INTO {_TABLE}(epoch_id,loaded_commit,origin_commit,contract_version,
                  consumer_digest,plan_pair_digest,checkpoint_digest,checkpoint_evidence)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT(epoch_id,loaded_commit) DO NOTHING""",
        (epoch_id, loaded_commit, origin, CONTRACT_VERSION, contract, plans,
         fingerprint(evidence), canonical_json(evidence)),
    )
    inspect_real_source_identity(connection, epoch_id=epoch_id,
                                 loaded_commit=loaded_commit, bundles=bundles)

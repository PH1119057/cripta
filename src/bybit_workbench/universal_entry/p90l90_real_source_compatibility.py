"""Reviewed REAL data-origin / executable-release compatibility, never an ARM.

The Observer only reads attestations. Registration is a separate operator
operation, preserving every existing epoch/checkpoint and the native-gap gates.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fingerprint import canonical_json, fingerprint
from .p90l90_causal_transport_join import P90L90CausalTransportJoin
from .p90l90_legacy_strategy import MAIN_ID, SUB_ID
from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION

CONTRACT_VERSION = "P90_REAL_SOURCE_RELEASE_COMPATIBILITY_V1"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_EPOCH = re.compile(r"p90-real-epoch-[0-9a-f]{40}\Z")
_SYMBOLS = frozenset().union(*ALLOWED_SYMBOLS.values())
_TABLE = "strategy_entry.p90l90_real_source_release_compatibility"
# These wiring adapters are reviewed in the release diff. All Strategy,
# Entry/Exit algorithms, codecs, contracts, account and private-proof modules
# below remain byte-identical to the retained origin release.
_ADAPTERS = frozenset({
    "p90l90_real_source_compatibility.py",
    "p90l90_real_open_restart.py",
    "p90l90_real_restart_recovery.py",
})


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


def consumer_contract_digest(root: Path | None = None) -> str:
    """Exact content proof, not Git ancestry or a caller's compatibility flag."""
    root = root or Path(__file__).resolve().parents[3]
    package = root / "src/bybit_workbench"
    paths: list[Path] = []
    for directory in ("universal_entry", "universal_exit"):
        found = sorted((package / directory).glob("*.py"))
        _require(bool(found), "CONSUMER_FILES_MISSING")
        paths.extend(p for p in found if p.name not in _ADAPTERS)
    for name in ("strategy_position.py", "exchange_position_identity.py",
                 "domain/models.py", "market_data/shared_closed_candles.py"):
        paths.append(package / name)
    digest = hashlib.sha256(CONTRACT_VERSION.encode())
    for path in sorted(paths):
        _require(path.is_file() and not path.is_symlink(), "CONSUMER_FILE_UNVERIFIED")
        digest.update(path.relative_to(package).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


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
                 and code == contract and pair == plans
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


def register_real_source_compatibility(connection: Any, *, epoch_id: str,
                                       loaded_commit: str,
                                       bundles: tuple[object, ...],
                                       current_root: Path, origin_root: Path) -> None:
    """Explicit operator INSERT only; never called by the Observer/installer."""
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
    _require(origin_root.resolve().name == origin, "ORIGIN_RELEASE_PATH_INVALID")
    for path, sha in ((current_root, loaded_commit), (origin_root, origin)):
        _require((path / "INSTALLED_COMMIT").read_text().strip() == sha,
                 "RETAINED_RELEASE_MARKER_INVALID")
    contract = consumer_contract_digest(current_root)
    _require(consumer_contract_digest(origin_root) == contract,
             "ORIGIN_CONSUMER_CONTRACT_CHANGED")
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

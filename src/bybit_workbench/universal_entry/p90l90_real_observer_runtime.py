"""Source-transaction REAL P90L90 Observer -> Exit/Execution composition.

This is one wiring point for the existing P90L90ObserverRealConsumer and REAL
native/closed-5m Exit owners. It does not create source attestations, register
active strategies, bypass the live Observer hardstop or send Bybit commands.
Required providers must be implemented by independent source/private/Execution
owners before installing this composition in the running Observer epoch.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bybit_workbench.universal_exit.p90l90_real_closed_exit_port import (
    ClosedCausalSourceProof,
    ClosedHistoryProvider,
)
from bybit_workbench.universal_exit.p90l90_real_exit_execution import SafetyPort
from bybit_workbench.universal_exit.p90l90_real_exit_port import (
    SourceCoverageProvider,
)
from bybit_workbench.universal_exit.p90l90_real_row_connection import (
    P90RealMappedExitConnection,
)
from bybit_workbench.universal_exit.p90l90_real_runtime_ports import (
    P90RealExitRuntimePorts,
    assemble_p90_real_exit_runtime_from_db_bundles,
)

from .p90l90_real_shared_candle_ledger import P90RealSharedCandleLedger

from .contracts import FrozenPolicy
from .p90l90_observer_real_consumer import (
    AccountAdmissionProvider,
    P90L90ObserverRealConsumer,
)


class P90RealObserverWiringUnverified(RuntimeError):
    """Missing independent REAL event or account execution owner."""


@dataclass(frozen=True, slots=True)
class P90RealObserverRuntime:
    consumer: P90L90ObserverRealConsumer
    exit_ports: P90RealExitRuntimePorts


def assemble_p90_real_observer_runtime(
    connection: Any,
    *,
    bundles: tuple[object, ...],
    source_commit: str,
    source_epoch_id: str,
    provenance: FrozenPolicy,
    observed_at: datetime,
    admitted_account: AccountAdmissionProvider,
    verified_native_coverage: SourceCoverageProvider,
    verified_closed_history: ClosedHistoryProvider,
    verified_closed_continuity: ClosedCausalSourceProof,
    verified_account_mutation_safety: SafetyPort,
    shared_candle_ledger: P90RealSharedCandleLedger | None = None,
    producer_release_commit: str | None = None,
    accepted_source_commits: frozenset[str] | None = None,
) -> P90RealObserverRuntime:
    """Bind source checkpoint, MAIN/SUB signed private and execution owners.

    RealConsumer.accept() must still be called only after exact live WS/mirror
    epoch proof. Native/closed proof providers must inspect independently
    committed original-source facts and must NOT be pre-approved booleans.
    """
    if (
        len(bundles) != 2
        or not isinstance(shared_candle_ledger, P90RealSharedCandleLedger)
        or not source_epoch_id.startswith("p90-real-epoch-")
        or len(source_epoch_id) != len("p90-real-epoch-") + 40
        or any(not callable(value) for value in (
            admitted_account,
            verified_native_coverage,
            verified_closed_history,
            verified_closed_continuity,
            verified_account_mutation_safety,
        ))
    ):
        raise P90RealObserverWiringUnverified(
            "P90_REAL_OBSERVER_ALL_INDEPENDENT_PORTS_REQUIRED"
        )
    # Ordinary Observer and causal-source checkpoint SQL need tuple rows.
    # Universal ExitPlan/MFE/Execution SQL requires named mapping rows.
    # A cursor-local row factory preserves ONE underlying PG transaction.
    mapped_exit = P90RealMappedExitConnection(connection)
    ports = assemble_p90_real_exit_runtime_from_db_bundles(
        connection,
        exit_connection=mapped_exit,
        bundles=bundles,
        observed_at=observed_at,
        source_commit=source_commit,
        independently_verified_native_coverage=verified_native_coverage,
        independently_verified_closed_history=verified_closed_history,
        independently_verified_closed_continuity=verified_closed_continuity,
        independently_verified_mutation_safety=verified_account_mutation_safety,
    )
    consumer = P90L90ObserverRealConsumer(
        connection,
        bundles=bundles,
        source_commit=source_commit,
        source_epoch_id=source_epoch_id,
        provenance=provenance,
        admission_provider=admitted_account,
        shared_candle_ledger=shared_candle_ledger,
        verified_exit_tick_port=ports.on_native_trade,
        verified_closed_candle_port=ports.on_closed_5m,
        producer_release_commit=producer_release_commit,
        accepted_source_commits=accepted_source_commits,
    )
    return P90RealObserverRuntime(consumer=consumer, exit_ports=ports)

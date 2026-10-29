"""Compose the independently owned P90 REAL MAIN/SUB Exit ports.

This is a dependency composition, NOT an observer activation, producer of
account attestations, or a proof that live source/Exchange safety exists.
No default callbacks, PAPER fallback, Exchange POST, credential read, or
service enablement are allowed here. The caller must supply separately
implemented, currently verified native coverage, closed-5m continuity and
account mutation safety; an untrusted bool or test stub is not runtime proof.

The REAL Observer remains blocked until its source epoch, account owners and
these ports are actually wired and tested with durable restart/Exchange
evidence. The ordinary universal_exit_consumer intentionally excludes P90.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
)

from .p90l90_real_closed_exit_port import (
    ClosedCausalSourceProof,
    ClosedHistoryProvider,
    P90RealClosedExitPort,
)
from .p90l90_real_exit_execution import (
    P90RealExitExecutionPort,
    SafetyPort,
)
from .p90l90_real_exit_port import (
    P90RealExitNativePort,
    SourceCoverageProvider,
)
from .p90l90_real_mfe_ledger import P90RealMfeLedger
from .p90l90_real_position_provider import P90RealPositionProvider
from .p90l90_real_private_router import P90RealPrivateEvidenceRouter
from .storage import PostgresExitShadowStore


class P90RealRuntimeCompositionUnverified(RuntimeError):
    """A complete set of independently proven REAL Exit ports is unavailable."""


@dataclass(frozen=True, slots=True)
class P90RealExitRuntimePorts:
    """One account-bound shared composition for native and 5m Exit."""

    position_provider: P90RealPositionProvider
    private_evidence: P90RealPrivateEvidenceRouter
    mfe_ledger: P90RealMfeLedger
    execution: P90RealExitExecutionPort
    on_native_trade: P90RealExitNativePort
    on_closed_5m: P90RealClosedExitPort

    # Constructing this value does NOT authorize a live Observer epoch.


def assemble_p90_real_exit_runtime(
    connection: Any,
    *,
    source_commit: str,
    account_by_strategy: Mapping[str, str],
    connector_by_strategy: Mapping[str, str],
    independently_verified_native_coverage: SourceCoverageProvider,
    independently_verified_closed_history: ClosedHistoryProvider,
    independently_verified_closed_continuity: ClosedCausalSourceProof,
    independently_verified_mutation_safety: SafetyPort,
) -> P90RealExitRuntimePorts:
    """Bind existing Universal Exit and Execution owners, never invent proofs.

    Same PostgreSQL connection must be used by native source checkpoint,
    PositionProvider, MFE ledger, ExitStore and ExitExecutionPort. External
    coverage/mutation providers must be implemented by independent runtime
    owners; no hardcoded True or fallback is offered by this module.
    """
    if (
        not isinstance(source_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
        or set(account_by_strategy) != set(ALLOWED_SYMBOLS)
        or set(connector_by_strategy) != set(ALLOWED_SYMBOLS)
        or any(not callable(port) for port in (
            independently_verified_native_coverage,
            independently_verified_closed_history,
            independently_verified_closed_continuity,
            independently_verified_mutation_safety,
        ))
    ):
        raise P90RealRuntimeCompositionUnverified(
            "P90_REAL_EXIT_RUNTIME_INDEPENDENT_SOURCE_AND_ACCOUNT_PORTS_REQUIRED"
        )
    if not hasattr(connection, "transaction") or not hasattr(connection, "execute"):
        raise P90RealRuntimeCompositionUnverified(
            "P90_REAL_EXIT_RUNTIME_SHARED_POSTGRES_CONNECTION_REQUIRED"
        )
    # The existing typed adapters reject colliding/foreign MAIN/SUB account
    # and connector identities, missing signed private transport and absent
    # safety port. Building them performs NO network, DB or Exchange write.
    positions = P90RealPositionProvider(
        connection, account_by_strategy=account_by_strategy,
    )
    private = P90RealPrivateEvidenceRouter(
        account_by_strategy=account_by_strategy,
        connector_by_strategy=connector_by_strategy,
    )
    mfe = P90RealMfeLedger(connection)
    store = PostgresExitShadowStore(connection)
    execution = P90RealExitExecutionPort(
        connection,
        independently_verified_execution_safety=independently_verified_mutation_safety,
        account_by_strategy=account_by_strategy,
    )
    native = P90RealExitNativePort(
        position_provider=positions,
        independently_verified_source_coverage=independently_verified_native_coverage,
        independently_signed_private_evidence=private,
        source_commit=source_commit,
        mfe_ledger=mfe,
        exit_store=store,
        verified_execution_port=execution,
    )
    closed = P90RealClosedExitPort(
        position_provider=positions,
        independently_verified_closed_history=independently_verified_closed_history,
        independent_causal_source_proof=independently_verified_closed_continuity,
        independently_signed_private_evidence=private.for_closed_boundary,
        source_commit=source_commit,
        mfe_ledger=mfe,
        exit_store=store,
        verified_execution_port=execution,
    )
    return P90RealExitRuntimePorts(
        position_provider=positions,
        private_evidence=private,
        mfe_ledger=mfe,
        execution=execution,
        on_native_trade=native,
        on_closed_5m=closed,
    )

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot
from bybit_workbench.exchange.bybit.models import (
    ApiKeyInfo,
    MainnetAccountWideSnapshot,
)

from .mainnet_safety import MainnetSafetySnapshot, MutationBlocked


@dataclass(frozen=True, slots=True)
class MainnetReadinessContext:
    """Read-only connection identity and channel state shared with execution.

    The context contains no credentials and grants no write authority.  It is
    produced only after the ordered GET-only connection test and reconciliation.
    """

    endpoint: str
    api_key: ApiKeyInfo
    health: BybitHealthSnapshot
    reconciliation_complete: bool


class AccountWideReadPort(Protocol):
    """Structural documentation for the narrow account-wide REST reader."""

    async def mainnet_account_wide_snapshot(
        self,
        symbol: str,
    ) -> MainnetAccountWideSnapshot:
        ...


class RestBackedMainnetSafetyStateProvider:
    """Build fresh gateway truth from GET-only REST and read-runtime health.

    Every call performs a new account-wide read.  Strategies cannot inject any
    field into the returned snapshot, and a missing/stale read-only connection
    fails closed before the write delegate can be reached.
    """

    def __init__(
        self,
        reader: AccountWideReadPort,
        endpoint: str,
        context_provider: Callable[[], MainnetReadinessContext | None],
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("Mainnet state endpoint must use https://")
        self.reader = reader
        self.endpoint = endpoint.rstrip("/")
        self.context_provider = context_provider

    async def snapshot(self, symbol: str) -> MainnetSafetySnapshot:
        selected = symbol.strip().upper()
        if not selected:
            raise ValueError("symbol is required")
        initial_context = self.context_provider()
        if initial_context is None:
            raise MutationBlocked(
                "ordered GET-only connection test and reconciliation are not available"
            )
        self._validate_context(initial_context)

        # Account-wide truth is intentionally expensive: it enumerates every contract
        # surface the key can mutate.  Do not seal the WS timestamps observed before
        # that REST sweep into the mutation snapshot, because they may age past the
        # tight pre-send freshness budget while the sockets remain perfectly healthy.
        account_wide = await self.reader.mainnet_account_wide_snapshot(selected)
        if account_wide.instrument.symbol != selected:
            raise MutationBlocked("account-wide reader returned an unexpected symbol")

        final_context = self.context_provider()
        if final_context is None:
            raise MutationBlocked(
                "read-only context disappeared during Mainnet pre-send check"
            )
        self._validate_context(final_context)
        if self._context_identity(final_context) != self._context_identity(initial_context):
            raise MutationBlocked(
                "read-only connection identity changed during Mainnet pre-send check"
            )

        public_at = final_context.health.public.last_message_at
        private_at = final_context.health.private.last_message_at
        if public_at is None or private_at is None:
            raise MutationBlocked("Public WS and Private WS observations are required")

        return MainnetSafetySnapshot(
            endpoint=self.endpoint,
            api_key=final_context.api_key,
            instrument=account_wide.instrument,
            account=account_wide.account,
            position=account_wide.position,
            other_positions=account_wide.other_positions,
            open_orders=account_wide.open_orders,
            public_observed_at=public_at,
            private_observed_at=private_at,
            rest_observed_at=account_wide.observed_at,
            reconciliation_complete=final_context.reconciliation_complete,
            positions_complete=True,
            open_orders_complete=True,
        )

    def _validate_context(self, context: MainnetReadinessContext) -> None:
        if context.endpoint.rstrip("/") != self.endpoint:
            raise MutationBlocked("read-only context endpoint does not match write endpoint")
        if not context.reconciliation_complete:
            raise MutationBlocked("read-only reconciliation is not complete")
        if (
            context.health.public.last_message_at is None
            or context.health.private.last_message_at is None
        ):
            raise MutationBlocked("Public WS and Private WS observations are required")
        if not context.health.public.fresh or not context.health.private.fresh:
            raise MutationBlocked("Public WS and Private WS must be fresh")

    @staticmethod
    def _context_identity(context: MainnetReadinessContext) -> tuple[object, ...]:
        key = context.api_key
        return (
            context.endpoint.rstrip("/"),
            key.note,
            key.created_at,
            key.key_type,
            key.is_master,
            key.parent_uid,
            key.unified_account,
        )

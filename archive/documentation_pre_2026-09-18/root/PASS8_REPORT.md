# PASS 8 — Micro-Live risk-bound arming preparation

Workbench: `0.8.5`

## Purpose

Pass 8 prepares the first real-money Micro-Live smoke without sending a trade during
source verification. The operator can choose the symbol and timing manually; `UNIUSDT`
is only the first accepted smoke candidate, not an architectural allowlist.

## Risk model

- `Risk / trade, %` is an editable GUI field.
- Default: `1.00%` of current synchronized equity.
- `Absolute risk cap, USDT` is optional; default `0` means disabled.
- If both limits are enabled, the smaller risk budget wins.
- Position size is calculated from the entry-to-stop distance plus conservative fee and
  slippage friction, then rounded down to current Bybit `qtyStep` and checked against
  current `minNotional`, quantity limits and available balance.
- Micro-Live requires requested leverage `1x` and caps percentage risk at `10%`.

## Arming safety changes

A short-lived `MainnetArmingTicket` now seals an exact normalized entry plan:

- symbol;
- client/order link id;
- side;
- quantity;
- limit price;
- server stop;
- optional take profit;
- selected risk percentage;
- calculated risk budget;
- estimated loss at stop.

The Mainnet gateway re-derives exchange/account truth and rejects any mutation whose
entry fields differ from the sealed plan. Changing symbol, timeframe, strategy,
direction, entry, stop, take profit, leverage or risk inputs invalidates the checked
plan and destroys the in-memory Mainnet session/ticket.

## First smoke behavior

The first smoke uses `manual_protected_trade` so the operator chooses the exact moment,
entry and stop. This manual strategy is intentionally operator-confirmed and does not
pretend to be an automated historical signal. Automatic strategies remain subject to
the exact historical eligibility gate before they can be armed.

`BYBIT_WORKBENCH_ALLOW_LIVE_TRADING` still defaults to `0`. Source verification and
normal application startup cannot remove that external lock.

No account-configuration mutation is added: margin mode and leverage remain manual
Bybit settings and are rechecked from fresh exchange state before an entry can pass.

## R1 regional WebSocket / permission consistency fix

After the first Kazakhstan Mainnet GUI connection attempt, two integration defects were
fixed without enabling live trading:

- the locked pybit public V5 WebSocket template hard-coded `.com`, so the accepted
  `tld="kz"` argument did not affect the public linear stream; Workbench now rewrites
  only that known pybit template to use `{TLD}` and fails closed on an unknown template;
- `Derivatives: ["DerivativesTrade"]`, already accepted by Pass 7 for a Unified Account,
  is now treated consistently by the GUI arming blockers and Mainnet safety gate.

Regression coverage was added for the regional public WebSocket template, fail-closed
unknown upstream templates, allowed `DerivativesTrade`, and forbidden unknown API
surfaces.

## r2 WebSocket resilience correction

- Regional Kazakhstan WebSocket endpoint mapping remains `stream.bybit.kz`.
- Transient WebSocket constructor/transport failures no longer terminate the read-only runtime permanently; the runtime closes the partial session and retries with capped exponential backoff while all mutation paths remain blocked.
- A dropped public/private transport causes a full read-only session rebuild and REST reconciliation before READY can be restored.
- Public health still requires actual fresh market-data callbacks; private health may remain fresh from an authenticated connected transport even when a flat account has no order/execution/position/wallet events.
- Authentication, permission, protocol and validation failures are not classified as reconnectable and remain fail-closed.


## r4 / Workbench 0.8.2 connection lifecycle feedback

- `Подключить read-only` now gives immediate visible feedback before any network work: the
  button becomes `Подключение…`, the disconnect action becomes `Остановить подключение`,
  and a prominent status line explains the current phase.
- Runtime startup moves the state machine to `SYNCING` synchronously, so a slow WebSocket
  constructor can no longer leave the GUI looking idle/`DISCONNECTED` after a valid click.
- Transient WebSocket failures remain `DEGRADED` during exponential-backoff recovery and
  return through `SYNCING` on each retry instead of visually collapsing to `DISCONNECTED`.
- Manual disconnect gives immediate `Отключение…` feedback while the background thread is
  being asked to stop; all trading actions remain fail-closed throughout.
- Connection failures are surfaced both in the existing error banner and the connection
  status line; the operator no longer has to infer whether a button click was accepted.

## r5 / Workbench 0.8.3 symbol history and searchable selector

- Fixed the three Ruff E501 violations introduced by the connection-status feedback text.
- The symbol field is now an editable combo box with case-insensitive substring completion.
- Symbols from successful READY sessions are stored as an MRU list in
  `var/symbol_history.json`; credentials are never written there.
- The history is deduplicated, persists between launches, and keeps up to 50 symbols.
- Manual symbol entry remains supported, including Bybit-style numeric prefixes such as
  `1000PEPEUSDT`.


## r9 / Workbench 0.8.5 transport-health, diagnostic risk sizing and chart UX

- WebSocket transport liveness is separated from market-data freshness. A connected
  session is no longer torn down merely because a ticker/kline update is late.
- Direct regional WebSockets track actual transport activity/heartbeats; reconnect is
  reserved for a dead socket/thread/heartbeat path.
- Reconnect budget counts consecutive failures and resets after a stable session.
- Risk Gate keeps diagnostic sizing on rejected plans and reports the exchange minimum
  viable quantity/loss/risk percentage for `minimum_notional`. It never raises the risk
  automatically.
- The chart uses a real date/time axis and conventional solid green/red OHLC candles
  with visible wicks and an initial recent-candle viewport.
- Live trading remains SHADOW/DISARMED by default.

# PASS 7 — Mainnet GET-only acceptance

Workbench: `0.7.0`  
Strategies: `0.2.0`

## Purpose

Collect a redacted Mainnet acceptance report from the dedicated Unified Trading
account without creating any order, changing leverage/margin mode, transferring
funds, or opening WebSocket trading channels.

The acceptance path owns only `BybitReadOnlyAdapter` / `PybitReadOnlyTransport`.
There is no write transport dependency in the runner.

## Report contents

- selected REST endpoint and Bybit server clock offset;
- master/subaccount classification;
- personal API-key type, Read/Write state, IP-binding count and active expiry;
- exact `ContractTrade`, Spot, Wallet, Options/USDC and other permission groups;
- UTA flag, `accountType`, `unifiedMarginStatus`, account margin mode;
- selected-symbol one-way/hedge mode and configured leverage;
- current contract positions and active contract orders (without order IDs);
- maker/taker fee rate and exact public instrument rules;
- explicit Micro-Live blockers.

The report intentionally excludes API key value, API secret and bound IP values.
It emits a separate SHA-256 file.

## Production fixes discovered before acceptance

Bybit documents `parentUid="0"` for a master account. It is now normalized to
`None`, so a real master key is not mistaken for a subaccount.

Bybit can also return a negative `deadlineDay` and Unix-epoch `expiredAt` sentinel
when expiry is not active. Those sentinels are no longer interpreted as an expired
key. A real positive deadline / future expiry remains enforced.

Position parsing gained a diagnostic tolerant mode so a configured hedge-mode
symbol is reported as a blocker instead of aborting the GET-only acceptance.
Normal execution paths remain strict one-way by default.

## Windows commands

After source verification:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\accept_mainnet\_windows.ps1 `
  -Symbol UNIUSDT `
  -Endpoint https://api.bybit.kz
```

The script forces `BYBIT_WORKBENCH_ALLOW_LIVE_TRADING=0` and
`BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION=0` for the entire run.

Successful data collection ends with:

```text
PASS 7 GET-only acceptance report generated successfully.
```

`micro_live_ready=false` is not a script failure; it means the redacted report
contains one or more account/key configuration blockers that must be reviewed
before a separate Micro-Live arming manifest is created.

## Acceptance hardening after first real Mainnet run

Unified API keys can legitimately expose `Derivatives: ["DerivativesTrade"]` in
addition to `ContractTrade`. This exact UTA permission is allowed; unrelated
permission groups remain blocking.

Bybit documents that account-wide wallet fields are not applicable in isolated
margin. When those totals are empty, the read-only mapper now derives conservative
USD equity / wallet / available / unrealised values from the returned per-coin
rows. The isolated available-balance formula follows Bybit's documented
`walletBalance - totalPositionIM - totalOrderIM - locked - bonus` rule. If a safe
USD conversion cannot be derived, the mapper still fails closed.

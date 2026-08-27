# MAYAK Market Monitor — P1 hard-stop report

## BASELINE

- Source of truth: `C:\cripta`.
- Project: `bybit-strategy-workbench` version `0.8.5`.
- Git repository: not present; `pyproject.toml` is the software-version authority.
- MAYAK implementation before this work: absent; only concept documents existed.
- Authoritative Windows gate: `scripts/check_windows.ps1`.
- Frozen research period: `2026-05-18T00:00:00Z` through `2026-08-16T00:00:00Z`.
- ALL9 frozen Entry truth: 1063 events.
- NEW5 is the exact complement of the 14-asset panel: AAVE, AVAX, BNB, LTC, SUI.

## CHANGED

- Added independent `src/bybit_workbench/mayak` bounded context.
- Added immutable P0-A contracts and account-independent market context.
- Added pure causal feature mathematics and closed-bar historical engine.
- Added exact frozen ALL9 event adapter and future-metadata separation.
- Added fail-closed 14-asset data audit with downloads disabled.
- Added discovery statistics, clustered/isolated diagnostics, freeze+SHA256, and P1 gate.
- Added scripts: `mayak_data_audit.py`, `mayak_p1_discovery.py`, `mayak_p1_verdict.py`.
- Added 13 MAYAK unit/regression tests.
- Added machine truth under `reports/mayak/p1`.

## DOES NOT CHANGE

No existing trading source was modified. The following remain unchanged:

- frozen Entry fingerprint and Entry behavior;
- P50;
- Probe;
- Scout;
- Scale;
- Exit;
- Risk, sizing, leverage, and structural stops;
- Execution, credentials, order routing, and server-side protection.

MAYAK imports no execution/private credential/order-placement module and cannot publish
a trading mutation.

## RESEARCH

- Data audit: PASS for all 14 assets.
- Dataset fingerprint: `477e711ff18cce1dd4d618d0ba74d474609604890199490257dc1ff16a65dc31`.
- Missing 5m bars in the evaluation interval: 0.
- Duplicate timestamps: 0.
- BTC and ETH: present and complete.
- Public trade archive coverage: 91 archives with 91 manifest hashes per asset.
- ALL9 normalized events: 1063; eligible primary outcomes: 1054; incomplete data-end: 9.
- Causal feature rows: 6378 (`T-30m`, `T-15m`, `T-10m`, `T-5m`, `T-1m`, `T`).
- Primary comparisons: 504.
- Clustered/isolated diagnostic comparisons: 504.
- Frozen discovery manifest SHA256:
  `b3ad6e498871945a99972efa6864bbf580ecf32bdc744218e5ef1d216f381a9b`.
- Selected candidates: 0.
- NEW5 classification: `FAILED` because no ALL9 hypothesis survived to confirmation.
- NEW5 outcome tables opened: no.
- Retuning: none.
- P1 verdict: **NO EVIDENCE**.
- P2 verdict: **NOT AUTHORIZED**.

The strongest apparent breadth differences had about 0.30-0.34 standardized effects,
but failed the predeclared bootstrap requirement on both primary comparisons and marked
roughly 46-50% of continuation events as false alarms. The evidence does not support a
stable market-state service for Entry environment.

## PRODUCTION

- Runtime version: not created.
- Sea State/score: not created.
- API/PostgreSQL/systemd deployment: not created.
- Production SHADOW: not deployed.

This is required behavior after `P1 = NO EVIDENCE`, not missing implementation.

## CHECKED

Executed successfully:

```text
python -m ruff check src/bybit_workbench/mayak scripts/mayak*.py tests/test_mayak*.py
python -m mypy src/bybit_workbench/mayak
python scripts/mayak_data_audit.py --root C:\cripta --output C:\cripta\reports\mayak\p1
python scripts/mayak_p1_discovery.py --root C:\cripta --output C:\cripta\reports\mayak\p1
python scripts/mayak_p1_verdict.py --output C:\cripta\reports\mayak\p1
pytest MAYAK tests: 13 passed
powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
```

Final authoritative Windows result with workspace-local TEMP:

```text
Ruff: PASS (156 source files)
mypy: PASS
pytest: 652 passed, 1 intentional soak skip
headless smoke: PASS
GUI smoke: PASS; SHADOW/DISARMED
```

The first baseline gate attempt had 32 pytest setup errors because the managed sandbox
denied access to `%LOCALAPPDATA%\Temp\pytest-of-alex`; it had 607 passes and no assertion
failures. Re-running the same authoritative gate with writable workspace-local TEMP was green.

## NOT CHECKED HERE

- NEW5 outcome replication, because there is no frozen discovery hypothesis to test.
- Temporal OOS.
- P2 benchmark/state representation.
- Research/live equivalence.
- Linux/server gate, PostgreSQL, API, systemd, reconnect/restart/reboot, rollback.
- Real Ubuntu production host.

These downstream checks are contractually forbidden or inapplicable after `NO EVIDENCE`.

## BLOCKERS

Contractual research hard stop, sections 39, 40, and 72:

```text
P1 = NO EVIDENCE
```

Continuing to P2 would manufacture a Sea State from a non-replicable discovery result.

## NEXT RESEARCH

- Future temporal OOS after a new unseen sample exists.
- Scout × MAYAK × Scale only after an independently supported MAYAK hypothesis exists.
- MAYAK usefulness for Exit/Risk/Portfolio remains separate future research.

The governing principle remains: **Маяк показывает состояние моря. Капитан принимает решение.**

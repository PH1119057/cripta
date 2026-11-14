# P53 1M ENTRY DISPLACEMENT V1.5

Baseline: bybit-workbench 0.8.5 / P48.2 + accepted P53 V1.4.

## Root cause

V1.4 used `five[0].open` as the initial 1m seed. The frozen `trade_5m.csv` may include warm-up candles earlier than the first fingerprinted public-trade archive. In the observed UNI failure, this selected `3.232` while the first comparable archive-boundary 5m candle at `2026-05-17T00:00:00Z` opened at `3.483`.

V1.5 derives the earliest public-trade archive day from the frozen dataset manifest, requires exactly one frozen 5m candle at that day's `00:00:00Z`, and uses only that candle's `open` as the causal seed. Exact OHLCV equivalence remains strict; there is no tolerance.

Cache version remains V3. A first day whose cached open conflicts with the corrected seed is rebuilt; compatible later days are reused.

## Install

```powershell
cd C:\cripta
Expand-Archive `
    -LiteralPath .\P53_1M_ENTRY_DISPLACEMENT_V1_5.zip `
    -DestinationPath . `
    -Force

powershell -ExecutionPolicy Bypass -File `
    .\P53_1M_ENTRY_DISPLACEMENT_V1_5\APPLY_P53_1M_ENTRY_DISPLACEMENT_V1_5.ps1
```

The installer is fail-closed: verify V1.4 baseline -> verify hashes -> temp overlay -> PowerShell syntax/ASCII -> py_compile -> Ruff -> mypy -> targeted pytest -> broad overlay pytest -> copy to real project -> authoritative `scripts\check_windows.ps1`. On failure before copy, the real project is untouched; on final-gate failure, applied files are rolled back.

## Research

After `INSTALLED` and `Authoritative Windows gate: GREEN`:

```powershell
powershell -ExecutionPolicy Bypass -File `
    .\scripts\research_entry_1m_displacement_p53_windows.ps1
```

Do not delete `reports\entry_1m_displacement_p53\ALL9_P53_WORKING\cache_1m`; V1.5 is designed to reuse compatible V3 cache.

## Scope

Research only. Downloads: DISABLED. Frozen 1063 Entry cohort unchanged. Entry/Exit/Risk/Execution/live/UI unchanged. NEW5/P46/holdout untouched.

# P53 1M Entry Displacement V1.1

Research-only patch for the exact uploaded `CRIPTA_SOURCE_CURRENT.zip` baseline (`0.8.5 / P48.2`).

## Why V1.1 exists

The first V1 package was correctly rejected by its fail-closed installer before any project file was copied. Windows mypy found two preparation errors: both calls to the existing helper `flow_reversal_v1._archive_map` passed an extra `symbol` argument, while the baseline helper contract is `_archive_map(dataset_dir)`.

V1.1 is a clean replacement package, not an incremental edit. It fixes both call sites and adds a regression test that locks the single-argument helper contract. The research formula and the frozen 1063-entry experiment are unchanged.

During local re-checking of the clean overlay, another latent installer issue was found before sending V1.1: the uploaded source snapshot intentionally omitted `bybit_workbench.spec` and market `reports`, while some project tests require them. V1.1 copies `bybit_workbench.spec` from the real project into the temporary overlay and labels the overlay pytest honestly: it excludes only `test_mayak_research_truth.py`, because that test is bound to the real frozen report tree. The final authoritative `scripts\check_windows.ps1` still runs on the real installed project and remains mandatory; failure rolls the payload back.

## What it does

- keeps the exact frozen 1063 Entry V1 points unchanged;
- reconstructs the frozen 15m+5m geometry first;
- adds a causal 1m research overlay from local public-trade tape only;
- normalizes LONG/SHORT displacement so negative means deeper/adverse and positive means outward/in trade direction;
- checks whether deeper 1m prices were actually touched within 3 hours;
- writes research outputs only when the research script is explicitly run.

## What it does not do

It does not change Entry, Exit, Risk, Execution, live runtime, UI, P46/NEW5, or user data. Installation itself does not write to `reports\`.

## Install

From `C:\cripta`, after extracting the V1.1 ZIP:

```powershell
powershell -ExecutionPolicy Bypass -File .\P53_1M_ENTRY_DISPLACEMENT_V1_1\APPLY_P53_1M_ENTRY_DISPLACEMENT_V1_1.ps1
```

The installer is fail-closed: baseline/payload hashes -> temp overlay -> PowerShell syntax/ASCII -> py_compile -> Ruff -> mypy -> targeted pytest -> broad overlay pytest -> copy to real project -> authoritative `scripts\check_windows.ps1`. On any precheck failure it does not copy payload into the project. On a final-gate failure it rolls back the copied files.

## Run research

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\research_entry_1m_displacement_p53_windows.ps1
```

Default output: `reports\entry_1m_displacement_p53\ALL9_P53_WORKING`.
Downloads are DISABLED.

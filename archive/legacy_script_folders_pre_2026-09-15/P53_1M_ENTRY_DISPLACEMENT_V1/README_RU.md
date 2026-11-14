# P53 1M Entry Displacement V1

Research-only patch for the current `C:\cripta` baseline (`0.8.5 / P48.2`).

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

From `C:\cripta`, after extracting this ZIP somewhere locally:

```powershell
powershell -ExecutionPolicy Bypass -File <EXTRACTED_PATH>\P53_1M_ENTRY_DISPLACEMENT_V1\APPLY_P53_1M_ENTRY_DISPLACEMENT_V1.ps1
```

The installer is fail-closed: baseline/payload hashes -> temp overlay -> py_compile -> Ruff -> mypy -> targeted pytest -> full pytest -> copy to real project -> authoritative `scripts\check_windows.ps1`. On any precheck failure it does not copy payload into the project. On a final-gate failure it rolls back the copied files.

## Run research

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\research_entry_1m_displacement_p53_windows.ps1
```

Default output: `reports\entry_1m_displacement_p53\ALL9_P53_WORKING`.
Downloads are DISABLED.

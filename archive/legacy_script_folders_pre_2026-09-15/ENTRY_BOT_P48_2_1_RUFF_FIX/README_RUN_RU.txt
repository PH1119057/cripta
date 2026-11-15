ENTRY BOT P48.2.1 - LIVE AUDIT + SHADOW PRE-LIMIT - RUFF FIX

Baseline:
- C:\cripta
- bybit-workbench 0.8.5
- P48 V1.7 causal TAPE 5/5 warm-up already installed

Changes:
- mandatory append-only Entry candidate history in SQLite schema v9;
- records candidate armed/cleared, distance band transitions, touch veto/signal;
- records diagnostic +0.10/+0.50/+1.00/-1.00/-3.00 and recovery after -1%;
- records SHADOW pre-limit arm/cancel/touch events only;
- red/yellow/green distance highlighting in Bot Mode;
- Audit N counter in runtime header;
- CSV export script.

Does NOT change:
- frozen Entry V1 rules/thresholds;
- P46;
- Exit/Risk;
- leverage, sizing, stop or TP;
- Mainnet execution: AUTO ENTRY remains LOCKED;
- reports or market data during installation.

Install from C:\cripta:
  powershell -ExecutionPolicy Bypass -File .\ENTRY_BOT_P48_2_1_RUFF_FIX\APPLY_ENTRY_BOT_P48_2_1.ps1

Authoritative gate:
  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

Optional history export after the bot has collected events:
  powershell -ExecutionPolicy Bypass -File .\scripts\export_entry_bot_history_windows.ps1

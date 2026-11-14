P47E — FROZEN CLOSED-1H HYPOTHESIS OUT-OF-SAMPLE VALIDATION

Purpose
-------
Validate the UNI/LINK development hypothesis on untouched holdout assets without
retuning Entry, Exit, or the 1H definition.

Frozen hypothesis
-----------------
H1: runner-added trades predominantly occur against strict closed 1H trend.

Development assets excluded
---------------------------
UNIUSDT, LINKUSDT

Holdout assets
--------------
BTCUSDT, ETHUSDT, XRPUSDT, 1000PEPEUSDT, SOLUSDT, DOGEUSDT, ADAUSDT

Frozen Exit
-----------
- initial stop: -1.00%
- +0.10% -> BE
- +1.10% -> 50/50 split
- core locks +1.00%
- runner floor = BE
- runner MFE giveback = 4.00%
- horizon = 72h

Frozen 1H definition
--------------------
Only fully closed UTC 1H candles, OHLC rebuilt from raw public trades.
Strict trend requires structure, EMA20 position, and EMA20 slope to agree.

Run
---
powershell -ExecutionPolicy Bypass -File .\scripts\research_hourly_trend_oos_full_panel_windows.ps1

No downloads are performed by P47E. Missing frozen P40 or public-trade data fail closed.

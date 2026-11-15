ENTRY RUNTIME V5 - RESUME / NETWORK RESILIENCE / FAST LOCAL ANALYSIS

What this patch changes:
1. Stage-level resume. Completed P30/P31/P33/P34/... outputs are reused.
   The interrupted ETH run will continue at P35 instead of repeating P31/P33.
2. Small REST context (account ratio, mark price, index price) is prefetched before
   heavy computation. Requests use retries with exponential backoff.
3. Python network exceptions are printed to stdout so the full traceback survives
   PowerShell Start-Job/Receive-Job.
4. P33 uses the compact price-only raw-tape loader and NumPy vectorized path analysis.
   Entry thresholds and event-order semantics are unchanged.
5. P39/P40 use local orderbook ZIPs first and do not probe the Internet when heavy
   downloads are disabled.
6. P39/P40 process independent days in parallel (default 2 workers, configurable 1..4).
7. Existing per-day P39/P40 caches remain resumable.

This patch does NOT modify reports, trading execution, live strategy logic, arming,
risk, credentials, or Bybit account settings.

Install from C:\cripta:
  powershell -ExecutionPolicy Bypass -File .\ENTRY_RUNTIME_V5_RESUME_FAST\APPLY_ENTRY_RUNTIME_V5.ps1

Then:
  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\research_entry_full_panel_windows.ps1

Do NOT use -AllowDownload for the first run.

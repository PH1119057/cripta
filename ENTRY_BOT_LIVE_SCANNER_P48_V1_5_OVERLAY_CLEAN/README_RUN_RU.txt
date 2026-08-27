ENTRY BOT P48 V1.5 — WARMUP RESILIENCE

Fixes observed on live Bybit KZ screening:
- BOT MODE no longer auto-starts the scanner.
- NO CALIBRATION assets do not perform REST warm-up.
- REST TLS/read/network timeouts retry up to 4 attempts.
- A single asset that still fails warm-up is fail-closed while other ready assets continue.
- Failed warm-up assets cannot become signal-ready from a few subsequent live messages.
- Auto Mainnet Entry remains locked. P46 / Exit / Risk unchanged.

V1.5 additionally fixes the full-overlay Ruff import ordering found by the user gate.

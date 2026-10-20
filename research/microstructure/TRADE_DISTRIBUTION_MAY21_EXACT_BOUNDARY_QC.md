# CRIPTA — #27/#28 exact timestamp membership, 2026-05-21

**RESEARCH RESULT / NOT CANON / NOT READY.** GitHub main HEAD verified as `514b9a7dcafdc7b874894950f20649c79de4ba83` at start; reader source version `6938a7d74944d6b35662def03cf2fc1384d31cc0` (`research/microstructure/experiments/trade_distribution_boundary_membership_qc_v1.py`), downloaded from immutable GitHub raw and executed read-only as `cripta` under SentinelX, no filesystem writes.

Initial host status: load 5.48/5.52/6.26, 2186 MiB RAM available, four OLD90 replay worker processes still active, despite user's statement server was free. No process touched or stopped. Read-only bounded 60-minute APT test succeeded (rc0): 93665 raw records read (full compressed file), 2875 exactly inside first 60m by both implementations, window membership mismatch 0, minute index mismatch 0.

Full-day APT 2026-05-21 succeeded rc0: **93665 raw records**, `decimal_in_window=93665`, `float_in_window=93665`, `window_mismatches=0`, `minute_bucket_mismatches=0`, examples empty. This is exact membership agreement **on this raw file**, not proof of correctness for other days/instruments, source capture clocks or all other #27/#28 formula components.

On next separate INJ execution, OpenAI tool-security upstream **blocked** the invocation before a tool result. No evidence of host execution; class `BLOCKED_UPSTREAM_OPENAI`, **not** SentinelX `command_not_allowed` and not Unix EACCES. Per gate, no retry/change actor or encoding. DOT/LTC/ARB NOT CHECKED HERE for this exact full-day boundary reader. No more compute launched in this run. No formal §21 READY, no OOS.

Existing source version static test showed a possible float-boundary issue on hypothetical extra-fine timestamps, but it was **not observed** in full-day APT. Further cross-instrument evidence requires approved route/owner decision; do not silently claim 5/5.

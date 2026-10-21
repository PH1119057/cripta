# CRIPTA — проход 5/5: единый итог пяти рабочих пакетов по 30 индикаторам

**CHECKED HERE 2026-10-21. RESEARCH EVIDENCE / NOT CANON / NOT RESEARCH-READY.** GitHub authority `PH1119057/cripta:main`, pre-publication HEAD `6fafc41e181b0ff8d6558b138ea14a0bec7b06c4`. All numbers below are historical research measurement/source inventory, not trading edge. §21.6 of `docs/RESEARCH_COMPUTE_RULES_RU.md` remains binding without waiver. No Strategy/LIVE authorization.

## 1. Что закрывают пять проходов — и чего они не закрывают

The five-passage promise was fulfilled **as consolidated investigation/evidence-classification work packages**, not as full completion of all physical research, analytical cube, predictive OOS and net-of-fee P&L. A prior claim that five passes necessarily suffice for everything was not supported by the strict §21.6 gates. **NO `RESEARCH-READY` promotion or trading outcome is implied.**

| Group | IDs | Candidate cells | Measurement/source review | Strict `RESEARCH-READY` |
|---|---|---:|---|---:|
| 1 trade/tape | 1–8,27–28 | 50 | **50/50** May20 numeric/summary source evidence; 5 May20 compressed trade SHA256, 831927 raw fills and basic integrity; additional accounting+unique trade IDs checked on APT/INJ/DOT 529130 rows; partial extra checks upstream blocked for LTC/ARB | **0/50** |
| 2 price/flow | 14–22 | 45 | **40/45** May20 physical or proxy pilot cells; #14 ARB and #18/#20 LTC+ARB not executed on second day; #19/#22 5/5 independent joined 1440-minute source-hash QC; #21 one LTC low-side reclaim sensitive to tied timestamps | **0/45** |
| 3 orderbook | 9–13,29–30 | 35 | **33/35** historical pilot/May20 cells, different scopes; 10 Bybit ob200 ZIP files May19/20 located and SHA256 registered for five symbols; #30 INJ/ARB May20 job results unknown | **0/35** |
| 4 OI/liquidation/Spot/cross-venue | 23–26 | 20 | Historical May19 source observations: OI 5/5 via official API 1440 5m entries, Spot 5/5 69834 fills in-memory, processed liquidation 101 observed records across 29/120 symbol-hours with five current gzip hashes; #26 owner deferred last; **no verified completed #23/#24/#25 feature derivation** | **0/20** |
| **TOTAL** | **30** | **150** | **All 30 indicator slots classified; pilot scopes are heterogeneous** | **0/150** |

`130` of the `150` indicator×symbol cells belong to the three groups of existing May19/May20 historical feature measurements/draft worksheet families (50+45+35); this does **not** mean 130 fully executed/READY. Approximately 123 of those have historical physical/proxy executions in their indicator-specific controls (50+40+33), not necessarily the same day or QC depth. The remaining 20 group-4 source/deferred cells are likewise **not** complete §21 passports. Source observations for OI/Spot are **not** OI-change/Spot-perp-basis features.

## 2. Derived machine-readable register — audited and reproducible as inventory

`research/microstructure/PASS5_MASTER_150_STATUS_2026-10-21.json` contains **exactly 150 cells** over 30 indicator IDs and APT/INJ/DOT/LTC/ARB, with per-cell group, evidence stage, event/source notes, deferred/blocked status and `research_ready=false`. Independent JSON read-after-write verified: 150 entries, 30 IDs, five instruments, `READY=0`, OI source observations sum 1440, Spot fills sum 69834, processed May19 liquidation observations sum 101. This is a versioned **status inventory**, not an analytical cube, complete data series, independent full QC or formal §21.6 feature registry.

## 3. Open gates that genuinely prevent full research completion

1. **Full requested instrument-specific research period** with lossless source snapshot and exact count/window/missingness/quality manifests; existing successful controls largely May19/May20 and first hours May21.
2. **Historical causal clocks**: exact `event_time`, `observed_at`/publication semantics, conservative verified `available_after`, as-of lookback, reset, late data and stale-vs-unknown. Bybit public trade CSV has only `timestamp`, not archived receiver receipt. Official OI five-minute sample timestamps do not establish publication time. Cross-feed book/Spot/liquidation sample clocks are not proven synchronized.
3. **Per-formula independent primary-vs-secondary QC at intended granularity**: #1/2/3/5/6/7 candidate v2 5/5 tolerant numeric minute QC already done; #19/#22 5/5 joined-row hashes done; other proxies mostly aggregate summary, #4/#8/#27/#28 exact event-level outstanding; #9/11/29 all-minute independent job outcome unknown. No automatic repeats of failed/unknown jobs.
4. **Physical estimator identity and owner choices:** #10 top5 rank-stock OFI vs strict price-identity; #12 displayed depletion vs proven cancels; #13 stale 60-second recovery episodes and censor; #16 against-flow price proxy ≠ genuine passive absorption; #17 next5m return strictly future label; #21 print-extrema with timestamp ties ≠ verified orderbook sweep; #30 top5 stock recovery ≠ same-level/same-order resiliency. Record proxy under its own named version if approved; never rename without owner choice.
5. **Actual source materialization for #23/#25 and continuity QC for #24:** historical one-day official OI/Spot response evidence exists but original bytes were not persisted, raw hashes unavailable, no validated as-of OI Change or Spot/Perp basis; 91 unknown liquidation symbol-hours cannot be zero.
6. **Analytical cube & economic inference NOT EXECUTED**: no immutable full-series `INSTRUMENT × TIME × INDICATOR` dataset, no source-aligned outcome labels, no OOS/holdout experimental design/results, no after-commission P&L or proven edge. These are **future research deliverables**, not paper outputs that can be inferred from 30-indicator inventory.

## 4. Explicit decisions and recommended next operational block

- `PASS1–PASS5_EVIDENCE_AUDIT=COMPLETE` (source/status inventory and new raw checks). **`RESEARCH_PROJECT_COMPLETE=NO`**; `ALL_150_RESEARCH_READY=NO`; formal statuses `READY=0/150`.
- **HIGH PRIORITY 1**: choose exact owner-selected full period(s) and choose unambiguous physical proxy versions; restore/build instrument-specific raw capture manifests with explicit absence of receiver timestamps, do not manufacture historical `observed_at`.
- **HIGH PRIORITY 2**: compute/freeze verified causal features on one exact declared period per instrument with independently checked source/run lineage; update individual passports only when each exact §21.6 condition is met. Recover unknown jobs from existing host outputs first (no automatic heavy relaunch). Existing numerical QCs must be reused, not recalculated without defect.
- **HIGH PRIORITY 3**: only after sufficient trustworthy feature dataset exists, build immutable cube, separate future labels, non-leaking train/OOS/holdout, negative controls/placebos and net-of-commission trading analysis according to `RESEARCH_COMPUTE §21`. No positive P&L claimed now.
- **#26 Cross-Venue**: remains last by owner direction; not run and not promoted at stage four or five while Bybit prerequisites are incomplete.
- **Access/security**: approved `sudo -u cripta` research reader route worked for new checks. Previously OpenAI upstream-blocked invocations and SentinelX agent timeout/no-response branches remain **separate exact incident classes**; no bypass, actor switch or blanket privilege change. No server research scripts/jobs with filesystem writes prepared or started in this pass; no service restart, strategy/deploy or LIVE gate mutation.

## 5. Immutable GitHub evidence references

- Pass 1: `research/microstructure/PASS1_TRADE_TAPE_CHECKPOINT_2026-10-20.md`
- Pass 2: `research/microstructure/PASS2_PRICE_FLOW_CHECKPOINT_2026-10-21.md`
- Pass 3: `research/microstructure/PASS3_ORDERBOOK_CHECKPOINT_2026-10-21.md`
- Pass 4: `research/microstructure/PASS4_EXTERNAL_SOURCES_CHECKPOINT_2026-10-21.md`
- Pass 5: `research/microstructure/PASS5_MASTER_150_STATUS_2026-10-21.json`
- Official OI/Spot May19 recovery checkpoint: `research/microstructure/coverage_2026-05-19.md`
- Canon: `docs/RESEARCH_COMPUTE_RULES_RU.md §21`; source-gaps inventory is dated historical evidence (not override of newer recovery in the same coverage doc).

## 6. Stop/go

`CHECKED HERE:` GitHub source and saved results, OI/Spot recovery existing documentation, freshly calculated hashes from five processed liquidation files; four other groups' audited checkpoints. `NOT CHECKED HERE:` fresh Bybit OI/Spot download bytes for May19, complete multi-day stream, OOS/cube/P&L, present server worker process truth beyond earlier snapshots.

No new actual trade signal, no change to R1/Strategy version/Entry/Exit/Execution, no LIVE activation. **Results after commissions: NOT CALCULATED in this five-pass audit.**

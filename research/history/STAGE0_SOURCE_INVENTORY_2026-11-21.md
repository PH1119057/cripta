# CRIPTA #878 — этап 0: первая фактическая инвентаризация

**Дата проверки на сервере:** 2026-11-21 около 06:40–06:47 UTC.  
**GitHub source baseline:** `e3717c4e8feddea94edfd4d9aebdbdfc3b9b9cdc`.  
**Task:** [#878](https://github.com/PH1119057/cripta/issues/878); [roadmap](CONTINUOUS_MARKET_HISTORY_RESEARCH_PLAN.md).  
**Scope:** только read-only server discovery + сохранение evidence в `research/**`; `STAGE_GATE_0=NOT_PASSED`.

## 1. Точная граница утверждений

`CHECKED HERE`: GitHub `main` и clean server source mirror; фактические `df/free/id/getfacl`; 4 семейства найденных public-trade каталогов по пяти инструментам, календарь **по имени и ненулевой длине файла**, samples headers; доступность каталогов для effective Unix actor `cripta`; наличие historical geometry и Entry result directories; job runner/intake service liveness и durable job paths.

`NOT CHECKED HERE`: полный recursive inventory всех migrated/cold/current source roots и consumers, повторный SHA256 readback **каждого** raw file, проверка содержимого каждой дневной CSV/gzip, независимое 5m/15m OHLC/Wilder ATR200 QC, full source-order event equivalence, все Entry/Exit/position ledger parity, точный job owner/outcome для двух старых записей, peak RSS и safe parallel workload. **Следующие «отсутствующие» даты отсутствуют только в четырёх осмотренных корнях, а не доказанно во всей системе/у провайдера.**

## 2. Host / capacity / actor — live preflight

| Факт | CHECKED HERE |
|---|---|
| Host | connected SentinelX `robot`, agent 0.11.14 |
| Main | GitHub `e3717c4e8feddea94edfd4d9aebdbdfc3b9b9cdc`; `/srv/cripta/source_checkout` exact same HEAD, `status --porcelain` empty |
| CPU | `os.cpu_count()=4` |
| RAM | total `4,105,383,936` B, available `2,727,899,136` B, swap `0` |
| Data SSD | `/data/cripta`: 210,240,860,160 B total, **13,317,492,736 B available**, use 94% |
| System SSD | `/`: 21,064,069,120 B total, **3,766,001,664 B available**, use 82% |
| Load | `1.96 / 1.87 / 1.83` (1 / 5 / 15 min) at sampled time |
| Effective actor | `cripta`: uid=107, gid=109; supplementary group `cripta-share` |
| Research roots | `/data/cripta/research/{runs,manifests,cache,worktrees,tmp}` observed; `cripta` read/write/traverse check returned True on runs/worktrees/manifests/tmp |
| Data root | `/data/cripta/datasets/raw` readable by `cripta`; inspect-only, **not** an authorization to write/delete raw |
| Runner | `cripta-job-intake.service` and `cripta-job-runner.service` active; actual target job behavior **not** proven by `active` |

**Permission observation:** generic `sentinelx` actor got Unix `Permission denied` on `ls /data/cripta/datasets/raw` and on some `du` trees; the bounded `sudo -u cripta python3` read-only checks succeeded. This was actor-specific Unix permission, **not** missing market data and not a request to widen ACLs. `getfacl` shows `cripta:cripta` rwx with inherited ACL on research roots.

**Resource decision:** no native download, full replay, broad compression or raw purge initiated. Resource budget for a new worker remains `NOT VERIFIED`; 94%-full data SSD and zero swap require per-chunk size/RSS measurement and preservation of existing jobs before any long compute.

## 3. Native public-trade input coverage — four confirmed roots

Initial universe: `LTCUSDT / INJUSDT / DOTUSDT / ARBUSDT / APTUSDT`. All five returned **the same date coverage** in these exact roots.

| Source ID | Physical root | UTC dates present (inclusive) | Days per symbol | Files per symbol | Important |
|---|---|---|---:|---:|---|
| `BULL25` | `/data/cripta/research/runs/p90l90_bull_2025_holdout_v1/inputs/{symbol}/public_trades` | 2025-03-22..2025-09-30 | 193 | 193 | 10-day warmup preceding Apr–Sep evaluation |
| `OLD90` | `/data/cripta/datasets/raw/20260518_20260816/{symbol}/public_trades` | 2026-05-17..2026-08-15 | 91 | 91 | Original 90d plus 1-day immediate prewarm |
| `WARM90` | `/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/inputs/{symbol}/public_trades` | 2026-05-08..2026-08-15 | 100 | 100 | 91 entries per symbol are symlinks; shares `OLD90` calendar, adds nine warmup dates |
| `RECENT29` | `/data/cripta/research/runs/p90l90_prev7d_29d_20261025/inputs/{symbol}/public_trades` | 2026-09-11..2026-10-19 | 39 | 39 | 32 entries per symbol are symlinks; Sep21–Oct19 evaluation with prior warmup |

**Filename-calendar checks:** all four ranges contiguous **within their respective min..max filename intervals** for each of the five symbols; 0 filename gaps, 0 zero-sized files, 0 duplicated dates **inside each directory**. Per-symbol deduplicated across these four roots: `193+100+39 = 332` unique UTC days, because `OLD90` is wholly included in `WARM90`. Do **not** sum physical bytes of symlinked paths as disk usage.

### Verified name-presence calendar at current upper bound

Target calendar: `[2025-01-01T00:00:00Z, 2026-11-21T00:00:00Z)`, since 2026-11-20 UTC had fully closed at inspection. `689` expected UTC days/symbol.

| Status | Exact inclusive period | Days / symbol |
|---|---|---:|
| PRESENT BY NAME + NONZERO | 2025-03-22..2025-09-30 | 193 |
| PRESENT BY NAME + NONZERO | 2026-05-08..2026-08-15 | 100 |
| PRESENT BY NAME + NONZERO | 2026-09-11..2026-10-19 | 39 |
| **NOT FOUND IN THESE FOUR ROOTS** | 2025-01-01..2025-03-21 | 80 |
| **NOT FOUND IN THESE FOUR ROOTS** | 2025-10-01..2026-05-07 | 219 |
| **NOT FOUND IN THESE FOUR ROOTS** | 2026-08-16..2026-09-10 | 26 |
| **NOT FOUND IN THESE FOUR ROOTS** | 2026-10-20..2026-11-20 | 32 |
| **TOTAL** | 2025-01-01..2026-11-20 | **332/689 present by name, 357/689 not in sampled roots** |

No conclusion yet about source availability in migrated legacy archives, provider archives, other research runs or recently recorded MarketSource.

### Schema spot checks (read-only gzip first line)

- 2025 `LTCUSDT2025-03-22.csv.gz`: `timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional`.
- 2026 `LTCUSDT2026-05-17.csv.gz` and `LTCUSDT2026-09-11.csv.gz`: same plus trailing `RPI` column.
- **Implication:** reader must explicitly handle source schema/version; old and recent CSV cannot be treated as byte-identical without schema validation.

## 4. Existing reusable geometries and Entry evidence

`/data/cripta/research/runs/p90l90_bull_2025_holdout_v1/results/l53_geometry_full_2025_v1/parts`: observed ten compressed outputs, five `states_{symbol}.csv.gz` and five `touches_{symbol}.csv.gz`. Existing `qc.json` includes per-symbol counts and SHA256 labels; `manifest.json` explicitly states **`RUNNING_NOT_VERIFIED`**. These are candidate reference artifacts, **not** independently accepted native equivalence for #878.

`.../results/entry_full_2025_v1/{symbol}/`: observed `candidates_{symbol}.jsonl`, `qc.json`, `manifest.json`, source commit and snapshot. 90d and 29d run roots each contain `entry_* / exit_* / l53_clean*` directories. Existing 2025 input archive state advertises `complete` for 965 files, but that historical state does not substitute for current per-file readback/QC.

Existing #454 snapshot artifacts are present (`minute_snapshot_pilot_APTUSDT_20260518_20261105` and seven-day `minute_chunk_v1_APTUSDT_20260518_7d_20261105`). Their partial minute features do **not** preserve the exact native order needed to settle TP-versus-Stop first-event semantics. Source consumers #158/#227 and frozen #638/#866 remain protected.

## 5. Existing jobs — preserve, do not duplicate

Observed `/data/cripta/jobs/running` has exactly two directories:

1. `C2-R866-H9CONT-3P-10CASE-20261120`: `status.json` says `running`, but its mtime was **2026-11-20T00:44:09Z**, about 30 hours before this check. Corresponding manifest notes a staged dependency on a predecessor. No verified live worker/outcome in this checkpoint. **`JOB_OUTCOME_UNKNOWN`** until independently reconciled.
2. `C2-R646-H29-PILOT-20261114A`: bundle has `pilot_status.json: SMOKE_EXIT_ZERO`, recorded PID 244808 absent from `/proc` at inspection; directory still under `running`. **Cleanup/ownership state unresolved**; do not delete or resubmit.

`queued`: 0 directory entries; `completed`: 49 directory entries; `failed`: 9 entries. Counts are filesystem observations, **not** independent acceptance of each result or proof that no other server-side process exists. Current runner/intake services show active PIDs, which does not certify the two listed jobs.

No job was created, resumed, restarted, cancelled or altered by #878.

## 6. Gate 0 outcome and next exact action

**GATE 0 = NOT PASSED.** Verified source/date inventory is **partial**; missing complete consumer/cold-data inventory, exact writer/job reconciliation and worker resource budget. Do not acquire large inputs, purge raw archives, start a long job or mutate production.

**NEXT_ONE_ACTION:** безопасно завершить **read-only** discovery of other migrated/current/cold native source roots and check exact outcome/writer for two pre-existing `running` job directories, then update the coverage matrix. Only after that determine smallest real-data sample, bounded worker RSS and first approved pilot.

**Mutation performed by this checkpoint:** only research-path GitHub evidence file + roadmap state update. `EXCHANGE=UNTOUCHED`, `PRODUCTION_DB=UNTOUCHED`, `LIVE=UNTOUCHED`, `RAW_PURGE=NO`.

## 7. Reproduce checks / provenance

Used connected SentinelX `robot` with bounded read-only calls: `df -B1 / /data/cripta`, `free -b`, `uptime`, `id cripta`, `ls`, `getfacl`, `systemctl status cripta-job-{intake,runner}.service`, `systemctl list-units --all --type=service`, and `sudo -u cripta python3 -c` using only `Path.iterdir/stat/read_text`, `gzip.open(...).readline()`, `os.access`, `/proc/<pid>.exists()`. No filesystem write commands in these probes.

**Future independent QC:** SHA256 source readback, date-to-UTC-boundary and row-order verification, schema-semantic checks for `RPI`, first-event native equivalence against matched exact frozen scenario, plus month-checkpoint trade-by-trade parity.

## 8. Дополнительный независимый read-only проход — 2026-11-21 около 10:05–10:10 UTC

**BASE (exact GitHub main before probes):** `00f80f4bf5584fc2d39153270fe0f0fa5b66e9da`. Mirror `/srv/cripta/source_checkout` HEAD тот же. **Actor:** `sudo -u cripta python3`, только read-only `Path.iterdir/read_text`, `gzip/csv`, `hashlib.sha256`, `/proc`; без write job/server. `TASK_RESUME=RESUME_FOUND` (#878, existing plan/evidence, previous Stage 0 merged). Предыдущие задачи #866/#646 **не** перезапускались.

### 8.1 Дополнительные физические roots — inventory по первому уровню

- `/data/cripta/script_archive` доступен actor `cripta`; корни `legacy_data_archive_20260930` содержат `entry_v1_20260912T024510Z`; `legacy_data_legacy_20260930` содержит `cripta1_snapshot_20260822` (`src/docs/reports/scripts`); `legacy_data_lifecycle_v1_20260930` содержит `overlay/evidence`. **Не** заявлено об исчерпывающем поиске всех вложенных архивов.
- `/data/cripta/research/cache/_legacy_data_research_cache_20260930` пуст по верхнему уровню; `legacy_srv_research_cache/eo3_full_path_1m_cache` показывает другие девять монет (`UNIUSDT, 1000PEPEUSDT, SOLUSDT, DOGEUSDT, LINKUSDT, XRPUSDT, ADAUSDT, BTCUSDT, ETHUSDT`), не целевые пять. `/data/cripta/research/manifests` инвентаризирован только по верхнему уровню.
- `/data/cripta/datasets/raw/bybit_public_trades_daily_v1` содержит только symbol dirs `BTCUSDT`, `ETHUSDT`; это не источник закрытия gap пяти символов. `/data/cripta/datasets/canonical/mayak_dispatcher_market_v1` — feature/context CSV/gzip + provenance, **не** оригинальный native public trades input без отдельного fidelity proof.
- Bounded target search: 276 directories в `/data/cripta/research/runs`, 15 `inputs|data|source` roots на проверенных naming patterns, 15 попаданий `{symbol}/public_trades` (пять символов × уже найденные BULL25/WARM90/RECENT29), **0 дополнительных date ranges**. Это **не** полный рекурсивный поиск произвольных вложенных имён; вывод о глобальном отсутствии ещё запрещён.

### 8.2 Реальный маленький native-data QC (не полная независимая parity)

Streaming full gzip/CSV read + SHA256 compressed source; `row timestamp` в пределах UTC day, цены > 0, неубывающие timestamps, обе стороны Buy/Sell. Только два малых репрезентативных файла:

| Root / symbol UTC date | Compressed bytes | Trades parsed | Invalid | Off-day | Timestamp descending | Equal-timestamp adjacent pairs | Exact compressed SHA256 |
|---|---:|---:|---:|---:|---:|---:|---|
| BULL25 / ARBUSDT 2025-03-22 | 1,748,556 | 41,745 | 0 | 0 | 0 | **26,157** | `646aa8c8da0ed698db06cb4e7f65a5bfa703b520a9994f5a616dd2e0191b7ce7` |
| RECENT29 / LTCUSDT 2026-09-11 | 4,135,630 | 118,896 | 0 | 0 | 0 | **80,507** | `8f55b94adca40585dcb3ee5f3bfef3c5bd7562932120569e41a86729b843254f` |

- **Schema observed:** 2025 ARB = 10 columns, recent LTC = same fields plus `RPI`. This difference is proven only for the two inspected files; additional schema versions still require discovery.
- **Crucial finding:** many trades share identical timestamps. Timestamp sort alone is **not** a valid exact event-order replacement; stable original row order/tie break and source provenance are obligatory. No first-event TP/Stop equivalence has been claimed.
- **NOT VERIFIED:** independent symbol/side sizes, exact deduplication, full-file native row semantics vs provider, all native events over the target calendar (not counted), per-day SHA256 over all 1,660 logical source-day instances, native cost/Entry/Exit parity, ongoing ingest of MarketSource. Do not extrapolate 2-file QC to 332-days coverage.

### 8.3 External job and storage safety

- `/data/cripta/jobs/running`: unchanged two records. #866 `status.json` says `running` but has `STAGED_ONLY. DO NOT SUBMIT UNTIL PREVIOUS JOB ... VERIFIED` in manifest notes; no active exact matching process observed in bounded `/proc` process-name scan (the scan's own command matched itself). This is **not** enough to assert completed/cancelled; `JOB_OUTCOME_UNKNOWN` persists.
- #646 `bundle/pilot_status.json` says `SMOKE_EXIT_ZERO` for PID 244808, but bundle still under `running`. No cleanup or resubmit; exact runner ownership/outcome requires independent reconciliation.
- On 2026-11-21, live `df -B1 /data/cripta /`: Data SSD free **13,317,177,344 B**, system SSD free **3,721,195,520 B**, data use 94%; `free -b`: available RAM **2,721,501,184 B**, swap 0. This is a point-in-time observation, not a reservation of bytes.
- `cripta-native-public-trade-source.service` **active**, PID 378530 at check; its live capture data is **not** proven to be historical public-trade daily archives.
- `cripta-market-hot-retention.service` **FAILED** at 2026-11-21 10:06:59 UTC: first retention ExecStart exit 0, second `cripta-native-public-trade-hot-retention.py --apply --batch-limit 15000` exit 1. **Important adjacent operational blocker** for capacity planning; no changes under #878 to this service. Generic SentinelX journal access returned Unix permission denied; exact traceback/root cause and current next timer cycle **NOT VERIFIED**. This needs isolation under existing storage/service owner (#642), not a speculative repair from #878.
- No research worker, data download, server-side output, raw purge, Exchange or production DB mutation started by #878.

### 8.4 Stage status and next one action

`GATE_0=NOT_PASSED`. Stage 0 read-only coverage and two-file gzip QC have meaningful evidence. **NEXT_ONE_ACTION:** reconcile stale #866/#646 research job outcome through approved runner/manifest read rail, and finish a bounded deeper discovery of cold/migrated trade sources without global filesystem walk; separately notify storage owner of failed native hot retention with exact failure evidence before bulk compute. Then decide smallest measured memory/SSD-safe pilot.

`NOT CHECKED HERE`: full exact native source capture chain and current reader fingerprints; every input day; detailed job trace/lock and review of successful/failed runner runs; source/position/Exit native parity; recent producer archive provenance; service failure root cause. No gate promotion.

## 9. Runner/outcome/other archive gates — 2026-11-21 11:43–11:47 UTC (read-only)

**BASE exact GitHub main:** `c8eea34b3d49e99c147d3d8f7195684e6d7c6be8`. Source scope: Issue #878 and previously merged #883 evidence. **NOT** a new research job or service incident repair.

### 9.1 Approved runner semantics and external task ownership

Read the **actual loaded** `/data/cripta/research/tooling/current/research/server/jobs/runner.py` (3,480 bytes) as `cripta`. Its `main()` handles queued jobs serially via `next_job()`, each `execute()` runs a `subprocess.run`, persists `status.json`/`result.json` and moves the job directory to `completed` or `failed`. An old `running` directory **without terminal result** is not successful research evidence.

At this check:
- Runner service PID **4136843** (active) and `/proc/4136843/task/4136843/children` contains exactly **483140**. Child command names `C2-R866-HS10TO2-LS-183D-20261121/bundle/job_launcher.py`. Job manifest is Issue **#866**, 183d 36-portfolio LONG/SHORT HS −10…−2 sweep; its report `stdout.log` had just advanced to **27,170,751 / expected 197,794,465 ticks**, and stderr was empty. **LIVE JOB RUNNING** as observed, no completion/QC assertion. Do not launch #878 competing heavy worker.
- `C2-R866-H9CONT-3P-10CASE-20261120` still in `jobs/running`, `status.json: running`; corresponding stdout last updated about **118,204 seconds** before 11:44 UTC, ends during 90d at `day_epoch=20647`, has no `result.json` and no matching separate worker in observed `/proc`; runner has only the other child. **ORPHANED/STALE CANDIDATE — OUTCOME UNKNOWN**, not `FAILED` or `COMPLETE`. Do not delete, resubmit or alter another issue's state.
- `C2-R646-H29-PILOT-20261114A` still under `jobs/running`, has no canonical `status.json`; `bundle/pilot_status.json` says `SMOKE_EXIT_ZERO` for PID 244808, no native runner report dir for this exact name. Historical smoke exit is **not** proof of accepted runner terminal status. **OWNER/CLEANUP OUTCOME UNRESOLVED**. No mutation.
- Exact predecessor `C2-R866-S48-183D-FULL1-20261120` is under `jobs/completed`; `status.json` and `/srv/cripta-share/reports/jobs/<id>/result.json` both record `completed`, `exit_code=0`, finished epoch 1795135305. Its research-output QC **NOT CHECKED IN #878**.
- `jobs/queued` contains **0 directories** at this instant; `jobs/running` **3** dirs (two stale/unresolved + one verified active). A directory count does not override exact PID/job evidence.

### 9.2 Additional cold/cache source inventory

- Additional bounded, metadata-only scan over `/data/cripta/script_archive`: **3,500 directories, 8,249 files**, name-match hits for the five target symbols/`public_trades` **0** in visited nodes. Two protected OS/PostgreSQL archive directories gave actor `cripta` Unix `Permission denied`; **64 directories remained pending at scan cap**. Does **not** scan internal ZIP/TAR payloads and does **not** prove no relevant archive beyond cap.
- `/data/cripta/research/cache/r158_spot_firstmonth_20261030/{symbol}` contains 30 compressed files for each target symbol, names `2026-05-18..2026-06-16`; header `id,timestamp,price,volume,side,rpi`. This cache belongs to **#158 SPOT first-month**, not a verified LINEAR native exact-price-event equivalent and not a new calendar range (those dates already lie inside WARM90). It must not silently fill target LINEAR raw gaps.
- Metadata sum of all **logical** compressed `public_trades` files across BULL25 193d + WARM90 100d + RECENT29 39d for 5 symbols = **13,306,684,338 bytes** (7,925,391,431 + 3,038,474,839 + 2,342,818,068). Of these, WARM90 has **91 symlinks/symbol** and RECENT29 **32 symlinks/symbol**. This is **not** unique physical disk consumption, and using it as `du` would double-count shared originals. It is a **rough logical input budget**, not a measured exact compact-source worker footprint.

### 9.3 Independent capacity + retention observations

At check, `df -B1`: `/data/cripta` free **13,302,910,976 bytes** (94% used), system `/` free 3,765,387,264 bytes; `free -b`: available RAM **2,527,444,992 bytes**, swap **0**. These values are snapshots only, not reserved capacity.

Native producer and runner service were operational in previous checks; read-only `systemctl status` reconfirmed runner active with its current child. Critically, timer-driven `cripta-market-hot-retention.service` independently **FAILED AGAIN** at **2026-11-21 11:46:43 UTC**: first `cripta-market-source-hot-retention.py --apply --batch-limit 15000` ExecStart exit 0, second `cripta-native-public-trade-hot-retention.py --apply --batch-limit 15000` exit 1. Root cause/traceback remain `NOT VERIFIED` through current actor/tool; no restart, repair, ACL/DB/deletion or other service mutation in #878. Existing owner route: #642.

### 9.4 Status and one next action

**`GATE_0=NOT_PASSED`; `ACTIVE_STAGE=0`; `TASK_RESUME=RESUME_FOUND`; `RESEARCH_JOB_#878=NOT_STARTED`.** Source naming gaps still 357 dates/symbol in visited source roots, unproven globally; no full source integrity/event replay parity.

**NEXT_ONE_ACTION:** use approved read-only owner/runner rail to reconcile **two stale external job records #866/#646** with their owners (without job mutation); independently follow #642 retention failure and reserve safe bytes before any new data acquisition. Continue only finite cold-source manifest/ZIP entry listing when preflight supports bounded I/O; then choose tiny exact native-pilot scope/worker budget. No new full-history download yet.

**CHECKED HERE:** GitHub main SHA, actual runner Python/worker PID/child, job dirs and stdout/result paths, bounded metadata-only archive/cache search, logical compressed byte budget, df/free, retention unit state. **NOT CHECKED HERE:** cold archive ZIP/TAR interiors, complete 689-day source calendar across all providers, exact lineage of SPOT cache, independent replay parity, retention traceback, existing #866 downstream QC, actual safe writer free-space reservation. All production trading/exchange/DB/Strategy/Live surfaces untouched by #878.

## 10. Reuse provenance — existing run manifests, without repeated native full scan

Read-only source identity at `/data/cripta/research/runs`:

1. **2025 BULL25:** `p90l90_bull_2025_holdout_v1/run_manifest.json` records `acquisition_stage=FULL_QC_PASS`, `archive_object_count=965` (193 UTC dates × 5 symbols), `archive_trade_rows=204446371`, `status=INPUT_COMPLETE_QC_PASS_NO_STRATEGY_REPLAY`, provider template `https://public.bybit.com/trading/{symbol}/{symbol}{YYYY-MM-DD}.csv.gz`, and immutable `source_snapshot_sha256` mapping. Independently inspected `qc_full.json` records `stage=ACQUISITION_QC_PASS`, `files=965`, `total_native_trades=204446371`. Historical documented input QC is useful reuse evidence, **not independent new full-sample QC by #878**. Exact fresh `sha256` of `ARBUSDT2025-03-22.csv.gz` matches `inputs/MANIFEST.json` entry `646aa8c8da0ed698db06cb4e7f65a5bfa703b520a9994f5a616dd2e0191b7ce7`.
2. **2026 WARM90:** `p90l90_90d_warmup_v2_20261024/run_manifest.json` has `independent_input_qc.status=ALL_FIVE_PASS` for **500 object-days** May08–Aug15 (5 × 100 days). The historical QC contract explicitly includes per-file SHA256, gzip CRC, rows, first/last UTC timestamps, contiguous 100 dates, **9 regular files and 91 SHA-linked files per symbol**. Recorded totals: APT 14,904,155, ARB 19,280,482, DOT 17,282,499, INJ 22,032,057, LTC 7,630,879 native rows. **No fresh full readback** of all 500 inputs performed in #878.
3. **2026 RECENT29:** `p90l90_prev7d_29d_20261025/inputs/manifest_{symbol}.json` contains **39 per-symbol file records** (Sep11–Oct19), with date, bytes, row count, SHA256 and first/last timestamp; 7 `new_download` entries/symbol. Recorded row totals: LTC 5,901,497; INJ 10,946,663; DOT 9,308,909; ARB 25,014,881; APT 10,692,442. Fresh full read `LTCUSDT2026-09-11.csv.gz` SHA256 matches its manifest entry `8f55b94adca40585dcb3ee5f3bfef3c5bd7562932120569e41a86729b843254f`. **195 hashes recorded ≠ independently revalidated today;** preservation of trade row order and exact source reader remains mandatory.

**Data reuse decision:** inputs for known 332-day calendar have substantial historical QC/provenance and two independently revalidated examples. Use existing source objects/manifests with version-fingerprinted consumers, not duplicate downloads, while performing the remaining independent QC/fidelity checks. **Do not infer** continuous 689-day coverage or native Entry/Exit replay parity. `GATE_0=NOT_PASSED`.

## 11. In-memory source-order compression feasibility (two representative dates) — 2026-11-21 ~13:25–13:27 UTC

**Baseline:** GitHub `main=bc378567f90aa427e56a8b01b275eae4d8a75f66`, server mirror HEAD exact/clean. **Executor:** `sudo -u cripta python3` on `robot`; no new job and no durable server write. Exactly two previously read source files, one old 10-column and one new 11-column schema, were read again in bounded memory-only pilot.

### 11.1 Lossless selected-price-event representation

Experimental selected-event tuple per native row = `(original_csv_row_ordinal, timestamp_string, price_string)`, **no floats** and no reordering/sort. A tab-delimited UTF-8 stream was compressed using `gzip.GzipFile(..., compresslevel=3, mtime=0)` into `io.BytesIO`, decoded again and SHA256 compared over the full selected uncompressed stream; row count and sequential ordinal were checked. Repeated timestamps retain their native sequence because the ordinal is preserved.

| Native source | Rows | Source gzip bytes | Selected price-event gzip bytes | Selected/source | Equal-timestamp adjacent pairs | Readback |
|---|---:|---:|---:|---:|---:|---|
| `BULL25 ARBUSDT 2025-03-22` | 41,745 | 1,748,556 | **249,698** | **14.28%** | 26,157 | count/ordinal/digest exact |
| `RECENT29 LTCUSDT 2026-09-11` | 118,896 | 4,135,630 | **661,699** | **16.00%** | 80,507 | count/ordinal/digest exact |

- ARB selected-stream SHA256 (original and decompressed match): `ae97c71baafb2bbf53f8c19b0cf1232b3c455b71418f235fb5321968378cf88c`.
- LTC selected-stream SHA256: `8fa136d00beecea763ddd13f898e643992fba3da6a0dd2f3a7e45a206a33e3f6`.
- **Strict scope:** neither representation contains `symbol/side/size/trdMatchID/tickDirection/grossValue/homeNotional/foreignNotional/RPI` when present, except identity implied by outer source/manifest. **Not** a complete MarketFacts source or a validated replacement for native, liquidation/OI/OB or native first-event semantics. Two days do not project savings to all symbols/days.

### 11.2 Control experiment preserving all original trade fields

Experimentally constructed a stream of `row_ordinal + ALL original csv.DictReader field strings` (10 original columns for ARB2025, 11 for LTC2026), with separator safety assert and exact uncompressed digest/ordinal/count equality on decompress:

| Native source | Original gzip bytes | Full-field experiment gzip bytes | Relative |
|---|---:|---:|---:|
| `ARBUSDT 2025-03-22` | 1,748,556 | **2,052,994** | **117.41%** (+17.41%) |
| `LTCUSDT 2026-09-11` | 4,135,630 | **5,017,869** | **121.33%** (+21.33%) |

This naïve full-fact encoding **does not save space**, despite exact selected textual value preservation. It is not source byte-equivalent gzip and has not undergone consumer/first-event parity. **Architecture conclusion NOT YET AUTHORIZED:** use separately versioned exact price-path and full-fact/native source retained where needed; first prove all consumer requirements and formal parity before any substitution, migration or raw reclaim. No `GeometrySpec`, Entry/Exit code, native source, or service was changed.

### 11.3 Other current gates observed

- GitHub main + server source mirror = `bc378567f90aa427e56a8b01b275eae4d8a75f66` and clean (bounded preflight).
- `/data/cripta` free **13,299,777,536 bytes** (94% use); RAM available **2,495,152,128 bytes**, swap 0.
- Existing #866 183d native HS sweep continues; at 13:24:55 UTC `stdout.log` had progressed to **187,804,627 / 197,794,465** ticks with current child PID 483140 under runner 4136843. This is process/progress evidence, **not** completed.
- `cripta-market-hot-retention.service` timer failed again at **13:24:26 UTC**, second native-retention phase exit1. Read-only inspection of installed `cripta-native-public-trade-hot-retention.py` shows `--apply` requires effective Unix `postgres` UID and fail-closed preflight; `systemctl show ... --property=User,Group` confirmed `User=postgres, Group=postgres`. **Root cause still UNKNOWN** (exact stderr inaccessible from current read actor); do not attribute failure to wrong Unix User. No service manipulation, re-arm or privileged apply attempted.

### 11.4 Gate / follow-up

`GATE_0=NOT_PASSED`, `GATE_2=NOT_ATTEMPTED` (this is read-only preflight feasibility, **not** Stage 2 acceptance). **Next one action:** reconcile old #866/#646 job terminal/cleanup state under owner rail and retention failure #642 under existing approved service owner; then approve bounded resource budget for an independent test preserving consumer-required fields and proving exact native first-event parity. No new source data acquisition or raw deletion.

## 12. Provider presence preflight — Bybit public archive HTTP HEAD only (2026-11-21 ~13:30 UTC)

**Read-only network action:** actor SentinelX command `curl -I -L -sS --max-time 10|12` against exact public URL template `https://public.bybit.com/trading/{symbol}/{symbol}{YYYY-MM-DD}.csv.gz`; no GET, no archive payload, no source file/DB/server writes. Provider returns AmazonS3 / CloudFront headers. `HTTP 200` confirms the named object exists and a declared nonzero compressed response size at probe time; does **not** validate compressed bytes/CRC/schema/event continuity/first-event semantics.

**All five symbols on target period's first and last completed UTC day:**

| Symbol | 2025-01-01 | Content-Length B | 2026-11-20 | Content-Length B |
|---|---|---:|---|---:|
| LTCUSDT | HTTP 200 | 5,978,941 | HTTP 200 | 4,285,094 |
| INJUSDT | HTTP 200 | 5,918,874 | HTTP 200 | 10,463,361 |
| DOTUSDT | HTTP 200 | 7,665,120 | HTTP 200 | 6,862,727 |
| ARBUSDT | HTTP 200 | 6,891,956 | HTTP 200 | 9,590,978 |
| APTUSDT | HTTP 200 | 11,213,689 | HTTP 200 | 7,493,541 |

All 10 returned `HTTP/2 200`, with a nonempty `Last-Modified` response header; final day 2026-11-20 was already fully closed at probe.

**Representative interior missing-range probes (LTCUSDT only):**
- `2026-04-15`: HTTP 200, 2,125,264 B.
- `2026-08-20`: HTTP 200, 3,256,105 B.
- `2026-10-20`: HTTP 200, 3,528,780 B.
- `2025-01-01` above covers the 2025 initial missing range, 5,978,941 B.

These dates were previously `NOT FOUND IN SAMPLED LOCAL ROOTS`, **not** proven unavailable from Bybit. The provider **has** objects on tested gap boundary/interior dates, so the future acquisition planner should first query remote existence/size/provenance for targeted exact missing ranges and avoid redundant downloads for verified local days.

**GATES:** 5-symbol start/end provider object presence `HEAD_PASS_10/10`; 3 additional interior LTC gap probes `HEAD_PASS_3/3`. This is **not** 357-days × 5-symbol provider coverage, nor source integrity or dataset acquisition. `GATE_0=NOT_PASSED` due existing research-job owner/cleanup and disk/native-retention issues; no download started.

## 13. External runner transition observed, not #878 E2E — 2026-11-21 13:32:56 UTC

At the next read-only check using `cripta`, the separate `#866` job `C2-R866-HS10TO2-LS-183D-20261121` was under `/data/cripta/jobs/completed` (**not** `running`) with durable `status.json` and `/srv/cripta-share/reports/jobs/<id>/result.json`: `status=completed`, `exit_code=0`, `finished_at_epoch=1795267901`, `duration_seconds=7477`, `error=""`. Its `stdout.log` reported `status=QC_PASS`, `ticks=197794465`, `rows=27475` under `hs_depth_183d`. Runner `4136843` had **no child** in `/proc/4136843/task/4136843/children` at the time of check.

This is **verified terminal completion and self-reported job QC for an external #866 calculation**, **not** independent verification of its entire result ledger by #878, and **not** any Stage 0 or native-price-codec acceptance. The legacy `C2-R866-H9CONT-3P-10CASE-20261120` and `C2-R646-H29-PILOT-20261114A` terminal/cleanup outcomes **remain unresolved**; do not falsely mark all `running` dirs as active, nor silently clean up.

**Still blocked:** native-hot-retention repeatedly failing under #642 and data SSD at 94% use. `GATE_0=NOT_PASSED`; `#878_COMPUTE_JOB=NOT_STARTED`. Any future job requires fresh resource/actor/writer preflight and separate approved intake manifest after storage gate.

## 14. Exact retention guard + 10-file in-memory synthetic first-contact parity — 2026-11-21 ~14:53–14:58 UTC

**Exact baseline:** GitHub `main=d38ac3aa06aa6c2e5bf3f9132ef9ad3dbcf2749c`. Server mirror HEAD matched/clean at preflight. **Research actor:** `sudo -u cripta python3`. Read-only provenance of source paths, executable runtime service code, PostgreSQL state and native data; no production, Exchange, source archive, service or job mutation in #878.

### 14.1 Why native hot-retention fails: observed fail-closed financial guard

- Repeated `cripta-market-hot-retention.service` exit failure independently observed on **2026-11-21 14:52:25 UTC**: first market-source retention process exit0, second native-publicTrade retention `--apply --batch-limit 15000` exit1. Unit's exact captured `stderr` was **not** readable by this journal actor; do not assert that exact stderr equals the following independent check.
- Read installed `/usr/local/libexec/cripta-native-public-trade-hot-retention.py` via exact `cripta` read rail. Both `collect_check()` and `run_apply()` call `_financial_consistency()` **before eligible native event deletion**. The function executes `SELECT count(*) FROM runtime.hot_orders`, then requires `hot_orders == 0`, otherwise throws `NativeTradeRetentionBlocked("NATIVE_TRADE_RETENTION_HOT_ORDER_PRESENT")`. In addition it checks hot_position vs nonclosed ownership counts and exact open pin identity.
- Invoked only approved read-only command `sudo -u cripta python3 -B /usr/local/libexec/cripta-native-public-trade-hot-retention.py --check --batch-limit 1` on host `robot`. It returned **exit1**, structured `status=BLOCKED`, `error_class=NativeTradeRetentionBlocked`, `reason=NATIVE_TRADE_RETENTION_HOT_ORDER_PRESENT`. `--check` uses `BEGIN READ ONLY` in the script; **no deletion** was attempted by #878.
- Independently executed a single `BEGIN READ ONLY` PostgreSQL transaction through approved Unix `cripta`, DB `current_user=cripta`, `current_database=cripta`. Aggregate-only counts: `runtime.hot_orders=2`, `runtime.hot_positions=1`, `runtime.position_ownership WHERE state<>'CLOSED' = 1`. Counts sampled at this instant only; they do **not** establish real exchange order state, order identifiers or whether protective orders belong to one specific position.
- **FINDING:** current **read-only** retention gate is demonstrably blocked by active financial-order records, and the identical financial guard is called in `--apply`. This supports expected fail-closed behavior rather than a proven actor/permission defect. A separate apply stderr/role/environment cause is **NOT CHECKED HERE**, so no unsupported exact apply traceback assertion. Do **not** delete hot orders, tamper with open positions or remove protection to make retention pass. The storage owner remains Issue [#642](https://github.com/PH1119057/cripta/issues/642), which received a separate read-only evidence comment.

### 14.2 Expanded native source-order parity feasibility across all five symbols

**Sample inputs:** `BULL25` `2025-03-22` from `/data/cripta/research/runs/p90l90_bull_2025_holdout_v1/inputs/{symbol}/public_trades`; `RECENT29` `2026-09-11` from `/data/cripta/research/runs/p90l90_prev7d_29d_20261025/inputs/{symbol}/public_trades`; files `{symbol}{YYYY-MM-DD}.csv.gz`. All **ten** complete native gzip files read using streaming `csv.DictReader`. 2025 sample: 10 original fields, 2026: 11 including `RPI`. No copied source files persisted.

**Selected source-order price event** `(native CSV row ordinal, original timestamp text, original price text)`. Encode using tab-delimited lines in `io.BytesIO` + `gzip.GzipFile(compresslevel=3,mtime=0)`. Decompress/read all rows, verify consecutive ordinals, exact SHA256 of the entire decoded textual stream, row count and adjacent timestamp-tie count against native. For each file, choose first original price as a no-lookahead reference and run **eight synthetic first-contact tests**: LONG/SHORT × symmetrical thresholds ±0.05%, ±0.10%, ±0.20%, ±0.50% (`Decimal` comparisons). Compare exact first-hit **ordinal, timestamp text, price text, favorable/adverse label** on native events and decoded compact events. These are **generic price-contact benchmarks, not Strategy Entry/Exit/Economic/Stop replay**.

As a control for source-fact fidelity, an entirely separate `row ordinal + every original CSV field string` tab stream was gzip-encoded/decompressed, with full value-stream SHA256/row ordinal/number of columns asserted exactly (all ten cases). This control is text-value exact, **not original gzip byte identity**; the selected price stream omits side, qty, trade ID, tickDirection and optional RPI.

| Family, symbol, UTC date | Native trade rows | Original gzip B | Price-order stream gzip B | All-fields+ordinal gzip B |
|---|---:|---:|---:|---:|
| BULL25 LTC 2025-03-22 | 113,214 | 3,989,716 | 645,609 | 4,814,495 |
| BULL25 INJ 2025-03-22 | 105,632 | 4,204,591 | 666,397 | 4,975,787 |
| BULL25 DOT 2025-03-22 | 129,771 | 5,219,220 | 830,236 | 6,175,608 |
| BULL25 ARB 2025-03-22 | 41,745 | 1,748,556 | 249,698 | 2,052,994 |
| BULL25 APT 2025-03-22 | 98,397 | 4,054,142 | 536,703 | 4,755,412 |
| RECENT29 LTC 2026-09-11 | 118,896 | 4,135,630 | 661,699 | 5,017,869 |
| RECENT29 INJ 2026-09-11 | 179,124 | 6,537,878 | 1,010,389 | 7,868,457 |
| RECENT29 DOT 2026-09-11 | 438,095 | 16,067,557 | 2,426,744 | 19,334,745 |
| RECENT29 ARB 2026-09-11 | 655,736 | 25,007,866 | 3,576,471 | 29,934,303 |
| RECENT29 APT 2026-09-11 | 280,141 | 10,961,038 | 1,537,431 | 13,121,212 |
| **TOTAL** | **2,160,751** | **81,926,194** | **12,141,377** | **98,050,882** |

**Observed PASS (bounded):** ten of ten full original/selected textual readbacks, 80 of 80 synthetic exact first-contact comparisons, original-row order and tied timestamps preserved; 2,160,751 event rows. Selected price-order stream = **14.82%** of original gzip bytes weighted by compressed bytes. All-source-field+ordinal text control = **119.68%** of original gzip (19.68% larger). Python `resource.getrusage(...).ru_maxrss` in these single-process checks: about **25,996 KiB** price-path run and **80,620 KiB** all-field control. The experiments did not create outputs, call a trading API, open orders, write database rows or launch a runner job.

**No generalization:** just two selected UTC days × five symbols, and one gzip text encoding. This is **not** validated retention format for all consumers, source identity/CRC/fidelity of absent dates, complete GeometrySpec, native Strategy Entry/Exit, actual hard-stop/TP first-event equivalence, side/size/ID/RPI semantics or economics after fees. Future codec architecture and raw deletion require independent parity + retention/restore gates; do not turn experiment into policy.

### 14.3 Capacity implication and next bounded gate

Point-in-time `df -B1`: data disk free **13,280,264,192 bytes**, **94% used**; system disk free 3,745,296,384 B; available RAM 2,734,866,432 B, swap 0. Historical sampled source file-days per five-symbol target = 1,660; 357×5 = 1,785 dates/symbol missing **in sampled local roots only**. Illustratively if unverified missing days had the **same mean compressed size** as the known 13,306,684,338 B / 1,660, their raw total would be ≈**14.31 GB**—already above currently free 13.28 GB, **before reserve/overheads**. This is **only a budget heuristic**, not a measured forecast of variable historical trade volume, and symlink/physical-accounting details remain separately tracked.

**Decision:** no full-gap acquisition/full native replay when financial guard blocks cleanup and the SSD is 94% used. Continue existing-source, bounded in-memory validation only. `STAGE_0=IN_PROGRESS`, `GATE_0=NOT_PASSED`, `0/11`. Stale external #866 H9CONT/#646 job owner/terminal state still unresolved. Next exact action: preserve financial guards; #642 owner to reconcile safe retention/capacity with the live hot-order lifecycle, #866/#646 owners to reconcile old runner metadata, and only then use approved exact consumer contracts for the formal complete-field/native first-event pilot. **No production, source, service, Exchange or LIVE mutation by #878.**

## 15. Source manifest ↔ live file-system metadata inventory for every known native day (bounded, read-only)

One additional standalone `sudo -u cripta python3` read-only scan on host `robot` validated **every available logical file-day** in the three identified five-symbol `public_trades` roots. Historical BULL25 `inputs/MANIFEST.json` uses keyed entries, WARM90 and RECENT29 use `inputs/manifest_{symbol}.json` with distinct `size/bytes` field names. The validator normalized them for each of LTCUSDT, INJUSDT, DOTUSDT, ARBUSDT, APTUSDT and checked: path existence, target readable file type, actual `stat().st_size` equals manifest size and is positive, strict native filename/date equality, no duplicate or missing UTC dates **inside that family's specified range**, present 64-hex SHA256 manifest field and symlink resolution. No source data payload was opened for this inventory, so **not** a new full source hash/CRC verification.

| Logical source root | UTC per-symbol dates | Logical files | Symlinked files | Manifest/FS/date/size defects |
|---|---|---:|---:|---:|
| BULL25 | 2025-03-22..2025-09-30 (193) | 965 | 0 | **0** |
| WARM90 | 2026-05-08..2026-08-15 (100) | 500 | 455 (91/symbol) | **0** |
| RECENT29 | 2026-09-11..2026-10-19 (39) | 195 | 160 (32/symbol) | **0** |
| **TOTAL** | **332 distinct dates / symbol across these non-overlapping ranges** | **1,660** | **615** | **0** |

**CHECKED HERE:** all 615 symlinks resolved to existing regular files, and their paths are physically outside the corresponding `WARM90`/`RECENT29` family root. Logical total `13,306,684,338` bytes from file `stat` (follows symlinks) matches historical prior scan exactly, but **is not physical disk consumption**. Known roots have no internal filename-date holes. This validates file/manifest *metadata integrity*, not underlying bytes, source order for all objects, remote provider completeness, original order for repeated timestamps beyond tested samples, or any full native Strategy replay.

**NOT CHECKED HERE:** every-file hash recalculation and gzip CRC; source-event row semantics/venue consistency, continuity of the unobserved 357/689 UTC dates per symbol, archive content inside unscanned ZIPs, durable retained data after future purge, consumer/position/Exit parity. `GATE_0=NOT_PASSED`; safe storage/financial gates unchanged.

## 16. Exact native ledger pointer QC — actual Entry/Exit anchors, two eval days (2026-11-21)

**Task:** #878 (research-only), **BASE:** GitHub `main=9bf479284d81ce02c9a91a504d106d8578a2f583`; server source checkout HEAD exact at read-only preflight, clean. **Actor:** `cripta` UID 107, only read-only native gzip, immutable #866 ledger/result, and existing source snapshot. This verifies a **bounded link from stored historical event pointers to original Bybit prints**. It is *not* a regenerated independent Strategy Entry/Exit policy replay.

### 16.1 Reconcile separate stale jobs without altering their state

**#866 H9CONT three-period job** `C2-R866-H9CONT-3P-10CASE-20261120` remains under `/data/cripta/jobs/running`, `status.json=running`, no job-level `result.json` and no matching live worker. Its *internal 183d period* `/srv/cripta-share/reports/jobs/<job>/h9_continuity_183d/result.json` is present, `status=QC_PASS`, `native_ticks=197794465`, `ledger_rows=13052` and ten portfolio scenarios. Independently streamed SHA256 over full **9,033,269 B** `trade_ledger.jsonl` is **`1541f1c2ddd68a5ae04ab080d72d6f9ff74dbbb9d7615e97ee710c158dfc0b31`**, exactly the declared result hash. Its next `90d` progress stopped at `day_epoch=20647`, `ticks=54343593`, stdout last modified 2026-11-20 **02:54:27 UTC**; no 90d final QC or 29d run-result was observed. Current runner service started at **06:54:46 UTC** that day, after partial-work timestamps, but actual death/restart cause **NOT VERIFIED**. Do not rerun finished 183d blindly; overall job **OUTCOME UNKNOWN / PARTIALLY RECONCILED**.

**#646 historical pilot** `C2-R646-H29-PILOT-20261114A` has only a direct SentinelX `bundle/pilot.log`/`pilot_status.json`, showing `SMOKE_PASS_NO_WRITES`, **130,697 native ticks, 18 scenarios**, historical PID 244808, `SMOKE_EXIT_ZERO`, elapsed 90.1s. Its `pilot_metadata.json` declares `execution=direct tracked SentinelX background cripta Python pilot`, `mode=smoke`. It is **not** an approved runner-completed full 29d job: no canonical `status.json` or `/srv/cripta-share/reports/jobs/<id>/result.json`, and the stale directory remains in `running`. No cleanup/resubmit. These exact findings were separately posted to #866 and #646.

### 16.2 Correct source-order contract verified against pinned code

The 2025 native reader in `scripts/research/p90l90_bull2025_portfolio_v1.py` `native_ticks` increments `seq += 1` for each original CSV record **including warmup**, so `source_order` is **one-based global per-symbol**, not zero-based physical per-day row index. Correct 0-based local index for source date D is:

```text
native_day_row_index = ledger.source_order
                       - sum(manifest.rows of that symbol strictly before D) - 1
```

The frozen source Entry reader uses `trade_index += 1` and `rec["trdMatchID"]` as Entry identifier. SHA256 of four current read-only source scripts `p90l90_bull2025_portfolio_v1.py`, `p90l90_bull2025_entry_v1.py`, `p90l90_tp_only_second48_v1.py`, `p90l90_portfolio_pause_v1.py` exactly matched the saved 183d result's `source_snapshot_sha256` for each. **The earlier naive zero-based alignment was a verifier indexing mistake, not demonstrated trading-code drift.**

The pinned `p90l90_tp_only_second48_v1.py` `tp_only_on_trade` returns:
- `HARD_STOP` at original first trade **print price**;
- `RESTING_TP` at the previously placed **limit TP price**, when current first eligible native print *crosses* the limit (maker), so recorded exit price may differ from the print;
- `MARKETABLE_TP` at current native print when eligible.
The H9CONT custom extension records `SOFT_STOP` at current print. **Do not compare RESTING_TP limit fill price for strict equality to crossing print**; compare first eligible source pointer/order/time and directional crossing.

### 16.3 Independent stored native-ledger event-pointer parity (not strategy replay)

Read all original five-symbol gzips for **2025-04-01** and **2025-05-30**, two evaluation days, **10 files, 2,259,413 original trade rows**. Independently rehashed the full **89,316,092 B** of original compressed bytes; all **10/10 SHA256 + sizes** matched the existing `qc_full.json` metadata. For each date: use per-symbol warmup-inclusive manifest prefix, locate each actual saved ledger Entry/Exit by one-based source order in native CSV; construct a separate `row_ordinal<TAB>timestamp<TAB>price` gzip stream **in memory**, then compare the same original print/time/source-order against its decoded version and the frozen ledger.

| UTC evaluation day | Native ledger Entry references | Exit references | Unique Entry/Exit event identities | Result |
|---|---:|---:|---:|---|
| 2025-04-01 | **110** | **90** | **20** | all pass |
| 2025-05-30 | **53** | **84** | **22** | all pass |
| **TOTAL** | **163** | **174** | **42** | **337/337 pass** |

The 337 references include repetition of the same actual event in ten independent portfolio scenarios; **42** is the count of distinct event identities. Full breakdown: **163/163 Entry** source time/print/real `trdMatchID` exact; **174/174 Exit** source order/time exact; of exits **24/24 HARD_STOP** print price exact, **24/24 SOFT_STOP** print price exact, **126/126 RESTING_TP** maker price-crossing condition exact. **10 references** (one underlying distinct INJUSDT LONG RESTING_TP event repeated across scenarios) have expected fill-price-vs-print difference `8.9890` vs `8.9900`; this is explained by the frozen limit-fill code, not unexplained drift. **Zero unexplained mismatches.** In-memory price stream **13,668,520 B** vs original gzips **89,316,092 B**, ratio **15.304%** for these two days. No stable persisted compact object was created on server.

### 16.4 Reproducible RESEARCH-only bounded audit instrument

A new, separate **non-production** instrument `research/history/native_ledger_pointer_qc.py` now encodes the source-of-truth-sensitive checks: explicit CLI input roots/dates (max 3 days), original gzip max 32 MiB/file, in-memory compact stream max 32 MiB/file, `qc_full.json` manifest/size/hash and saved result/ledger hash, one-based source order, strict Entry print/trade ID, and maker RESTING_TP directional print crossing. It emits machine-readable JSON PASS/FAIL to stdout; does **not** download, write files, touch PostgreSQL, submit jobs or call Bybit. First actual in-memory execution of its exact source code under `cripta` yielded `SELFTEST_PASS` and `PASS_BOUNDED_EVENT_POINTER_QC` on the above two dates (337 references/42 unique, 10 files, 2,259,413 rows, 0 server writes). Exact code was then published to the research branch, followed by an empty-stream safety refinement and a `tests/research/test_native_ledger_pointer_qc.py` regression suite (unit, strict wrong trade ID/hash/zero-based source order/non-crossed maker, date bounds). **New GitHub CI/suite results are not yet verified at this checkpoint.**

**Important NOT VERIFIED:** independent regeneration of candidate eligibility/geometry/position state, full source-order detection of first achievable stop vs TP across all events, checkpointed 29/90/183d trade-by-trade replay, costs/simulated execution realism; 357 unobserved local dates per symbol; all 1,660 fresh source-hash reads; protected orders and Exit still open in production. Task **Stage 0 remains OPEN, 0/11**. Related #642 native retention remains financially fail-closed due hot orders; raw deletion/capacity remediation is **not** part of this research change.


### 16.5 GitHub workstream-boundary validation

First merge attempt of PR #887 was **BLOCKED** by required `scope` check despite four passing `quality` jobs. Exact trusted main policy `.github/scripts/workstream-scope.js` classifies plain `tests/` as **CORE** and `tests/research/` as **RESEARCH**; the first test path `tests/test_research_native_ledger_pointer_qc.py` was therefore cross-contour in the `research/*` branch. The original test file existed **only in the PR branch**, not `main`. The same tests were moved without logic changes to `tests/research/test_native_ledger_pointer_qc.py` and the import repository-root path adjusted from `parents[1]` to `parents[2]`. The old branch-only `tests/` file was removed; the complete PR-vs-main diff now contains exclusively `research/history/*` and `tests/research/*` paths. **No security/workstream check was disabled or bypassed.** New branch HEAD full `scope`/`quality` run and independent merge confirmation remain required before `MERGED` can be claimed.

## 17. Independent historical -10% HARD_STOP first-crossing proof — 2026-11-21

**Scope:** #878 RESEARCH only, base GitHub main 240b4fe3e0215dd96be955dcf279161e34cdf34f. No production, Exchange, DB, or research-job mutation. Source frozen from separate #866 H9CONT 183d partial ledger with independent full SHA256 1541f1c2ddd68a5ae04ab080d72d6f9ff74dbbb9d7615e97ee710c158dfc0b31. The whole three-period #866 job is NOT COMPLETED.

**Model provenance:** Original scripts/research/p90l90_exit_90d_v2.py SHA256 = 3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10, matching #866 source_snapshot_sha256. Frozen initial_position uses an initial -10% HARD_STOP relative to original Entry: LONG threshold Entry × 0.90, SHORT Entry × 1.10; hits_stop checks first native print after entry, before resting TP on each eligible trade. This is historical research model logic, NOT current production stop authorization. One-based cumulative per-symbol source_order including warmup was independently established in §16.

**Method:** Select same-day HARD_STOP entries/exits and scan EVERY original native trade in physical row order strictly after the entry ordinal, through the recorded exit. Preserve tied timestamp ordering. Find the FIRST print crossing the fixed -10% threshold. Independently decode a deterministic compressed ordinal/timestamp/price stream in memory and repeat. Compare original first crossing, decoded first crossing, and immutable ledger exit exact row ordinal, timestamp text, print price. Verify source gzip hashes against existing BULL25 qc_full manifest, entry trade ID, all anchor times/prices.

| UTC day | Symbol | Side | Entry source_order | First-stop exit source_order | -10% level | Exit print | Scenario references | Result |
|---|---|---|---:|---:|---:|---:|---:|---|
| 2025-05-08 | ARBUSDT | SHORT | 4080911 | 4160869 | 0.34430 | 0.3443 | 9 | PASS |
| 2025-05-08 | INJUSDT | SHORT | 6972549 | 7082717 | 10.63920 | 10.6400 | 9 | PASS |
| 2025-09-22 | ARBUSDT | LONG | 43273685 | 43461416 | 0.43119 | 0.4311 | 2 | PASS |
| 2025-09-22 | DOTUSDT | LONG | 46676875 | 46820078 | 3.81483 | 3.8148 | 2 | PASS |
| 2025-09-22 | INJUSDT | LONG | 42036384 | 42114290 | 12.17430 | 12.1740 | 4 | PASS |

**Observed read-only 5-file pilot:** 5/5 distinct first-crossing matches, 26 scenario references, 1,402,199 native rows, 55,929,628 B original gzip vs 8,373,752 B selected compact gzip (14.972%); peak process RSS 25,272 KiB, 0 writes. These 5 were deliberately sampled; not random or population-level validation.

**Reproducible extension:** research/history/native_ledger_pointer_qc.py gains an optional --verify-hardstop-first-hit flag, pinned to exact frozen model SHA above; it requires at least one same-day HARD_STOP, max 32 unique stops per symbol/day, positive one-based source orders, exact anchors and unique earliest crossing on both native and compact events. In-memory execution of exact new source under actor cripta: SELFTEST_PASS (7 cases); full 2-day, 5-symbol scan = 10 files, 2,616,045 rows, 102,556,474 B original gzip vs 15,388,968 B compact (15.005%). Source pointer checks **209/209** (96 Entry, 70 HARD_STOP exit, 43 RESTING_TP exit), 47 distinct event identities. Additional earliest stop check **5/5 unique**, 26 scenario references, zero mismatch. No server write, raw download, service or trading operation. Additional targeted regression tests in tests/research/test_native_ledger_pointer_qc.py cover LONG/SHORT, tied timestamps, an earlier crossing and unpinned historical source; GitHub CI still needs independent PASS.

**NOT VERIFIED:** dynamic TP activation and first-fill priority, SOFT_STOP time/geometry, full independent Strategy Entry/Exit replay, portfolio contention and fees/exchange fills, all 183d dates, retention/restore. Stage 0 remains GATE_0=NOT_PASSED, 0/11. Existing #642 financial-order protection cannot be bypassed; latest retention systemd failed 2026-11-21 17:43:28Z, exact systemd stderr NOT CHECKED HERE. Data disk free 13,279,727,616 bytes at last check, 94% used. Old external #866/#646 job directories not modified. Next: finish CI, preserve evidence in GitHub main only if all scope/quality checks pass, then separately verify bounded dynamic TP/soft prerequisites while respecting the external storage/owner gates.

## 18. Causal TPOnly local Exit replay from original native prints and frozen closed L5-3 states

**Task:** #878 RESEARCH, GitHub source base main 13665d912f975a3afd10ecdfb2c82013daad0430. **Selected model:** existing separate #866 H9CONT 183d partial ledger, portfolio scenario CLASSIC_S48_OFF (no global new-Entry pause). The 183d ledger is independently immutable-hash verified; full #866 183d/90d/29d job is NOT terminal. Frozen historical code SHA256 verified against result source_snapshot_sha256 for p90l90_tp_only_second48_v1.py = b25210dbd9977fe85f568a52867b3673aea65c17308e19612cb216efae68164e, p90l90_exit_90d_v2.py = 3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10, and p90l90_bull2025_portfolio_v1.py = ebbf6d79bca9d9353b717833becd64eb7f36c88351ec1e77fe41ff152d3f4ee1.

### 18.1 Causal local Exit transition contract tested

- Fix accepted Entry to its exact ledger pointer (one-based per symbol across warmup) and verify the original Entry trade ID/time/price against immutable BULL25 source CSV. This does NOT recompute Entry candidate generation, width filters or portfolio occupancy.
- Independently SHA256-check daily original native gzip vs BULL25 qc_full.json and separately hash the source frozen L5-3 states file vs its symbol Entry manifest frozen_l53_sha256. Extract ONLY source states at 5m bucket boundaries between Entry and Exit; state_effective_ts must equal source_bar_end_ts and be <= each consuming trade timestamp.
- Reimplement only frozen TPOnly event precedence on each subsequent native trade, in source row order with timestamp ties preserved: (1) initial -10% HARD_STOP trade print, (2) previously active RESTING_TP maker limit crossing, (3) at first print after closed 5m bucket change, opposite RAW L5-3 target when >= +0.5% favorable; if already reached then MARKETABLE_TP at current print, else activate/update new resting limit from NEXT trade. Track bucket transitions and actual target changes.
- Separately encode selected Entry→Exit interval records as compressed ordinal/timestamp/price in memory, decode, and independently repeat the local Exit transitions. Require exact first reason, cumulative native source order, original timestamp text, modeled fill price and maker flag against saved ledger. No precomputed future MFE/MAE or next-bucket state is used on an earlier trade.

### 18.2 Real four-case observations

| UTC date | Symbol/side | Original Entry order | First Exit order | First reason | Closed 5m transitions | TP target changes | Exact first event |
|---|---|---:|---:|---|---:|---:|---|
| 2025-04-26 | INJUSDT LONG | 5,398,801 | 5,418,936 | MARKETABLE_TP | 36 | 7 | PASS |
| 2025-06-15 | LTCUSDT SHORT | 14,371,662 | 14,379,229 | MARKETABLE_TP | 25 | 9 | PASS |
| 2025-07-24 | DOTUSDT LONG | 32,399,116 | 32,418,394 | MARKETABLE_TP | 12 | 2 | PASS |
| 2025-08-10 | APTUSDT SHORT | 20,401,997 | 20,409,012 | MARKETABLE_TP | 22 | 0 | PASS |

**RESULT=PASS 4/4** unique selected actual historical MARKETABLE_TP first events, exact native and decoded compact Event/Exit parity. Three positions have genuinely changing TP targets; the fourth shows correct first marketable crossing on a new bucket with unchanged target history. Full native day files scanned: **4**, **881,605 trade rows**, **34,450,433 original gzip bytes**. The in-memory compressed **selected Entry→Exit intervals only** occupy **353,318 B**; not a fair ratio against full-day gzip, and not a complete all-consumer codec. Ad-hoc read-only pilot peak process RSS ~29,032 KiB; no source/DB/server filesystem write and no research-runner submit.

**Repeatability:** Added separate research-only stdlib CLI research/history/native_tp_causal_qc.py with explicit original/ledger/frozen/Entry-manifest paths and up to four symbol:entry_order:exit_order selectors. It validates original daily gzip SHA256, original/frozen research manifest SHA256, ledger hash, three pinned historical code SHA256 fingerprints; input caps: source gzip <=32MiB, frozen gzip <=16MiB, interval <=30,000 native trade rows, holding <=6h, compact interval <=4MiB in memory. Exact new CLI source executed as Unix cripta via Python -B -c without filesystem output: SELFTEST_PASS 5 checks and PASS_BOUNDED_CAUSAL_TPONLY_FIRST_EXIT for all four above. Added ten research-scoped positive/negative regression tests (earlier resting TP and earlier hard stop, incorrect model pin, source tamper, missing/noncausal L5-3 state, trade ID drift, case cap, SHORT opposite raw boundary); GitHub full scope/quality CI still requires independent verification before merge.

**Limits / gates:** This verifies selected historical TPOnly *local Exit after already accepted Entry*. It does NOT independently rebuild Entry signals, frozen geometry construction/ATR200 seed, H9 filters, SOFT_STOP, multi-position portfolio competition, fees/funding/slippage/actual Exchange fills, provider continuity or all historical days. GATE_0=NOT_PASSED / 0 of 11. No production release or trading mutation. Separate #642 financial-retention HOT_ORDER_PRESENT and data-SSD capacity, plus stale #866/#646 job outcomes, remain blocked/unreconciled. NEXT: required GitHub scope+quality checks, then precise approved bounded next Exit/geometry causality step, not a bulk 2025→present download.

## 19. Original native RAW-L53 first SOFT_STOP before the 18h H9 timer

**Scope:** #878 RESEARCH only, GitHub main starting baseline e278fa1f97bfc207cfa89f02c543ef842001400f. Independent immutable #866 H9CONT 183d partial result, ledger hash as in §16. No production/Exchange/DB/service/job mutation. Dedicated historical scenario H9CONT_18H_S48_OFF only; no new financial rules.

**Frozen provenance:** H9 source p90l90_h9_continuity_18_24_36_48_second48_v1.py SHA256 7354de1b4174d88ac53da74d0a27f886054325838e227a1cf05fc85c47372187, historical SOFT driver p90l90_soft_stop_2025_183d_replay_v1.py SHA256 a9ea1d19defce039fdb02d7465cdc39930ed949616c41ee95fa30bc6fb5fea48, TPOnly b25210dbd9977fe85f568a52867b3673aea65c17308e19612cb216efae68164e, base p90l90_exit_90d_v2.py 3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10. Exact hashes agree with partial ledger result source_snapshot_sha256. These are historical frozen research executable models, NOT current production policy.

**Causal bounded scope:** Choose same-UTC-day ledger SOFT_STOP rows with soft_stop_cause=L53_RAW_MINUS4_WIDTH4; require Exit strictly under 18h from Entry, so 18h continuous-adverse H9 timer cannot trigger earlier (timer first_h9_loss >= Entry). Fix accepted Entry pointer from immutable ledger and verify exact original trade ID/time/price from public native CSV, one-based global symbol source order. Read only BULL25 original gz files and closed-causal frozen L5-3 states, each SHA256 checked against its QC/Entry manifest. On each following original trade: apply initial -10% HARD_STOP, previously active resting limit TP, then new-5m closed RAW opposite TP marketable/activation, then RAW condition favorable(side,Entry,opposite_RAW) <= -4% AND working_width_pct_lower >= 4 to trigger SOFT_STOP at print. Do NOT use a future state before state_effective_ts=source_bar_end_ts. Repeat independently over in-memory compressed source-order/timestamp/price interval. Assert first reason, original global ordinal, exact time/price matches original ledger.

| UTC day | Symbol | Side | Entry global order | First SOFT_STOP order | Result | Closed-5m transitions | TP updates |
|---|---|---|---:|---:|---|---:|---:|
| 2025-04-06 | APTUSDT | LONG | 1,635,594 | 1,730,552 | PASS | 156 | 10 |
| 2025-05-22 | INJUSDT | SHORT | 10,355,889 | 10,539,864 | PASS | 180 | 21 |

**Observed 2/2 selected first SOFT_STOP exits PASS**, both native and in-memory compact event streams match exact recorded first ordinal/time/price and no earlier competing HARD_STOP, RESTING_TP or MARKETABLE_TP. Read two original full daily files **449,284 source rows**, **18,054,108 B** gz with fresh SHA; **1,771,713 B** compressed **selected Entry→Exit intervals only** (not a whole-day compression ratio). Initial ad-hoc bounded pilot peak Python RSS about 99,960 KiB. Exact independently-authored new RESEARCH source research/history/native_raw_softstop_qc.py ran in-memory with SELFTEST_PASS (5) and PASS_BOUNDED_RAW_L53_FIRST_SOFTSTOP (2/2) under Unix cripta, zero server file writes. Separate tests/research/test_native_raw_softstop_qc.py adds **12** fixture and negative tests: both sides, earlier first stop/TP, insufficient width, missing/noncausal frozen state, changed source bytes/Entry ID, H9 timer not safely excluded, unsupported cause, duplicate case selector.

**Reproducibility contract:** explicit ledger and source/frozen/Entry-manifest paths, explicit historical h9/soft source files and SHA256, max2 selectors, same UTC day, actual hold strictly <18h, original daily file <=32MiB, frozen file <=16MiB, interval <=250,000 rows and compressed <=12MiB. Source/ledger hashes fail closed and the runner writes only JSON to stdout. **GitHub required scope/quality and merge NOT YET CHECKED** at this checkpoint.

**NOT CHECKED HERE:** continuous adverse H9 timer timing (including 790 historical H9_LOSS_CONTINUOUS scenario references in #866 partial), geometry/ATR200 recomputation, real Entry candidate selection, portfolio occupancy, SOFT_STOP across all source days or causality across UTC-day boundary, money/venue fills. The 363 raw-L53 and 790 H9-continuous scenario counts reflect a historical partial result, not new independent full cohort validation. Financial #642 native-retention HOT_ORDER_PRESENT and 94% SSD used remain; old #866/#646 job metadata outcomes unresolved. Stage0 GATE_0=NOT_PASSED, 0/11, no bulk download/raw deletion. NEXT: GitHub scope+quality CI, exact merge readback only if PASS, then separately research H9 timer state causal replay.

## 20. Cross-day native H9_LOSS_CONTINUOUS first-event parity — 2026-11-22

**Authority and scope:** #878 RESEARCH, exact branch baseline GitHub main c1273cabdea579ad618eed6329c7b4fc81a62ac0, matching source mirror at first preflight. Immutable external #866 H9CONT 183d **partial** QC ledger source, NOT the completed #866 three-period job. SHA256 of ledger 1541f1c2ddd68a5ae04ab080d72d6f9ff74dbbb9d7615e97ee710c158dfc0b31. Frozen historical H9 source SHA256 7354de1b4174d88ac53da74d0a27f886054325838e227a1cf05fc85c47372187; soft source a9ea1d19defce039fdb02d7465cdc39930ed949616c41ee95fa30bc6fb5fea48; TPOnly b25210dbd9977fe85f568a52867b3673aea65c17308e19612cb216efae68164e; old Exit base 3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10. All pinned to saved result, and actual H9/soft source files independently SHA-checked.

### 20.1 Exact historical local causal logic checked

Original frozen TPOnly Position has **tp=None at Entry** (important difference from an earlier simplified read-only pilot). A TP only becomes active on a subsequent NEW closed 5m bucket if opposite RAW L5-3 target is at least +0.5% favorable. First eligible print against newly available target is MARKETABLE_TP; a preexisting resting target is checked against prior prints as maker-limit modeled fill. On EVERY original trade after saved accepted Entry, independent first-event replay checks -10% HARD_STOP, preexisting RESTING_TP, new-5m MARKETABLE_TP/target update, then RAW SOFT_STOP unfavorable <=-4% and working_width_pct_lower>=4 or H9_LOSS_CONTINUOUS due after 18 hours, with RAW cause precedence when both due.

H9 is independently reconstructed from raw source_high/source_low of **108 fully closed 5m states**, then combined with current 15m anchor at floor(bucket/900)*900: low=min(5m low, anchor low), high=max(5m high, anchor high). Frozen states are SHA-checked against 2025 Entry manifest, require state_effective_ts=source_bar_end_ts and consecutive 300s; no forward candle or later bucket state is used early. H9 first adverse timestamp: max(closed5m bucket, actual Entry time) at first unfavorable H9<=0 after Entry; a favorable H9>0 resets timer. Native source gzip for each adjacent UTC day SHA256 checked against original BULL25 qc_full manifest; preserve original row order and exact cumulative global 1-based source order, tied timestamps. Separately decompress an in-memory gzip stream of original ordinal/time/price for full selected Entry→Exit interval and replay same local causal decisions. Check first reason/cause/order/time/price and first adverse start against original ledger.

### 20.2 Two selected real crossing-day cases (LONG/SHORT)

| Source UTC days | Symbol/side | Entry global source order | First Exit global source order | Exact first adverse timestamp | First Exit print | Native+compact first event |
|---|---|---:|---:|---:|---:|---|
| 2025-05-23 to 2025-05-24 | ARBUSDT LONG | 7,312,360 | 7,519,733 | 1748033100 | 0.3967 | PASS |
| 2025-08-07 to 2025-08-08 | APTUSDT SHORT | 20,004,560 | 20,257,485 | 1754628300 | 4.6840 | PASS |

**2/2 first H9_LOSS_CONTINUOUS exactly confirmed**, no earlier competing TP, HARD_STOP or RAW SOFT_STOP, each with 1 adverse start, 0 recoveries and 325 closed 5m bucket transitions. **Four full original daily files, 680,250 original trades, 28,255,482 original gzip bytes.** The selected Entry→Exit intervals contain 460,300 original price events, compressed in memory to 2,533,247 B; different denominators mean **not** a comparable whole-day compression rate. Prior separate LONG-only pilot measured peak RSS 109,552 KiB; exact combined CLI RSS NOT MEASURED. All ran via approved read-only actor cripta and Python -B -c with 0 server writes, no new job or source download.

**Reproducible implementation:** new separate RESEARCH code research/history/native_h9_continuous_qc.py. Exact source executed SELFTEST_PASS 6 synthetic cases (including H9 favorable reset/restart, preexisting HARD_STOP and LONG/SHORT), and PASS_BOUNDED_H9_CONTINUOUS_FIRST_EXIT on both real immutable cases. Bounded: max two selectors, two adjacent UTC source days, 18–36h duration, max32MiB gzip/day, max16MiB frozen gzip, max300,000 original ticks per Entry→Exit interval, max16MiB in-memory compact, max70,000 frozen state rows. Fifteen positive and negative regression cases in tests/research/test_native_h9_continuous_qc.py preserve source/time/missing candle and timer semantics. Required GitHub CI and final PR merge are **NOT CHECKED HERE** until independent workflow evidence.

**Limit:** 153 saved H9_LOSS_CONTINUOUS ledger rows in scenario H9CONT_18H_S48_OFF were checked for original self-consistency only, NOT independently replayed; zero same-day. Two hand-selected cases cannot validate full 183d, all H9 resets, source-to-candidate Entry decisions, GeometrySpec/ATR200 reconstruction, position occupancy, fees/slippage/Exchange fills or complete continuous 2025→present coverage. Previous 5 HARD_STOP, 4 dynamic TP and 2 raw SOFT_STOP selected checks remain separate, NOT universal all-event equivalence.

**Gate:** Stage0 IN PROGRESS, GATE_0=NOT_PASSED, 0/11. #642 hot-order fail-closed retention, SSD data 94% used (13,279,727,616 bytes free at latest 2026-11-22 read); stale external #866/#646 job metadata still unresolved. No production/runtime/DB/Exchange mutation. **NEXT_ONE_ACTION:** review scoped GitHub PR, require PASS from scope and all four quality CI jobs, merge only on same exact HEAD and independently read back from main; then proceed with independent no-bulk OOS/coverage prerequisites while separate storage/job owners reconcile blockers.

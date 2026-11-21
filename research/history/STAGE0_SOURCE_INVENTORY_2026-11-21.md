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

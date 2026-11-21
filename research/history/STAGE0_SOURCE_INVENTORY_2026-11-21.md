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

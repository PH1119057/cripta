# CRIPTA — непрерывная история рынка и универсальный исследовательский конструктор

> **Постоянная точка продолжения между чатами.** Это рабочий **RESEARCH** roadmap/checkpoint, **не** новый канонический документ и **не** подтверждение реализации. Authority правил: `docs/DOCUMENTATION_INDEX_RU.md` и активный GitHub `main`.  
> **Tracker:** [Issue #878](https://github.com/PH1119057/cripta/issues/878).  
> **Owner direction:** 2026-11-20. **Initial baseline:** `7e387c7d06949f0f1c4627d5169c855bd3a1f42f`.

## 0. Живой статус — ОБНОВЛЯТЬ ПЕРВЫМ

| Поле | Текущее значение |
|---|---|
| `TASK_ID` | `RESEARCH-HISTORY-20251120` |
| `ISSUE` | `#878 OPEN` |
| `ACTIVE_STAGE` | `0 — Инвентаризация и проверка существующих работ` |
| `TASK_STATUS` | `STAGE 0 PARTIAL / 5/5 FIRST HARDSTOP + 4/4 CAUSAL TPOnly MARKETABLE / FINANCIAL RETENTION BLOCK` |
| `STAGE_GATES_PASSED` | `0 / 11` |
| `LAST_UPDATE` | `2026-11-21 — 4/4 real causal first MARKETABLE_TP exits, closed-only L5-3, independent native vs compact` |
| `SOURCE_BASELINE` | `7e387c7d06949f0f1c4627d5169c855bd3a1f42f` |
| `LAST_LIVE_GITHUB_MAIN_CHECK` | `13665d912f975a3afd10ecdfb2c82013daad0430` до current Stage 0 PR; **перепроверять каждый содержательный сеанс** |
| `RESEARCH_JOB` | `NOT STARTED BY #878`; separate #866 HS 183d job **completed exit0** at 13:31 UTC; two old job records unresolved |
| `EXECUTED_DATA_QC` | `NOT VERIFIED` для нового каркаса |
| `PRODUCTION` | `UNTOUCHED BY THIS TASK` |
| `NEXT_ONE_ACTION` | Пройти GitHub scope/quality для отдельного native_tp_causal_qc.py с 10 позитивными/отрицательными тестами; затем выбрать bounded SOFT_STOP/H9 causal pilot только на существующих оригинальных данных. #642/#866/#646 external gates сохраняются. [Evidence Stage0 §18](STAGE0_SOURCE_INVENTORY_2026-11-21.md) |

**Правило обновления:** после каждого доказанного промежуточного результата сначала изменять эту таблицу, затем checklist и журнал evidence ниже в одном Git changeset. Не повышать статус по плану, коммиту, CI или имени процесса без фактической проверки. При `UNKNOWN` — сначала восстановить outcome, не запускать повторно.

**Алгоритм входа в новом чате:** текущий GitHub `main` SHA → обязательный pre-read META/WORK/SECURITY/INDEX → маршруты RESEARCH, DEV, MAP, ARCH/GLOSSARY/TC/OBS при затрагивании → Issue #878 + этот файл → DEV §50.4 Issue/PR/branch/worktree/dirty/job discovery → read-only verification → **одно** следующее разрешённое действие → запись доказательств в этот файл. Если версия/задача/actor изменились — перечитать соответствующий канон. Последний сохранённый статус не является доказательством живого состояния сервера.

## 1. Цель, границы и неизменяемые принципы

**Цель:** построить один непрерывный, причинный, воспроизводимый исследовательский фундамент за период с `2025-01-01T00:00:00Z` до **последнего доказанно полностью закрытого UTC-дня** (на дату поручения верхняя граница exclusive `2026-11-20T00:00:00Z`), с достаточным *causal prewarm* перед началом. Начальный universe: `LTCUSDT, INJUSDT, DOTUSDT, ARBUSDT, APTUSDT`; позже добавление символов не меняет формат.

Логическая исследовательская схема (это **не** изменение пяти уровней CRIPTA):

```text
1 Market facts / exact native price path
  -> 2 Causal GeometrySpec + GeometryTimeline
  -> 3 Independent, versioned FeatureTimeline
  -> 4 Immutable geometrical EntryCandidates + EntryPolicy
  -> 5 Research Position / Portfolio state
  -> 6 Initial Protection + versioned ExitPolicy
  -> 7 Ledger / Comparisons / HardStop history / QC
```

- Изменение `PREV7D→PREV14D→PREV21D`, width-фильтра или Soft Stop/TP/trailing **не требует** повторного скачивания и native parsing, если сохранённый поток содержит необходимые данные.
- Изменение результативно значимых параметров геометрии = новый `GeometrySpec fingerprint` и новая timeline, **не** перезапись старой.
- Рынок, геометрия и MAYAK/Dispatcher facts не решают `LONG/SHORT`, `ACCEPT/BLOCK` или торговый Exit. Interpretation принадлежит только выбранной research Strategy/Policy.
- Каждый research сценарий закрепляет exact `ENTRY / EXIT / EXECUTION+COST / FILTERS+FLAGS / DATASET+UNIVERSE+PERIOD / OCCUPANCY+RE-ENTRY+TERMINAL`. Базовая экономика `TAKER/TAKER`; maker и slippage sensitivity отдельно.
- Изменение research результата **никогда** само не изменяет production `StrategyCard`.
- **Protected:** production Strategy/Entry/Exit/Execution, Exchange/order/position, LIVE-arm, production PostgreSQL, текущие сервисы, existing native archives, original frozen 29/90/183d reference ledgers. `RAW_PURGE=NO` до отдельного точного consumer/reacquisition/restore/equivalence/approval gate.
- LONG/SHORT допустимо моделировать раздельными независимыми портфелями; это не разрешает встречные реальные позиции в Bybit one-way account.

## 2. Единый checklist — двигать только по доказанным gate

### [ ] Этап 0 — read-only discovery, identity и resource/actor preflight
- [x] Получен текущий на 20.11.2026 GitHub `main=7e387c7...`, локальный серверный mirror HEAD тот же, `status --porcelain` пуст.
- [x] Прочитан base pre-read и профильные документы, включая INDEX §17 и DEV §50.4.
- [x] Проверены GitHub Issues/PR и зарегистрированный server worktree list: 151 записей; прямого worktree нового плана по имени не обнаружено. Создан отдельный tracker #878; см. #454.
- [x] Подтверждено, что host `robot` отвечает, а `cripta-job-intake.service` и `cripta-job-runner.service` значатся running; это **не** проверка их jobs/behavior.
- [x] Выполнена первичная read-only инвентаризация `jobs/{running,queued,completed,failed}`: две записи в `running`, остальные состояния по каталогам; **outcome двух записей не доказан**, полное discovery остаётся открытым (см. [Stage 0 evidence](STAGE0_SOURCE_INVENTORY_2026-11-21.md)).
- [ ] Просмотреть **все** действующие/незавершённые exact research jobs и durable locks/checkpoints, включая возможную незафиксированную работу других чатов; проверить owner/actor/writer и target paths.
- [x] Зафиксированы read-only CPU=4, RAM=4.105GB / available=2.728GB, swap=0, `/data/cripta` free=13.317GB (94% use), Unix `cripta` uid107, базовые ACL и действующие job/intake services. **Нагрузка/peak RSS будущего worker ещё не измерялись.**
- [ ] Зафиксировать RAM, CPU, доступные SSD bytes, I/O, системные рабочие службы, effective Unix UID, ACL, sandbox, source paths; изолировать исследование от активной торговли.
- [x] По четырём native public-trade roots найдено **332 уникальных дня на монету** (2025 Mar22–Sep30, 2026 May8–Aug15 и Sep11–Oct19), без нулевых файлов и внутренних календарных разрывов. Обнаружены существующие L5-3 states/touches и Entry candidates; **не доказана полная coverage и независимая replay equivalence**.
- [ ] Инвентаризовать datasets/manifests/reference/native geometry/Entry и мигрированные legacy sources; установить reuse scope #454 против нового `PriceEventStream`.
- [ ] Составить exact actor/path, protected surface, source-and-reader version inventory и backlog пропусков.
- **GATE 0:** `TASK_DISCOVERY=PASS`, task/worktree/job/writer identity без конфликтов, safe resource/write preflight доказан. До этого не начинать массовый compute и filesystem mutation.

### [ ] Этап 1 — непрерывный календарь существующих источников
- [ ] Матрица `symbol × UTC day × source type`: Bybit public trades, ready closed 5m/15m bars, geometry, candidate lists, exact native reference manifests и prewarm.
- [ ] Для каждой ячейки `PRESENT_VERIFIED / PARTIAL / MISSING / CORRUPT / NO_DATA`, gaps, duplicates, SHA256, provider/source provenance.
- [ ] Проверить отсутствие исходника в иных approved/migrated roots перед новой загрузкой.
- **GATE 1:** проверенный coverage-календарь; неизвестное не подменяется нулём; список реально недостающих диапазонов.

### [ ] Этап 2 — компактный причинный market fact / exact price-event store
- [ ] Сравнить готовые Bybit 5m/15m OHLC/candle close/timestamps против native bars, отдельно ATR200 seed.
- [ ] Исследовать несколько компактных encodings `timestamp + source_order + price + other required event attributes`; учесть одинаковые timestamps, дубликаты и source semantics.
- [ ] Bounded streaming/chunk read, manifest/provenance/SHA256, independent QC, измеренные storage/RSS/CPU/I/O.
- [ ] При недоказанной first-event эквивалентности удерживать точный native path для класса событий; **OHLC не доказывает TP-before-Stop**.
- **GATE 2:** exact source-order price path и проверенная native parity; никаких исходных raw deletion по результату одного minute pilot.

### [ ] Этап 3 — независимая GeometrySpec/timeline
- [ ] Восстановить exact frozen GeometrySpec из source snapshot, не из памяти.
- [ ] H9: 108×5m и 36×15m полностью закрытых свечей; L5-3: 36×5m; RAW high/low, Wilder ATR200, inner/outer, working width, previous/structural changes.
- [ ] Версионировать `symbol + geometry_spec_id/fingerprint + effective_time/source_bar_end/available_after + quality/provenance`.
- [ ] Проверить causal prewarm, паритет ATR, смены структуры и timestamp availability.
- **GATE 3:** нулевая unexplained independent native geometry parity; новая формула не изменяет прежнюю timeline.

### [ ] Этап 4 — versioned дополнительные признаки
- [ ] Отдельные `PREV7D / PREV14D / PREV21D` с независимыми formula/window/fingerprint, `available_after`, `NO_DATA`.
- [ ] Разделять *медиану ширины L5-3*, *недельную амплитуду цены*, *self-normalized режим монеты*; OBS `1w/2w/4w` не подменяет PREV7D.
- [ ] QC: UTC full-day boundaries, causal availability, missing/partial days.
- **GATE 4:** замена feature window **не** пересчитывает market/geometry layers.

### [ ] Этап 5 — immutable Entry candidates + подключаемые EntryPolicy
- [ ] Из frozen P90L90 source восстановить exact H9 RAW union/depth, first crossing, source order и price.
- [ ] Сохранить **все** потенциальные геометрические candidates до width/stabilization/retest/context/cooldown/`SECOND48`/slot filters.
- [ ] Версионировать политики, результаты каждого условия, `ACCEPT/BLOCK` и causal reasons.
- **GATE 5:** другой набор фильтров воспроизводится без native reparse и не теряет заблокированные кандидаты.

### [ ] Этап 6 — ResearchPosition / Protection / Exit / month checkpoints
- [ ] Exact Entry snapshot, position/slot/cash, current price cursor, Hard Stop history, SECOND48 pause/deque, terminal censored positions.
- [ ] Initial protection отдельно от dynamic TP/Hard Stop/Soft Stop/BE/trailing/structural/time Exit; first-event priority и causal reprice/stop update.
- [ ] Определить полные сериализуемые checkpoints, включая open positions и все result-affecting states.
- [ ] Сценарии друг от друга финансово и причинно изолированы; строгая после-комиссионная бухгалтерия.
- **GATE 6:** монолитный replay == month checkpoint replay **сделка в сделку**.

### [ ] Этап 7 — проверка трёх насыщенных пилотных интервалов
- [ ] **2026-10-07–16:** историческое evidence 8 Hard Stop, 9 Soft Stop, 5 SECOND48 (числа — ориентир из задания, не текущий QC).
- [ ] **2025-05-22–31:** историческое evidence 10 Hard Stop, 4 Soft Stop, 6 SECOND48.
- [ ] **2026-05-25–06-03:** историческое evidence 3 Hard Stop, 4 Soft Stop, SECOND48 без активаций.
- [ ] Для каждого интервала exact preceding checkpoint или causal prewarm; не обнулять positions/counters на границе.
- [ ] Сравнить every qualified/blocked candidate, Entry/Exit timestamp/price/source_order, first-event reason, SL/TP/trail, SECOND48, fees, after-fee PnL, open positions.
- **GATE 7:** `UNEXPLAINED_EVENT_DIFFS=0`; несоответствие локализуется, эталон **не** переписывать.

### [ ] Этап 8 — frozen 29/90/183-day full regression
- [ ] Сопоставить exact independent source/scenario fingerprints и native replay each period.
- [ ] Trade-by-trade + blocked-candidate parity, checkpoint vs continuous identity, fee/position/termination checks.
- [ ] Failure-injection: gaps, corrupted chunk, bad ATR prewarm, checkpoint mismatch, tie ordering.
- **GATE 8:** все три периода `PASS` на уровне отдельных событий; одинаковый общий PnL без этого недостаточен.

### [ ] Этап 9 — полный сбор истории пяти монет
- [ ] Reuse verified source, acquire only missing days/week chunks, causal convert, independent QC, manifest/SHA, cleanup **только новых временных копий** после доказанного gate.
- [ ] Одобренный автономный server-side research intake/runner (RESEARCH §8.1), source snapshot/job ID, bounded workers, heartbeat/checkpoints, measured resource budget.
- [ ] Все symbol/day coverage проверены; недостаточные данные имеют явный quality status, а не молчаливый skip.
- **GATE 9:** подтверждённый continuous 2025→last full UTC-day dataset; `COMPLETE` только после exit/QC/manifest proof.

### [ ] Этап 10 — единый непрерывный research replay и конструктор
- [ ] Конфигурируемые версии Geometry, Entry filters, Exit/Protection поверх одного verified fundament.
- [ ] Единый ledger potential/accepted/blocked Entries, Hard/Soft Stops, TP, censored, MAE/MFE, SECOND48, fees и quality.
- [ ] Full-period continuous replay сравнить с checkpointed; benchmark повторных сценариев и bytes/RAM/CPU/I/O.
- [ ] Итоговая reproducibility documentation; нет automatic promotion в production Strategy/LIVE.
- **GATE 10 / PROJECT COMPLETE:** все предыдущие gates подтверждены manifests/evidence, независимая эквивалентность доказана, switch 7→14→21/Exit без market rebuild реально испытан.

## 3. Существующие смежные работы — НЕ ДУБЛИРОВАТЬ

| Ресурс | Роль и граница |
|---|---|
| [#454](https://github.com/PH1119057/cripta/issues/454) | Существующий компактный APT minute-snapshot pilot: 2026-05-18, 268 455 bytes, 34 560 численных сравнений PASS по evidence Issue. **Не** доказана source-order/TP-stop replay equivalence. Историческое raw не удалять. |
| [#158](https://github.com/PH1119057/cripta/issues/158) | Незавершённый first-month microstructure, native print/OB200 consumers. |
| [#227](https://github.com/PH1119057/cripta/issues/227) | Требует native repeated price-contact/tick ordering. |
| [#638](https://github.com/PH1119057/cripta/issues/638) | Отдельный unified wallet P90 replay frozen 29/90/183; его source/ledger — **reference**, не merge в новый scope. |
| [#866](https://github.com/PH1119057/cripta/issues/866) | Отдельный H9/L5-3 Soft Stop native replay; не отменять running jobs. |
| [#878](https://github.com/PH1119057/cripta/issues/878) | Текущий трекер всей инфраструктурной дорожной карты. |

## 4. Правила доказательств и перехода между этапами

Каждый `GATE=PASS` сопровождается: `BASE_SHA`, exact source/reader fingerprints, `ISSUE/BRANCH/PR`, executor/actor, dataset/period/universe, job ID/state, immutable manifest/log path и SHA256, test commands/results, measured coverage/resources, `CHECKED HERE / NOT CHECKED HERE`, blockers, следующее одно действие. Допустимые `STAGE_STATUS`: `NOT_STARTED / IN_PROGRESS / BLOCKED / FAILED / VERIFIED_COMPLETE`. Пометка чекбокса `[x]` только при выполненном gate, не при написании кода.

**HARD STOP:** не проверен актуальный канон; конфликт worktree/writer/job; source/path/permission unknown для writer; native first-event mismatch; loss of causality; недоказанная сохранность raw/consumer; test regression; любые непредвиденные production/exchange эффекты. После tool timeout — сначала установить outcome.

**Рабочие корни:** Git source в разрешённом `research/**`; большие runs/manifests/caches в утверждённом `/data/cripta/research` под доказанным effective actor/ACL; source checkout — зеркало, а не writer. Новые data paths зависят от read-only preflight и текущего MAP, не от этого плана.

## 5. Журнал решений и evidence — дополнять, не подменять


### 2026-11-21 — independent frozen closed-only L5-3 TPOnly first-market-exit recheck
- **CHECKED HERE:** [Stage0 §18](STAGE0_SOURCE_INVENTORY_2026-11-21.md) — exact #866 183d partial source fingerprints for historical TPOnly / base / 2025 portfolio match frozen code; original daily gzip source and separately frozen 5m L5-3 states hashes match archived QC/Entry manifests. Deterministic LOCAL historical TPOnly Exit transitions on four selected same-UTC-day CLASSIC_S48_OFF positions, with Entry fixed to ledger (not regenerated): first -10% HARD_STOP → old resting TP → newly available opposite RAW L5-3 candidate at next fully closed 5m bucket, marketable if reached. **4/4 MARKETABLE_TP first events exact original native vs decoded compact order/time/price and saved ledger**, no earlier stop/TP, 36/25/12/22 bucket transitions, 7/9/2/0 TP target changes for INJ LONG/LTC SHORT/DOT LONG/APT SHORT.
- **Measured:** 4 original complete day files / 881,605 original native rows / 34,450,433 B gzip, selected Entry→Exit intervals only 353,318 B compressed. Previously observed pilot RSS ~29,032 KiB. The compact value is **not** a comparable full-day compressed ratio. All runs read-only on host cripta, no server/production output.
- **Reproducible QC source:** research/history/native_tp_causal_qc.py, explicit source/frozen/Entry-manifest/ledger paths + max 4 cases, max6h/case, max32MiB source, max16MiB frozen state, max30k interval trade rows, max4MiB in-memory compact. Code selftest 5 PASS and all four real cases PASS from exact inline source; tests/research/test_native_tp_causal_qc.py includes 10 positive/negative fixture tests. **GitHub CI NOT YET VERIFIED** at this checkpoint.
- **Limits:** no independent Entry/H9/ATR200 construction, SOFT_STOP, multi-symbol portfolios, money/Exchange fills; no Stage0 promotion (0/11), no bulk download/purge/runner; #642 safety-retention and #866/#646 job ownership remain unresolved.
- **NEXT:** required GitHub scope + 4 quality jobs, then independent main readback; next small causal SOFT_STOP research candidate only after this release checkpoint.




### 2026-11-21 — independent historical first-HARD_STOP event pilot
- **CHECKED HERE:** [Stage0 §17](STAGE0_SOURCE_INVENTORY_2026-11-21.md) — frozen historical source SHA256 3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10 matches reference #866 183d result; independent scan from original Entry strictly to Exit confirms **5/5 unique native -10% first crossings** (2 SHORT/3 LONG), 26 scenario references, no earlier stop crossing, exact compact source-order/time/price parity. Five selected gz files, 1,402,199 rows; 55,929,628 original vs 8,373,752 compressed price-order bytes; measured peak RSS ≈25,272 KiB.
- **Expanded existing checker:** `native_ledger_pointer_qc.py --verify-hardstop-first-hit` pinned to frozen model, fail-closed on unpinned SHA, incorrect ordinal, absent/earlier cross, >32 same-day probes; short in-memory run over all five symbols on 2025-05-08/2025-09-22: 10 files, 2,616,045 native rows, **209/209 actual pointer checks**, 5/5 unique first hardstops, 102,556,474 B native / 15,388,968 B compact, 0 server writes. Selftest 7 cases, targeted LONG/SHORT/negative cases added under `tests/research/`; **GitHub full CI NOT YET VERIFIED** at this checkpoint.
- **No gate promotion:** sample deliberately selected; not independent dynamic TP, SOFT_STOP, geometry, new Entry candidate, portfolio or economics replay; `GATE_0=NOT_PASSED`, 0/11. #642 hot-order protection, disk 94% used, two old #866/#646 record ownership/outcome unresolved. No new research job/production/DB/Exchange/LIVE mutation.
- **NEXT:** CI/scope review, exact PR merge only if PASS, then bounded dynamic TP/SOFT_STOP causal reference proof; no bulk download/purge.



**CI scope note #887:** `quality` passed on initial head but required `scope` rejected `tests/` (CORE) in `research/*`. Test moved to canonical `tests/research/` without logic changes; old path deleted. New full scope/quality PASS and independent merge still NOT CHECKED.



### 2026-11-21 — первый реальный native Entry/Exit source-pointer тест, immutable partial job rescue
- **CHECKED HERE:** [Stage0 forensic §16](STAGE0_SOURCE_INVENTORY_2026-11-21.md) — отдельный старый #866 H9CONT 183d partial `QC_PASS` result (`197794465` ticks, `13052` ledger lines), verified complete ledger SHA256 `1541f1c2ddd68a5ae04ab080d72d6f9ff74dbbb9d7615e97ee710c158dfc0b31`. Subsequent 90d progress stale; full three-period runner job **NOT COMPLETED**, active worker absent. #646 direct 1h smoke reports `SMOKE_EXIT_ZERO` / `130697` ticks/18 scenarios, not a canonical runner-completed 29d job. Both owners notified, zero cleanup/restarts.
- **Real evaluation days (2025-04-01 and 2025-05-30)** — ten original gzip archives fresh hash/size verified against historical `qc_full` `10/10`, original `2259413` rows, saved ledger Entry **163/163** exact source-order/time/price/trdMatchID; Exit **174/174** source time/order and correct execution-price contract: HARD_STOP 24, SOFT_STOP 24 exact prints, RESTING_TP 126 directional limit crossings (10 expected limit-vs-print differences, one unique event). **337 ledger references, 42 unique event identities; zero unexplained diff**. Frozen source hashes for relevant readers and TP semantics matched ledger's `source_snapshot_sha256`. Strict one-based global source order recovered; previous naive zero-based mismatch is a verifier bug, not code regression. Price-only in-memory gzip 15.304% of original gzip on these days.
- **Source engineering:** `research/history/native_ledger_pointer_qc.py` with explicit read-only CLI and bounded per-file limits (max 3 dates, 32MiB gzip source + 32MiB in-memory stream/file), immutable result/source hashes and fail-closed row/event assertions. Exact script source selftest and two-day source-mode PASS without server writes. New `tests/research/test_native_ledger_pointer_qc.py` regression suite staged in GitHub branch; CI **NOT YET VERIFIED** at this checkpoint.
- **NOT CHECKED HERE:** full independent dynamic Strategy Entry/Exit/portfolio replay, all three periods' checkpoint parity, API/Exchange, full calendar; FINANCIAL retention guard still `HOT_ORDER_PRESENT` (2 hot orders/one open position at previous read-only check). `GATE_0=NOT_PASSED`, `0/11`. No DB/Trading/Exchange/LIVE/server filesystem mutation under #878.
- **NEXT ONE ACTION:** pass code regression/CI, merge exact research-only source/evidence if PASS, then resolve external stale job metadata and #642 financial-safe storage budget before heavy work.



### 2026-11-21 — all-known-native-source manifest / filesystem integrity audit
- **CHECKED HERE:** [Stage0 evidence §15](STAGE0_SOURCE_INVENTORY_2026-11-21.md). All **1,660** logical known original file-days (965 BULL25, 500 WARM90, 195 RECENT29) verified via normalized manifests against current filesystem: strict filename/calendar, path/size positive and matching, SHA256 field present, all symlinks resolvable, no per-range gaps or duplicates. **0 metadata discrepancies**; 615 paths are symlinks, so logical gzip bytes 13,306,684,338 must not be mistaken for physical `du`.
- **NOT CHECKED HERE:** 1,660 new SHA256/CRC payload reads, entire native source-order/Entry/Exit parity, 357 unsampled UTC dates per symbol, provider data/retention proof. Do not upgrade existing historical manifests to fresh full QC by this metadata-only audit.
- **GATE:** still `STAGE_0=IN_PROGRESS`, `GATE_0=NOT_PASSED` and `0/11`; no server write, bulk source download or financial mutation. The `HOT_ORDER_PRESENT` safeguard remains under #642, while old #866/#646 terminal/cleanup ownership remains open.



### 2026-11-21 — Stage0 расширенный sample + точный защитный gate retention
- **CHECKED HERE:** [Stage0 §14](STAGE0_SOURCE_INVENTORY_2026-11-21.md) — 10 original native files (5 symbols × 2025-03-22 / 2026-09-11), 2,160,751 native trades, exact price/timestamp/source-ordinal roundtrip and **80/80 synthetic threshold first-contact matches**, weighted price stream 12,141,377 vs original gzip 81,926,194 B = **14.82%**. Generic price-contact test, **not** real Strategy native first-event parity. Single-process sampled peak RSS ≈26 MiB.
- **All-field control:** 2,160,751 full source CSV text values + ordinal roundtrip exact, but gzip output 98,050,882 vs original 81,926,194 B = **119.68%** (larger), peak RSS ≈79 MiB. No generic full MarketFacts codec proven; original raw not replaced.
- **Native retention blocker:** installed `--check --batch-limit 1` under `cripta` returned `NativeTradeRetentionBlocked: NATIVE_TRADE_RETENTION_HOT_ORDER_PRESENT`. Independent `BEGIN READ ONLY` aggregate DB counts: hot_orders **2**, hot_positions **1**, nonclosed ownership **1**. Installed script explicitly requires `hot_orders == 0` in both check/apply before deletion; this is a fail-closed protection guard. Unit failed 14:52:25 UTC, its exact stderr still unavailable. No guard bypass or financial mutation. Evidence posted to storage owner #642.
- **Storage:** Data SSD **13,280,264,192 B free / 94% used**; naïve missing raw budget based on sampled historical mean ≈14.31GB (unverified approximation, not fixed actual sizes) already exceeds free before reserves. Bulk backfill remains blocked. Old orphan/cleanup #866/#646 outcomes unresolved.
- **GATE:** `ACTIVE_STAGE=0`, `GATE_0=NOT_PASSED`, `STAGE_GATES_PASSED=0/11`. No new #878 job, downloads, raw deletion, DB/Exchange/Strategy/LIVE/service change. **NEXT:** separate owner #642 financial-order-safe retention/capacity decision and #866/#646 job resolution, then formal full-consumer native replay pilot.



### 2026-11-21 — внешний #866 183d job достиг terminal `completed`
- **CHECKED HERE:** [Stage0 §13](STAGE0_SOURCE_INVENTORY_2026-11-21.md) — separate `C2-R866-HS10TO2-LS-183D-20261121` moved to `jobs/completed`, durable `status.json` and `result.json` say `exit_code=0`, finished at epoch `1795267901`. Worker `4136843` observed with no child; own `stdout.log` reports `QC_PASS`, `197,794,465` ticks and `27,475` rows. External job QC not independently verified by #878; do not close #866.
- **GATE:** active external worker no longer in flight at observed instant, but two old #866/#646 orphan/cleanup records remain and native retention is still broken. `GATE_0=NOT_PASSED` (0/11); no #878 worker, code or raw-disk mutation.



### 2026-11-21 — Stage 0 remote Bybit object presence по границам истории
- **CHECKED HERE:** [Stage0 §12](STAGE0_SOURCE_INVENTORY_2026-11-21.md) — `curl -I` (**HEAD only**, no archive download) for all 5 symbols on `2025-01-01` and `2026-11-20`: **10/10 HTTP 200**, nonzero Content-Length. LTC additionally at `2026-04-15`, `2026-08-20`, `2026-10-20`: **3/3 HTTP 200**. All are outside sampled local covered dates and verify remote *object presence*, not complete provider day matrix, CRC, contents, native parity or actual acquisition.
- **Reuse:** BULL25/WARM90/RECENT29 local source remains preserved. These HEAD observations support a bounded future exact gap-only acquisition plan after storage/job gates, not blanket downloads.
- **GATES:** `0/11`, `GATE_0=NOT_PASSED`, `GATE_2=NOT_ATTEMPTED`; no new research job/server write. NEXT: external job outcomes + native retention storage owner, then safe full-field/native event pilot.



### 2026-11-21 — Stage 0 feasibility: точный ценовой порядок без изменения сервера
- **CHECKED HERE:** [Stage0 §11](STAGE0_SOURCE_INVENTORY_2026-11-21.md) — два существующих native gzip прочитаны under `cripta`, цена/время/исходный порядковый номер сериализованы как строки в in-memory gzip, SHA256 + readback of all 41,745 and 118,896 rows **exact**; получено `14.28%` и `16.00%` от размеров native gzip. Native timestamp ties сохранились в source order.
- **CONTROL:** all original trade field strings + ordinal, same in-memory codec, is `117.41%` and `121.33%` of native source gzip: **увеличение**, не готовый компрессор full MarketFacts. Price-only stream lacks side, size, trade ID, RPI, OHLC/Geometry/Entry/Exit dependency evidence; cannot substitute native before consumer/QC gates.
- **Capacity & jobs:** data free `13,299,777,536` B at observation, 94% used; #866 active 183d worker, no #878 job. Timer-driven native-hot-retention failed again at 13:24:26 UTC. Installed script requires `postgres` UID, and `systemctl show` says `User=postgres`; exact error cause unknown.
- **GATES:** `GATE_0=NOT_PASSED`, `GATE_2=NOT_ATTEMPTED`; new code, disk writes, DB/Trading/Exchange changes and archive purges **none**. NEXT: close stale job/retention safety with respective owners and choose formal complete-field/native-event pilot.



### 2026-11-21 — найдено повторное использование старых source QC
- **CHECKED HERE:** [Stage0 source inventory §10](STAGE0_SOURCE_INVENTORY_2026-11-21.md). Исторический 2025 BULL25 `965` files / `204,446,371` rows with `FULL_QC_PASS` evidence, 2026 WARM90 `500` files with `ALL_FIVE_PASS` independent-input-QC manifest; RECENT29 `195` per-file manifest entries with hashes/row counts, but fresh full independent QC not redone. Two fresh sample SHA256 matches old 2025/2026 manifests.
- **REUSE RULE:** preserve exact source order and old manifests/fingerprints; do not acquire duplicates of known days; historical manifest PASS and two fresh samples **do not** pass all #878 native-entry/event parity.
- **GATE 0:** remains `NOT_PASSED`; external job-state and disk/native retention gates outstanding.



### 2026-11-21 — этап 0, live runner identity и холодные источники
- **CHECKED HERE:** [STAGE0_SOURCE_INVENTORY_2026-11-21.md §9](STAGE0_SOURCE_INVENTORY_2026-11-21.md) — actual loaded runner.py, PID 4136843 → единственный child PID 483140 #866 HS−10…−2 (183d, 36 scenarios), обновляемый stdout. Это **чужой активный job**; #878 новый не стартовал.
- **EXTERNAL JOB OUTCOMES:** старый #866 H9CONT `running` без result/worker и ~33h старый stdout = **STALE/ORPHAN CANDIDATE, OUTCOME UNKNOWN**; #646 `SMOKE_EXIT_ZERO` в старом пилотном status, без canonical result, под `running` = **CLEANUP UNKNOWN**. Предшествующий #866 S48 183d имеет `completed, exit_code=0`, но research QC в #878 не доказан.
- **Data sources:** 3,500 cold archive directories/8,249 files scanned by name (partial; 64 pending, no ZIP internals, 2 Unix permission denials). #158 SPOT 30d cache for each symbol duplicates target calendar dates but is **not** proven native LINEAR. Five-symbol logical gzip input bytes `13,306,684,338` across 332 days; symlink overlap, physical bytes **NOT VERIFIED**.
- **Capacity/safety:** 13,302,910,976 B data SSD free, 94% used, 2,527,444,992 B RAM available, zero swap. `cripta-market-hot-retention.service` repeatedly failed in timer run at 11:46:43 UTC, second native hot retention phase exit1; owner #642. **No #878 core/trading/server mutation or data download.**
- **GATE:** `0/11`, `GATE_0=NOT_PASSED`, remaining job cleanup, storage/cold-source and writer budget gates.
- **NEXT:** reconcile exact stale jobs by owner/runner, resolve #642 storage blocker and only then bounded native acquisition/QC pilot.



### 2026-11-21 — этап 0, дополнительный read-only discovery и native source sample QC
- **CHECKED HERE:** 276 run dirs, bounded `inputs/data/source` path scan: 15 target `public_trades` directory hits, **0 новых ranges**; top-level migrated/current/cold paths checked with limited depth. Два отдельных compressed native files прочитаны полностью: 2025-03-22 ARBUSDT 41,745 trades и 2026-09-11 LTCUSDT 118,896 trades, SHA256, UTC/price/nondecreasing timestamp checks pass; `RPI` schema difference confirmed. Equal-timestamp adjacent pairs **26,157** и **80,507** соответственно → exact original source row order обязателен.
- **Capacity:** Data SSD 94% used, 13,317,177,344 B free at check; RAM available 2,721,501,184 B, zero swap. Existing #866 and #646 running directories not reconciled; no runner job submitted.
- **Adjacent risk:** `cripta-market-hot-retention.service` failed 2026-11-21 10:06:59 UTC (second native retention ExecStart status 1), root cause unknown, no change under #878. Owner #642 must assess separately; guard against new bulk data.
- **GATE 0:** NOT PASSED (0/11), daily coverage outside inspected roots remains UNKNOWN, full native parity NOT VERIFIED. Detailed [Stage 0 evidence §8](STAGE0_SOURCE_INVENTORY_2026-11-21.md).
- **NEXT:** reconcile exact stale job owners/outcomes, bounded cold/migrated source discovery, then minimal measured pilot without touching prod/raw retention.



### 2026-11-21 — этап 0, первый live read-only источник
- **CHECKED HERE:** [STAGE0_SOURCE_INVENTORY_2026-11-21.md](STAGE0_SOURCE_INVENTORY_2026-11-21.md) — exact roots, actor, resource preflight, day intervals, raw header schema `RPI`, existing 2025 L5-3/Entry artifacts, job directory states. GitHub base `e3717c4e8feddea94edfd4d9aebdbdfc3b9b9cdc`; source mirror clean/HEAD same.
- **Coverage:** 5 symbols × 332/689 UTC days by filename/positive size **in four sampled roots**; other 357 days **NOT FOUND IN SAMPLED ROOTS**, not proven absent globally. 2025 Apr–Sep existing geometry manifest status `RUNNING_NOT_VERIFIED`, not promoted to evidence pass.
- **Capacity:** Data SSD available 13,317,492,736 bytes (94% used), RAM available 2,727,899,136 bytes, 4 CPU, no swap. Not authorized to download large chunks before worker/RSS/disk preflight.
- **Jobs:** two `running` directories for other work (#866/#646); one old stale `running` status, other has `SMOKE_EXIT_ZERO` but still in `running`. `JOB_OUTCOME_UNKNOWN` until independent reconciliation, no extra worker started.
- **Stage gate:** `GATE_0=NOT_PASSED`; `SOURCE_QC=NOT_VERIFIED`; no production/DB/Exchange/LIVE mutation and no raw purge.
- **NEXT:** read-only migrated/cold/current source discovery and resolve exact external job ownership/status before any new runner.


### 2026-11-20 — план / исходный checkpoint
- **Owner:** вести долгосрочный план и чек-лист прямо в GitHub `research/`, чтобы любой новый чат видел текущую стадию.
- **CHECKED HERE:** GitHub `main=7e387c7d06949f0f1c4627d5169c855bd3a1f42f` до публикации; Project Source 11 документов соответствовали GitHub по blob SHA; серверный source_checkout HEAD exact, рабочее дерево чистое; server worktree list 151 записей, среди них нет очевидно совпадающего worktree по имени; хост robot operational; research intake и runner units running; GitHub Issues #454/#158/#227/#638/#866 обнаружены, новая Issue #878 создана.
- **NOT CHECKED HERE:** server dataset calendar/bytes/coverage, live job/writer/locks, RAM/SSD/current load, exact actor/FS ACL for proposed writer, native replay E2E, real execution/QC. Существующие unit liveness не доказывают работу конкретного research job.
- **Change:** только research plan/Issue. Нет запуска research job, DB/Exchange/Strategy/production changes.
- **Следующее действие:** Этап 0 — закончить read-only inventory datasets/job/writer/resources, записать фактическую матрицу и только затем выбрать пилотные chunks.

**Правило последовательности:** активная стадия одна. Сначала закрыть gate текущей стадии, затем начать следующую. Исключения для независимых подготовительных read-only задач разрешены без повышения статуса и без маскировки незакрытого gate.

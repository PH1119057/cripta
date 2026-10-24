# CRIPTA — research / compute / data rules

**Версия:** 2.5 · 2026-10-21
**Статус:** routed canonical process contract

Читать перед research, replay, OOS/holdout, большими dataset jobs,
длительными вычислениями и data-forensic.

### Tool refusals и сохранение прогресса

Обязательный маршрут: `DEVELOPMENT_RELEASE §19.5–19.6`, `CURRENT_PROJECT_MAP §1.4`. При OpenAI tool-security block фиксировать `BLOCKED_UPSTREAM_OPENAI`; не считать его Unix EACCES, не просить рестарт SentinelX без evidence. При `command_not_allowed` — `BLOCKED_SENTINELX_POLICY`; при Unix `EACCES` — `BLOCKED_UNIX_PERMISSION`; при job timeout — `JOB_OUTCOME_UNKNOWN` до отдельной проверки. Один и тот же отклонённый вызов не повторять циклом и не пытаться обойти защиту.

Для воспроизводимых исследований: в GitHub хранить версионированный исходник/reader и exact source paths; начинать с ограниченного smoke, далее coin/day bounded jobs; проверенные численные результаты, coverage, hashes, failures и next action сразу сохранять в research journal/manifest. Исследование в памяти без подтверждённого stdout/manifest = `NOT VERIFIED`; GitHub source commit сам по себе не означает executed/QC pass. Не объявлять расчёт завершённым из-за подготовленного кода.

Общие source-of-truth / Hard Stop правила задаёт
`docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`.

Если research/replay/OOS/data-forensic script/service/job пишет на server
filesystem, создаёт/удаляет/переименовывает объекты или меняет effective actor,
обязателен cross-route pre-read:
- `docs/DEVELOPMENT_RELEASE_RULES_RU*.md §5.1`;
- `docs/DEVELOPMENT_RELEASE_RULES_RU*.md §19.1–19.4`;
- `docs/CURRENT_PROJECT_MAP_RU*.md §1.4`.

Research route не отменяет server-side permission/actor contract.

## 1. Scope

Этот документ владеет detailed research/compute discipline и не задаёт
Strategy policy.

Нумерация разделов локальна для этого документа и непрерывна; она не делится
с DEVELOPMENT_RELEASE или WORK.

## 2. Долгие jobs наблюдаемы

Каждый долгий stage обязан показывать stage, processed/total, %, elapsed, ETA, heartbeat ~20–30 sec и cache hit/miss, где это применимо.

Особенно это относится к dependency download, full pytest, large DB backfill, archive, research и soak.

## 3. Console contract

Простая операция — одна физическая строка.

Сложная операция с `if/for/heredoc/Python/SQL/complex quoting/multiple fail-closed checks` оформляется готовым `.sh/.py/.ps1` + одна строка запуска.

Не перекладывать ручное редактирование production на пользователя.

## 4. CHECKED и NOT CHECKED HERE разделять

Каждый отчёт обязан явно различать `CHECKED HERE` и `NOT CHECKED HERE`.

Нельзя выдавать предположение за проверку, особенно для GitHub remote state, actual loaded runtime, DB privileges, exchange truth и service state.

## 5. Не делать категорический вывод из неполного forensic

Запрещён шаблон:

```text
у пользователя A нет ~/.ssh
-> значит на сервере нет GitHub credentials
```

Если проверен только один user/context, формулировка должна быть `НЕ НАЙДЕНО В ЭТОМ КОНТЕКСТЕ`, а не `ЭТОГО НЕТ В СИСТЕМЕ`.

## 6. Runtime-проверка после запуска

Длительный процесс проверяется минимум дважды: сразу после старта и повторно примерно через 5–10 секунд. Проверять, где применимо: реальные worker PID, runner/parent PID, process state, CPU, RAM/RSS, stderr, рост output/progress и ожидаемое число workers.

Если процесс завершился раньше, нужно доказать успешное завершение и проверить результат. На `?`, «проверь», «состояние» статус всегда получать заново с сервера, а не из памяти предыдущего ответа.

## 7. Малый сквозной тест до массового запуска

До полного тяжёлого расчёта выполнить минимальный end-to-end проход на реальных данных и проверить source path, schema/headers, timestamp semantics, units, side/direction semantics, границы дат, output schema и несколько значений вручную.

Технически успешный скрипт с пустыми, неверно прочитанными или семантически неверными данными = `FAILED`.

## 8. Dependency и interpreter preflight

До запуска проверить exact runtime: Python executable/version, required modules/binaries, permissions, source mounts/paths, free disk и available RAM. Нельзя впервые обнаруживать отсутствующую зависимость в полном расчёте. Для автономного research предпочитать stdlib Python, если внешняя зависимость заранее не проверена и не даёт существенной выгоды.

Текущий approved ChatGPT server-management rail — SentinelX. Перед выводом «сервер недоступен» проверить host/capabilities и переподключиться/повторно проверить server state. Потеря канала ответа SentinelX или orphaned tool job сама по себе не означает падение server process: сначала проверить PID/process/output/result manifest на host, затем решать о restart.

SentinelX agent actor не равен автоматически runtime Unix-user. До PostgreSQL/research job фиксировать `effective Unix user -> interpreter -> psycopg -> socket -> dbname -> DB current_user -> SELECT smoke`. Для CRIPTA read/research использовать доказанный runtime actor/role `cripta` либо иной заранее доказанный эквивалентный path. Wrong-user connection failure — preflight defect, не повод менять grants.

## 9. Большие данные обрабатывать потоково

Raw trades, orderbook tapes и большие market archives по умолчанию обрабатывать streaming/chunk/bucket способом с bounded cache. Полный период нельзя складывать в RAM, если это не доказано безопасным и необходимым.

```text
stream source -> causal aggregation -> bounded cache -> intermediate output -> release memory
```

Перед масштабированием измерить peak/RSS одного worker.

## 10. Parallelism определяется CPU + RAM + I/O

Число workers выбирается по CPU, RAM per worker, disk/decompression I/O, source contention и независимости частей задачи. Запрещено механически делать `workers = symbols`.

Если сервер имеет 4 CPU и независимый research безопасно делится, доступные CPU следует использовать. Но сначала доказать безопасность по RAM/I/O. Для неравномерных задач использовать ограниченное число workers с очередью символов/chunks.

## 11. Причинность research-данных

Любой point-in-time dataset обязан соблюдать `feature_time <= decision_time`. На `T` используются только данные, реально известные к `T`. Future MFE/MAE, stop, recovery, final outcome, будущие zone shifts и будущие MAYAK/Dispatcher states допустимы только как последующие labels/targets, но не входные признаки.

Отсутствие данных хранится как `NO_DATA` и не превращается молча в `0`, `NONE` или `NORMAL`. Для liquidation exact history при отсутствии исходных событий = `NO_DATA`; proxy допустим только как отдельно названный и версионированный `LIQUIDATION_PROXY`.

## 12. Сначала универсальный dataset, потом гипотезы

Если тяжёлые raw-источники нужны многим анализам, сначала строится нейтральный причинный dataset общего назначения:

```text
ALL ENTRY -> minute-by-minute causal state -> reusable research dataset -> analytical passes
```

Нельзя заставлять каждый Exit-кандидат повторно читать многомесячный raw archive, если первичные состояния можно один раз сохранить без future leakage. Существующий Exit/stop не должен заранее определять классы, если исследуется новый способ оценки сделки.

## 13. Пилот не является доказательством

Пилот нужен для проверки механики, данных, наличия явления и стоимости расчёта. Общий вывод требует full-universe проверки и, где применимо, разрезов symbol/direction/time regime, coverage, false positives/negatives и economics after commissions.

Для кандидата считать минимум: пойманные/пропущенные проблемные случаи, испорченные хорошие сделки, saved losses, lost good trades, destroyed recoveries, extra fees/slippage и итог после комиссий.

### 13.1 Conservative execution-cost baseline

OWNER DECISION 2026-10-04:

Для поиска и сравнения торговых Strategy/Entry/Exit кандидатов базовый
research/backtest cost model всегда считается консервативно:

```text
ENTRY_FEE_MODEL = TAKER
EXIT_FEE_MODEL  = TAKER
BASELINE        = TAKER / TAKER
```

Причина: до реализации нельзя гарантировать, что фактический Entry или Exit
получит maker fill. Нельзя объявлять слабоположительную стратегию прибыльной
за счёт предполагаемой maker-комиссии. Maker/maker, maker/taker и иные
варианты допускаются только как отдельный sensitivity-case рядом с
`TAKER / TAKER`, но не вместо baseline.

Slippage считается отдельной чувствительностью, если есть доказанная модель
или owner-approved assumption. Taker baseline не заменяет slippage-модель,
но обеспечивает консервативный fee floor для раннего отбора кандидатов.

### 13.2 Causal passive-order optimization для заранее известного уровня

Если точка потенциального Entry или Take Profit известна заранее из уже
доступной causal geometry, Research обязан отдельно проверить возможность
пассивного limit/maker исполнения как execution optimization.

Для кандидата с шестисвечным подтверждением owner direction 2026-10-04:
на пятой закрытой свече допускается отдельный research-вариант с заранее
выставленной limit-заявкой на известном уровне, а на шестой свече проверяется,
сохраняются ли остальные условия допуска.

При этом:
- такой passive/pre-placement вариант не меняет базовый `TAKER / TAKER`
  economics;
- fill до полного подтверждения считается отдельной execution semantics и
  должен быть явно помечен; его нельзя молча приравнивать к Entry после
  шестой свечи;
- заранее известный Take Profit также исследуется как passive limit/maker
  candidate, но baseline economics остаётся taker до доказанной execution
  semantics;
- досрочное закрытие/flip по новому market/structure event может потребовать
  taker; его maker/taker природа исследуется отдельно;
- никакой pre-placement не переносится в LIVE без exact Strategy version,
  Execution contract, cancellation/revalidation rules, tests и обычного
  owner-approved promotion path.

## 14. Не смешивать наблюдение, причину и интерпретацию

H3/H9 shift, orderbook, OI, flow, liquidation и Dispatcher context — наблюдаемые события/состояния. Нельзя автоматически считать structural shift причиной движения, алгоритмическую заявку spoofing, а очищенный стакан «реальными людьми».

Research должен по возможности проверять цепочку `market facts/state -> MAYAK/objective context -> structural change -> subsequent trade path`. Семантика полей подтверждается источником/каноническим parser contract до экономической интерпретации.

## 15. Файловые пространства разных сред не взаимозаменяемы

ChatGPT runtime/container, удалённый сервер, GitHub/connector storage, Project Source/File Library и локальный компьютер пользователя являются разными файловыми пространствами. Одинаковый путь или имя файла не означает, что объект существует или доступен в другой среде.

Перед любой операцией чтения, записи, копирования, упаковки, скачивания или передачи файла обязательно определить:

```text
SOURCE_ENVIRONMENT
SOURCE_PATH_OR_RESOURCE
DESTINATION_ENVIRONMENT
DESTINATION_PATH_OR_RESOURCE
TRANSFER_MECHANISM
SOURCE_EXISTS=YES
DESTINATION_PARENT_EXISTS=YES
ACCESS_ALLOWED=YES
```

Запрещено:

- использовать `/mnt/data/...` как путь удалённого сервера только потому, что он существует в ChatGPT runtime;
- использовать `/srv/...`, `C:\...` или другой server/local path внутри ChatGPT runtime без фактического mount/transfer;
- считать GitHub/connector file reference обычным локальным файлом;
- придумывать `sandbox:/mnt/data/...` ссылку, если файл не создан и не проверен именно в активном ChatGPT runtime;
- сообщать пользователю, что файл «выгружен», «скачан» или «готов», пока destination object не проверен в той среде, откуда пользователь реально сможет его получить.

Перед cross-environment transfer используется только поддерживаемый механизм передачи: connector/file action, явная загрузка/скачивание, staged attachment или другой проверенный transport. После передачи сравнить размер и, когда возможно, SHA256 либо иной устойчивый идентификатор содержимого.

Если прямого transport между двумя средами нет, статус = `BLOCKED`; нельзя имитировать передачу путём обращения к пути другой среды.

## 16. Обязательный launch/complete checklist

Канонические определения `PREPARED / RUNNING / COMPLETE / FAILED / BLOCKED`
находятся в `docs/CRIPTA_GLOSSARY_RU*.md §15`. Этот раздел задаёт только
research/compute evidence gates для переходов между этими состояниями.

До `RUNNING` тяжёлого compute/research:

```text
SOURCE_SAMPLE_CHECK=PASS
SCHEMA_SEMANTICS_CHECK=PASS
INTERPRETER_DEPENDENCIES=PASS
SMALL_E2E=PASS
ONE_WORKER_MEMORY_MEASURED=YES
PARALLELISM_SAFE=YES
WORKERS_EXPECTED=<N>
WORKERS_ACTUAL=<N>
CHECK_AFTER_5_10_SECONDS=PASS
STDERR_EMPTY_OR_EXPLAINED=YES
OUTPUT_GROWING_OR_COMPLETE=YES
```

До `COMPLETE`:

```text
RUNNER_EXIT=PASS
ALL_WORKERS_ACCOUNTED=YES
EXPECTED_UNIVERSE=ACTUAL_UNIVERSE
EXPECTED_ROWS/RANGE=VERIFIED
ERROR_LOGS=CHECKED
NO_OOM_KILL=VERIFIED_IF_RELEVANT
OUTPUT_SCHEMA=VERIFIED
DATA_NOT_EMPTY=YES
QUALITY/NO_DATA_EXPLICIT=YES
RESULT_MANIFEST=WRITTEN
```

Без обязательного evidence статус остаётся `RUNNING`, `FAILED` или `BLOCKED`, но не `COMPLETE`.


## 17. Research filesystem contour и обязательный source snapshot

OWNER DECISION 2026-09-29: все новые server-side research writes живут на
data-disk, в отдельном research contour.

Target:

```text
RESEARCH_ROOT = /data/cripta/research

/data/cripta/research/
  worktrees/          # isolated Git worktrees / experimental source
  runs/               # one directory per exact research run
  cache/              # disposable/rebuildable research cache
  manifests/          # cross-run inventories/evidence
  tmp/                # bounded temporary research files
```

Большие immutable/raw datasets могут иметь отдельный approved root на `/data`
(например `/data/cripta/datasets`) и не обязаны физически дублироваться внутрь
`RESEARCH_ROOT`. Главное правило: research не создаёт persistent outputs на
system disk `/srv`.

После начала migration новые research worktrees/runs в
`/srv/cripta/research_runs` и `/data/cripta/research_runs` запрещены.
Legacy objects остаются transition-only до доказанного переноса/архивации.
Точный transition/current state хранится в `CURRENT_PROJECT_MAP §1.5`.

### 17.1 Поиск migrated legacy research / старой статистики

Отсутствие старого absolute path само по себе **не доказывает отсутствие
данных**. После filesystem migration `legacy path miss != data missing`.

Перед повторным расчётом, заявлением `NOT FOUND` или выводом «старой статистики
нет» выполняется read-only discovery в таком порядке:

```text
1. /data/cripta/research/runs
2. /data/cripta/research/manifests
3. /data/cripta/research/cache          [если искомое могло быть cache/intermediate]
4. /srv/cripta-share/reports           [current shared operational reports]
5. /data/cripta/script_archive         [forensic/cold archive only]
```

Exact current names/paths migrated legacy bundles принадлежат
`CURRENT_PROJECT_MAP §1.5`, а не этому process contract. На current host
`/srv/cripta-share/reports` и `/data/cripta/reports` являются одним backing
report directory; operational fact перепроверяется по MAP/host, а не считается
вечной архитектурой.

Discovery выполняется не только по remembered absolute path. Использовать
доступные ключи:
- basename/file fragment;
- `run_id` / experiment name;
- Strategy/Entry/Exit identifier;
- symbol/coin;
- period/date;
- известное число/metric token;
- CSV/JSON/Parquet/SQLite/manifest/log/result filename.

До нового replay/recompute обязателен отчёт, какие current roots реально
проверены. Cold archive не становится executable Research source автоматически:
для повторного использования payload из `ARCHIVE_ROOT` нужен явный
restore/forensic + provenance/equivalence шаг.

Migrated historical result остаётся research evidence и не наследует текущую
Strategy policy только потому, что payload найден.


До `RUNNING` каждого research run обязателен воспроизводимый source capture.
Минимальный run bundle:

```text
run_manifest.json
source_commit.txt
command.txt
environment.txt
input_provenance.json
source_snapshot/
source_sha256.txt
logs/
results/
```

`source_snapshot/` обязан содержать exact исполняемый исследовательский код,
который не идентифицируется одним опубликованным Git commit. Если worktree
dirty/untracked, в snapshot обязательно попадают все result-affecting modified
и untracked files с hashes; допустим также binary patch + exact untracked
payload, если этого достаточно для byte-exact восстановления.

Правило fail-closed:

```text
SOURCE_REPRODUCIBILITY=PASS
```

обязательно до тяжёлого запуска. Если later run невозможно связать с exact
source bytes, его результат остаётся evidence с недостаточной
воспроизводимостью и не используется как equivalence baseline.

Research может читать published source/canonical data и approved datasets.
Production/runtime запрещено импортировать, исполнять или подхватывать policy/
Python/config непосредственно из `RESEARCH_ROOT`.

Promotion path:

```text
RESEARCH ARTIFACT
-> OWNER DECISION
-> CANON / IMPLEMENTATION
-> GITHUB COMMIT
-> TEST
-> VERIFIED RELEASE
-> RUNTIME
```

## 18. Bybit KZ quarantine: data acquisition / retention

OWNER DECISION 2026-10-01:

```text
QUARANTINED_SYMBOLS =
  1000PEPEUSDT
  DOGEUSDT
  NEARUSDT
  XLMUSDT
```

Пока действует этот owner decision:
- эти четыре symbol не входят в current Research acquisition universe;
- их запрещено добавлять в frozen-dataset downloader defaults, expansion jobs,
  latency telemetry universe и opportunity-tracker defaults;
- existing raw/orderbook/public-trades, symbol-specific caches, derived
  indicators и Research outputs по этой четвёрке подлежат удалению с
  `/data/cripta`;
- liquidation archive по этой четвёрке не создаётся/не докачивается;
- historical trading/audit records в PostgreSQL не удаляются этим правилом:
  это другой audit/operational data contour;
- возврат любого из четырёх symbol в acquisition/retention требует нового
  явного owner decision и актуальной exchange-eligibility проверки.




## 19. R1 research screening / symbol selection lineage

OWNER DECISION 2026-10-04: current R1 research contract is defined in
`TRADING_CONTOUR §1.10`. This section owns the research screening procedure and
records why the current first five symbols were selected.

### 19.1 Required screening matrix

Each candidate symbol is evaluated on the same exact R1 contract with the
conservative `TAKER / TAKER` baseline from §13.1. The minimum directional /
period matrix is:

```text
OLD90 LONG
OLD90 SHORT
RECENT14 LONG
RECENT14 SHORT
```

Each cell reports after-commission economics and sample count. A positive cell
means average trade economics for that exact symbol × direction × period is
strictly above zero after the baseline commissions.

Ranking is shown first by number of positive cells: 4/4, then 3/4, 2/4, 1/4,
0/4. Within a tier, aggregate economics/sample evidence is shown for owner
review; the ranking itself does not automatically activate or exclude a symbol.
Selection into a live/shadow universe remains an explicit owner decision.

### 19.2 Current first-five R1 cohort — evidence checkpoint 2026-10-04

The current 15-symbol screen produced this first-five owner-selected cohort:

```text
APTUSDT  = 4/4 positive
INJUSDT  = 4/4 positive
DOTUSDT  = 4/4 positive
LTCUSDT  = 3/4 positive
ARBUSDT  = 3/4 positive
```

Selection basis was the R1 four-cell directional/period screen after
`TAKER / TAKER` commissions, not coin narrative, market capitalization or a
Dispatcher rating. At the evidence checkpoint, these five symbols together
produced fewer trades than the full 15-symbol universe while improving aggregate
after-commission economics. Exact numbers remain research results in the run
artifacts and are not frozen here as Strategy policy.

The five-symbol set is a current research/live-candidate cohort, not a permanent
R1 universe. Additional symbols must be tested through the same exact matrix and
reported with the exact calculation contract before owner inclusion/exclusion.
Dataset reuse does not allow policy reuse from another Strategy or historical
screen.

### 19.3 Further R1 symbol research

For new symbols, use the same R1 geometry/Entry/Exit semantics and the current
approved cost baseline. Report at minimum:
- the four directional/period cells and sample counts;
- aggregate money after commissions;
- trades/day and money/day for the tested horizon;
- materially negative tails / lifecycle findings;
- any data-quality or exchange-eligibility limitation.

Changing Entry, Exit, STAY semantics, fee model or lifecycle while screening a
new symbol creates a different research variant and must be labelled separately;
it cannot be mixed into the R1 cohort ranking.

Before any real activation, current exchange eligibility, exact Strategy version,
initial loss-containment, implementation equivalence and all LIVE-arm gates from
`TRADING_CONTOUR §4.7` remain mandatory. R1 research evidence alone never
satisfies those gates.

### 19.4 R1 execution-selection checkpoint — OWNER DECISION 2026-10-04

The implementation target selected from the current R1 research chain is:

```text
ENTRY SIGNAL
  strict confirmed R1 only

ENTRY EXECUTION
  PostOnly LIMIT at 0.10% favorable offset from confirmed Entry
  no pre-confirmation placement
  no taker fallback
  cancel/skip on exact-level change, rule invalidation, target-before-fill or superseding opposite signal

NORMAL EXIT
  already-resting opposite-inner TP = maker reduce-only limit

HARD EXIT
  target-reprice marketable / forced close = immediate taker
  no trailing/chase/PostOnly waiting
```

Evidence summary:
- current first-five cohort `APT/INJ/DOT/LTC/ARB`, OLD90 + RECENT14: direct confirmed Entry with taker entry + resting-maker TP produced approximately +245.40 USD after commissions on 100 USD nominal per trade; 0.10% favorable PostOnly Entry with causal occupancy produced approximately +323.00 USD after commissions;
- all 15 screened symbols over the same two periods moved from approximately +247.01 USD to +457.39 USD after commissions under the same execution change;
- 0.10% offset changed the actual trade sequence/occupancy and is therefore an execution-policy result, not a fee-only rescore;
- delayed maker handling for hard exits is deliberately rejected despite small historical improvements because the waiting tail can be materially worse in unseen market conditions.

These numbers are research evidence, not guaranteed future economics. The policy decision being promoted is the execution semantics above, not the historical P&L.

### 19.5 Full R1 documentation research checkpoint

CHECKED HERE 2026-10-04: current canonical R1 was compared against the complete
2026-10-04 research chain. No trading-semantic mismatch was found between the
selected implementation target and the evidence used to select it.

Evidence lineage:

```text
l53_r1_strict_stay_taker_20261004
  strict all-six STAY check

l53_r1_hardstops_taker_20261004
  -1.0 / -1.5 / -2.0 hard-stop variants rejected

l53_r11_preplacement_entry_20261004
  fifth-state pre-confirmation maker placement rejected

l53_r11_entry_offset_postonly_20261004
  confirmed Entry offsets 0.05 / 0.10 / 0.20% with causal occupancy;
  0.10% selected

l53_r11_e010_hard_exit_postonly_20261004
  delayed maker hard-exit event study;
  result deliberately NOT promoted
```

The selected R1 policy is therefore exactly:

```text
strict R1 signal
+ confirmed-entry PostOnly offset 0.10%
+ no pre-confirmation Entry
+ no timeout-to-taker fallback
+ dynamic opposite-inner resting maker TP
+ immediate taker for marketable reprice / forced close
+ one-way close-and-reverse
+ no tested hard stop promoted
```

Implementation gaps in current code are not research permission to alter these
rules. They are implementation findings and must be closed against
`TRADING_CONTOUR §1.10.1`.

The current first implementation stage is SHADOW/test only. Real activation
still requires a separately owner-approved initial loss-containment contract;
historical V1/S2/S3 protection values must not be inherited.


## 20. Multi-venue research identity — OWNER DECISION 2026-10-16

Research datasets that contain public market facts must preserve causal identity
at least as:

~~~text
market_source_id
venue_id or immutable venue lineage
instrument_id
event/observed time
quality/freshness/provenance
~~~

A raw symbol string is not sufficient as a globally unique instrument key.

Multiple CRIPTA TradingAccounts consuming the same MarketSource must not multiply
the public market dataset. Account/Strategy fields are joined as labels/evidence
only when a research question requires actual admission, execution, economics or
attribution.

Cross-venue research preserves each contributing venue/source before computing
spreads, divergence, leader/lagger, basis or arbitrage features. A derived
aggregate never replaces raw contributor identity.

For DEX/on-chain research, provenance may additionally require chain/network,
protocol/pool and block/transaction/event identity. Wallet/TradingAccount
identity remains private execution lineage, not public market grain.

Downloaded reconstructible external data still follows bounded-cache and source
snapshot/provenance rules. This section changes dataset identity only and does
not create trading policy.

## 21. Единый измерительный контракт исследования и многомерный аналитический куб — OWNER DECISION 2026-10-18

### 21.1 Scope и граница решения

Исследовательские факты хранятся **по каждому точному InstrumentIdentity отдельно**. Первичный фокус текущего 30-индикаторного исследования — Bybit и отдельно APTUSDT, INJUSDT, DOTUSDT, LTCUSDT, ARBUSDT за выбранные контрольные периоды. Исследование других Venue, внешних источников и cross-venue lead–lag остаётся последним отдельным этапом; данное решение не запускает его.

Общая схема вычислений универсальна, но результат, coverage, baseline, нормализация, гипотеза, распределение, направленность и статус принадлежат **конкретному инструменту**, а не усреднённой корзине. Ни один найденный на одной монете эффект, числовой порог, результат или торговая трактовка не переносится на другую без её самостоятельного evidence. Рыночный режим/общее движение может добавляться как отдельный контекст; не заменяет индивидуальную диагностику. Внешние факторы не создаются задним числом и не считаются проверенными до отдельного исследования.

### 21.2 Последовательные слои данных

\`\`\`text
SOURCE (immutable event / exact identity / timestamps / provenance)
-> PHYSICAL MEASURE (original and verified physical units)
-> NORMALIZED FEATURE (explicit denominator/window/baseline/version)
-> ANALYTICAL CUBE (instrument-scoped time series and dimensions)
\`\`\`

Слои логически раздельны; нельзя перезаписывать исходные физические величины их нормализованными версиями. Нормализация без известного знаменателя и moment-of-availability = NO_DATA, не ноль. Отдельный indicator variant не маскируется под ту же формулу. Контекст/labels будущего записываются отдельно от causal features.

### 21.3 Система единиц и знаменателей

| Физическая величина / derived measure | Хранимое представление | Обязательная семантика |
|---|---|---|
| Price | quote currency / 1 base asset (для текущего Bybit USDT-инструмента: USDT/base) | InstrumentIdentity, trade/mid/bid/ask/mark/index type, timestamp, precision |
| Quantity | base asset или contracts (как объявлено source) | contract multiplier, conversion contract, source type |
| Executed turnover/notional | quote currency (USDT для USDT-инструмента) | sum(price × base quantity) либо проверенное source notional; side/event semantics |
| Price return | % или basis points (bps; 1%=100 bps) | reference price, start/end, direction, aggregation window; процентные пункты — только разность двух процентных значений |
| Buy/Sell delta, imbalance | signed quote notional, count и/или ratio | sign convention; ratio numerator, denominator and zero denominator rule |
| Open Interest | source contracts/base quantity и optionally quote notional | **only same exact instrument**; publication/availability time; relative change denominator = prior observed OI of this instrument, with explicit unit; no cross-symbol OI denominator |
| Liquidations | count and observed quote notional | venue/instrument/side/event truth, provider coverage and missingness; no inferred zero |
| Order-book depth | quote notional within a specified bps distance from defined price | snapshot/delta reconstruction, liquidity side, levels and time |
| Volatility/range | price % or bps | exact causal window, return/range formula and reference |
| Coin capacity | **specific coin**, not aggregate cryptocurrency market capacity | mandatory capacity kind: observed coin traded turnover / coin-specific capitalization / available depth / other explicitly defined measure; source, coverage, date and denominator; these kinds are NOT interchangeable |
| Volume Profile | separately versioned histogram of traded quantity or quote turnover by price | price bins/tick, base-vs-quote weighting, window, as-of cutoff and POC tie-break |
| VWAP | sum(price × base traded quantity) / sum(base traded quantity) | exact instrument, trade source, window/reset and as-of; quote-notional-weighted price is a DIFFERENT named estimator, not silently VWAP |
| Trade economics (if applicable) | USDT and % **после комиссий** | notional, execution price, TAKER/TAKER baseline (§13.1), separately recorded slippage assumptions |

Для каждого относительного процента обязательны числитель, знаменатель, единица и дата/время знаменателя. Например, \`liquidations_usdt / coin_market_cap_usd\`, \`liquidations_usdt / coin_turnover_usdt\` и \`liquidations_usdt / instrument_open_interest_usdt\` — три самостоятельных именованных feature, а не одна «доля ликвидаций». Coin market capitalization не равна доступной ликвидности стакана. Сопоставление валют требует явного курса/времени и версии пересчёта.

В исследовательском коде не наследовать старую quote-notional-weighted «VWAP» из \`public_trades_day_v1.py\` под стандартным именем: ранее он считал \`sum(price*foreignNotional)/sum(foreignNotional)\`; текущий causal reader считает \`sum(price*size)/sum(size)\`. Старое значение остаётся историческим evidence отдельного estimator; корректность source quantity/notional проверяется по raw schema. Совпадение POC по двум типам весов не доказывает их эквивалентности.

### 21.4 Время, причинность, качество и направления

Минимальный факт содержит: exact \`market_source_id\`, \`venue_id\`, \`instrument_id\`, \`indicator_id\`, \`formula_version\`, \`event_time\`, \`observed_at\`/source-publication semantics, \`available_after\`, as-of window (включительно/исключительно), value, physical/normalized unit, numerator/denominator lineage, quality/freshness, source/run provenance.

Направление event (BUY/SELL, up/down), направление последующего движения, наблюдаемая угроза/благоприятный исход и любые future labels — **раздельные** поля с точной семантикой и собственным временем доступности. Нельзя сделать будущую реакцию цены входным признаком в момент события. Quality \`NO_DATA\`, \`UNKNOWN\`, \`STALE\`, \`PARTIAL\` не равны нулю или отсутствию риска. Окна одного инструмента не наследуют покрытие другого. Границы UTC и возможные нестандартные разрешения timestamps сохраняются из source, а не округляются до потери event order.

### 21.5 Analytical cube: минимальное зерно и допустимые агрегаты

Минимальные три оси: **INSTRUMENT × TIME × INDICATOR**. Практическое зерно дополнительно включает \`market_source_id\`/\`venue_id\`, exact \`formula_version\`, \`window_id\`, unit/denominator identity, quality, as-of, run/source identity. Direction, regime, feature variant, labels, horizon и quality — дополнительные измерения, но не причина схлопывать инструменты или интерпретации.

У каждого показателя в паспорте декларируется aggregate operator: ADDITIVE, WEIGHTED_WITH_DENOMINATOR, RECOMPUTE_FROM_COMPONENTS или NON_AGGREGATABLE. Сырые денежные обороты могут суммироваться только на непересекающихся событиях и совместимых инструментах/единицах; проценты, VWAP, POC, корреляции, z-score, ratios и доходность **не усреднять автоматически** между монетами, окнами или Venue. Для weighted aggregate сохранять веса и достаточные числитель/знаменатель. Cube поддерживает запросы сравнения монет, но canonical feature остаётся per instrument.

### 21.6 Версионированный паспорт и RESEARCH-READY gate

До присвоения статуса каждой паре \`(exact instrument_id, indicator_id, formula_version, source/window scope)\` должен быть зарегистрирован и QC-проверен паспорт:

1. Однозначное физическое определение, formula_version, source fields/parser/schema, direction/sign.
2. Unit, price convention, numerator, denominator, conversions, normalization/baseline; distinct variant IDs for distinct estimators.
3. UTC event/observed/available-after semantics, lookback/window inclusion, reset, late/missing/partial data rules.
4. Aggregation operator and cube grain; no cross-coin averaging/defaults.
5. Immutable version-controlled reader/source + raw source provenance; independent sample/manual QC, sufficient source coverage, causal no-leak verification.
6. **Full requested period on THIS instrument**, count/timestamp/value range/quality QC, source and run manifest; failures and missingness explicit.

Status is **per pair**. Indicator-wide \`RESEARCH-READY\` requires all owner-selected research cohort instruments to pass the same declared scope, without assuming identical numeric outcomes. Existing earlier group status such as \`15/30\` is historical cohort checkpoint, NOT automatically \`75/150\`; old artifacts remain evidence and are audited against this new gate before promotion. A prepared formula or single-symbol smoke is not READY. A new instrument joins with its own untested state and does not inherit readiness. Registration and QC do not create Strategy version, Entry/Exit condition, positive P&L claim, activation or LIVE authorization.

### 21.7 Sequencing and ownership

First register measurement contracts; then implement/verify independently for each coin; then populate multi-dimensional cube and analyze per-coin favorable/adverse signals. Market context is a separate axis with independently established source/causality. The current unfinished Bybit indicators take precedence over external/cross-venue research. Machine-readable feature registry may be added as a **derived, version-controlled implementation artifact**, never a second canonical rule owner. Research/compute methodology remains owned by this §21; shared stable terms by GLOSSARY; DOCUMENTATION_INDEX records the decision.

## 22. Independent L5-3 + H9 Entry-only research — OWNER DECISION 2026-10-21

Scope: independent research hypothesis only. **No modification of current R1, StrategyCard, production Entry/Exit, deployment or LIVE.** Historical R1/STAY6 research is not the policy source for this run. Only the explicitly agreed research geometry below applies.

**Historical scope / non-retroactivity:** the original rules in this §22 (including the **H9 working-range denominator**, the unresolved aggregation/fill implementation gate and the original Exit research menu) apply only to the 2026-09-21..2026-10-20 UTC research version. They are preserved as historical methodology/evidence and MUST NOT silently become defaults for the separate P90L90 run. For P90L90 the owner resolved its own RAW union-span formula and crossing rule in §22.1; this decision does not retroactively change or validate results under §22.

- Research window: 2026-09-21 through 2026-10-20 UTC inclusive (use only completed, QC-verified days; exclude incomplete day explicitly); initial symbols APTUSDT, ARBUSDT, DOTUSDT, INJUSDT, LTCUSDT.
- Compute immutable, causal, separately versioned geometry/timeline from closed candles: **L5-3 = 36 × 5m**; **H9-5m = 108 × 5m**; **H9-15m = 36 × 15m**. L5-3 is independent of H9 and is not H3. H3 is 36 × 5m + 12 × 15m, not an Entry filter for this test.
- All three component geometries retain their individually calculated Wilder ATR200 and ATR × 0.5 boundary-zone facts for provenance. **Entry-side overlap/depth in this experiment uses raw structural extrema, not an ATR-derived threshold**, per explicit owner clarification. Do not confuse this research-only choice with the pre-existing R1 production contract.
- Only the **intended entry side** participates in admission: LONG compares lower/low structural sides; SHORT compares upper/high structural sides. Opposite-side confluence or equality of entire working ranges is **not** required.
- The 5m and 15m H9 sides must agree on the relevant entry-side zone; L5-3 is evaluated relative to that H9 side. **100% depth reference is the full H9 working range between its lower and upper working boundaries** (not price percentage and not boundary-zone thickness). Accept L5-3 depth from H9 intended entry edge into H9 working range **0..15% inclusive**; deeper than 15% is rejected. Record both component boundaries, selected H9 reference and normalized depth for each candidate; no implicit fallback to a 15m-only H9.
- **STAY6 = OFF**; no market-regime, OI, cooldown, flip, position occupancy or Exit is allowed to silently filter the immutable candidate event stream. Separate geometry state, qualified entry-side alignment, prospective price touch/signal, placed order and confirmed fill. The Entry-only archive is **all potential events**, including ones a later scenario cannot take because its position slot is busy.
- Research pipeline: raw/MAYAK-Dispatcher source inventory & gaps → causal geometry timeline → standalone Entry-only event stream and QC → frozen Entry fingerprint → independent Exit scenario replays on frozen stream. Exit families to examine after Entry QC: opposite raw structural L5-3 extremum TP without ATR, moving/dynamic TP, TP floor +0.20%, TP floor +0.13%, time caps 12/18/24h and no cap, hourly MAE/MFE/survival and path-dependent follow-on entries. No Exit included in the Entry-only stage.
- **Fail-closed implementation gate:** the owner specified direction, raw structural entry side, H9 working-range denominator and 15% depth. Any still-underspecified mapping of the two H9 components to one operative entry edge/working range, or price-touch timing/execution rule, must be explicitly resolved before creating final Entry events. Do not invent a max/min/average/confluence aggregation or inherit old replay rules. Interim component-wise diagnostics may be computed but must be labeled **NOT ENTRY**.
- Source/manifest/QC must preserve source snapshots and hashes, no look-ahead, full time coverage, missing-data classification, per-symbol LONG/SHORT counts and sampled manual geometric checks. Store research outputs only in /data/cripta/research/ under the actual cripta writer after permission preflight.

Research-specific owner intention is not a silent update of any separate existing Strategy version/canon. Changes to production contracts require their own explicit canon/update gate.

### 22.1 P90L90 — independent RAW H9 union-depth, two width-median cohorts and three Exit variants (OWNER DECISION 2026-10-24)

**Research-only scope:** new isolated historical methodology; no change to R1/StrategyCard, production Entry/Exit/Execution, Dashboard, PostgreSQL, trading runtime, deployment, exchange permissions or LIVE. This subsection owns P90L90 only. §22's 2026-09-21..2026-10-20 research and its H9 **working** denominator remain a distinct historical version, not a fallback for P90L90.

**Instrument/time identity.** Bybit public-trade source, LTCUSDT, DOTUSDT, ARBUSDT, APTUSDT, INJUSDT, UTC window `[2026-05-18T00:00:00Z, 2026-08-16T00:00:00Z)` (90 UTC days). Record exact venue/instrument identity, dataset and per-day QC. May 17 public trades are warmup-only and MUST NOT generate in-window Entry candidates. Absence of ready 5m/15m ATR200 or insufficient prior daily width history is explicit `NOT_READY`, never a zero-signal day.

**Causal geometry and frozen input.** Use only fully closed candles available at each 5m decision instant: L5-3 = 36 closed 5m; H9-5m = 108 closed 5m; H9-15m = 36 closed 15m. Each component has its own Wilder ATR200 and working inner boundaries `low_raw + 0.5 × ATR200` / `high_raw - 0.5 × ATR200`. Reuse the exact existing immutable `L5-3 CLEAN v1` research geometry/working-width facts with provenance and hashes; DO NOT silently rebuild or replace its ATR200 seed/medians. Compute P90L90 H9 independently with separately documented May 17 warmup, original raw prices and causal 15m closes. At time `t`, 15m H9 may use only the last 15m bar closed no later than `t`. Warmup choices are part of versioned provenance, not a Strategy default.

**OWNER VARIANT A — strict RAW side agreement and union-depth.** On LONG require `H9_5m.low_raw == H9_15m.low_raw`, and use `N_long = L5_3.low_raw - H9_5m.low_raw`. On SHORT require `H9_5m.high_raw == H9_15m.high_raw`, and use `N_short = H9_5m.high_raw - L5_3.high_raw`. For both sides use exactly:

```text
RAW_H9_UNION_SPAN = max(H9_5m.high_raw, H9_15m.high_raw)
                  - min(H9_5m.low_raw, H9_15m.low_raw)
depth_pct = 100 * N_side / RAW_H9_UNION_SPAN
admit: RAW_H9_UNION_SPAN > 0 and 0 <= depth_pct <= 15
```

The comparison is of exact source structural price levels under a declared source price precision; do not invent tolerance, inferred H9 aggregation, ATR-working-range denominator, cross-side equality, min/max-of-side fallback or historical §22 policy. Preserve both H9 components, chosen side, numerator, denominator, depth and QC per event.

**Two independently reproducible L5-3 working-width cohorts.** `working_width_pct = 100 × (L5_3.upper_inner - L5_3.lower_inner) / L5_3.lower_inner`, using the frozen L5-3 states. For each symbol separately:

- `FIXED90`: single median of the available L5-3 working-width states in the declared 90-day window, ±0.5 **percentage points** inclusive. This is retrospective and has `LOOKAHEAD=YES` for earlier dates, and MUST NOT be presented as a deployable causal filter.
- `PREV7D`: median from the previous seven **fully completed UTC calendar days**, recalculated at UTC midnight, same ±0.5 percentage-point band; exclude current/incomplete day and any incomplete prior day. Earliest common complete comparison day is 2026-05-26. No forward leakage.

Report both a common comparison `[2026-05-26T00:00:00Z, 2026-08-16T00:00:00Z)` (82 complete UTC days) and the longest fully valid additional FIXED90 window separately. Do not conflate different window lengths or select a preferred version after viewing profits.

**Immutable candidate event stream.** Within each qualifying completed 5m state, first crossing by adjacent ordered Bybit public-trade records of the relevant **L5-3 RAW** boundary: LONG `prior_price > L5_3.low_raw` and `current_price <= L5_3.low_raw`; SHORT `prior_price < L5_3.high_raw` and `current_price >= L5_3.high_raw`. At most one candidate for each `(symbol, five_minute_state, side)`, preserving first-crossing source order, timestamps (including equal timestamp ties), record/trade identifiers, prior/current prices and full state. Validate archived row order against native trade metadata/tickDirection and classify remaining timestamp-tie ambiguity; do not impose arbitrary sort by trade ID. Public-trade crossing is a **research proxy**, not proof of a marketable order's submitted/accepted/filled state. STAY6/H3/OI/market-regime/cooldown and position occupancy MUST NOT filter this frozen candidate archive.

**Six independent scenario replays.** Cross `FIXED90` and `PREV7D` with `LOCK050`, `BE013`, `TRAIL035`. Each scenario has its own independent one-open-position-per-symbol slot; while occupied, later candidate crossings are counted as ignored, without modifying the immutable stream. A candidate crossing is not exchange execution equivalence. Historical replay assumes entry at the specified tick-proxy price; price precision, slippage, latency and spread limitations must be stated.

- Initial catastrophic hard stop: **−10% gross**, active from assumed opening until exit.
- After first observed favorable move of at least **+0.5% gross**, activate independently: `LOCK050` fixed protection at +0.5% gross; `BE013` fixed protection at +0.13% gross; `TRAIL035` favorable high-/low-watermark trailing distance **0.35 percentage points**. No retroactive execution on the activation record; event chronology and all intratick assumptions must be explicit.
- Dynamic TP uses the opposite **RAW L5-3** structural edge, updated only from fully closed subsequent 5m states. Arm or replace it only if the prospective TP is at least **+0.5% gross** favorable relative to the assumed entry; no silently invented substitute fixed TP if that test fails. Resting maker eligibility requires modeled order placement *before* the matching event. Marketable placements/replacements and stops cannot be charged maker by default.
- **No duration cap.** At archive end distinguish closed/realized from still-open/right-censored trades; disclose any separately computed mark-to-market without mixing it with realized net.

**Financial assumptions and QC.** Nominal 10 USDT per assumed position; baseline entry/marketable exit taker 0.055% per side, demonstrably resting maker TP 0.02%, all charged to realized **net** USDT. Declare funding/slippage/latency/spread as unmodeled where applicable; never claim zero real costs or actual Bybit fills. Per symbol, direction, width cohort and Exit give candidate/accepted/busy/closed/censored counts; TP+/TP−, protected+/−, hard-stop and other exit reasons, fee reconciliation, sum of positive/negative realized P&L and net, holding time and independent checks for lookahead, 5m/15m close timing, timestamp ties, stop activation, maker eligibility and missing raw records. Persist versioned source snapshots, run manifest, input hashes, results and independent QC exclusively in the approved `/data/cripta/research/` contour through the exact authorized `cripta` actor. Register each validated per-instrument result only through §23 and the approved Git history; never transfer P90L90 thresholds into LIVE policy by implication.

**Implementation gate:** `OWNER DECISION` for Variant A is confirmed, but canonical publication, reviewed research source, exact actor/path preflight, independent QC and per-scenario reproducibility must be independently verified before reporting a full P90L90 replay as complete. If this §22.1 is only present in a draft or branch, status is `CANON_UPDATE_REQUIRED`, and implementation/replay remains `HARD_STOP` under project rules.

## 23. Долговечная история исследований по инструменту и карточка монеты — OWNER DECISION 2026-10-21

### 23.1 Назначение и ownership

Каждое завершённое исследование, replay, OOS/holdout или отрицательный тест, результат которого анализируется по монете, **обязательно** регистрируется как отдельный InstrumentResearchRecord по каждому exact InstrumentIdentity. InstrumentResearchLedger — append-only / versioned evidence-журнал; исследовательский контур/Analyst является владельцем расчёта и подготовленного результата. InstrumentResearchCardView — read-only справочная проекция этого журнала в карточке монеты; она не владеет расчётом, Strategy settings, Entry/Exit, торговым сигналом, активацией или Exchange mutation. Общие cross-instrument runs связываются общим run ID, но каждая монета имеет собственные цифры.

Карточка монеты должна показывать **две логические области**: (1) краткую сводку последних/выбранных исследований с явными периодом и статусом; (2) хронологию отдельных исследований, включая отрицательные, failed и незакрытые результаты. Никакая UI-сводка не замещает individual immutable record; не смешивать статистику разных методов, периодов, комиссий, направлений и номиналов. Изменение политики нового исследования не удаляет старые результаты.

### 23.2 Обязательный паспорт одной записи

Для каждого exact instrument × run/version × scenario × time scope обязательно сохранять компактный человекочитаемый результат и машинно-валидируемое содержание:

1. Стабильный record_id, research_run_id, scenario/version/fingerprint, время запуска/завершения **UTC** (с возможностью показа локального времени), дата регистрации, версия скрипта и immutable source hash; точный instrument_id, venue/market source и symbol как display label. Strategy Candidate/hypothesis identity, а для real strategy — exact StrategyCard/version отдельно; research не притворяется LIVE.
2. Исторический период [start_utc, end_utc) и его фактическое coverage, число полных суток, source/dataset identity, completeness/QC, OOS/holdout или in-sample, causal no-lookahead / known lookahead, пропуски и ограничения применимости.
3. **Полный читаемый снимок тестированных параметров** Entry, Exit, защиты и lifecycle, а не короткий nickname. Исторический пример параметров исследования по §22 (2026-09-21..2026-10-20 UTC; НЕ P90L90): H9-5m = 108 closed 5m, H9-15m = 36 closed 15m, L5-3 = 36 closed 5m, exact side GAP=0, depth 0..15% of H9 working range, STAY6 off, индивидуальная медиана L5-3 по объявленному историческому окну с допуском ±0,5 **процентного пункта**, динамический противоположный RAW L5-3 TP и точный floor (+0,2% или +0,5% gross), защита (+0,5% fixed, BE +0,13% after +0,5%, или trailing 0,35% after +0,5%), hard stop −10%, время удержания/censor и занятость одного слота. Это **пример параметров отдельного исследования, не Strategy defaults и не утверждение о готовности LIVE**.
**Для P90L90 (§22.1) паспорт ОБЯЗАН вместо исторического H9 working-range знаменателя явно указывать `RAW_H9_UNION_SPAN`, правило строгого совпадения H9-5m/H9-15m на соответствующей стороне, `FIXED90` с `LOOKAHEAD=YES` либо причинный `PREV7D`, отдельную Exit-версию, фиксированный период и сопоставимый 82-дневный участок.** Нельзя сводить версии к одному идентификатору сценария или переносить историческую формулу §22 в P90L90.

4. Направления LONG/SHORT (отдельно и при совместимом контракте итого), число candidate Entry, допущенных/отфильтрованных сигналов, фактически открытых/закрытых позиций, повторных Entry, отклонённых по occupied slot, и открытых/censored на конце; не включать unrealized P&L в realized.
5. Число и сумма **после комиссии** для TP+, TP−, protected stop+, protected stop−, hard stop и других scenario exit reasons **по факту**, без выдуманного нуля; отдельно все положительные денежные компоненты, все отрицательные денежные компоненты (включая hard stop), итоговый **realized net P&L**, средний/медианный net trade return, optional MFE/MAE и время удержания. Если TP− нет, записать 0 только после QC, а hard stop не скрывать внутри общего Stop−. Результаты NO_DATA, UNKNOWN, PARTIAL не конвертируются в 0.
6. Номинал сделки, leverage/capital assumptions, суммарная база доходности, расчёт доходности **с объявленным denominator**, fee schedule по входу/каждому типу выхода (taker/maker), funding, spread/slippage, price-fill/order assumptions, отсутствие/наличие latency и модель повторного использования освобождённого слота. Не смешивать money per 10 USDT с per 100 USDT без маркированного пересчёта.
7. Статус RESEARCH RESULT / QC PASS|FAIL|PARTIAL / OOS verified|not verified / LIVE equivalence verified|not verified, ссылки на manifest/results/source snapshots, file hashes, audit/test evidence, причины failed/blocked/unknown; дата, автор и история коррекций.

### 23.3 Persistence / purge safety / revision

**Итоговый компактный per-coin паспорт и цифры должны переживать удаление больших исходных research files и исторического market-data cache**. Нельзя делать единственным содержимым записи путь, который может исчезнуть. По смыслу ARCH §48 это проектное долговечное evidence; сохраняются идентичность, исходные проценты/деньги, версия и источник, методология и QC. При purge можно удалить воспроизводимые тяжёлые входные файлы только по действующим retention gates, но нельзя удалять compact InstrumentResearchRecord. Если после purge независимое воспроизведение невозможно, явно зафиксировать REPRODUCIBILITY_LIMITED и причину; не выдавать старый replay за повторно проверенный.

Публикация записи — после QC и сохранения run/scenario lineage, с idempotency key для (instrument_id, run_id, scenario_id, version). Ошибки/исправления — **новая версия/связанный correction record**, без молчаливого переписывания чисел. Нельзя создавать одинаковую цифру из нескольких файлов как две разные попытки или объявлять FAILED равным нулевому P&L. Если запись подготовлена, но не сохранена, статус RESEARCH RESULT NOT REGISTERED; исследовательский цикл не считается закрытым как полностью каталогизированный.

### 23.4 Реализация и границы решения

ARCH §51 определяет данные/потребителей и необходимость долговечного retention; GLOSSARY §35 — термины; этот §23 — единственный владелец минимального содержания записей и процесса их фиксации. DOCUMENTATION_INDEX §46 регистрирует OWNER DECISION. **Физическое хранилище УТВЕРЖДЕНО владельцем**: единственный versioned compact-result JSON — `operations/dashboard/instrument_research_catalog.json` в GitHub `PH1119057/cripta:main` (ARCH §51, INDEX §48). Research/Analyst подготавливает validated per-instrument record, публикует через reviewed Git change c проверкой counts, fee/P&L reconciliation, источника и exact InstrumentIdentity. Успех записи = merged Git commit на main и проверенная возможность чтения, а не branch/local-file creation. Исправления — новые records с ссылками supersedes/corrects, не тихая мутация прошлых чисел; сценарии разных периодов не смешиваются. Снимок читает Dashboard только в рамках installed exact release; системная БД не является вторым writer. До удаления невосполнимых источников отдельно обязателен независимый проверенный backup/export compact JSON и hashes. read API, historical backfill, writer credentials, tested backup/restore, Dashboard UI и release/deploy требуют implementation/preflight/runtime QC: **не считать это реализованным только потому, что выбран Git JSON**. Не создавать вторую независимую базу research truth, не записывать результаты исследований в immutable StrategyCard, не менять торговые контуры или LIVE в рамках этой документационной правки.
## 24. Isolated research Git workstream — OWNER DECISION 2026-10-22

Исследование выполняется в отдельной `research/<task>` branch/worktree от актуального GitHub `main`. Публикуемые изменения ограничены `research/**`, `tests/research/**`, `scripts/research/**`. Такой branch НЕ меняет `docs/**`, `src/**`, trading `operations/**`, install/release/config/CI. Изменение research hypothesis и изменение канонической Strategy/Entry/Exit — разные решения и разные публикации. Scope и исключения определяются только DEVELOPMENT_RELEASE §50.

Все большие расчёты/artefacts и dirty/untracked source snapshots продолжают храниться по §17 на `/data/cripta/research`. Каждому run необходимы exact source commit/bytes, input provenance, source SHA256, results/QC; один Git branch без сохранённого run bundle НЕ доказывает воспроизводимость. Никакая публикация source не делает старые research findings торговой policy.

Validated InstrumentResearchRecord публикуется отдельно через exact `operations/dashboard/instrument_research_catalog.json` на GitHub `main` после record-QC §23, owner-approved-ledger-registration и review. Research не должен менять ради каталога `operations/dashboard/app.py` или UI. При необходимости канонического решения или code implementation создаётся согласованный `docs/*`, `core/*` или явно одобренный `cross/*` changeset. Параллельные research worktrees никогда не пишут в production source mirror/runtime и не изменяют чужие isolated tasks.

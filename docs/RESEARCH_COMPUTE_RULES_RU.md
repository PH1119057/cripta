# CRIPTA — research / compute / data rules

**Версия:** 2.3 · 2026-10-18
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

# CRIPTA — research / compute / data rules

**Версия:** 1.6 · 2026-09-30
**Статус:** routed canonical process contract

Читать перед research, replay, OOS/holdout, большими dataset jobs,
длительными вычислениями и data-forensic.

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
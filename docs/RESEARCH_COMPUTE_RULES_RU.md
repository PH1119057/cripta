# CRIPTA — research / compute / data rules

**Версия:** 1.1 · 2026-09-21
**Статус:** routed canonical process contract

Читать перед research, replay, OOS/holdout, большими dataset jobs,
длительными вычислениями и data-forensic.

Общие source-of-truth / Hard Stop правила задаёт
`CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`.

Текущий universe, физические источники, интервалы покрытия, gaps и правила
`NO_DATA` определяет `docs/RESEARCH_DATA_CONTOUR_RU*.md`. Перед любым
full-universe research/replay этот data contract читается вместе с данным
документом.

# 1. Scope

Этот документ владеет detailed research/compute discipline и не задаёт
Strategy policy.

## 38. Долгие jobs наблюдаемы

Каждый долгий stage обязан показывать stage, processed/total, %, elapsed, ETA, heartbeat ~20–30 sec и cache hit/miss, где это применимо.

Особенно это относится к dependency download, full pytest, large DB backfill, archive, research и soak.

## 39. Console contract

Простая операция — одна физическая строка.

Сложная операция с `if/for/heredoc/Python/SQL/complex quoting/multiple fail-closed checks` оформляется готовым `.sh/.py/.ps1` + одна строка запуска.

Не перекладывать ручное редактирование production на пользователя.

## 40. CHECKED и NOT CHECKED HERE разделять

Каждый отчёт обязан явно различать `CHECKED HERE` и `NOT CHECKED HERE`.

Нельзя выдавать предположение за проверку, особенно для GitHub remote state, actual loaded runtime, DB privileges, exchange truth и service state.

## 41. Не делать категорический вывод из неполного forensic

Запрещён шаблон:

```text
у пользователя A нет ~/.ssh
-> значит на сервере нет GitHub credentials
```

Если проверен только один user/context, формулировка должна быть `НЕ НАЙДЕНО В ЭТОМ КОНТЕКСТЕ`, а не `ЭТОГО НЕТ В СИСТЕМЕ`.

## 50. Runtime-проверка после запуска

Длительный процесс проверяется минимум дважды: сразу после старта и повторно примерно через 5–10 секунд. Проверять, где применимо: реальные worker PID, runner/parent PID, process state, CPU, RAM/RSS, stderr, рост output/progress и ожидаемое число workers.

Если процесс завершился раньше, нужно доказать успешное завершение и проверить результат. На `?`, «проверь», «состояние» статус всегда получать заново с сервера, а не из памяти предыдущего ответа.

## 51. Малый сквозной тест до массового запуска

До полного тяжёлого расчёта выполнить минимальный end-to-end проход на реальных данных и проверить source path, schema/headers, timestamp semantics, units, side/direction semantics, границы дат, output schema и несколько значений вручную.

Технически успешный скрипт с пустыми, неверно прочитанными или семантически неверными данными = `FAILED`.

## 52. Dependency и interpreter preflight

До запуска проверить exact runtime: Python executable/version, required modules/binaries, permissions, source mounts/paths, free disk и available RAM. Нельзя впервые обнаруживать отсутствующую зависимость в полном расчёте. Для автономного research предпочитать stdlib Python, если внешняя зависимость заранее не проверена и не даёт существенной выгоды.

## 53. Большие данные обрабатывать потоково

Raw trades, orderbook tapes и большие market archives по умолчанию обрабатывать streaming/chunk/bucket способом с bounded cache. Полный период нельзя складывать в RAM, если это не доказано безопасным и необходимым.

```text
stream source -> causal aggregation -> bounded cache -> intermediate output -> release memory
```

Перед масштабированием измерить peak/RSS одного worker.

## 54. Parallelism определяется CPU + RAM + I/O

Число workers выбирается по CPU, RAM per worker, disk/decompression I/O, source contention и независимости частей задачи. Запрещено механически делать `workers = symbols`.

Если сервер имеет 4 CPU и независимый research безопасно делится, доступные CPU следует использовать. Но сначала доказать безопасность по RAM/I/O. Для неравномерных задач использовать ограниченное число workers с очередью символов/chunks.

## 55. Причинность research-данных

Любой point-in-time dataset обязан соблюдать `feature_time <= decision_time`. На `T` используются только данные, реально известные к `T`. Future MFE/MAE, stop, recovery, final outcome, будущие zone shifts и будущие MAYAK/Dispatcher states допустимы только как последующие labels/targets, но не входные признаки.

Отсутствие данных хранится как `NO_DATA` и не превращается молча в `0`, `NONE` или `NORMAL`. Для liquidation exact history при отсутствии исходных событий = `NO_DATA`; proxy допустим только как отдельно названный и версионированный `LIQUIDATION_PROXY`.

## 56. Сначала универсальный dataset, потом гипотезы

Если тяжёлые raw-источники нужны многим анализам, сначала строится нейтральный причинный dataset общего назначения:

```text
ALL ENTRY -> minute-by-minute causal state -> reusable research dataset -> analytical passes
```

Нельзя заставлять каждый Exit-кандидат повторно читать многомесячный raw archive, если первичные состояния можно один раз сохранить без future leakage. Существующий Exit/stop не должен заранее определять классы, если исследуется новый способ оценки сделки.

## 57. Пилот не является доказательством

Пилот нужен для проверки механики, данных, наличия явления и стоимости расчёта. Общий вывод требует full-universe проверки и, где применимо, разрезов symbol/direction/time regime, coverage, false positives/negatives и economics after commissions.

Для кандидата считать минимум: пойманные/пропущенные проблемные случаи, испорченные хорошие сделки, saved losses, lost good trades, destroyed recoveries, extra fees/slippage и итог после комиссий.

## 58. Не смешивать наблюдение, причину и интерпретацию

H3/H9 shift, orderbook, OI, flow, liquidation и Dispatcher context — наблюдаемые события/состояния. Нельзя автоматически считать structural shift причиной движения, алгоритмическую заявку spoofing, а очищенный стакан «реальными людьми».

Research должен по возможности проверять цепочку `market facts/state -> MAYAK/objective context -> structural change -> subsequent trade path`. Семантика полей подтверждается источником/каноническим parser contract до экономической интерпретации.

## 59. Файловые пространства разных сред не взаимозаменяемы

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

## 60. Обязательный launch/complete checklist

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

## 61. Universe и data coverage не выводятся из памяти

Перед любым основным исследованием дополнительно применяется
`docs/RESEARCH_DATA_CONTOUR_RU*.md`.

Если владелец явно не задал другой universe, основной current full-universe
research считается на 20 symbols из RESEARCH_DATA_CONTOUR.

Старый 7/9/10/13-symbol dataset или Strategy universe не имеет права
автоматически уменьшать новый research universe.

Каждый запуск обязан иметь field-level coverage map. Общий временной диапазон
не означает одинаковое покрытие public trades / orderbook / OI / funding /
premium / liquidation.

Отчёт на неполном universe обязан иметь:

```text
UNIVERSE_STATUS=SUBSET|PARTIAL
FULL_RESULT=NO
```

и не может становиться основанием формулировки «результат по всем текущим
монетам».

Для exact liquidations отсутствие historical source = `NO_DATA`; наличие
public trades за тот же период не меняет этот статус.

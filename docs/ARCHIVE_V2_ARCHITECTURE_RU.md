# КРИПТА — АРХИВ V2.1

**Документ:** `ARCHIVE_V2_ARCHITECTURE_RU.md`  
**Версия:** 1.1  
**Дата:** 2026-08-31  
**Статус:** архитектурный контракт  
**Приоритет:** реализовать до следующего крупного исследовательского этапа

---

## 1. Цель

Одна кнопка в портале должна создавать **один скачиваемый Snapshot Bundle**, но внутри него данные должны быть физически разделены по назначению.

Текущий монолитный архив смешивает исходники, отчёты, статистику, PostgreSQL, research, журналы и случайный мусор. Это неудобно для анализа и увеличивает размер.

Новый формат должен позволять:

- быстро передать только код;
- быстро передать статистику выбранного периода;
- отдельно открыть отчёты;
- всегда иметь полный восстанавливаемый PostgreSQL dump;
- при необходимости добавить research;
- не тащить venv, старые releases, patch backups и дубликаты source tree.

---

## 2. Канонический Snapshot Bundle

Пример:

```text
CRIPTA_SNAPSHOT_20260831_120000.zip
│
├── 00_INDEX.json
├── 01_CODE.zip
├── 02_REPORTS_3D.zip
├── 03_STATISTICS_3D.zip
├── 04_POSTGRESQL_FULL.dump
├── 04_POSTGRESQL_MANIFEST.json
├── 05_RESEARCH.zip                 # optional
└── 06_LOGS_3D.zip
```

Внешний ZIP нужен только для удобной передачи одним файлом.

Внутренние компоненты независимы и имеют собственный SHA-256.

---

## 3. Общий cutoff

В момент создания job один раз фиксируется:

```text
bundle_cutoff_time_utc
```

Все временные аналитические exports должны быть ограничены:

```text
event_time <= bundle_cutoff_time_utc
```

Это устраняет временной skew, когда разные части архива продолжают дописываться во время упаковки.

PostgreSQL dump является собственным согласованным snapshot и фиксирует отдельное:

```text
database_dump_started_at
database_dump_finished_at
```

---

## 4. 00_INDEX.json

Минимум:

```text
bundle_id
bundle_version
created_at_utc
bundle_cutoff_time_utc

profile
period

git_head
branch
dirty

installed_source_fingerprint
loaded_versions

database_schema_version

components[]
service_states

archive_builder_version
```

Для каждого компонента:

```text
name
purpose
sha256
bytes
status
period_start
period_end
row_counts
```

---

## 5. 01_CODE.zip

Назначение:

> воспроизводимый текущий исходный код и его контракты, без runtime-данных и мусора.

Включать:

```text
AGENTS.md
pyproject.toml
lock files
src/
production/src/
monitoring/
position_supervisor/
operations/
dashboard/
config/
migrations/
scripts/
tests/
test_data/fixtures/
docs/ CANONICAL + RESEARCH docs
systemd templates owned by repo
```

Если фактический active test tree находится вне обычной repo-path, сначала определить его source-of-truth и включить один раз.

---

## 6. CODE — жёсткие исключения

Исключать по именам и шаблонам, а не только точному `venv`:

```text
.git/
.venv/
venv/
env/
*_venv/
*venv*/
test_gate_venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
node_modules/
cache/
staging/
backup/
backups/
releases/
old_releases/
incoming/
reports/
research_runs/
datasets/
*.zip
*.dump
*.sqlite
*.db
```

Исключения должны быть покрыты тестом.

Особенно проверить:

```text
test_gate_venv
source_checkout duplication
```

Они не должны попадать в `01_CODE.zip`.

---

## 7. Никакого дублирования source tree

Если `/srv/cripta` уже является текущим source tree, вложенный:

```text
source_checkout/
```

не должен автоматически архивироваться как второй экземпляр проекта.

Если `source_checkout` нужен только для archive smoke, его надо формировать **временно внутри job staging** из канонического git tree, а не хранить/упаковывать как постоянный дубликат.

---

## 8. 02_REPORTS_<period>.zip

Отдельный слой человеческих и машинных отчётов.

Включать только отчёты, относящиеся к выбранному периоду или актуальному состоянию:

```text
reports/*.md
reports/*.html
reports/*.csv
reports/*.json
reports/*.txt
```

Примеры:

- live trading summaries;
- Analyst reports;
- research summaries;
- diagnostic reports;
- completeness reports.

Не включать raw JSONL статистику — она относится к `03_STATISTICS`.

Не включать ZIP внутри ZIP, если отчёт можно положить исходным файлом.

---

## 9. 03_STATISTICS_<period>.zip

Это основной компактный слой для аналитики.

Структура:

```text
exchange/
mayak/
dispatcher/
strategy/
entry/
execution/
risk/
supervisor/
exit/
analytics/
system_quality/
```

Примеры:

```text
mayak/snapshots.jsonl
mayak/coin_minutes.jsonl
mayak/observation_journal.jsonl
mayak/liquidations.jsonl

dispatcher/runs.jsonl
dispatcher/assessments.jsonl

entry/decisions.jsonl
execution/orders.jsonl
execution/fills.jsonl

supervisor/snapshots.jsonl
supervisor/transitions.jsonl

analytics/event_links.jsonl
analytics/counterfactuals.jsonl
analytics/diagnoses.jsonl
```

---

## 10. STATISTICS экспортируется из PostgreSQL

Если таблица является production truth в PostgreSQL, компактный JSONL экспортировать из БД, а не копировать живой append-only файл.

Причины:

- можно применить точный cutoff;
- можно получить детерминированный row_count;
- легче проверить schema;
- меньше temporal skew.

Для каждой таблицы в manifest:

```text
schema
table
row_count
min_event_time
max_event_time
time_column
export_path
sha256
```

---

## 11. Периоды

Для REPORTS / STATISTICS / LOGS:

```text
3d
10d
all
```

Пользователь выбирает период в портале.

`all` должен иметь предупреждение о размере.

PostgreSQL dump всегда полный.

CODE всегда текущий.

---

## 12. 04_POSTGRESQL_FULL.dump

Полная восстанавливаемая база:

```text
pg_dump --format=custom
--no-owner
--no-privileges
```

Рядом:

```text
04_POSTGRESQL_MANIFEST.json
```

Минимум:

```text
dump_sha256
dump_bytes
database_name
dump_started_at
dump_finished_at
schema_version
restore_command_example
```

Dump обязателен для профиля `ANALYSIS_FULL` и `FULL_RECOVERY`.

---

## 13. 05_RESEARCH.zip

Research не должен автоматически попадать в каждый архив.

Включать только при профиле:

```text
RESEARCH
или
ANALYSIS_FULL_WITH_RESEARCH
```

Включать:

- research scripts;
- run configs;
- machine truth CSV/JSON/Parquet;
- provenance;
- dataset fingerprints;
- development/OOS labels;
- final reports.

Не включать multi-GB market datasets, если их canonical location остаётся на сервере.

---

## 14. 06_LOGS_<period>.zip

Отдельно:

```text
systemd journal extracts
mayak
dispatcher
correlator
supervisor
entry
execution
private_runtime
dashboard
errors
```

Для systemd предпочтительно делать:

```text
journalctl --since ... --until bundle_cutoff_time
```

чтобы лог не продолжал расти во время упаковки.

---

## 15. Профили архива

### CODE

```text
00_INDEX
01_CODE
```

### ANALYSIS_FULL — профиль по умолчанию для передачи ChatGPT/аудита

```text
00_INDEX
01_CODE
02_REPORTS
03_STATISTICS
04_POSTGRESQL_FULL
06_LOGS
```

### ANALYSIS_FULL_WITH_RESEARCH

То же +:

```text
05_RESEARCH
```

### FULL_RECOVERY

```text
00_INDEX
01_CODE
04_POSTGRESQL_FULL
critical sanitized runtime config
service manifests
```

### RESEARCH

```text
00_INDEX
01_CODE
03_STATISTICS
04_POSTGRESQL_FULL
05_RESEARCH
```

---

## 16. Портал

Блок должен иметь:

```text
Профиль:
[Анализ полный ▼]

Период статистики и журналов:
[3 дня ▼]

[Создать архив]
```

Варианты периода:

```text
3 дня
10 дней
весь период
```

После запуска браузер не ждёт упаковку в одном HTTP request.

---

## 17. Асинхронный job

Правильная схема:

```text
POST /api/project/archive-jobs
→ immediately {job_id}

GET /api/project/archive-jobs/{job_id}
→ current status

GET /reports/<finished bundle>
→ download
```

Тяжёлая работа выполняется серверным worker/thread/process.

---

## 18. Этапы

```text
PREPARE
CODE
REPORTS
STATISTICS
POSTGRESQL
RESEARCH
LOGS
INDEX
FINALIZE
SMOKE
DONE
```

Статус:

```text
stage
processed
total
percent
elapsed
eta
heartbeat_at
error
output_path
```

Heartbeat примерно каждые 20–30 секунд.

---

## 19. Переживание F5

Состояние job хранить серверно:

```text
/var/lib/cripta/archive_jobs/<job_id>.json
```

или в PostgreSQL.

После F5 портал снова показывает текущий job.

Не хранить истину только в JS-памяти вкладки.

---

## 20. Fail-closed

Ошибка на обязательном компоненте:

```text
job = FAILED
```

Не публиковать bundle как готовый.

Не трогать:

- production source;
- production DB;
- reports;
- user data.

Partial staging можно сохранить для диагностики или удалить после записи exact error.

---

## 21. Staging

Работать только в:

```text
/srv/cripta-share/.archive_jobs/<job_id>/
```

или другом отдельном temporary root.

Финальный bundle перемещается атомарно в:

```text
/srv/cripta-share/reports/
```

только после `SMOKE=PASS`.

---

## 22. Smoke

Проверить:

### Outer bundle
- ZIP integrity;
- `00_INDEX.json`;
- component SHA256;
- no missing component.

### CODE
- forbidden paths absent;
- full expected tests present;
- py_compile/import smoke;
- strongest practical extracted-source pytest gate.

### REPORTS
- files readable;
- JSON/CSV parse where applicable.

### STATISTICS
- every JSONL parses;
- row_count matches manifest;
- no row after cutoff;
- required tables represented.

### POSTGRESQL
- `pg_restore --list` succeeds.

### LOGS
- archive opens and period metadata present.

---

## 23. CODE smoke не должен требовать постоянного source_checkout

Для extracted test:

```text
extract 01_CODE.zip into temp
run tests there
```

Не создавать постоянный duplicate checkout в production root ради архиватора.

---

## 24. Секреты

Никогда не включать:

```text
.env
API keys
API secrets
private SSH keys
cookies
session secrets
passwords
tokens
```

Sanitized config экспортировать отдельно.

---

## 25. Installed vs loaded

`00_INDEX.json` должен различать:

```text
git_head
installed_files_fingerprint
loaded_service_versions
```

Нельзя считать установленный файл уже загруженным процессом без подтверждения.

---

## 26. Статистика должна расти без раздувания CODE

Рост:

```text
Mayak JSONL
Dispatcher assessments
Analyst links
```

увеличивает `STATISTICS`, а не `CODE`.

Это ключевая цель разделения.

---

## 27. Старый endpoint

Существующий:

```text
POST /api/project/package
```

после перехода V2:

- либо удалить;
- либо оставить временным compatibility wrapper, который запускает `ANALYSIS_FULL / 3d` job и сразу возвращает `job_id`.

Он не должен синхронно строить архив в HTTP request.

---

## 28. Критерий приёмки

Archive V2 принят только если из одного законченного bundle можно:

1. восстановить код;
2. воспроизвести тестовый gate;
3. восстановить PostgreSQL;
4. отдельно открыть отчёты;
5. отдельно анализировать статистику;
6. получить журналы выбранного периода;
7. доказать отсутствие venv/cache/duplicated checkout;
8. доказать component hashes;
9. доказать cutoff;
10. получить результат после F5 страницы.

---

## 29. Что Archive V2 не меняет

Не менять:

```text
Entry
Exit
Risk
Execution
Mayak logic
Dispatcher logic
Supervisor logic
trading settings
live orders
```

Это чисто operational/audit subsystem.

---

## 30. Главный принцип

> **Один Snapshot Bundle для передачи человеку — внутри независимые слои для кода, отчётов, статистики, PostgreSQL, исследований и журналов.**

# КРИПТА — АРХИТЕКТУРА АРХИВА V2

**Документ:** `ARCHIVE_V2_ARCHITECTURE_RU.md`  
**Версия:** 1.0  
**Дата:** 2026-08-30  
**Статус:** архитектурный контракт  
**Рекомендуемое размещение:** `docs/ARCHIVE_V2_ARCHITECTURE_RU.md`

---

## 1. Почему нужен V2

Старый «один ZIP со всем» смешивает:

- production source;
- тесты;
- PostgreSQL backup;
- live statistics;
- research;
- operational logs;
- patch backups;
- virtual environments;
- старые releases.

По мере роста JSONL такой архив становится тяжёлым, медленным и неудобным для адресного анализа.

Цель V2 — одна пользовательская операция, но **несколько логических слоёв**.

---

## 2. Корневой Snapshot Bundle

Пример:

```text
CRIPTA_SNAPSHOT_20260830_120000/
│
├── 00_INDEX.json
├── 01_CODE.zip
├── 02_LIVE_EVIDENCE_3D.zip
├── 03_POSTGRESQL_FULL.dump
├── 04_RESEARCH.zip              # optional
└── 05_LOGS_3D.zip
```

Все компоненты имеют один:

```text
snapshot_bundle_id
created_at_utc
project_commit
source_tree_fingerprint
```

---

## 3. 00_INDEX.json

Главный manifest набора.

Минимум:

```text
bundle_id
created_at_utc

git_head
branch
dirty
source_tree_fingerprint

db_schema_version
database_manifest_hash

components[]
time_window
service_states

mayak_version
dispatcher_version
strategy_versions

archive_builder_version
```

Для каждого компонента:

```text
filename
sha256
size
purpose
included_period
status
```

---

## 4. 01_CODE.zip

Отвечает только на вопрос:

> Какой код и конфигурация могли сформировать это состояние?

Включать:

- production source;
- `src/`;
- `production/src/`;
- полный active test tree;
- `monitoring/`;
- `dashboard/`;
- `config/`;
- `docs/`;
- `operations/`;
- service unit templates;
- migrations/schema;
- `AGENTS.md`;
- `pyproject.toml`;
- lock-file;
- git provenance;
- source manifest.

---

## 5. CODE — что исключать

Не включать:

```text
.venv/
venv/
*_venv/
test_gate_venv/
__pycache__/
.pytest_cache/
node_modules/
```

Также не включать автоматически:

- PostgreSQL dump;
- live JSONL;
- historical reports;
- research datasets;
- patch backups;
- old immutable releases;
- ZIP-патчи;
- incoming;
- secrets.

Исключение old release допускается только при отдельном recovery profile.

---

## 6. Полный test tree обязателен

Если server gate заявляет:

```text
819 passed
```

`01_CODE.zip` должен содержать тот active test tree и project dependencies metadata, которые позволяют этот gate воспроизвести.

Если часть тестов недоступна из snapshot:

```text
FULL_GATE_REPRODUCIBLE=false
```

и причина фиксируется в manifest.

---

## 7. 02_LIVE_EVIDENCE

Это не backup проекта.

Это компактная доказательная история выбранного периода.

Внутри:

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

---

## 8. Exchange evidence

Примеры:

```text
exchange/public_trades.jsonl
exchange/liquidations.jsonl
exchange/oi.jsonl
exchange/funding.jsonl
exchange/market_quality.jsonl
```

Не обязательно экспортировать каждый raw tick, если PostgreSQL/raw store остаётся источником истины и есть воспроизводимый агрегат.

---

## 9. Mayak evidence

```text
mayak/snapshots.jsonl
mayak/coin_minutes.jsonl
mayak/observation_journal.jsonl
mayak/events.jsonl
mayak/source_health.jsonl
```

---

## 10. Dispatcher evidence

```text
dispatcher/assessments.jsonl
dispatcher/profile_versions.jsonl
dispatcher/status_history.jsonl
```

---

## 11. Trading evidence

```text
strategy/signals.jsonl
strategy/rejected_signals.jsonl

entry/decisions.jsonl

execution/orders.jsonl
execution/fills.jsonl

supervisor/transitions.jsonl
supervisor/position_snapshots.jsonl

exit/exits.jsonl
risk/risk_decisions.jsonl
```

---

## 12. Analytics evidence

После появления Аналитика:

```text
analytics/event_links.jsonl
analytics/counterfactuals.jsonl
analytics/diagnoses.jsonl
analytics/daily_summary.jsonl
```

---

## 13. Time depth

Для `LIVE_EVIDENCE` и `LOGS`:

```text
3d
10d
all
```

Но `CODE` и PostgreSQL recovery backup не должны искусственно урезаться по времени.

---

## 14. 03_POSTGRESQL_FULL.dump

Это recovery truth.

Он существует отдельно от compact analytical exports.

Назначение:

- восстановление;
- глубокий audit;
- повторная выборка после появления нового вопроса.

Для обычного анализа предпочтительнее компактный `LIVE_EVIDENCE`.

---

## 15. PostgreSQL component

Рядом с dump должны существовать:

```text
DATABASE_MANIFEST.json
RESTORE_INSTRUCTIONS.md
schema/version
dump SHA256
```

---

## 16. 04_RESEARCH.zip

Создаётся только когда нужен research snapshot.

Включает:

- scripts;
- run configs;
- machine truth CSV/JSON/Parquet;
- reports;
- dataset manifests/fingerprints;
- OOS/holdout labels;
- software/provenance.

Не включать multi-GB raw market data, если они уже гарантированно существуют в каноническом dataset location.

---

## 17. 05_LOGS

Только эксплуатационные журналы выбранного периода:

```text
systemd
mayak
dispatcher
entry
private_runtime
supervisor
dashboard
execution
errors
```

---

## 18. Секреты запрещены

Никогда не включать:

```text
.env
API keys
API secrets
private SSH keys
tokens
password stores
browser credentials
```

Если config содержит секретное поле, экспортируется sanitized version.

---

## 19. Archive Profiles в портале

Рекомендуемые пользовательские режимы:

### `CODE`

Только воспроизводимый код.

### `ANALYSIS`

`CODE manifest + LIVE_EVIDENCE + selected LOGS`.

### `FULL_RECOVERY`

`CODE + PostgreSQL full dump + service/config recovery data`.

### `RESEARCH`

Research-specific bundle.

---

## 20. Асинхронная упаковка

Тяжёлая упаковка не должна жить внутри одного долгого browser `fetch`.

Модель:

```text
POST create archive job
→ job_id immediately
→ background worker
→ GET job status
→ heartbeat/progress
→ download when DONE
```

---

## 21. Этапы job

```text
PREPARE
CODE
POSTGRES
LIVE_EVIDENCE
RESEARCH
LOGS
MANIFEST
FINALIZE
SMOKE
DONE
```

---

## 22. Job status

Минимум:

```text
job_id
state
stage
processed
total
percent
started_at
heartbeat_at
elapsed
eta
output_path
error
```

---

## 23. Fail-closed

Если любой обязательный этап падает:

- не выдавать архив как `DONE`;
- не удалять предыдущий хороший snapshot;
- оставить exact error;
- partial bundle маркировать `FAILED/INCOMPLETE`;
- не трогать production DB/source.

---

## 24. Smoke распакованного архива

Проверять уже готовый артефакт:

- ZIP integrity;
- SHA256;
- JSON/JSONL parse;
- manifest consistency;
- required docs/config;
- imports/py_compile;
- targeted tests;
- максимально полный доступный test gate.

---

## 25. JSONL validation

Для каждого JSONL:

```text
file
line_count
first_time
last_time
parse_errors
```

Если parse error > 0:

```text
LIVE_EVIDENCE_VALID=false
```

---

## 26. Временная совместимость

Все compact exports должны иметь UTC-поля согласно:

`DATA_TIMELINE_CONTRACT_RU.md`.

Это позволяет загружать разные архивные компоненты независимо и соединять их позже.

---

## 27. Source tree vs installed tree

Manifest должен различать:

```text
GIT_SOURCE
INSTALLED_ON_DISK
LOADED_RUNNING
```

Если новый файл установлен, но процесс не перезапущен:

```text
installed_version != loaded_version
```

это фиксируется явно.

---

## 28. Дублирование допустимо только осознанно

PostgreSQL dump и JSONL могут содержать одни данные дважды, потому что служат разным целям.

Это нормальное дублирование:

- dump = recovery truth;
- JSONL = portable analytical evidence.

Но виртуальное окружение и старые patch backups внутри `CODE` — ненужное дублирование.

---

## 29. Адресный экспорт

Позднее портал должен уметь экспортировать не только период, но и объект:

```text
trade_id
signal_id
position_id
event_id
symbol
session
```

Например:

> M3 full stop + ±30m context.

---

## 30. Итог

Архив V2 — не один огромный ZIP.

Это **версионированный набор взаимосвязанных доказательных слоёв**, которые можно хранить, анализировать и восстанавливать независимо.

Главный принцип:

> **Код отвечает “что было установлено”, Live Evidence — “что происходило”, PostgreSQL — “как всё восстановить”, Research — “что исследовалось”, Logs — “как работала инфраструктура”.**

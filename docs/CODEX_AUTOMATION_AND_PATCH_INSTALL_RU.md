# КРИПТА — CODEX AUTOMATION И НЕЗАВИСИМАЯ УСТАНОВКА ПАТЧЕЙ

**Документ:** `CODEX_AUTOMATION_AND_PATCH_INSTALL_RU.md`  
**Версия:** 1.0  
**Дата:** 2026-08-31  
**Статус:** постоянный эксплуатационный контракт  
**Рекомендуемое размещение:** `docs/CODEX_AUTOMATION_AND_PATCH_INSTALL_RU.md`  
**Торговый эффект:** `NONE`

---

# 1. Назначение

Документ решает две инфраструктурные задачи:

1. уменьшить расход контекста/токенов Codex на повторяющиеся детерминированные операции;
2. обеспечить независимый от Codex, воспроизводимый и fail-closed способ установки патчей в проект.

Главный принцип:

> **Модель думает. Сервер считает, проверяет, архивирует и выполняет повторяемую механику.**

И второй:

> **Codex удобен, но не является единственной точкой, через которую можно безопасно изменить проект.**

---

# 2. Границы

Этот контур не изменяет торговую стратегию сам по себе.

Он обслуживает:

```text
development
validation
patch installation
archive
health
soak
audit
reproducibility
```

Он не имеет права автоматически:

```text
включать LIVE
открывать позиции
менять Entry
менять Exit
менять Risk
менять leverage
перенастраивать Mayak/Dispatcher
```

---

# 3. Источник истины

Перед любой операцией определить текущий реальный source of truth.

Для серверной ветки проекта:

```text
/srv/cripta
```

является установленным серверным деревом, которое должно быть проверено непосредственно перед patch/install/audit.

Никакой старый ZIP, локальная копия или старый manifest не может автоматически считаться текущим baseline.

---

# 4. Зачем разгружать Codex

Плохой цикл:

```text
Codex
→ запускает команду
→ получает 1000 строк
→ читает их
→ решает следующую команду
→ снова получает 1000 строк
```

Правильный цикл:

```text
Codex
→ запускает одну стандартную команду
→ сервер выполняет весь детерминированный workflow
→ полный журнал сохраняется на диск
→ Codex получает 10–30 строк summary
→ при FAIL открывает только failure extract
```

---

# 5. Три уровня результата

Каждый служебный инструмент должен формировать:

## FULL LOG

```text
полный stdout/stderr
все команды
timestamps
elapsed
```

Сохраняется на сервере.

## MACHINE RESULT

JSON:

```text
status
stage
exit_code
checks[]
artifacts[]
started_at
finished_at
elapsed
```

## HUMAN SUMMARY

Не более примерно 10–30 строк.

Codex по умолчанию читает только summary.

---

# 6. Стандартные команды

Целевой набор:

```text
cripta-state
cripta-gate
cripta-overlay-check
cripta-diff-report
cripta-docs-audit
cripta-archive
cripta-soak
cripta-field-proof
```

Это могут быть shell wrappers над Python-модулями.

Рекомендуемый каталог реализации:

```text
scripts/dev/
```

или отдельный:

```text
operations/devtools/
```

Но интерфейс команд должен оставаться стабильным.

---

# 7. `cripta-state`

Одна команда должна собирать текущую контрольную точку.

Пример:

```bash
cripta-state
```

Краткий вывод:

```text
STATE=OK

git_head=...
branch=main
dirty=false

services:
  mayak=ACTIVE
  dispatcher=ACTIVE
  correlator=ACTIVE
  supervisor=ACTIVE
  runtime=ACTIVE
  portal=ACTIVE

db_schema=...
open_positions=0
latest_archive=...
loaded_versions=...
```

---

# 8. `cripta-state` — полный machine result

JSON минимум:

```text
git
services
database
loaded_versions
installed_versions
open_positions
active_orders
latest_archives
clock
disk
memory
```

Не показывать secrets.

---

# 9. `cripta-gate`

Одна команда запускает полный стандартный gate.

Например:

```bash
cripta-gate
```

Внутри последовательно:

```text
syntax/import precheck
py_compile
ruff
mypy
targeted mandatory smoke
main pytest
Dispatcher tests
archive smoke if applicable
project-specific Linux gate
```

Не запускать Windows-only gate на Linux и не объявлять его пройденным.

---

# 10. `cripta-gate` — краткий PASS

Пример:

```text
FULL_GATE=PASS
py_compile=PASS
ruff=PASS
mypy=PASS
pytest=849 passed, 1 skipped
dispatcher=30 passed
archive_smoke=PASS
elapsed=00:04:18
full_log=/srv/cripta-share/reports/gates/gate_....log
json=/srv/cripta-share/reports/gates/gate_....json
```

Codex не нужно читать список 849 тестов.

---

# 11. `cripta-gate` — краткий FAIL

Пример:

```text
FULL_GATE=FAIL
stage=pytest
failed=1

first_failure:
tests/test_archive_v2.py::test_cutoff

failure_extract=/.../gate_..._failure.txt
full_log=/.../gate_....log
```

Codex открывает сначала `failure_extract`, а не весь журнал.

---

# 12. `cripta-diff-report`

Использование:

```bash
cripta-diff-report <baseline_commit>
```

Краткий результат:

```text
files_changed=7
insertions=418
deletions=63

production:
  operations/dashboard/archive_v2.py

tests:
  tests/test_archive_v2.py

docs:
  docs/ARCHIVE_V2_ARCHITECTURE_RU.md

trading_sensitive_files_touched=NONE
```

Полный diff сохраняется отдельно.

---

# 13. Sensitive path classification

`cripta-diff-report` должен отдельно маркировать:

```text
ENTRY
EXIT
RISK
EXECUTION
MAYAK
DISPATCHER
SUPERVISOR
OPERATIONS
DOCS
TESTS
RESEARCH
```

Если изменён чувствительный live-контур:

```text
TRADING_SENSITIVE_CHANGE=YES
```

Это не обязательно ошибка, но требует отдельного внимания.

---

# 14. `cripta-docs-audit`

Проверяет активную документацию.

Минимум:

```text
obsolete CODEX_RESUME files
completed NEXT_TASK files
duplicate architecture docs
transport README/MANIFEST under active docs
broken references from AGENTS
unknown authority status
conflicting versions
```

Краткий вывод:

```text
DOCS_AUDIT=PASS
canonical=...
historical=...
obsolete_active=0
broken_refs=0
```

---

# 15. `cripta-archive`

Archive V2 создаётся без участия модели:

```bash
cripta-archive create \
  --profile analysis-full \
  --period 3d
```

Результат:

```text
ARCHIVE=PASS
bundle=...
code_bytes=...
reports_bytes=...
statistics_bytes=...
postgresql_bytes=...
logs_bytes=...
smoke=PASS
sha256=...
```

---

# 16. `cripta-soak`

Для длительных наблюдений:

```bash
cripta-soak start mayak --hours 6
```

или:

```bash
cripta-soak start supervisor --hours 24
```

Job работает независимо от Codex.

---

# 17. Soak heartbeat

Каждые 20–30 секунд job обновляет:

```text
stage
elapsed
remaining/eta
sample_count
last_event
health
```

Но Codex не читает каждый heartbeat.

После завершения:

```text
SOAK=PASS/FAIL
summary.md
machine.json
full.csv/jsonl optional
```

---

# 18. `cripta-field-proof`

Проверяет конкретный end-to-end контракт.

Пример:

```bash
cripta-field-proof liquidation
```

Логика:

```text
wait for real liquidation
→ raw DB
→ Mayak derived snapshot
→ observation journal
→ dispatcher_handoff
→ causal timestamps
```

Результат:

```text
LIQUIDATION_E2E=PASS
event_id=...
raw=YES
derived=YES
journal=YES
handoff=YES
future_link=NO
```

---

# 19. Другие field-proof сценарии

Позднее:

```text
supervisor-real-position
dispatcher-assessment
archive-f5
restart-reconcile
trailing-idempotency
ws-reconnect
```

Каждый сценарий должен быть отдельным детерминированным модулем.

---

# 20. Автоматические периодические отчёты

Допустимо использовать systemd timers:

```text
nightly gate
daily project-state snapshot
daily data-completeness report
Mayak source health
weekly docs audit
```

Но timer не должен автоматически применять patch или менять trading configuration.

---

# 21. Патчи без Codex — цель

ChatGPT или другой разработчик должен иметь возможность подготовить серьёзный patch ZIP, а владелец проекта — установить его без ожидания Codex.

Windows используется как:

```text
transport / operator console
```

Linux-сервер выполняет:

```text
baseline verification
overlay
checks
backup
apply
restart
reconciliation
report
```

---

# 22. Канонический patch ZIP

Пример:

```text
P20260831_MAYAK_DATA_QUALITY_V1.zip
│
├── MANIFEST.json
├── README_RU.md
├── install.sh
├── payload/
│   ├── monitoring/...
│   ├── tests/...
│   └── docs/...
├── SHA256SUMS.txt
└── optional/
    └── rollback_notes.md
```

Если нужен Windows helper:

```text
INSTALL_FROM_WINDOWS.ps1
```

Он не должен самостоятельно редактировать Linux source.

Его задача:

```text
upload/copy
invoke SSH installer
show summary
```

---

# 23. MANIFEST patch

Минимум:

```text
patch_id
patch_version
created_at

expected_baseline
baseline_policy

changed_files[]
deleted_files[]

does_change[]
does_not_change[]

required_services[]
restart_services[]

prechecks[]
targeted_tests[]

payload_sha256
```

---

# 24. Baseline policy

Patch не должен слепо полагаться только на commit из чата.

Installer сначала читает реальный сервер:

```text
git_head
git_status
file hashes where required
service state
database schema if relevant
```

Возможные политики:

```text
EXACT_COMMIT
ALLOWED_COMMITS
FILE_HASH_CONTRACT
REBASE_REQUIRED
```

Если baseline несовместим:

```text
FAIL
```

Project files не трогать.

---

# 25. Серверный installer — только fail-closed

Правильная последовательность:

```text
1. UNPACK TO TEMP
2. VERIFY MANIFEST
3. VERIFY SHA256
4. READ CURRENT BASELINE
5. CREATE TEMP OVERLAY
6. APPLY PAYLOAD TO OVERLAY
7. RUN PRECHECKS ON OVERLAY
8. RUN TARGETED TESTS
9. RUN STRONGEST PRACTICAL GATE
10. ONLY IF GREEN → BACKUP REAL FILES
11. COPY CHANGED FILES
12. VERIFY INSTALLED HASHES
13. RESTART ONLY REQUIRED SERVICES
14. RECONCILE
15. POST-INSTALL SMOKE
16. WRITE INSTALL REPORT
```

---

# 26. Temp overlay

Никогда не проверять серьёзный patch только внутри его payload.

Нужно:

```text
current /srv/cripta
→ temp clone/overlay
→ patch real relative paths
→ tests against final combined state
```

Именно этот overlay является объектом pre-install gate.

---

# 27. До зелёного overlay production не трогать

Если падает:

```text
syntax
import
ruff
mypy
pytest
manifest
hash
baseline
```

не менять:

```text
/srv/cripta
production reports
PostgreSQL
user data
```

Вывести точную причину.

---

# 28. Backup

Перед real apply:

```text
/srv/cripta-share/backups/patches/<patch_id>/<timestamp>/
```

Сохранять:

```text
старые версии изменённых файлов
deleted files
git head
manifest
installed hash report
service states
```

---

# 29. Atomicity

По возможности:

- писать temporary file;
- fsync;
- atomic rename.

Если изменяется несколько файлов, installer должен иметь rollback path.

При mid-apply failure:

```text
restore backups
verify hashes
report ROLLED_BACK
```

---

# 30. Service restart

Patch manifest явно перечисляет:

```text
restart_services
```

Installer не должен перезапускать все службы «на всякий случай».

После restart проверить:

```text
systemctl active
loaded version
health endpoint/status
```

---

# 31. Открытая позиция и trading-sensitive patch

Перед любым patch, затрагивающим:

```text
Execution
Risk
Exit
private runtime
position reconciliation
```

installer обязан проверить exchange/runtime state.

Если есть открытая позиция и manifest не разрешает hot-update:

```text
BLOCKED_OPEN_POSITION
```

Патч не устанавливается.

---

# 32. Read-only/passive patch

Patch для:

```text
docs
archive
Analyst offline
passive Mayak statistics
passive Dispatcher
```

может иметь:

```text
live_trading_effect=NONE
```

Но всё равно проходит baseline и gate.

---

# 33. PowerShell workflow

На Windows пользователь может выполнять одну команду.

Пример:

```powershell
powershell -ExecutionPolicy Bypass -File `
  .\INSTALL_CRIPTA_PATCH.ps1 `
  -Patch "K:\Incoming\P20260831_MAYAK_DATA_QUALITY_V1.zip"
```

PowerShell helper:

1. проверяет существование ZIP;
2. считает локальный SHA256;
3. копирует ZIP на сервер;
4. вызывает серверный installer по SSH;
5. показывает серверный summary;
6. сохраняет полный install report в `K:\Reports`.

---

# 34. PowerShell 5.1

Helper должен быть совместим с Windows PowerShell 5.1:

- без PS7-only syntax;
- предпочтительно ASCII в executable `.ps1`;
- аккуратное quoting;
- явные exit codes;
- никакого скрытого изменения локального `C:\cripta`.

---

# 35. SSH workflow

Без PowerShell:

```bash
scp PATCH.zip robot:/srv/cripta-share/incoming/
ssh robot-admin \
  "sudo /srv/cripta/scripts/patch/install_patch.sh \
   /srv/cripta-share/incoming/PATCH.zip"
```

Конкретные alias/user/path должны соответствовать текущей инфраструктуре.

---

# 36. Один installer — два способа запуска

Критический принцип:

> PowerShell и SSH не должны иметь две разные реализации patch logic.

Оба вызывают один и тот же:

```text
server-side install_patch
```

Иначе со временем процедуры разойдутся.

---

# 37. Серверные команды patch subsystem

Рекомендуемый интерфейс:

```text
cripta-patch inspect PATCH.zip
cripta-patch precheck PATCH.zip
cripta-patch install PATCH.zip
cripta-patch rollback <install_id>
cripta-patch status <install_id>
```

---

# 38. `inspect`

Ничего не меняет.

Выводит:

```text
patch_id
baseline
files
services
trading_sensitive
does_change
does_not_change
sha256
```

---

# 39. `precheck`

Создаёт overlay и прогоняет gate, но **не устанавливает**.

Это удобный режим, если владелец хочет сначала увидеть результат.

---

# 40. `install`

Всегда включает повторный precheck непосредственно перед apply.

Нельзя считать вчерашний `precheck PASS` достаточным, если project baseline уже изменился.

---

# 41. `rollback`

Rollback разрешён только к backup конкретной установки.

Он также:

```text
verifies baseline
restores
checks hashes
restarts required services
runs post-rollback smoke
```

---

# 42. Install report

Каждая попытка сохраняется:

```text
install_id
patch_id

started_at
finished_at

baseline_before
baseline_after

precheck
tests
gate

changed_files
backup_path

service_restart
post_smoke

status:
INSTALLED
FAILED_PRECHECK
FAILED_APPLY
ROLLED_BACK
BLOCKED
```

---

# 43. Краткий вывод для человека

Пример:

```text
PATCH=PASS
patch_id=P20260831_...
baseline=...
overlay_gate=PASS
files_changed=4
backup=...
services_restarted=mayak
post_smoke=PASS
trading_effect=NONE
report=...
```

---

# 44. Полный журнал не отправлять модели автоматически

После успешной установки Codex/ChatGPT получает только summary.

Полный лог читать только если:

```text
FAIL
unexpected warning
audit request
```

---

# 45. Интеграция с Git

Installer не должен сам придумывать source-control policy.

После успешной установки возможны два режима:

## PATCH_APPLIED_TO_WORKTREE

Используется до review/commit.

## PATCH_COMMITTED

Если отдельный approved workflow явно разрешает installer создать commit.

По умолчанию безопаснее:

```text
installer applies
→ gate
→ user/agent reviews
→ commit/push separately
```

---

# 46. Нельзя silently overwrite dirty tree

Если production worktree dirty вне ожидаемых manifest paths:

```text
BLOCKED_DIRTY_TREE
```

Если dirty только в разрешённых временных/runtime paths, они должны быть явно исключены из source-control проверки.

---

# 47. После patch — installed vs loaded

Отчёт различает:

```text
source_on_disk
service_loaded_version
```

Если restart не выполнялся:

```text
INSTALLED_NOT_LOADED
```

Нельзя писать `verified live` только по hash файла.

---

# 48. Reconciliation

После trading-sensitive restart:

- positions;
- qty;
- avg fill;
- stops;
- active orders;
- IDs;
- exchange mode;
- leverage;

сверяются с Bybit.

Unknown mandatory state:

```text
BLOCKED / ERROR
```

---

# 49. Что должен делать Codex после появления этих инструментов

Типичный цикл:

```text
cripta-state
↓
read task
↓
change code
↓
cripta-diff-report BASELINE
↓
cripta-gate
↓
PASS?
  YES → compact report → commit
  NO  → open failure extract only
```

---

# 50. Что должен делать ChatGPT patch workflow

```text
fresh archive / source snapshot
↓
analyze
↓
build consolidated patch ZIP
↓
manifest + hashes
↓
user puts ZIP to K:\Incoming
↓
PowerShell or SSH
↓
server-side precheck/overlay/gate
↓
install
↓
report
```

Codex при этом может вообще не участвовать.

---

# 51. Что нельзя автоматизировать без отдельного решения

Не разрешать devtools автоматически:

```text
retune strategy
enable SHADOW
enable MICRO_LIVE
enable LIVE
change leverage
change monetary risk
modify holdout conclusions
approve research result
```

---

# 52. Приоритет реализации

После Archive V2:

## DEVOPS-P1

```text
cripta-state
cripta-gate
cripta-diff-report
```

## DEVOPS-P2

```text
cripta-overlay-check
cripta-patch inspect/precheck/install
PowerShell helper
```

## DEVOPS-P3

```text
cripta-docs-audit
```

## DEVOPS-P4

```text
cripta-soak
cripta-field-proof
```

## DEVOPS-P5

```text
nightly/daily systemd reports
```

---

# 53. Acceptance DEVOPS-P1

Codex должен суметь проверить проект двумя-тремя командами вместо десятков.

`cripta-gate PASS` должен возвращать компактный summary и отдельный полный лог.

---

# 54. Acceptance patch subsystem

Создать тестовый patch, который меняет безопасный fixture/doc file, и доказать:

1. `inspect` не меняет проект;
2. `precheck` не меняет проект;
3. плохой SHA блокирует;
4. плохой baseline блокирует;
5. failing test блокирует;
6. successful overlay разрешает apply;
7. backup создаётся;
8. install hashes совпадают;
9. post-smoke проходит;
10. rollback восстанавливает исходный hash;
11. PowerShell и SSH вызывают один серверный installer.

---

# 55. Токен-экономия как измеряемая характеристика

Devtools должны сохранять:

```text
commands_run
full_log_lines
summary_lines
```

Можно считать примерную эффективность:

```text
compression_ratio =
full_log_lines / summary_lines
```

Цель не экономия ради экономии, а устранение повторного чтения машинных логов моделью.

---

# 56. Итоговая схема

```text
                 MODEL / CODEX / CHATGPT
                         │
              архитектура / код / диагноз
                         │
                         ▼
              ┌────────────────────┐
              │ SERVER DEVTOOLS    │
              │ state / gate / diff│
              │ soak / proof       │
              └────────────────────┘
                         │
                         ▼
                    /srv/cripta

PATCH FROM CHATGPT
       │
       ▼
K:\Incoming / scp
       │
       ▼
PowerShell OR SSH
       │
       ▼
ONE SERVER INSTALLER
       │
       ▼
TEMP OVERLAY
       │
       ▼
GATES
       │
       ├── FAIL → NO CHANGE
       │
       ▼
BACKUP
       │
       ▼
APPLY
       │
       ▼
TARGETED RESTART
       │
       ▼
RECONCILE / SMOKE
       │
       ▼
REPORT
```

Главный принцип:

> **Модель не должна тратить интеллект на то, что Linux способен повторить детерминированно. И ни одна модель не должна быть обязательным посредником для безопасной установки нашего собственного патча.**


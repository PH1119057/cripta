# CRIPTA — правила работы для ChatGPT / разработчика

**Версия:** 1.3 · 2026-09-12
**Назначение:** обязательный процесс разработки, диагностики, patch/install, Git, PostgreSQL, проверок, консоли и архитектурной дисциплины.
**Приоритет:** вместе с `CRIPTA_ARCHITECTURE_RULES_RU_V1.md` является верхним рабочим контрактом для ChatGPT / разработчика.
**Source of truth:** GitHub `PH1119057/cripta:main` + синхронизированный `/srv/cripta/source_checkout`. Статическая копия в ChatGPT Project Source обязана соответствовать GitHub.

> Эта версия включает обязательные выводы из инцидента установки P1 LIVE STABILIZATION 2026-09-05/06, когда небольшой по коду patch потребовал большого числа подготовительных сборок и почти полного рабочего дня из-за ошибок среды, installer contract, PostgreSQL schema/permissions, Git metadata и Git transport/auth. Повторение этих классов ошибок считается нарушением процесса подготовки.

---

## 1. Не выходить за рамки задачи

Сначала определить scope:

```text
STABILIZATION
INFRASTRUCTURE
TRADING LOGIC
RESEARCH
DOCUMENTATION
```

При стабилизации нельзя самовольно переходить к изменению прикладной торговой архитектуры, Strategy, Entry, Exit, Execution, MAYAK, Dispatcher или исследовательской логики.

Найденное несоответствие `FINDING` не является разрешением немедленно менять архитектуру или торговую policy.

## 2. Source of truth

Текущий source of truth:

```text
GitHub PH1119057/cripta:main
+
/srv/cripta/source_checkout
```

Они должны быть фактически сверены.

Не являются source of truth: старый `C:\cripta`, старые ZIP, старые чаты, локальные заметки, transport manifests, устаревшие handoff и статическая Project Source, если она расходится с GitHub.

Нельзя выдавать исторический baseline за текущий HEAD.

## 3. Обязательный pre-read

### 3.1 В начале нового чата проекта

Прочитать:

1. `CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`;
2. `CRIPTA_ARCHITECTURE_RULES_RU_*.md`;
3. `docs/CURRENT_PROJECT_MAP_RU.md`.

### 3.2 Перед архитектурно чувствительной работой

Перед patch, production-code, MAYAK, Dispatcher, Strategy, Entry, Exit, Execution, account/capital context, signal lifecycle, Analyst/Supervisor ownership, research/OOS/holdout, re-arm/MICRO_LIVE/LIVE и изменением safety contract повторно прочитать:

1. `CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`;
2. `CRIPTA_ARCHITECTURE_RULES_RU_*.md`;
3. `AGENTS.md`;
4. `docs/DOCUMENT_AUTHORITY_RU.md`;
5. `docs/CURRENT_PROJECT_MAP_RU.md`;
6. `docs/PROJECT_ARCHITECTURE_RU.md`;
7. `docs/PROJECT_GOVERNANCE_RU.md`;
8. специализированные контракты реально затрагиваемых компонентов.

## 4. Hard Stop

Если предлагаемое действие противоречит действующим каноническим документам:

```text
ARCHITECTURE_CONFLICT=YES
HARD_STOP=YES
```

Действия:

```text
STOP
-> НИЧЕГО НЕ МЕНЯТЬ
-> УКАЗАТЬ ТОЧНЫЙ КОНФЛИКТ
-> ЗАПРОСИТЬ РЕШЕНИЕ ВЛАДЕЛЬЦА
```

Если код расходится с архитектурным документом — это finding, а не автоматическое разрешение переписать код.

## 5. Верхняя архитектура

Каноническая прикладная цепочка:

```text
MAYAK
  ↓
DISPATCHER
  ↓
STRATEGY
 ├─ ENTRY
 └─ EXIT
  ↓
EXECUTION
  ↓
EXCHANGE
```

Это пять верхнеуровневых слоёв. `Risk` не является самостоятельным верхним архитектурным слоем. Технический поддерживающий контур обеспечивает данные, связь, хранение, исполнение, восстановление, наблюдаемость и аудит, но не становится дополнительным торговым уровнем.

## 5.1 Обязательный end-to-end contract любого поля Strategy

Любое новое поле, параметр, policy, переключатель или сущность внутри `StrategyCard` /
`StrategyActivation` / `EntryPlan` / `ExitPlan` до merge обязано иметь явную классификацию и
матрицу потребителей. Простого хранения в JSON/UI недостаточно.

Для каждого поля фиксируется минимум:

```text
FIELD / POLICY
SEMANTIC_CLASS = DECISION_AFFECTING | EXECUTION_AFFECTING | METADATA
STRATEGY MATERIALIZER
MONITORING / CAUSAL EVIDENCE CONSUMER
ENTRY OR EXIT CONSUMER
EXECUTION CONSUMER / PROPAGATION
COMPATIBILITY CHECK
ACCEPTANCE TEST
MISSING / UNSUPPORTED = FAIL_CLOSED
```

Если поле влияет на торговый смысл, оно обязано фактически влиять на соответствующие
`Entry/Exit`, наблюдение и downstream `Execution`; downstream contract расширяется одновременно.
Запрещено добавлять decision/execution-affecting поле, которое только сохраняется, отображается или
теряется между слоями.

Если поле является только metadata (`name`, `description` и подобное), это должно быть явно
классифицировано как `METADATA / NON_DECISION_AFFECTING`; оно всё равно сохраняется в lineage/read-model
и не может неявно использоваться как торговая policy.

Новый параметр без совместимого consumer является `HARD STOP` для активации соответствующей Strategy,
а не разрешением игнорировать параметр. Ревизия, обнаружившая silent-ignore или несовместимость, должна
сразу исправить её в рамках разрешённого scope; отдельное решение владельца требуется только если для
исправления необходимо выбрать новый торговый смысл, которого нет в каноне/Strategy data.

## 6. Каждый patch имеет точный baseline

ZIP обязан указывать:

```text
patch_id
patch_version
build/revision
created_at
expected_baseline
baseline_policy
prerequisites
changed_files
deleted_files
EXISTING_MODIFY / NEW_FILE
does_change
does_not_change
required_services
restart_services
prechecks
targeted_tests
payload_sha256
```

Классификация путей выполняется только относительно фактического baseline.

## 7. Один логический patch — одна пользовательская версия

Запрещено превращать каждую ошибку подготовки в новую «production-версию» вида `V1.1 … V1.14`, если торговый/production payload по смыслу остаётся тем же.

Использовать два уровня:

```text
LOGICAL PATCH VERSION = V1
PREPARATION BUILD      = RC1 / RC2 / BUILD_01 / BUILD_02
```

Новая production semantic version нужна только если изменился утверждённый resulting contract или production payload.

Ошибка toolchain, installer, quoting, permissions, packaging, test harness, transport или diagnostics — это `PREPARATION_BUILD_FAILURE`, а не новая функциональная версия продукта.

## 8. Перед упаковкой обязательна единая Installation Readiness Matrix

До создания пользовательского ZIP разработчик обязан один раз собрать полную матрицу среды.

Минимум:

```text
SOURCE_HEAD
REMOTE_HEAD
WORKTREE_STATE
repo_owner
git_metadata_owner

test_python
production_python
uv
pytest
ruff
mypy
required_python_modules

postgres_socket
database
table_owner
migration_role
runtime_role
required_privileges
schema_constraints

systemd_services
live_paths
source_live_mapping

git_fetch_url
git_push_url
git_push_principal
ssh_alias_resolution
credential/deploy-key path
non-mutating auth check

temp_root
backup_root
disk_space
filesystem_permissions
```

Нельзя узнавать эти параметры по одному только после очередного падения installer.

## 9. Для каждого шага фиксировать Execution Context

До выполнения сложного installer workflow должна быть таблица:

```text
STEP
ACTOR / UNIX USER
INTERPRETER
DEPENDENCIES
READ TARGETS
WRITE TARGETS
REQUIRED PRIVILEGES
ROLLBACK OWNER
```

Пример классов:

```text
overlay tests        -> test Python / locked env
DB schema check      -> postgres + system Python
DB migration         -> postgres
runtime DB smoke     -> runtime role cripta
Git add/commit       -> repository owner cripta
Git push             -> фактический credential owner
service restart      -> root/systemd
```

Нельзя предполагать, что один Unix-user подходит для всех стадий.

## 10. Toolchain определяется до patch, а не во время падений

Перед упаковкой проверить Python compatibility, uv exact version, locked dependency install, pytest, pytest-asyncio, Ruff, mypy, Hypothesis, project imports и system-only modules, которые используют installer helpers.

Если инструмент недоступен — fail до live mutation.

Нельзя последовательно выпускать новые архивы только потому, что каждый следующий обнаружил `Ruff missing`, `Python missing`, `uv parser wrong` или `venv missing dependency`. После первого toolchain failure выполняется полный toolchain audit всего класса.

## 11. Self-test обязан работать в том interpreter, где он реально запускается

Если installer запускает helper как `$TEST_PYTHON helper.py --self-test`, self-test обязан быть проверен именно в `$TEST_PYTHON`.

Если self-test не использует production dependency, helper не должен импортировать её eagerly.

Принцип:

```text
pure self-test
-> stdlib-only / locked test env

real DB mode
-> lazy import DB driver
-> system production Python
```

Нельзя проверять helper только через `py_compile` и считать import/runtime contract доказанным.

## 12. Temp overlay — единственный объект pre-install проверки

Правильная последовательность:

```text
BASELINE
-> TEMP OVERLAY
-> APPLY REAL RELATIVE PATHS
-> FULL CHECKS
-> BACKUP
-> REAL MUTATION
```

Проверять только payload отдельно недостаточно. До зелёного overlay запрещено менять source/live/PostgreSQL/user data.

## 13. Strongest practical gate

По возможности:

```text
syntax
py_compile
Ruff
mypy
new tests
affected tests
full pytest
component tests
headless smoke
DB source-schema precheck
service-specific smoke
```

Gate должен быть scoped правильно. Нельзя заставлять новый patch «чинить» старый unrelated Ruff debt. Для legacy debt используется regression rule `NEW_DIAGNOSTICS=0`, если полная очистка не является scope задачи.

## 14. Expected non-zero не является аварией shell

Команды, где ненулевой exit code ожидаем и анализируется, нельзя оставлять под общим `set -e` / `ERR trap` без явной обработки.

Использовать локальный контроль `rc` или эквивалент. Нельзя получать installer failure из-за ожидаемого `Ruff rc=1`, если логика специально сравнивает baseline и patched diagnostics.

## 15. PostgreSQL schema — runtime truth, её нельзя угадывать по коду

Перед schema-sensitive patch read-only forensic обязан определить:

```text
table owner
columns
constraints
constraint definitions
existing enum/token values
indexes
foreign keys
runtime grants
migration grants
actual historical rows
```

Использовать `pg_get_constraintdef`, `has_table_privilege`, `to_regclass`, `information_schema` / `pg_catalog`.

Нельзя предполагать, что Python mapping автоматически совместим с существующим CHECK constraint.

## 16. Canonical token обязан проходить storage contract

Если код вводит/использует канонический token, проверить весь путь:

```text
PRODUCTION CODE
-> DB CHECK / ENUM
-> HISTORICAL ROWS
-> ANALYST / READ MODEL
-> UI
-> TESTS
```

Один canonical state — один canonical token.

Например, `OWNER_MODIFIED_STOP` не должен быть правильным в protection truth, но запрещён storage CHECK. Нельзя обходить несовместимость подменой на `UNKNOWN` или `OWNER_MANUAL_STOP`, если бизнес-смысл другой.

## 17. DB migration actor и runtime actor разделять

DDL и historical backfill выполняются только ролью, имеющей на это право. Runtime-role получает только минимально нужные права.

```text
postgres / migration role
-> DDL
-> historical UPDATE/backfill

cripta runtime role
-> operational SELECT/INSERT only where required
-> no historical UPDATE unless explicitly approved
```

Перед migration проверить реальные права, а не узнавать о `permission denied` после backup/apply.

## 18. Backup должен быть доступен тому actor, который его пишет

Root-only temp directory нельзя использовать как destination для команды, выполняемой от `postgres`, если `postgres` не может туда писать.

До backup проверить directory owner, mode, effective writer, output file creation и free space.

Предпочтительно root shell открывает output, а `postgres` пишет через inherited fd/stdout, либо заранее используется каталог с узкими корректными правами.

## 19. Migration + backfill должны быть атомарны

Если технически возможно:

```text
BEGIN
-> validate source schema
-> DDL
-> backfill
-> post-DB assertions
-> COMMIT
```

Ошибка должна вернуть исходные schema/data автоматически.

## 20. Rollback обязан проверять не только bytes, но и metadata/state

После rollback проверить source hashes, live hashes, source/live equality, file owner, file mode, `.git` ownership, worktree state, DB schema, DB rows, services active, gate state, open positions и pending commands.

`ROLLBACK=COMPLETE` можно печатать только после этих проверок.

## 21. Source/live copy сохраняет metadata

При apply и rollback source/live файлов сохранять owner, group и mode. Нельзя временным root-copy превращать рабочий source в root-owned. Для atomic replace metadata временной копии выставляется до rename.

## 22. `.git` — отдельный защищённый объект

Нормальное состояние server checkout:

```text
.git owner/group = repository owner
index owner/group = repository owner
```

Ни installer, ни diagnostics не имеют права оставлять root-owned Git metadata. После любой root-level операции рядом с repository проверять ownership `.git`.

## 23. Read-only означает семантически read-only

Надпись `READ_ONLY=YES` недостаточна. Некоторые команды чтения меняют служебное состояние.

Критический пример: `git status` может обновить `.git/index` stat-cache.

Поэтому все read-only Git-команды на server checkout выполняются только через эквивалент:

```bash
sudo -u cripta env GIT_OPTIONAL_LOCKS=0 git -C /srv/cripta/source_checkout ...
```

Запрещено запускать обычный `git status` из root diagnostic script.

Read-only forensic должен отдельно проверять, что bytes и metadata не изменены, а DB-доступ действительно только SELECT.

## 24. Git sync — только exact changeset

Запрещено `git add -A` и `git add .`.

Обязательный порядок:

```text
status
-> classify
-> exact expected paths
-> exact hashes where meaningful
-> git add -- <explicit paths>
-> staged name/status check
-> staged diff --check
-> commit
-> push
-> verify remote SHA
```

Неизвестный untracked path = hard stop.

## 25. Git add/commit и Git push могут иметь разных actors

Repository mutation (`add`, `commit`, `checkout`, `reset`, index write) выполняется от repository owner (`cripta`), если canonical server checkout принадлежит `cripta`.

Push может использовать отдельный credential owner, если это уже принятый инфраструктурный контракт.

На текущем сервере известная рабочая схема, которую перед использованием всё равно надо перечитать с сервера:

```text
fetch URL:
https://github.com/PH1119057/cripta.git

push URL:
git@github-cripta:PH1119057/cripta.git

existing GitHub Deploy Key:
robot
permission:
Read/write

working credential principal:
root

known private-key path:
/root/.ssh/cripta_github_deploy_ed25519
```

Если фактическая текущая конфигурация изменилась, приоритет имеет серверная реальность, а не этот исторический снимок.

## 26. Push transport проверяется ДО commit

До создания нового локального checkpoint обязательно проверить:

```text
remote.origin.url
remote.origin.pushurl
actual push Unix-user
SSH alias resolution
credential availability
non-interactive auth
remote main SHA
```

Не разрешается сначала делать commit, а только потом впервые выяснять, что push transport сломан.

Минимальный push preflight выполняется тем же Unix-user и тем же SSH/HTTPS transport, который будет использовать реальный push.

Если credentials принадлежат `root`, проверка от `alex` или `cripta` не доказывает отсутствие credentials.

## 27. Нельзя создавать новый credential, пока не исчерпан поиск существующего

Перед предложением нового deploy key, SSH key, token или credential обязательно проверить:

```text
remote pushurl
root SSH config
repository-owner SSH config
operator SSH config
actual credential principal
existing GitHub Deploy Keys
existing successful historical transport contract
```

Отсутствие `.ssh` у одного пользователя не означает отсутствия GitHub deploy key на сервере.

## 28. Root Git разрешён только для транспортной операции при доказанной необходимости

Если existing deploy key принадлежит `root`, допустим root-level push только при соблюдении:

```text
GIT_OPTIONAL_LOCKS=0
exact refspec
no add
no commit
no checkout
no reset
no worktree mutation
```

После push проверить `.git` ownership unchanged, worktree clean и `remote SHA == source SHA`.

Все repository-state mutations остаются за repository owner.

## 29. Source checkpoint не считается завершённым без remote verification

После push необходимо независимо прочитать GitHub `REMOTE_HEAD` и сравнить:

```text
REMOTE_HEAD == SOURCE_HEAD
```

Локальное сообщение `push succeeded` не заменяет отдельную remote verification.

## 30. Production checkpoint различает четыре версии

Нормальное состояние обязано различать:

```text
REMOTE_HEAD
SOURCE_HEAD
INSTALLED_COMMIT
LOADED_COMMIT
```

Нельзя писать просто «версия установлена», если не доказано, что реально загруженные сервисы соответствуют опубликованному source checkpoint.

## 31. Installer rail

Канонический ZIP root:

```text
MANIFEST.json
install.sh
SHA256SUMS.txt
README_RU.md optional
payload/...
```

Wrapper directory запрещён.

Sidecar:

```text
<sha256><two spaces><basename.zip>
```

Установка:

```bash
sudo /usr/local/sbin/cripta-apply-incoming <zip>
```

Patch не должен пытаться тихо self-update persistent runner, если это запрещено текущим runner contract.

## 32. Final ZIP проверяется после последнего изменения

После последней упаковки проверить SHA256, ZIP CRC, internal SHA256SUMS, root structure, manifest version, отсутствие wrapper/garbage, executable bits и payload identity.

После вычисления final SHA архив больше не менять.

## 33. После первого preparation failure проверять весь класс

Пример: `Ruff missing` означает проверить весь toolchain, а не только Ruff. `DB permission denied` означает проверить owner/grants всех реально изменяемых DB objects. `Git ownership drift` означает проверить весь `.git`, root Git calls, copy semantics и diagnostics.

## 34. После двух последовательных preparation failures — Preparation Freeze

Если один и тот же logical patch дважды подряд не дошёл до green apply из-за ошибок подготовки:

```text
PREPARATION_FREEZE=YES
```

Запрещено немедленно выпускать следующий build.

Сначала обязательны full environment matrix, full toolchain audit, full DB schema/permission audit, full Git transport/auth audit, full backup/rollback permission audit и все installer helper self-tests в exact interpreters.

Только потом собирается следующий release candidate.

## 35. Один forensic -> один repair

Нельзя делать серию `repair V1 -> repair V2 -> repair V3 -> repair V4`, если нет нового независимого факта.

Правильный порядок:

```text
READ-ONLY FORENSIC
-> exact root cause
-> class-wide evidence
-> ONE fail-closed repair
-> postcheck
```

## 36. Диагностика не должна сама создавать новый инцидент

Перед выдачей diagnostic script разработчик обязан проверить:

```text
does git read mutate index?
does command create cache?
does command alter atime/mtime?
does psql really run SELECT only?
does systemctl command mutate?
does temporary output affect source?
does sudo change effective HOME / credentials?
```

Если read-only script способен изменить repository metadata, он не имеет права называться read-only.

## 37. Дорогие проверки не отменяются, но инфраструктура должна кэшироваться

Финальный ZIP всё равно проходит strongest practical gate.

Но запрещено бесконечно заново скачивать одинаковый toolchain из-за каждой подготовительной опечатки.

Разрешено и рекомендуется использовать content-addressed uv cache, stable tool bootstrap cache, download cache и immutable lockfile-based environment reuse при сохранении воспроизводимости final overlay.

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

## 42. Пост-install completion chain фиксирован

Если installer дал `INSTALL=PASS` и `PATCH_APPLIED_TO_WORKTREE`, дальше используется один стандартный путь:

```text
post-install read-only verification
-> exact worktree review
-> exact hashes
-> source/live equality
-> DB/runtime evidence
-> exact stage
-> commit
-> push
-> remote verification
-> loaded/runtime checkpoint
-> STOP
```

Нельзя после PASS начинать новый произвольный аудит без отдельной причины.

## 43. Re-arm никогда не является побочным эффектом patch

Patch/install/commit/push не имеют права автоматически enable LIVE, re-arm gate, open position или enable symbols.

После stabilization checkpoint `GATE=DISARMED` сохраняется до отдельного явного решения владельца.

## 44. Тесты не подгонять под implementation

Если test падает, определить: production wrong или test contract stale.

Нельзя менять expectation только ради green и нельзя возвращать старую архитектуру ради старого теста. Если canonical docs изменились, architecture/governance tests должны быть осознанно приведены к текущему contract.

## 45. Запрещённая production-логика не прячется под `if False`

Если функция `NOT_PROVEN / DISABLED_BY_CONTRACT`, запрещённый исполняемый production path не должен просто лежать в коде «на будущее», если владелец отдельно это не утвердил.

## 46. LF/CRLF не путать с code drift

На сервере:

```text
core.autocrlf=false
core.eol=lf
```

Различать `byte-identical`, `newline-only` и `real content drift`. Нельзя молча нормализовать source.

## 47. Source/live mapping не угадывать

Live paths берутся только из installer/deployment contract. Verifier использует тот же mapping. Нельзя сравнивать случайно похожие файлы и объявлять `SOURCE_LIVE=EQUAL`.

## 48. После stable checkpoint остановиться

Если доказано installer PASS, post-install verify PASS, services active, source/live match, DB contract PASS, GitHub synchronized, worktree clean и gate в requested state — этап завершён.

Не начинать новый аудит, research или re-arm без отдельной команды владельца.

---

# Приложение A. Инцидент 2026-09-05/06 — обязательные уроки

| № | Класс ошибки | Что произошло | Постоянное правило |
|---:|---|---|---|
| 1 | Baseline mismatch | ранний patch ожидал неверные file hashes | baseline/hash contract проверять до упаковки |
| 2 | Transform fragility | patch-transform сломался на JS literal | transform self-test на exact baseline до ZIP |
| 3 | Missing Ruff | installer впервые узнал, что Ruff недоступен | полный toolchain matrix до packaging |
| 4 | Missing canonical test Python | окружение тестов определялось по ходу | interpreter contract фиксировать заранее |
| 5 | uv parser | bootstrap/version parsing был подготовлен неверно | helper tests до ZIP |
| 6 | Over-scoped Ruff | новый patch споткнулся о legacy dashboard debt | regression rule `NEW_DIAGNOSTICS=0` для unrelated debt |
| 7 | ERR trap | ожидаемый Ruff non-zero превратился в аварийный installer failure | expected non-zero обрабатывать явно |
| 8 | Stale tests | full pytest выявил stale re-arm/governance assumptions | сравнивать tests с текущими canonical docs |
| 9 | Backup permissions | `pg_dump`/postgres не мог писать в root-only temp path | проверять effective writer до backup |
| 10 | DB mutation role | migration запускалась ролью `cripta` без UPDATE | migration actor/privileges проверять заранее |
| 11 | Git/source metadata | rollback/repair проходы оставляли ownership/index проблемы | metadata является частью postcondition |
| 12 | Constraint incompatibility | `OWNER_MODIFIED_STOP` был правильным в code/protection truth, но запрещён DB CHECK | проверять canonical token по всей storage chain |
| 13 | False read-only diagnostic | root `git status` переписал `.git/index` как `root:root` | read-only Git только repo owner + `GIT_OPTIONAL_LOCKS=0` |
| 14 | Self-test dependency leak | pure DB helper self-test импортировал `psycopg` в overlay venv | self-test запускать в exact interpreter; dependency import lazy |
| 15 | Git push actor confusion | commit был создан, а push впервые проверил неправильный auth context | push transport/auth preflight до commit |
| 16 | Forgotten existing deploy key | отсутствие `.ssh` у `cripta`/`alex` ошибочно трактовалось как отсутствие deploy key вообще | сначала искать actual push principal/root/existing GitHub Deploy Key |
| 17 | Unneeded new credential proposal | был предложен новый deploy key, хотя `robot` уже существовал и имел Read/write | не создавать credentials до полной инвентаризации существующих |
| 18 | Too many package versions | preparation defects превратились в длинную V1.x цепочку | logical version отделять от RC/build revision |
| 19 | Too many sequential repairs | состояние Git исправлялось серией repair-итераций | один forensic -> один доказанный repair |
| 20 | Excessive wall-clock | малый production patch занял почти рабочий день | после двух prep failures — Preparation Freeze и full class audit |

---

# Приложение B. Обязательный pre-package checklist

```text
ARCHITECTURE_PRE_READ=PASS
CURRENT_GITHUB_HEAD=<sha>
SERVER_SOURCE_HEAD=<sha>
REMOTE_SOURCE_EQUAL=YES
WORKTREE_EXPECTED=YES

ENVIRONMENT_MATRIX=PASS
TOOLCHAIN_MATRIX=PASS
DB_SCHEMA_MATRIX=PASS
DB_PRIVILEGE_MATRIX=PASS
GIT_TRANSPORT_MATRIX=PASS
BACKUP_PERMISSION_MATRIX=PASS

OVERLAY_TRANSFORM=PASS
HELPER_SELFTESTS=PASS
FINAL_OVERLAY_GATE=PASS

FINAL_ZIP_SHA256=PASS
FINAL_ZIP_CRC=PASS
INTERNAL_SHA256SUMS=PASS
```

Если хотя бы один пункт не проверен:

```text
NOT_READY_FOR_USER_INSTALL
```

---

# Приложение C. Обязательный post-install checklist

```text
INSTALL=PASS
SOURCE_LIVE=EQUAL
SERVICES=ACTIVE
DB_CONTRACT=PASS
RUNTIME_SMOKE=PASS
GATE=<explicit expected state>

WORKTREE_CHANGESET=EXACT
GIT_METADATA_OWNER=EXPECTED
COMMIT=CREATED
PUSH=PASS
REMOTE_HEAD==SOURCE_HEAD
WORKTREE=CLEAN

CHECKPOINT=STABLE
STOP=YES
```

---

# 49. Главный процессный принцип

Цель не в том, чтобы «в конце концов установить patch».

Цель:

```text
ОДИН РАЗ ПРАВИЛЬНО ПОНЯТЬ СРЕДУ
-> ОДИН РАЗ ПРАВИЛЬНО СОБРАТЬ
-> ОДИН РАЗ ПРОГНАТЬ СИЛЬНЫЙ GATE
-> ОДИН РАЗ УСТАНОВИТЬ
-> ОДИН РАЗ ЗАФИКСИРОВАТЬ CHECKPOINT
```

Если небольшой patch требует длинной цепочки подготовительных версий, это признак дефекта процесса подготовки, а не нормальная стоимость разработки.

ChatGPT / разработчик обязан остановить такой цикл и исправить сам процесс.

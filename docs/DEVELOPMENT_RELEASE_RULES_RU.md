# CRIPTA — development / release / PostgreSQL rules

**Версия:** 1.0 · 2026-09-19
**Статус:** routed canonical process contract

Читать перед patch, source mutation, Git, PostgreSQL migration, packaging,
release, deploy, service restart, rollback и production forensic.

Общие source-of-truth / Hard Stop правила задаёт
`CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`.

# 1. Scope

Этот документ владеет подробной process-механикой разработки и релиза.
Trading policy здесь не определяется.

## 6. Каждый patch/package имеет exact Git identity

Production package обязан указывать минимум:

```text
patch_id
patch_version
build/revision
created_at
source_repository
release_ref
release_commit
release_tree_sha
expected_baseline_commit
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

release_commit — exact commit, из которого построен deploy-affecting payload.
expected_baseline_commit — exact baseline, относительно которого рассчитан patch.
payload_sha256 подтверждает bytes, но не заменяет Git identity.

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

## 12. Git-first release order и temp overlay

Единственный допустимый общий порядок production changeset:

```text
BASELINE / FORENSIC
-> ISOLATED WORKTREE / TEMP OVERLAY
-> FULL CHECKS
-> EXACT COMMIT
-> PUSH
-> INDEPENDENT REMOTE COMMIT VERIFICATION
-> BUILD PACKAGE FROM VERIFIED COMMIT       [если package нужен]
-> PACKAGE IDENTITY / INTEGRITY CHECK
-> BACKUP / ROLLBACK CHECKPOINT
-> DEPLOY EXACT VERIFIED COMMIT
-> POST-DEPLOY / RUNTIME VERIFICATION
```

ZIP/installer — transport/install rail, а не альтернативный source of truth.

Deployment разрешён только из exact Git commit, уже существующего в GitHub main
или owner-approved release ref. Uncommitted worktree, локальный ZIP или sidecar
SHA256 authority не создают.

Если используется ZIP:

```text
MANIFEST.release_commit = independently verified release commit
payload bytes <-> release_commit = proven
expected baseline = verified
```

До independent remote SHA verification production deploy запрещён.

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

## 25. Publisher context и deploy-host разделяются

Repository mutation (add/commit/index/worktree) выполняется от repository owner.
Push выполняется из отдельно разрешённого publisher context.

Deploy-host НЕ обязан иметь GitHub write credential. Нормальная роль
production/deploy host:

```text
READ / FETCH / VERIFY RELEASE IDENTITY
+
DEPLOY / RESTART / VERIFY RUNTIME
```

Наличие GitHub write credential на deploy-host — отдельное security decision,
а не installer prerequisite.

Конкретные Unix users, SSH aliases, key paths, Deploy Key names и transport
details не являются канонической архитектурой и не хранятся в обязательном
pre-read.

## 26. Push transport проверяется ДО commit/publish

Publisher context до publish проверяет exact:
remote/push ref, principal, non-interactive auth и remote SHA.

Если push transport не доказан, changeset не объявляется publish-ready.
Проверка deploy-host read/fetch identity выполняется отдельно и не требует
write credential.

## 27. Нельзя создавать новый credential без доказанной необходимости

Сначала выполняется read-only forensic существующих approved transports и
principals. Отсутствие credential у одного Unix-user не доказывает отсутствие
approved publisher context.

Новый credential создаётся только по отдельному security decision с минимальными
правами.

## 28. Privileged Git не является нормальным release path

Privileged transport допустим только как явно доказанный исключительный
publisher context и не должен выполнять add/commit/checkout/reset/worktree
mutation.

После такой операции обязательны ownership check, clean worktree и independent
remote SHA verification.

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

cripta-apply-incoming является только transport/staging/install mechanism.
Он:
- не создаёт source authority;
- не выбирает release commit;
- не делает git push;
- не превращает локальный ZIP в release;
- обязан прочитать MANIFEST.release_commit;
- обязан fail-closed проверить package/baseline/release identity;
- обязан оставить INSTALLED_COMMIT равным exact verified release_commit.

Production package запрещено применять, если release_commit missing, remote
commit не verified, baseline mismatch либо payload нельзя доказуемо связать с
release_commit.

## 32. Final ZIP проверяется после последнего изменения

После последней упаковки проверить:

```text
MANIFEST.release_commit
MANIFEST.expected_baseline_commit
release commit exists on approved remote ref
release tree SHA
payload <-> release_commit correspondence
ZIP SHA256
ZIP CRC
internal SHA256SUMS
root structure
manifest version
absence of wrapper/garbage
executable bits
```

После вычисления final SHA архив больше не менять.

## 32.1 Public repository / secret-scan gate

Если repository public либо changeset затрагивает credentials/security/release
infrastructure, security checkpoint требует full-history secret scan approved
tool'ом (например gitleaks/trufflehog или эквивалентом).

Scan должен охватывать Git history и текущие paths, включая config/,
operations/, patch_backups/, archive/historical payloads.

Отсутствие scanner/tooling = NOT CHECKED, а не PASS.

Public/private visibility является owner decision. Operational state,
credential identifiers/paths и security-sensitive details не публикуются в
каноне без необходимости.

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

## 42. Пост-deploy completion chain фиксирован

Commit/push/remote verification происходят ДО production deploy по §12. После
deploy нельзя «догонять GitHub» тем же changeset.

Стандартный post-deploy путь:

```text
loaded release identity
-> source/live mapping + exact hashes
-> DB/schema/grants evidence
-> service/runtime evidence
-> gate/permission state
-> repeated runtime check
-> RUNTIME LIVENESS VERIFIED / BEHAVIOR VERIFIED as applicable
-> BLOCKED/FAILED if required evidence is absent
-> STOP
```

Если после deploy требуется изменить source, это новый changeset и он снова
проходит §12 от isolated worktree до GitHub до следующего deploy.

Нельзя после стабильного checkpoint начинать новый произвольный аудит без
отдельной причины.

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

Если доказано deploy PASS, post-deploy verify PASS, services в ожидаемом state, source/live match, DB contract PASS, GitHub synchronized, worktree clean и gate в requested state — этап завершён.

Не начинать новый аудит, research или re-arm без отдельной команды владельца.

---

## 49. Статусы работы нельзя смешивать

Для разработки, research, patch, миграции, длительного расчёта и установки использовать явные состояния:

```text
PREPARED
RUNNING
COMPLETE
FAILED
BLOCKED
```

`PREPARED` = код/задача подготовлены, выполнение не доказано. `RUNNING` =
реальный worker подтверждён runtime evidence. `COMPLETE` = конкретная
операция/расчёт успешно закончены и результат проверен. `FAILED` = процесс
упал/убит/результат неполон или некорректен. `BLOCKED` = действие запрещено
gate/архитектурой/отсутствием обязательных данных.

Это process-state словарь и он не заменяет evidence/status vocabulary META:
`CHECKED HERE / NOT CHECKED HERE / FINDING / RESEARCH RESULT / OWNER DECISION /
CANON / IMPLEMENTED / DEPLOYED / RUNTIME VERIFIED`. Runtime verification при
этом всегда раскладывается на LIVENESS и BEHAVIOR по GLOSSARY. Например
`COMPLETE` для test run не означает `DEPLOYED`, а установленный файл не
доказывает ни runtime liveness, ни runtime behavior.

Запрещено считать PID оболочки доказательством вычисления, `exit_code=0` доказательством корректности данных, наличие output-файла доказательством полноты, а установленный файл — доказательством `LOADED/RUNNING`.

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
| 16 | Forgotten existing credential | отсутствие credential у одного principal ошибочно трактовалось как отсутствие рабочего transport вообще | сначала искать actual push principal и существующий approved transport |
| 17 | Unneeded new credential proposal | был предложен новый credential, хотя рабочий transport уже существовал | не создавать credentials до полной инвентаризации существующих |
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

RELEASE_REF=<ref>
RELEASE_COMMIT=<sha>
RELEASE_TREE_SHA=<sha>
REMOTE_RELEASE_COMMIT_VERIFIED=YES

MANIFEST_RELEASE_COMMIT=<sha>
MANIFEST_RELEASE_COMMIT_MATCH=YES
EXPECTED_BASELINE_COMMIT=<sha>
BASELINE_MATCH=YES

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

PAYLOAD_MATCHES_RELEASE_COMMIT=PASS
FINAL_ZIP_SHA256=PASS
FINAL_ZIP_CRC=PASS
INTERNAL_SHA256SUMS=PASS

DEPLOY_HOST_GITHUB_WRITE_CREDENTIAL_REQUIRED=NO
```

Если хотя бы один обязательный пункт не проверен:
NOT_READY_FOR_USER_INSTALL.

---

# Приложение C. Обязательный release/deploy checklist

```text
WORKTREE_CHANGESET=EXACT
GIT_METADATA_OWNER=EXPECTED
TESTS=PASS

COMMIT=CREATED
PUSH=PASS
REMOTE_RELEASE_COMMIT_VERIFIED=PASS

MANIFEST_RELEASE_COMMIT_MATCH=PASS      [если package rail используется]
PAYLOAD_MATCHES_RELEASE_COMMIT=PASS     [если package rail используется]

BACKUP=PASS
DEPLOY_EXACT_VERIFIED_COMMIT=PASS

INSTALLED_COMMIT=<release_commit>
SOURCE_LIVE=EQUAL

SERVICES=<explicit expected state>
DB_CONTRACT=PASS
RUNTIME_SMOKE=PASS
LOADED_COMMIT=<exact runtime build/source commit>
GATE=<explicit expected state>

WORKTREE=CLEAN
CHECKPOINT=STABLE
STOP=YES
```

INSTALLED_COMMIT и LOADED_COMMIT не копируются из MANIFEST автоматически.
Они подтверждаются отдельной post-deploy/runtime проверкой.

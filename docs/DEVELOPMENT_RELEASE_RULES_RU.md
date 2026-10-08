# CRIPTA — development / release / PostgreSQL rules

**Версия:** 1.9 · 2026-10-04
**Статус:** routed canonical process contract

Читать перед patch, source mutation, Git, PostgreSQL migration, packaging,
release, deploy, service restart, rollback и production forensic.

Общие source-of-truth / Hard Stop правила задаёт
`docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`.

## 1. Scope

Этот документ владеет подробной process-механикой разработки и релиза.
Trading policy здесь не определяется.

Нумерация разделов локальна для этого документа и непрерывна; она не делится
с RESEARCH_COMPUTE или WORK.

## 2. Каждый patch/package имеет exact Git identity

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

## 3. Один логический patch — одна пользовательская версия

Запрещено превращать каждую ошибку подготовки в новую «production-версию» вида `V1.1 … V1.14`, если торговый/production payload по смыслу остаётся тем же.

Использовать два уровня:

```text
LOGICAL PATCH VERSION = V1
PREPARATION BUILD      = RC1 / RC2 / BUILD_01 / BUILD_02
```

Новая production semantic version нужна только если изменился утверждённый resulting contract или production payload.

Ошибка toolchain, installer, quoting, permissions, packaging, test harness, transport или diagnostics — это `PREPARATION_BUILD_FAILURE`, а не новая функциональная версия продукта.

## 4. Перед упаковкой обязательна единая Installation Readiness Matrix

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

## 5. Для каждого шага фиксировать Execution Context

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

## 5.1 Server-side script authoring начинается с actor/path contract

До написания executable server-side script разработчик обязан перечислить все
его filesystem mutations и назначить exact actor для каждой операции.

Минимальный authoring contract:

```text
SCRIPT / SERVICE
EFFECTIVE_ACTOR
READ_PATHS
WRITE_PATHS
CREATE_CHILD_PARENTS
RENAME_DELETE_PARENTS
SYSTEMD_SANDBOX [если есть]
DB_ROLE [если есть]
EXPECTED_OWNER/GROUP/MODE/ACL
PREFLIGHT_COMMAND
```

Правила:
- `mkdir(parents=True)` не считается безопасным только потому, что конечная
  подпапка обычно уже существует: parent, который script способен создать,
  должен быть writable exact actor'ом;
- fixed output path нельзя выбирать по удобству разработчика; он должен
  соответствовать current server profile или иметь отдельный approved path
  contract;
- root может подготовить OS-level path только с exact scope и обязан выставить
  final metadata до первого write менее привилегированного actor;
- script не должен зависеть от root HOME, root-only temp directory, случайной
  supplementary group или текущего interactive shell;
- если persistent systemd service пишет на filesystem, permission preflight
  должен быть воспроизводим до start; для критичного create-parent path
  рекомендуется fail-fast `ExecStartPre`;
- новый server script без actor/path matrix = `PREPARED=NO`.

Current production host actor/path snapshot хранится только в
`docs/CURRENT_PROJECT_MAP_RU*.md §1.4`. Он перепроверяется перед mutation и не
превращается в вечный архитектурный default.

## 6. Toolchain определяется до patch, а не во время падений

Перед упаковкой проверить Python compatibility, uv exact version, locked dependency install, pytest, pytest-asyncio, Ruff, mypy, Hypothesis, project imports и system-only modules, которые используют installer helpers.

Если инструмент недоступен — fail до live mutation.

Нельзя последовательно выпускать новые архивы только потому, что каждый следующий обнаружил `Ruff missing`, `Python missing`, `uv parser wrong` или `venv missing dependency`. После первого toolchain failure выполняется полный toolchain audit всего класса.

## 7. Self-test обязан работать в том interpreter, где он реально запускается

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

## 8. Git-first release order и temp overlay

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

## 8.1 Presentation-only Dashboard UI release — независим от trading runtime

OWNER DECISION 2026-10-05:

Presentation/read-model-only изменение Dashboard не является изменением
торгового runtime и не должно останавливать реальную торговлю.

```text
presentation/read-model Dashboard
-> Git-first verified Dashboard commit
-> fail-closed scope validation
-> atomic Dashboard asset/read-model deploy
-> Dashboard verification

НЕ ТРЕБУЕТ:
mainnet gate = 0
Execution OFF
Strategy deactivation
restart Entry/Exit/private runtime
restart trading services
```

Для этого существует отдельная identity:

```text
DASHBOARD_UI_COMMIT
DASHBOARD_UI_ROOT = /srv/cripta/dashboard-ui
```

`DASHBOARD_UI_COMMIT` не заменяет `INSTALLED_COMMIT` / `LOADED_COMMIT`:
application/trading runtime и Dashboard presentation/read-model bundle
версионируются независимо.

Independent Dashboard scope разрешён для:
- HTML/CSS/presentation JavaScript;
- read-only Dashboard projections/aggregations/formatting, которые только читают
  уже существующие runtime/DB facts и формируют operator read-model;
- export formatting тех же read-only фактов.

Scope **не имеет права менять**:
- authentication/authorization;
- POST/control/mutation handlers, request payloads или control endpoint
  semantics;
- Strategy/Entry/Exit/Execution behavior;
- gates, permissions, LIVE-arm/re-arm;
- exchange mutation;
- decision/execution-affecting JavaScript;
- non-Dashboard runtime service/unit/config semantics.

Если меняется только static asset — restart Dashboard не нужен.
Если меняется approved read-only Dashboard backend — разрешён restart только
`cripta-dashboard.service`; trading services restart запрещён.

Любое control/auth/mutation/decision-affecting изменение не считается
Dashboard read-model change и идёт через обычный runtime release contract.

Canonical UI-only deploy rail обязан:
- брать bytes только из exact verified Git commit, уже опубликованного на
  approved GitHub ref;
- fail-closed проверять presentation/read-model scope;
- не изменять `mainnet gate`, StrategyActivation или execution permissions;
- до/после доказывать неизменность trading gate/permissions;
- не останавливать и не перезапускать trading services;
- для static asset, читаемого на каждый HTTP request, не перезапускать даже
  dashboard service без необходимости;
- для read-only backend change перезапускать только Dashboard и доказывать, что
  PIDs/NRestarts trading services не изменились;
- атомарно переключать только Dashboard presentation/read-model bundle;
- сохранять exact `DASHBOARD_UI_COMMIT` и source/live hash evidence.

Full runtime installer обязан сохранять эту физическую независимость и
подключать runtime Dashboard к current UI asset, а не возвращать presentation
asset под общий trading-runtime lifecycle.

## 9. Strongest practical gate

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

## 10. Expected non-zero не является аварией shell

Команды, где ненулевой exit code ожидаем и анализируется, нельзя оставлять под общим `set -e` / `ERR trap` без явной обработки.

Использовать локальный контроль `rc` или эквивалент. Нельзя получать installer failure из-за ожидаемого `Ruff rc=1`, если логика специально сравнивает baseline и patched diagnostics.

## 11. PostgreSQL schema — runtime truth, её нельзя угадывать по коду

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

## 12. Canonical token обязан проходить storage contract

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

## 13. DB migration actor и runtime actor разделять

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

Для ChatGPT текущий approved server-management rail — SentinelX. Его service principal не считается автоматически repository owner, runtime actor или migration actor. Перед PostgreSQL operation фиксировать `SentinelX actor -> effective Unix actor -> interpreter -> DB role`; runtime/read/research smoke выполняется доказанным `cripta` actor/role path, DDL/backfill — только migration actor. Wrong-user failure не является основанием менять grants.

## 14. Backup должен быть доступен тому actor, который его пишет

Root-only temp directory нельзя использовать как destination для команды, выполняемой от `postgres`, если `postgres` не может туда писать.

До backup проверить directory owner, mode, effective writer, output file creation и free space.

Предпочтительно root shell открывает output, а `postgres` пишет через inherited fd/stdout, либо заранее используется каталог с узкими корректными правами.

## 14.1 Retention deploy rollback backups

`/data/cripta/script_archive/release_backups` хранит локальные rollback checkpoints,
создаваемые непосредственно перед verified deploy. Это не основной системный
backup и не Research dataset/result archive.

Owner decision 2026-10-01:

```text
NORMAL RETENTION
  current deployed release
  + exactly 1 latest previous deploy rollback backup

CONTROL CHECKPOINT
  allowed only for explicitly approved major system restructuring:
  DB structure/migration, major service topology, large runtime contour change,
  or equivalent high-impact release
  + must be explicitly marked by the deploy
  + maximum lifetime = 7 * 24h
  + after expiry it is deleted by the next successful deploy retention pass
```

Правила:
- обычный deploy не сохраняет цепочку старых rollback snapshots «на всякий
  случай»;
- после успешного deploy retention оставляет только самый свежий deploy backup;
- дополнительно может временно сохраняться максимум один marked
  `CONTROL_CHECKPOINT`, если он ещё не старше 7 суток;
- если newest deploy backup сам является control checkpoint, дополнительный
  предыдущий control checkpoint не сохраняется;
- expired control checkpoint не защищается от удаления;
- control checkpoint создаётся только явным owner-approved release decision,
  а не автоматически из размера changeset;
- retention выполняется только после успешного deploy/runtime safety checks,
  чтобы не удалить предыдущую rollback point при failed cutover;
- backup deletion ограничивается exact `release_backups` root и каталогами
  canonical timestamp/release naming contract;
- основной verified system backup
  `/data/cripta/backups/system/<timestamp>` имеет отдельную retention policy и
  этим правилом не удаляется.

Current installer contract:
- `CRIPTA_RELEASE_CONTROL_CHECKPOINT=0|1`;
- при значении `1` обязателен непустой
  `CRIPTA_RELEASE_CONTROL_REASON`;
- installer пишет marker `CONTROL_CHECKPOINT` с UTC creation/expiry metadata;
- maximum protected age = 604800 seconds.

## 14.2 Retention verified system backups

`/data/cripta/backups/system/<UTC timestamp>` — основной локальный verified
system backup. Он шире deploy rollback checkpoint: содержит полный PostgreSQL
dump `cripta`, архив project/runtime surface под `/srv/cripta`, shared
reports и критичные system/nginx/TLS/credential configs. Heavy datasets и
Research runs в него намеренно не входят.

Owner decision 2026-10-01:

```text
SYSTEM BACKUP RETENTION = exactly 2 latest verified generations
```

Правила:
- retention применяется только после успешного создания и verification нового
  system backup;
- удаляются только каталоги canonical timestamp-form
  `20??????T??????Z` внутри exact `/data/cripta/backups/system`;
- два newest verified поколения сохраняются, более старые удаляются;
- incomplete `.<timestamp>.tmp` не считаются verified generation и удаляются
  существующим cleanup trap;
- deploy rollback retention из §14.1 независим и не считается одной из двух
  system-backup generations;
- heavy datasets / Research evidence требуют отдельного data/research backup
  решения и не считаются покрытыми system backup только потому, что project
  runtime сохранён.

## 15. Migration + backfill должны быть атомарны

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

## 16. Rollback обязан проверять не только bytes, но и metadata/state

После rollback проверить source hashes, live hashes, source/live equality, file owner, file mode, `.git` ownership, worktree state, DB schema, DB rows, services active, gate state, open positions и pending commands.

`ROLLBACK=COMPLETE` можно печатать только после этих проверок.

## 17. Source/live copy сохраняет metadata

При apply и rollback source/live файлов сохранять owner, group и mode. Нельзя временным root-copy превращать рабочий source в root-owned. Для atomic replace metadata временной копии выставляется до rename.

## 18. `.git` — отдельный защищённый объект

Нормальное состояние server checkout:

```text
.git owner/group = repository owner
index owner/group = repository owner
```

Ни installer, ни diagnostics не имеют права оставлять root-owned Git metadata. После любой root-level операции рядом с repository проверять ownership `.git`.

## 19. Read-only означает семантически read-only

Надпись `READ_ONLY=YES` недостаточна. Некоторые команды чтения меняют служебное состояние.

Критический пример: `git status` может обновить `.git/index` stat-cache.

Поэтому все read-only Git-команды на server checkout выполняются только через эквивалент:

```bash
sudo -u cripta env GIT_OPTIONAL_LOCKS=0 git -C /srv/cripta/source_checkout ...
```

Запрещено запускать обычный `git status` из root diagnostic script.

Read-only forensic должен отдельно проверять, что bytes и metadata не изменены, а DB-доступ действительно только SELECT.

## 19.1 Filesystem permission preflight выполняется ДО server-side mutation

Перед любым script/install/research/service action, который будет создавать,
писать, переименовывать или удалять filesystem object, обязательна exact
permission matrix:

```text
STEP
EFFECTIVE_ACTOR
PATH
OPERATION = READ | WRITE | CREATE_CHILD | EXECUTE | RENAME | DELETE
PARENT_PATH
OWNER / GROUP / MODE / ACL
SYSTEMD_SANDBOX [если применимо]
PREFLIGHT_RESULT
EXPECTED_POST_OWNER / GROUP / MODE
```

Проверка выполняется именно от фактического Unix actor либо через доказанный
`runuser -u <actor>` / equivalent. Проверка от root, SentinelX service
principal или другого пользователя не доказывает права `cripta`, `postgres`
или service user.

Стандартный read-only helper:

```bash
operations/infrastructure/cripta-permission-preflight \
  --actor <user> \
  --check create-child:<exact_parent>
```

Installed equivalent: `/usr/local/sbin/cripta-permission-preflight`.
Helper не исправляет права и не создаёт payload; он только fail-closed
проверяет exact actor/path.

Script не получает статус `PREPARED` до PASS этой матрицы.

## 19.2 Проверяется право на операцию, а не только mode целевого файла

Минимальная filesystem semantics:

- READ требует traversal по всем parent components + read на target;
- CREATE/MKDIR требует write + execute на фактическом parent directory;
- WRITE existing target требует write на target и traversal parents;
- RENAME/DELETE требует write + execute на соответствующих parent directories;
- EXECUTE требует execute/traverse и доступ к interpreter/dependencies;
- ACL и supplementary groups являются частью effective permission state;
- `sudo`/shell redirection и child process могут иметь разных effective
  writers — writer определяется по фактической операции, а не по строке команды.

Root-created temp/backup/work directory нельзя передавать `cripta` или
`postgres`, пока owner/group/mode/ACL не выставлены до первого write этого
actor. Предпочтительно создавать рабочий каталог сразу final actor'ом.

## 19.3 Systemd permission состоит из двух независимых gates

`ReadWritePaths=` / `ReadOnlyPaths=` / `ProtectSystem=` определяют sandbox,
но не выдают Unix DAC/ACL права.

Для systemd service, который пишет на диск, до deploy обязательны одновременно:

```text
SERVICE User/Group/SupplementaryGroups = VERIFIED
SYSTEMD SANDBOX WRITE PATH             = PASS
UNIX OWNER/GROUP/MODE/ACL              = PASS
EXACT CREATE/WRITE PARENT              = PASS AS SERVICE USER
```

Если service создаёт output directory, рекомендуется fail-fast `ExecStartPre`
через canonical permission-preflight exact writer/path. Наличие
`ReadWritePaths=/path` при `test -w /path = false` не является PASS.

## 19.4 Permission failure — preparation defect, а не повод расширить права

При `PermissionError`, `EACCES`, `EPERM`, `permission denied` или
read-only filesystem error:

```text
STOP
-> identify exact effective actor
-> inspect full parent traversal + ACL + supplementary groups
-> inspect systemd sandbox where applicable
-> audit all write targets of the same script/service
-> apply one least-privilege exact-path repair
-> rerun preflight as actual actor
-> only then rerun workload
```

Запрещено как автоматический repair:
- `chmod 777`;
- recursive `chown/chmod` без exact scope;
- запуск всего workflow от root только потому, что service user получил DENIED;
- выдача лишней group/DB/sudo privilege вместо исправления exact path contract.

После permission repair обязательны metadata/ACL postcheck и реальный
create/write smoke тем actor'ом, которому предназначена запись, если такой
smoke безопасен для данного пути.

## 20. Git sync — только exact changeset

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

## 21. Publisher context и deploy-host разделяются

Repository mutation (add/commit/index/worktree) выполняется от repository owner.
Push выполняется из отдельно разрешённого publisher context.

Deploy-host НЕ обязан иметь GitHub write credential. GitHub `main` является publication authority; после публикации server operational mirror и локальное зеркало владельца синхронизируются существующими approved механизмами GitHub/server/local sync. Исполнитель не должен пытаться вручную мутировать read-only server checkout только ради доставки уже опубликованного changeset; он проверяет, что mirror дошёл до exact GitHub commit, а при задержке диагностирует штатный sync mechanism.

Нормальная роль production/deploy host:

```text
READ / FETCH / VERIFY RELEASE IDENTITY
+
DEPLOY / RESTART / VERIFY RUNTIME
```

Наличие GitHub write credential на deploy-host — отдельное security decision,
а не installer prerequisite.

Credential/key/SSH aliases, Deploy Key names и другие sensitive transport
details не являются канонической архитектурой и не хранятся в mandatory
pre-read.

При этом non-secret current production actor/path profile разрешён и обязателен
в `CURRENT_PROJECT_MAP §1.4`, потому что он нужен для безопасного server-side
authoring. Такой snapshot всегда датирован, считается operational state, а не
архитектурой, и перепроверяется перед mutation.

## 22. Push transport проверяется ДО commit/publish

Publisher context до publish проверяет exact:
remote/push ref, principal, non-interactive auth и remote SHA.

Если push transport не доказан, changeset не объявляется publish-ready.
Проверка deploy-host read/fetch identity выполняется отдельно и не требует
write credential.

## 23. Нельзя создавать новый credential без доказанной необходимости

Сначала выполняется read-only forensic существующих approved transports и
principals. Отсутствие credential у одного Unix-user не доказывает отсутствие
approved publisher context.

Новый credential создаётся только по отдельному security decision с минимальными
правами.

## 24. Privileged Git не является нормальным release path

Privileged transport допустим только как явно доказанный исключительный
publisher context и не должен выполнять add/commit/checkout/reset/worktree
mutation.

После такой операции обязательны ownership check, clean worktree и independent
remote SHA verification.

## 25. Source checkpoint не считается завершённым без remote verification

После push необходимо независимо прочитать GitHub `REMOTE_HEAD` и сравнить:

```text
REMOTE_HEAD == SOURCE_HEAD
```

Локальное сообщение `push succeeded` не заменяет отдельную remote verification.

## 26. Production checkpoint различает четыре версии

Нормальное состояние обязано различать:

```text
REMOTE_HEAD
SOURCE_HEAD
INSTALLED_COMMIT
LOADED_COMMIT
```

Нельзя писать просто «версия установлена», если не доказано, что реально загруженные сервисы соответствуют опубликованному source checkpoint.

## 26.1 Operational delta не маскируется под full release

Если отдельный verified file/unit/config был применён из Git commit без полного
package/release deploy, `INSTALLED_COMMIT` не переписывается фиктивно на этот
commit.

Обязательно дополнительно фиксировать:

```text
OPERATIONAL_DELTA_COMMIT(S)
AFFECTED_LIVE_PATHS
SOURCE_HASH / LIVE_HASH или exact loaded unit content
DEPLOYED_EVIDENCE
LOADED_EVIDENCE [где применимо]
```

`LOADED_COMMIT` продолжает обозначать exact application/runtime build, если
его bytes не менялись. Operational unit/config delta указывается отдельно.

Состояние с operational delta допустимо как временный технический checkpoint,
но для real-arm `SOURCE_LIVE_IDENTITY=PASS` запрещён, пока все
decision/execution-affecting production artifacts не сведены обратно к одному
exact verified release composition и не пройдены соответствующие tests/runtime
checks.

MAP/current runtime report обязан показывать четыре базовые identity и все
активные operational deltas; фраза «installed/current release» без этого
недостаточна.

## 27. Installer rail

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

## 28. Final ZIP проверяется после последнего изменения

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

## 28.1 Public repository / secret-scan gate

Если repository public либо changeset затрагивает credentials/security/release
infrastructure, security checkpoint требует full-history secret scan approved
tool'ом (например gitleaks/trufflehog или эквивалентом).

Scan должен охватывать Git history и текущие paths, включая config/,
operations/, patch_backups/, archive/historical payloads.

Отсутствие scanner/tooling = NOT CHECKED, а не PASS.

Public/private visibility является owner decision. Operational state,
credential identifiers/paths и security-sensitive details не публикуются в
каноне без необходимости.

## 29. После первого preparation failure проверять весь класс

Пример: `Ruff missing` означает проверить весь toolchain, а не только Ruff. `DB permission denied` означает проверить owner/grants всех реально изменяемых DB objects. `Git ownership drift` означает проверить весь `.git`, root Git calls, copy semantics и diagnostics.

## 30. После двух последовательных preparation failures — Preparation Freeze

Если один и тот же logical patch дважды подряд не дошёл до green apply из-за ошибок подготовки:

```text
PREPARATION_FREEZE=YES
```

Запрещено немедленно выпускать следующий build.

Сначала обязательны full environment matrix, full toolchain audit, full DB schema/permission audit, full Git transport/auth audit, full backup/rollback permission audit и все installer helper self-tests в exact interpreters.

Только потом собирается следующий release candidate.

## 31. Один forensic -> один repair

Нельзя делать серию `repair V1 -> repair V2 -> repair V3 -> repair V4`, если нет нового независимого факта.

Правильный порядок:

```text
READ-ONLY FORENSIC
-> exact root cause
-> class-wide evidence
-> ONE fail-closed repair
-> postcheck
```

## 32. Диагностика не должна сама создавать новый инцидент

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

## 33. Дорогие проверки не отменяются, но инфраструктура должна кэшироваться

Финальный ZIP всё равно проходит strongest practical gate.

Но запрещено бесконечно заново скачивать одинаковый toolchain из-за каждой подготовительной опечатки.

Разрешено и рекомендуется использовать content-addressed uv cache, stable tool bootstrap cache, download cache и immutable lockfile-based environment reuse при сохранении воспроизводимости final overlay.

## 34. Пост-deploy completion chain фиксирован

Commit/push/remote verification происходят ДО production deploy по §8. После
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
проходит §8 от isolated worktree до GitHub до следующего deploy.

Нельзя после стабильного checkpoint начинать новый произвольный аудит без
отдельной причины.

## 35. Re-arm никогда не является побочным эффектом patch

Patch/install/commit/push не имеют права автоматически enable LIVE, re-arm gate, open position или enable symbols.

После stabilization checkpoint `GATE=DISARMED` сохраняется до отдельного явного решения владельца.

## 36. Тесты не подгонять под implementation

Если test падает, определить: production wrong или test contract stale.

Нельзя менять expectation только ради green и нельзя возвращать старую архитектуру ради старого теста. Если canonical docs изменились, architecture/governance tests должны быть осознанно приведены к текущему contract.

## 37. Запрещённая production-логика не прячется под `if False`

Если функция `NOT_PROVEN / DISABLED_BY_CONTRACT`, запрещённый исполняемый production path не должен просто лежать в коде «на будущее», если владелец отдельно это не утвердил.

## 38. LF/CRLF не путать с code drift

На сервере:

```text
core.autocrlf=false
core.eol=lf
```

Различать `byte-identical`, `newline-only` и `real content drift`. Нельзя молча нормализовать source.

## 39. Source/live mapping не угадывать

Live paths берутся только из installer/deployment contract. Verifier использует тот же mapping. Нельзя сравнивать случайно похожие файлы и объявлять `SOURCE_LIVE=EQUAL`.

## 40. После stable checkpoint остановиться

Если доказано deploy PASS, post-deploy verify PASS, services в ожидаемом state, source/live match, DB contract PASS, GitHub synchronized, worktree clean и gate в requested state — этап завершён.

Не начинать новый аудит, research или re-arm без отдельной команды владельца.

---

## 41. Статусы работы нельзя смешивать

Канонические определения process-state terms
`PREPARED / RUNNING / COMPLETE / FAILED / BLOCKED` находятся только в
`docs/CRIPTA_GLOSSARY_RU*.md §15`.

Для разработки, research, patch, миграции, длительного расчёта и установки
использовать именно эти состояния без локального переопределения.

Они не заменяют evidence/status vocabulary META:
`CHECKED HERE / NOT CHECKED HERE / FINDING / RESEARCH RESULT / OWNER DECISION /
CANON / IMPLEMENTED / DEPLOYED / RUNTIME VERIFIED`. Runtime verification при
этом всегда раскладывается на LIVENESS и BEHAVIOR по GLOSSARY. Например
`COMPLETE` для test run не означает `DEPLOYED`, а установленный файл не
доказывает ни runtime liveness, ни runtime behavior.

Запрещено считать PID оболочки доказательством вычисления, `exit_code=0` доказательством корректности данных, наличие output-файла доказательством полноты, а установленный файл — доказательством `LOADED/RUNNING`.


## 42. SOURCE / RUNTIME / RESEARCH filesystem contours

OWNER DECISION 2026-09-29 задаёт target physical layout:

```text
SOURCE_ROOT        = /srv/cripta/source_checkout
RUNTIME_CODE_ROOT  = /srv/cripta/runtime
RESEARCH_ROOT      = /data/cripta/research
ARCHIVE_ROOT       = /data/cripta/script_archive
```

`SOURCE_ROOT` — synchronized operational mirror GitHub `main`: published source,
docs и repository metadata. Он не является live runtime root и не является
research output root.

`RUNTIME_CODE_ROOT` — target root executable production release composition.
До coordinated runtime migration legacy live paths остаются действительными
только если перечислены в current MAP. Нельзя переносить active runtime
каталоги простым `mv`; required path changes проходят обычный
Git/test/release/deploy/runtime-evidence chain.

`RESEARCH_ROOT` находится на `/data`. Новые research worktrees, run outputs,
temporary outputs, source snapshots, manifests и research caches на системном
диске запрещены. Raw/shared datasets могут находиться в другом exact approved
root на `/data`.

`ARCHIVE_ROOT` хранит historical/offloaded artifacts и не является executable
source ни для runtime, ни для нового research run без явного restore/equivalence
шага.

Границы зависимостей:

```text
SOURCE -> RESEARCH     allowed read/copy with provenance
SOURCE -> RUNTIME      only verified release/deploy
RESEARCH -> SOURCE     only through reviewed Git changeset
RESEARCH -> RUNTIME    direct dependency forbidden
RUNTIME -> RESEARCH    import/load/execute forbidden
ARCHIVE -> *           only explicit restore/forensic, never implicit
```

Filesystem migration обязана быть staged и reversible:

```text
CANON TARGET
-> current-reference forensic
-> actor/path preflight
-> copy/rename with preservation
-> byte/hash equivalence
-> update consumers
-> test
-> deploy/reload where applicable
-> runtime evidence
-> only then remove legacy source path
```

Compatibility symlink разрешён только как explicitly dated migration bridge,
с owner/path/expiry в MAP. Он не должен скрывать продолжающиеся новые writes в
legacy contour.


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
| 21 | Systemd sandbox != Unix permission | `ReadWritePaths` был открыт, но MAYAK report два дня падал на root-owned output parent | проверять sandbox + exact Unix actor/path permission до start |
| 22 | Wrong write-root owner | research script получил `PermissionError` на root-owned `research_runs` | output/work root создаётся final writer'ом или получает exact owner/group/mode/ACL до workload |

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
EFFECTIVE_ACTOR_PERMISSION_MATRIX=PASS
SYSTEMD_WRITE_PERMISSION_MATRIX=PASS
SERVER_SCRIPT_PERMISSION_PREFLIGHT=PASS

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
EFFECTIVE_ACTOR_PERMISSION_PREFLIGHT=PASS
DEPLOY_EXACT_VERIFIED_COMMIT=PASS

INSTALLED_COMMIT=<release_commit>
SOURCE_LIVE=EQUAL

SERVICES=<explicit expected state>
DB_CONTRACT=PASS
RUNTIME_SMOKE=PASS
LOADED_COMMIT=<exact runtime build/source commit>
OPERATIONAL_DELTA_COMMITS=<NONE|explicit verified set>
GATE=<explicit expected state>

WORKTREE=CLEAN
CHECKPOINT=STABLE
STOP=YES
```

INSTALLED_COMMIT и LOADED_COMMIT не копируются из MANIFEST автоматически.
Они подтверждаются отдельной post-deploy/runtime проверкой.

## 43. Owner-visible incremental development checkpoints

OWNER DECISION 2026-10-04: длительная development/release работа не выполняется
как один непрерывный multi-hour проход без промежуточного owner-visible
checkpoint.

Обязательная единица работы:

```text
ONE STAGE
-> exact goal
-> bounded mutation scope
-> exact verification
-> owner-visible checkpoint
-> only then NEXT STAGE
```

Правила:

1. Один stage должен иметь одну понятную цель. Нельзя без необходимости
   объединять в один непрерывный проход UI change, schema investigation,
   migration authoring, service redesign, deployment и LIVE readiness.

2. После завершения stage обязательно сообщить владельцу:
   ```text
   STAGE = PASS | BLOCKED | FAILED
   CHANGED
   CHECKED HERE
   GITHUB / DEPLOY / RUNTIME status
   NEXT STAGE
   ```
   Переход к следующему независимому stage не должен скрывать уже достигнутый
   stable checkpoint.

3. Потеря tool connection, timeout или delivery failure не означает, что stage
   надо повторить. Сначала выполнить read-only recovery:
   ```text
   inspect authoritative state
   -> classify what completed
   -> resume from last proved checkpoint
   ```
   Mutation не повторяется вслепую.

4. Git publication является отдельным checkpoint. После publication exact
   `REMOTE_HEAD` проверяется до следующего operational stage.

5. **Source sync + exact verified deploy являются одной operational stage** для
   обычного Git-first release:
   ```text
   verify REMOTE_HEAD
   -> sync operational mirror to exact commit
   -> verify SOURCE_HEAD
   -> deploy exact verified release
   -> verify INSTALLED_COMMIT / LOADED_COMMIT
   -> checkpoint
   ```
   Их нельзя искусственно растягивать на два owner-facing этапа, если между
   ними нет реального blocker / owner decision.

6. PostgreSQL migration выполняется как отдельная mutation только когда текущий
   release действительно содержит schema/data migration. Уже доказанная
   migration не является поводом заново исследовать DB во время несвязанного
   UI change. Release installer всё равно обязан применить required idempotent
   migration согласно exact release contract.

7. Локальное UI изменение не расширяет scope автоматически. Если задача —
   добавить/изменить кнопку, таблицу или отображение существующего control
   contract, обязательны targeted UI/control tests и release verification.
   Повторный research, redesign DB, изменение Strategy semantics или
   архитектурная переработка разрешены только при найденном concrete blocker,
   который кратко фиксируется владельцу до расширения scope.

8. Если в ходе stage найден новый blocker, который требует другого
   архитектурного/торгового решения, текущий stage получает `BLOCKED`;
   нельзя молча уходить в многочасовую соседнюю разработку.

9. После stable checkpoint применять §40: остановиться и сообщить результат.
   Следующий stage начинается как новая bounded unit of work.

Для текущего owner workflow нормальная крупная последовательность:

```text
CANON / DEVELOPMENT RULE
-> IMPLEMENTATION + TARGETED TEST
-> GITHUB PUBLICATION
-> SYNC + DEPLOY [единый stage]
-> REQUIRED MIGRATION [только если release её содержит]
-> RUNTIME/UI VERIFICATION
-> отдельный OWNER ARM, если он вообще требуется
```

Это process rule не изменяет trading policy и не ослабляет fail-closed
release/LIVE gates.



## 44. Installer и торговая независимость — OWNER DECISION 2026-10-08

Installer имеет только release/backup/schema/service ответственность; наличие любых
Exchange positions/orders, StrategyPosition и queued/running trading commands
не запрещает deploy и не разрешает installer исполнять, исправлять или удалять
торговые команды. Gate, execution permissions, live-arm sessions installer
не открывает, не закрывает и не модифицирует.

После переключения и запуска штатный private-runtime reconciliation получает
актуальные позиции, ордера и protection **от Bybit к БД**. Никакого replay
локального снимка на Bybit. Installer только наблюдает успешную свежую сверку
и сообщает об отсутствии evidence как operational finding без вмешательства
в торговые объекты. Сохранение transactional delivery/идемпотентности
незавершённых команд — обязанность Execution/Lifecycle.

Любой release по-прежнему требует exact Git commit, backup/rollback,
совместимых DB migrations, import/runtime/schema tests и сохранения owner-arm
state; сервисы нельзя считать защищёнными только потому, что они active.

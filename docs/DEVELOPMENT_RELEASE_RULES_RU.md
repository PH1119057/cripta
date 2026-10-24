# CRIPTA — development / release / PostgreSQL rules

**Версия:** 2.2 · 2026-10-24
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


## 19.5 SentinelX / effective actor: обязательный маршрут доступа

Этот раздел определяет **процедуру выбора способа доступа**, а не раздаёт
дополнительные полномочия. Актуальные Unix users, filesystem roots и snapshot
фактически подтверждённых операций — в `CURRENT_PROJECT_MAP §1.4`.
При расхождении с сервером сначала фиксировать `CHECKED HERE / FINDING`.

Три независимых уровня разрешений:

1. **Tool policy:** SentinelX host online + operation/capability + allowlisted
   prefix/path. `command_not_allowed` означает отказ policy/parser агента,
   а не Unix `EACCES`.
2. **Unix / DB effective actor:** фактические UID/GID/groups, traverse/read/write
   права на exact path, systemd sandbox и, отдельно, PostgreSQL role/grants.
3. **Project authorization:** read-only forensic, source mutation, DB migration,
   deploy и LIVE-arm имеют разные owner approvals и канонические gates.
   Успех уровней 1–2 не даёт права на уровень 3.

Матрица маршрутизации:

| Задача | Инструмент | Фактический actor | Ограничение |
| --- | --- | --- | --- |
| GitHub `main` и документация | GitHub connector, `ref=main` | GitHub identity | Только remote — authority; SHA подтверждать отдельно |
| Состояние сервера, unit, PID | SentinelX `capabilities`, `service_status`, `exec: systemctl show` | `sentinelx` (management reader) | `active` != runtime behavior verified |
| Git forensic на source mirror | `sudo -u cripta env GIT_OPTIONAL_LOCKS=0 git -C /srv/cripta/source_checkout ...` при allowlist | `cripta` | Не выполнять root Git или команды, меняющие index |
| Protected runtime state | SentinelX `exec` с allowlisted `sudo -u cripta python3 -c '...'` | `cripta` | Только выборочные read-only операции и безопасный вывод |
| Research raw archives / dataset provenance (read-only) | SentinelX `exec` только через уже разрешённый `sudo -u cripta python3 -c '...'` с отдельным коротким smoke | `cripta` | Точный dataset path, `os.geteuid()` и `os.stat`/readability проверяются в ОДНОМ effective-actor context до SHA256/QC; обычный `stat` через `sentinelx` не проверяет доступ `cripta`. Не читать секреты, не расширять права, не обходить tool-security block |
| PostgreSQL read forensic | `sudo -u cripta python3 -c 'import psycopg; ...'` | Unix `cripta`, DB role проверяется `current_user` | Только SELECT/metadata; подключение к `dbname=cripta user=cripta` доказать smoke |
| PostgreSQL DDL/backfill | Approved migration rail | Exact authorized migration actor/role | Права и backup проверить до DDL; runtime `cripta` не подменяет мигратора |
| systemd/unit/path mutation | Approved server-management rail | `root` для exact OS steps | Actor/path matrix и explicit deploy approval |
| Runtime/research file writes | Approved service/installer/job | Exact service `User`, обычно `cripta` | §5.1 + §19.1–19.4; не наследовать права SentinelX |
| Торговая мутация | Только canonical Execution path | Real execution service identity | LIVE gates и explicit owner approval; диагностика прав их не заменяет |

Для `sentinel_exec` отправлять **одну разрешённую команду**: chained shell
`;`, `&&`, pipes, shell quoting и вложенный `sudo` могут быть отклонены
ещё policy/parser layer. Нельзя интерпретировать такой отказ как неисправность
Unix permissions. `script_run` также проходит отдельный host/product security
gate; его отказ не разрешает обходить policy либо менять `config.yaml`.

Рабочий read-only пример, проверенный 2026-10-15 на host `robot`:

```bash
sudo -u cripta python3 -c 'import os; print(os.geteuid()); print(os.readlink("/srv/cripta/runtime/current"))'
sudo -u cripta python3 -c 'import psycopg; c=psycopg.connect("dbname=cripta user=cripta"); print(c.execute("select current_user,current_database()").fetchone())'
```

Это **два независимых вызова**, не один multiline/chained `exec`.
Python/psycopg доступность, DB schema и права обязаны подтверждаться
в фактическом actor/interpreter контексте. В диагностическом выводе запрещены
secrets, env credentials, private tokens и персональные данные. Не открывать
целиком `/etc/cripta/*.env` ради проверки identity; читать только явно
утверждённые несекретные поля или использовать status metadata.

Для read-only dataset provenance **сначала выбрать actor из матрицы**, а не выполнять удобный host-level `stat` под actor управления. `whoami` в отдельном host-management вызове доказывает только actor того вызова; он не является проверкой доступности raw archive для `cripta`. Если конкретный `sudo -u cripta ...` вызов заблокирован OpenAI upstream, классифицировать его как `BLOCKED_UPSTREAM_OPENAI / RESEARCH_ACTOR_PATH_NOT_CHECKED`: исполнение на host не доказано. Автоматически не повторять вызов и не менять actor, payload или tool для обхода защиты. Новая попытка по отдельной явной команде владельца рассматривается по §19.6.1 при условии, что текущий tool-security gate допускает её; запрещённый платформой вызов не выполняется.

`Permission denied` классифицировать только после определения exact actor
и target path. Если `sentinelx` не может читать `/var/lib/cripta`,
сначала выбрать already-approved `cripta` read path, а не предлагать
`chmod/chown`, расширять sudo или изменять SentinelX policy.
Если ни один approved путь не существует: `BLOCKED` с exact отказом,
без самостоятельного расширения доступа.

Для любой mutation отдельно требуются `SCRIPT/SERVICE, EFFECTIVE_ACTOR,
READ_PATHS, WRITE_PATHS, CREATE_CHILD_PARENTS, RENAME_DELETE_PARENTS,
SYSTEMD_SANDBOX, DB_ROLE, EXPECTED_OWNER/GROUP/MODE/ACL, PREFLIGHT_COMMAND`;
затем §19.1–19.4 и server snapshot MAP §1.4. `ReadWritePaths=`
не доказывает Unix write permission. Успешный read-only smoke
не считается разрешением на mutation.

## 19.6 Ошибки tool access — обязательная классификация, не server permission

Для каждого отказа фиксировать: точную операцию, actor, exit/returncode, исходное сообщение, достигла ли команда host, и `CHECKED HERE` либо `NOT CHECKED HERE`. Не говорить «SentinelX не дал доступ», если фактически ошибку вернул защитный уровень OpenAI.

| Наблюдаемый отказ | Доказанный уровень | Допустимое действие |
|---|---|---|
| `Этот вызов инструмента был заблокирован системами безопасности OpenAI` | OpenAI tool-security gate; host execution НЕ доказано | Записать факт; без обхода защит, без предположений о конкретном сработавшем правиле |
| `command_not_allowed` | SentinelX command allowlist / parser | Проверить capabilities/§19.5 и точную форму уже разрешённой команды; не расширять policy самостоятельно |
| `Permission denied` / `EACCES` из реального host stdout/stderr | Unix filesystem gate для конкретного effective actor/path | Read-only actor/path preflight; до mutation §5.1, §19.1–19.4 |
| `agent did not respond` / timeout / job_id | Неопределённый транспорт/исполнение; не доказан ни отказ Unix, ни падение host | Проверить job notifications, host/PID/result manifest; не дублировать тяжёлый job |
| HTTP `429` (подтверждённый rate limit, не quota/billing exhaustion), `500` / `502` / `503` / `504`, network reset, временная ошибка соединения GitHub/OpenAI API/другого API | Потенциально временный отказ; неизвестно, исполнен ли запрос | Сначала проверить фактический результат и `error.code`/`Retry-After`, затем bounded retry ниже, только если безопасно |
| `returncode=0` с корректным output | Конкретная команда завершилась | Не распространять этот PASS на другие paths/actors/mutations |

### 19.6.1 Ограниченные повторы временных отказов — OWNER DECISION 2026-10-23

**Различать временный сбой доставки/сервиса и явный запрет операции.**
Ранее успешно выполнявшаяся задача, включая одинаковый скрипт на разных
монетах, не считается неисправной только по одному отказу. Однако успех
для других монет также не доказывает, что текущий отказ — именно сетевой:
нужны точные tool/actor/path, код отказа и сведения о достижении host.

Если отказ классифицирован как **временный transport/API/service failure**
(например подтверждённый временный rate limit `429`, HTTP `5xx`, network reset, временное отсутствие ответа),
для безопасно повторяемой операции сделать **до пяти повторных попыток
после исходной**, с возрастающими интервалами **10, 30, 60, 120 и 300 секунд**.
После каждой попытки проверить полученный ответ, а при неопределённом исходе
— durable result/job ID, манифест или фактическое состояние до новой mutation.
Это локальный backoff при временной ошибке инструмента, **не** разрешение
опрашивать длительные research jobs чаще периода, установленного
`RESEARCH_COMPUTE`, и не обещание background-контроля без рабочей задачи/канала.
Если tool/исполнитель не умеет выдерживать интервалы, нельзя подменять
backoff пятью немедленными вызовами; сохранить checkpoint и организовать
разрешённое отложенное продолжение.

Для **неидемпотентного** запроса (создание файла/заказа, Git commit,
research job, deploy, DB write и др.) timeout или потеря ответа означают
`OUTCOME UNKNOWN`, а не разрешение создать то же повторно. Повторять
проверку состояния можно; повторять саму mutation — только после
независимого подтверждения, что исходная не была выполнена, и после
повторной проверки actor/path/owner approval, необходимых для этой mutation.
Не восстанавливать «неудачную монету» новым run при существующих partial
artifacts, lock/job или завершённом результате.

**Автоматические повторы не распространяются** на:
- OpenAI upstream `BLOCKED_UPSTREAM_OPENAI`: выполнить запись отказа;
  **не применять автоматический backoff**. Отказ отдельного вызова не доказывает
  постоянную недоступность операции. Для нового явного распоряжения владельца
  действует ограниченный ручной retry ниже; не менять tool, actor, права,
  transport или payload ради обхода защиты;
- SentinelX `command_not_allowed`: проверить capabilities и документированную
  форму разрешённого маршрута, но не отправлять тот же отвергнутый вызов
  пять раз и не расширять policy самостоятельно;
- Unix `EACCES`/`EPERM`, отказ авторизации GitHub `401`/`403`, явный
  запрет ресурса: остановить mutation, зафиксировать точные права/условия;
  backoff без изменения причины не заменяет авторизацию.

**Ручной повтор по прямой команде владельца — OWNER DECISION 2026-10-24.**
Если владелец после отказа явно говорит «повтори», «попробуй ещё раз» или
эквивалентно распоряжается повторить **ту же операцию**, это однозначное
разрешение на одну новую попытку, без повторного запроса согласия и без
дискуссии о постоянной недоступности по единственному отказу. Сначала
проверить, не была ли предыдущая неидемпотентная операция уже выполнена,
и сохранить её exact actor/path/ref и результат. Затем **если текущий
инструмент и платформа позволяют отправить эту операцию**, сделать одну
обычную попытку тем же разрешённым маршрутом при всех остальных
действующих gates. Не изменять actor, payload, credential, transport,
полномочия или название операции для обхода tool-security. При повторном
явном security-denial остановиться, сохранить evidence и сообщить
`BLOCKED_UPSTREAM_OPENAI`; не считать это `EACCES` или постоянным запретом
всего GitHub/сервера. Owner approval не отменяет ограничений платформы,
не санкционирует deploy, migration или LIVE-arm.

После пяти исчерпанных **временных** повторов сообщить точный уровень
ошибки, проверенный результат или unknown outcome, уже выполненные
части, источник evidence и допустимое следующее действие.
Не утверждать, что GitHub недоступен, только потому что SentinelX
отклонил отдельную серверную команду: инструменты и уровни отказа
проверяются независимо. Не рекомендовать перезапуск агента без
доказательства именно agent failure.

Проверенная схема read-only: `sentinel_capabilities` → одна allowlisted `sudo -u cripta python3 -c '...'` команда → точный результат. Пример smoke: `sudo -u cripta python3 -c 'import os; print(os.geteuid())'`; ожидаемый uid проверять фактически, не считать постоянным. Длинные исследования — через утверждённый job/compute contract, короткий smoke и версионированный source; без нового обходного transport. Если OpenAI повторно блокирует разрешённую работу, сохранять обезличенные evidence для owner/support escalation; внутреннее правило блокировки неизвестно.

### 19.6.2 ChatGPT, OpenAI API и клиентская сеть — OWNER DECISION 2026-10-23

**Разделять четыре независимые границы доставки и исполнения:**

1. **Браузер владельца ↔ сервис ChatGPT/OpenAI.** Wi-Fi, провайдер,
   VPN, расширения и WebSocket могут нарушить отправку сообщения,
   отображение ответа и уведомления. Потеря клиентского соединения
   не доказывает сбой уже принятой серверной tool operation или
   исследовательского job; его исход устанавливается по result/job evidence,
   а не по состоянию UI.
2. **ChatGPT/OpenAI orchestration ↔ вызов инструмента.** Аргументы
   GitHub/SentinelX-инструмента формируются серверной средой ChatGPT,
   а не отправляются непосредственно из браузера владельца. Ошибка
   проверки tool arguments/schema, ошибка доставки tool result и
   OpenAI tool-security block — разные события. Внутренний точный
   маршрут OpenAI без trace считать NOT CHECKED HERE.
3. **OpenAI API, если он действительно вызывается в данном workflow.**
   Ошибка API/SDK/HTTP не тождественна сбою браузера ChatGPT или
   отказу стороннего инструмента. OpenAPI — название спецификации
   описания интерфейсов, не синоним OpenAI API. Нельзя приписывать
   каждому вызову ChatGPT или GitHub прямое обращение к OpenAI API.
4. **Tool connector ↔ GitHub / SentinelX / host.** Указывать exact
   connector, tool, actor, request/job ID, ответ и доказательство достижения
   target. Отказ SentinelX command_not_allowed не означает сбой GitHub;
   плохой Wi-Fi клиента не является доказанной причиной upstream tool block.

Для сообщения «неправильный запрос» / «ошибка OpenAI» зафиксировать
timestamp, участок, имя tool/endpoint (без секретов), исходное сообщение,
HTTP status, error.type/error.code, request ID, Retry-After (если доступны),
и достиг ли вызов target. Отсутствующие поля = NOT CHECKED HERE; не
приписывать ошибке HTTP-код только по формулировке UI.
Для клиентской сети использовать безопасную диагностику браузера и
официальную status-страницу OpenAI, но не выдавать догадку за evidence.

| Наблюдаемая ошибка | Классификация и действие |
|---|---|
| ChatGPT UI network error, WebSocket disconnect, пропажа ответа | Проверить связь и состояние чата/инструмента; до повторной mutation проверить результат на target |
| Tool schema/argument validation, HTTP 400 BadRequestError | Проверить имя инструмента, поля, типы, схему, размер запроса и допустимые параметры; неизменённый ошибочный вызов по таймеру не повторять |
| OpenAI API 429 c rate_limit_error / slow_down | Использовать Retry-After, снизить частоту; только для безопасных повторов применять §19.6.1 с учётом внутренних повторов SDK |
| 429 с credit_balance_exhausted, organization_spend_limit_exceeded, project_spend_limit_exceeded, organization_usage_limit_exceeded или иным quota/billing limit | Не transient: повтор без исправления лимита не поможет; самостоятельно менять credentials, project или billing запрещено |
| OpenAI API transport exception/timeout, HTTP 500/503 или иной подтверждённый transient 5xx | Проверить, не исполнен ли запрос; затем §19.6.1 и Retry-After, не дублируя job или mutation с неизвестным исходом |
| HTTP 401/403 или явный security denial | Записать точный уровень отказа и остановить запрещённую операцию; без смены actor, ключа, transport или маскировки payload ради обхода |
| BLOCKED_UPSTREAM_OPENAI | Не выполнять автоматический retry. При отдельном явном «повтори» выполнить процедуру ручной попытки §19.6.1, только если текущий tool-security gate её допускает; повторный отказ зафиксировать и остановиться |

Пять попыток из §19.6.1 — верхняя граница при доказанной временной ошибке,
а не требование повторять любой OpenAI-отказ. При Retry-After длиннее
базового интервала использовать не меньшую задержку; не умножать
повторы SDK и внешнего orchestration бесконтрольно. Если удерживать
backoff нельзя, сохранить checkpoint и использовать разрешённую
отложенную проверку. При результате SUCCESS продолжать с последнего
подтверждённого шага, а не перезапускать весь workflow.

Для публичной документации и Issue сохранять лишь безопасные
обезличенные evidence: время, доступный error code, request ID при
необходимости. Не публиковать API keys, cookies, auth headers, secrets,
полные приватные HAR/logs или токены.

Официальная техническая справка (не отдельный канон):
[ChatGPT errors](https://help.openai.com/en/articles/7996703-troubleshooting-chatgpt-error-messages),
[ChatGPT network](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps),
[OpenAI API errors](https://developers.openai.com/api/docs/guides/error-codes).


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

## 26.2 OWNER DECISION 2026-10-19 — trading-scoped R1 SOURCE_LIVE_IDENTITY

Для R1 pre-arm торговый release identity определяется **установленным exact Git
release и bytes исполняемого торгового контура**, а не требованием равенства
постоянно меняющегося GitHub `main` установленному commit.

- `REMOTE_HEAD == SOURCE_HEAD` проверяется отдельно; `INSTALLED_COMMIT == LOADED_COMMIT
  == release state` обязателен и не подменяется документальным коммитом.
- Delta между установленным release и GitHub `main` по `docs/**`,
  `research/**`, `tests/**`, `README.md`,
  `AGENTS.md` не запрещает новые входы **при сохранении прочих LIVE gates**.
  Research не может непосредственно исполняться или загружаться trading runtime.
- Любой delta в `src/**`, trading `operations/**`, release dependencies,
  security, config и **неизвестном пути** — fail-closed до проверенного deploy.
  Это консервативная граница, а не разрешение новых каталогов в обход gate.
- Изменение исполняемого trading code, runtime control, зависимостей и effective
  actor всегда проходит обычный release/CI/rollback. Разрешение документальных
  commits **не** разрешает обойти `PAPER_REAL_DECISION_PARITY`, reconciliation,
  position ownership, risk gates либо owner LIVE-arm.
- Presentation-only Dashboard changes, затрагивающие auth/control/mutation,
  не входят в исключение и требуют full release; любые изменения
  `operations/dashboard/**` остаются blocked до verified release.
  При сомнении классификации — `HARD_STOP=YES`.

Canonical owner классификации пути — этот раздел; Trading Contour §4.7
использует результат gate и не дублирует allowlist.

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

## 45. Routine restart recovery contract — OWNER DECISION 2026-10-08

Installer installs exact verified software, regardless of positions/orders, without Exchange mutations. Restarted Execution/Lifecycle must reconcile current Exchange-to-DB inventory, paging all results, and historical order/execution events, then record a recovery epoch; installer only observes recovery. Tests must cover 60 concurrent positions, opens/closes during downtime, pagination, delayed WS events, duplicate recovery, two independent connectors, ambiguous pending mutations and partial Exchange read. Never infer an empty account from an incomplete snapshot.

## 46. Release-bound REAL Entry admission and operator CRITICAL — OWNER DECISION 2026-10-09

This section supplements §44 and §45. Existing Exchange positions/orders,
StrategyPosition, pending execution commands, Exit and reconciliation **never**
block the installer. Installer remains release/backup/schema/service-only and
does not mutate mainnet gate, execution permissions, LIVE-arm sessions or trading
objects as an implicit side effect.

For an application/trading runtime release transition, the independent owner-
authorized control plane must inhibit **new REAL Entry admission** before
switching LOADED_COMMIT. The inhibition must not interrupt existing Exchange
protection, Exit, position supervision, private-runtime reconciliation, or
resolution of pending/ambiguous commands. It must not manufacture an Exchange
mutation or relabel an open position as a deployment blocker.

OWNER DECISION 2026-10-15 — historical flat-inventory retirement condition:
**SUPERSEDED by OWNER DECISION 2026-10-21**. Requiring zero Exchange
positions/orders, zero pending commands, zero reservations/slot claims or a
current StrategyExecutionBinding before retiring a previous-release LIVE-arm
session incorrectly coupled **new Entry authorization** to independent
existing-position/Exit lifecycle. These conditions are not retirement gates.
The former proven-flat criterion has no current normative effect.

OWNER DECISION 2026-10-21 — superseded Entry authorization on release switch:
Once a verified application LOADED_COMMIT changes, all prior-release ACTIVE
LIVE-arm sessions are invalid for **new REAL Entry** immediately by exact
release identity, irrespective of existing Exchange positions, protection,
pending orders/commands, reservations, ownership, historical bindings or
current account inventory. The independent Lifecycle/control plane (never
the installer) retires these old ACTIVE rows to CLOSED when the global
mainnet new-Entry gate is OFF and the current exact LOADED_COMMIT is proven.
For each retirement it durably preserves the original session identity,
old and current release hashes, timestamp and reason
`RELEASE_SUPERSEDED_ENTRY_AUTHORIZATION_ONLY`. It neither assigns an
unproven TradingAccount to a legacy Strategy-level arm nor claims that
positions/orders were flat. UNKNOWN Exchange exposure does not prevent
retirement of expired **Entry authorization**.

Retirement is strictly a control-plane/archive action: it cannot close,
cancel, change or reassign any Exchange order/position, protective stop,
pending command, capital reservation, physical slot or Exit owner. Execution,
Lifecycle and Universal Exit continue independent fresh authenticated Exchange
reconciliation and exact position/ExitPlan recovery under §44–45 and ARCH §46.
Recovery success or a closed historical arm never auto-enables mainnet,
execution permissions or Strategy activation and never grants a new LIVE arm.
Unproven fresh release readiness blocks **new REAL Entry only**, with an
explicit, scope-specific reason; it does not roll back a successful installer
merely because an Exchange position is open. No new polling service is added:
the existing Lifecycle Supervisor performs retirement in its normal cycle.

Once LOADED_COMMIT changes, release-bound LIVE-arm sessions from a different
application commit are **not transferable**. Reopening new REAL Entry requires
exact-release readiness evidence and a **new explicit OWNER ARM**, never silent
installer re-arm. No prior owner approval or boolean mainnet gate alone
authorizes Entry against a mismatched release.

A persistent control-plane health invariant must independently check:
- global mainnet gate and active Strategy/Execution permission;
- exact installed/loaded application release identity;
- release-bound ACTIVE LIVE-arm sessions for all effective REAL Strategy scopes;
- the actual consumer's REAL Entry admission readiness (including explicit
  `REAL_EXECUTION_SELECTED_BUT_NOT_ARM_READY` rejection).

On mismatch, failure or unavailable prerequisite with new REAL Entry ostensibly
enabled, fail closed for **new** Entry and emit durable operator CRITICAL with
stable identity, actual reason, release/session IDs, affected symbols, detection
time and recovery state. It must not wait for a new trading signal or require
that a normal EntryDecision be recategorized as a lifecycle fault.

The authenticated portal must show a conspicuous **red top-of-screen banner**
while the CRITICAL remains active, irrespective of audio setting, tab, reload,
browser session or acknowledgement. Acknowledgement records operator awareness,
not fault resolution. Banner resolves only after independent fresh recovery
evidence; it must state plainly that existing positions and Exit continue.
Audio is supplemental and subject to browser policy.

Release tests must cover: open position + pending order + deploy, commit
mismatch with gate ON, silent/no-signal periods, stale/not-ready admission,
durable CRITICAL creation/deduplication/recovery, banner after refresh with
sound disabled, failed/incomplete reconciliation and preserved Exit. Never
claim release readiness merely because systemd reports services active.


### 46.1 Automatic post-deploy readiness with manual OWNER ARM — OWNER DECISION 2026-10-20

The owner requires release completion to drive an automatic, bounded
post-deploy verification using existing owners, rather than leaving stale
LIVE-arm incidents for the operator to diagnose. Do not move trading-state
mutation into the canonical installer: it remains release/backup/schema/
service-only as specified above. Orchestrate through the independent
owner-authorized control plane / Lifecycle Supervisor.

Report a durable, operator-visible exact-release state:
DEPLOYED, RECONCILING, BLOCKED(reason, scope, evidence, time), or
READY_FOR_OWNER_ARM. Validate remote/source/installed/loaded identity,
required services, schema and consumers, authenticated complete fresh
Exchange inventory and identity, reconciliation, existing position/Exit
continuity, ambiguous commands and claims, authorized account bindings,
per-scope Strategy settings/permissions, release-bound admission and
exchange/account capabilities before READY. Existing exposure is never a
flat-account prerequisite and must not block the installer; the independent
runtime may report a specific unresolved protection/Exit fault as BLOCKED
for new-entry readiness while continuing Exit. Superseded Entry-only LIVE-arm
sessions retire independently under §46, including with open positions and
without a current legacy StrategyExecutionBinding. Do not clone, migrate or
silently open new-release arm sessions.

READY_FOR_OWNER_ARM is a technical pre-arm status, NOT approval of financial
risk, not LIVE readiness for execution without owner action, and never
automatically sets the mainnet gate or creates/extends OWNER ARM. The operator
must explicitly authorize exact Strategy/account/release scopes; re-evaluate
freshness and safety atomically at that action and reject stale or mismatched
evidence. A partial, UNKNOWN or failed check is BLOCKED with precise cause;
existing position protection, Exit and reconciliation continue unchanged.
Readiness reporting/notifications may not claim delivery unless configured and
verified. Implementation requires tests across stale arm, missing binding,
multiple accounts, one-way/hedge policy, lost connection, exit ownership,
restart, dashboard display and manual authorization boundaries.

## 47. Parallel development and serialized production deploy — OWNER DECISION 2026-10-11

Parallel development, independent feature branches, tests, review and publication
of nonconflicting changes are allowed. GitHub `PH1119057/cripta:main` remains
the sole source authority; a developer must verify the current remote base and
reconcile concurrent upstream changes before publishing. Never force-push over
another contributor's work or silently overwrite an intervening changeset.
Conflicting modifications require explicit integration and applicable tests;
a later commit must include already accepted changes rather than implicitly
reverting them.

**Publishing source is not deploying production.** Documentation-only changes
may be published while another release is being prepared, provided they do not
silently change active canon, mandatory routing, release content or LIVE rights.
Such publication changes `REMOTE_HEAD` / `SOURCE_HEAD` and must not be
misrepresented as a new `INSTALLED_COMMIT` or `LOADED_COMMIT`.

Production deploy is a **single-writer operation** across all contributors,
operator sessions, automations and deploy entrypoints targeting the same
production environment. Before any filesystem, database schema, service or
release-pointer mutation, the installer must acquire one effective exclusive
cross-process deploy lock. The lock covers preparation-dependent state
validation, installation, migrations, service restart, post-deploy verification
and final completion/rollback. A second deploy must fail closed or wait visibly;
it may not interleave with the first. Lock ownership, release SHA and lifecycle
must be auditable. Stale/abandoned lock handling requires verified owner/process
and state evidence, not blind removal. Lock correctness must be tested across
two independent concurrent deploy invocations and failure/rollback paths.

Each installation pins an exact GitHub-verified release composition. Immediately
before acquiring mutation rights it rechecks `REMOTE_HEAD`, `SOURCE_HEAD`,
expected baseline, `INSTALLED_COMMIT`, active operational deltas and any
in-progress release; if assumptions changed, stop and require a fresh
integration/release plan. Changes published after a release SHA was selected
do not silently enter that release. Concurrent documentation updates do not
justify bypassing exact application release composition checks.

A deployment lock is **not** a trading lock and does not itself alter the
mainnet gate, LIVE-arm, existing positions, protective orders or Exit owner.
The §44–§46 separation and explicit owner re-arm rules remain mandatory.
Until the effective lock rail and its concurrency tests are verified, report
`DEPLOY_SERIALIZATION=NOT_VERIFIED` and do not claim concurrent deployment
safety merely from this documentation decision.

## 48. Identity/key/storage migration gate — OWNER DECISION 2026-10-16

Any migration from the current single-account identities to ARCH §49 is treated
as a major PostgreSQL/runtime contract change. Destructive cleanup is the final
stage, never discovery.

Required order:

~~~text
R0  READ-ONLY DB/source dependency map
R1  additive identity schema/columns/registries
R2  register current proven Venue/MarketSource/TradingAccount identities
R3  proven-only backfill
R4  preserve ambiguous legacy identity as UNKNOWN/LEGACY
R5  account-scope private state / position / order / execution keys
R6  introduce StrategyExecutionBinding / ExecutionLeg
R7  market-source/instrument identity cutover
R8  compact analytical representation + consumer migration
R9  dual-read/parity/restart/recovery verification
R10 new constraints + prove legacy writer/reader path unused
R11 retention/deletion only after equivalence and backup gates
R12 physical PostgreSQL space reclaim by an approved method
R13 at least 7 days disk/runtime/research observation
~~~

### 48.1 Additive before destructive

The first production migration must preserve the old readable path while new
identities are introduced. Existing columns/keys/tables are not renamed,
dropped or repurposed as the first step.

Backfill may assign a concrete trading_account_id only when exact evidence proves
the account. Historical NULL/ambiguous/BYBIT:UNIFIED-era facts that cannot be
proven to a unique future account remain explicit LEGACY/UNKNOWN; symbol/time
proximity is not sufficient.

Dual compatibility must have one declared authority and deterministic parity.
Uncontrolled permanent dual-write truth is forbidden. If temporary dual write
is required, reconciliation/equality checks and retirement criteria are part of
the migration.

### 48.2 Consumer and key cutover gate

Before enforcing new account-scoped keys, inventory and test all readers/writers
including runtime, Dashboard, reports, archive/export, replay/research,
Lifecycle Supervisor and release/pre-arm checks.

Order/exec identifiers are scoped according to exact adapter/provider semantics.
Do not globally widen/narrow uniqueness by guess.

Before old identity retirement:

~~~text
NEW_WRITER_PATH                 = PASS
NEW_READER_PATH                 = PASS
ACCOUNT_SCOPE_PARITY            = PASS
MARKET_SOURCE_PARITY            = PASS
RESTART_RECOVERY                = PASS
REPORT_REPLAY_RESEARCH          = PASS
LEGACY_REQUIRED_READERS         = 0
BACKUP_ROLLBACK                 = PASS
~~~

### 48.3 Destructive retention/reclaim gate

Retention/purge follows ARCH §48 only after the identity migration and compact
analytics consumers are proven. Deleting rows is not proof of filesystem space
reclaim. PostgreSQL reclaim method must be chosen from current table/index/free
space/lock evidence and must not blindly use a rewrite operation when disk
headroom is insufficient.

Any irreversible deletion requires a verified restore path to the retained
backup/cold evidence appropriate to that data class.

All normal actor/path/DB-role, backup, release, service and LIVE-arm rules in
this document still apply. Migration/deploy never re-arms LIVE automatically.

## 49. One-time emergency PostgreSQL System SSD recovery — OWNER DECISION 2026-10-18

ARCH §50 permits a one-time physical cold-copy and clean operational PostgreSQL rebuild before logical R10b/R11/R12 completion. Not permission for row deletion, strategy/source changes, identity/key reinterpretation or LIVE.

Mandatory BEFORE mutations: fresh GitHub main; installed/loaded release and operational deltas; exact OS mount/volume/path inventory; all systemd service/timer/job writers and dependencies; independently verified Bybit open positions and orders with fail-closed Exit owner shutdown; mainnet gate=0 and zero active LIVE-arm; tested logical backup AND separately tested full physical cluster rollback including WAL, roles, configs, tablespaces, owner/mode/ACL; exact writer/path/sandbox permissions under §5.1 and §19.1–19.4; sufficient Data capacity with margin; explicit minimal operational DB table/role/consumer/recovery manifest and rollback trigger. Missing gate is HARD STOP.

Sequence: (1) disarm writers and timers in dependency order; prove no writes/clients; stop production PostgreSQL cleanly. (2) remove only verified stopped validation PGDATA under /data/cripta/pg_restore_validation_20261018, retain checked logical archive. (3) copy full stopped production PGDATA from /var/lib/postgresql/16/main plus external configuration under /etc/postgresql/16/main and all required WAL/tablespaces to Data; preserve metadata, verify bytes/hashes and isolated boot/rollback. (4) only after complete recovery proof, retire original System SSD cluster reversibly and make room. (5) build new minimal operational PG on System SSD; transfer only fully inventoried indispensable state, irreplaceable history/fees/ownership/audit and correct roles, grants, constraints, triggers; test consumer equivalence and restart/recovery. (6) restart passive/read-only CRIPTA services in dependency order, verify liveness AND behavior, then remaining permitted services; observe storage for seven days. LIVE remains OFF pending separate explicit owner arm.

Never move running PGDATA, drop unique history, change Strategy/Entry/Exit/analytics code, use uncontrolled root/recursive permission escalation or silently deploy a different release. Archive and new operational DB are distinct. Source sync is not deploy. R10b/R11/R12 status remains NOT COMPLETE until independent canonical gates PASS.
## 50. Изоляция Git workstreams и границы публикации — OWNER DECISION 2026-10-22

**Authority неизменен:** единственный канонический publisher — GitHub `PH1119057/cripta:main`. Research, docs, code и Dashboard остаются в одном репозитории; это разделение процесса, НЕ перенос capability, source roots или stores. Изолированные ветки/worktrees не дают отдельного authority; публикация в `main` не является deploy, а deploy не является LIVE-arm. Existing worktrees/branches/PRs не удаляются, не переписываются и не сливаются автоматически.

Каждая независимая работа начинается от проверенного актуального remote `main`, имеет ровно один task owner, отдельную тематическую ветку и isolated worktree. Перед публикацией проверить новый remote head, разрешить изменения upstream без force push и без перезаписи чужих изменений, явно перечислить изменённые пути, выполнить относящиеся к задаче тесты и сохранить research evidence. При совпадении изменяемых canonical файлов параллельных задач один editorial integration owner последовательно разрешает PR; изоляция веток не устраняет текстовые конфликты в одном файле автоматически.

### 50.1 Три стандартных типа веток

- `research/<task>`: только `research/**`, `tests/research/**`, `scripts/research/**`; source snapshot / manifests / large run outputs по RESEARCH_COMPUTE §17 — на data-disk, без vendor datasets и outputs в Git. Исключение: точный `operations/dashboard/instrument_research_catalog.json` допускается только с независимой владельческой регистрацией `owner-approved-ledger-registration`, отдельным QC и контрактом RESEARCH_COMPUTE §23. Это не разрешает менять Dashboard code.
- `docs/<task>`: только `docs/**`, root `README.md`, `AGENTS.md` и точный derived `operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt`. Изменения по INDEX §17; authoring/верификация канона не равны patch или runtime release. Исследовательский исполнитель не вправе параллельно переписывать эти файлы своей research веткой.
- `core/<task>`: executable `src/**`, `production/**`, `operations/**` за исключением каталога research JSON и derived Project Instructions, `tests/**` за исключением `tests/research/**`, `scripts/**` за исключением `scripts/research/**`, CI `.github/workflows/**` и `.github/scripts/**`, и явно допускаемые build root configs (`pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `.gitattributes`). Canonical docs и чужие research outputs не менять. Любой опасный trading/control/mutation change сохраняет полные release, rollback и LIVE gates.

Точный scope checker является **implementation**, а не вторым владельцем архитектуры. Неизвестные пути и переименования проверяются по обоим старым/новым путям и блокируются fail-closed. Смена имени ветки не может заменить approved cross-contour review.

### 50.2 Межконтурные changesets

Если один необходимый changeset затрагивает более одного контура (например, CANON UPDATE + CI implementation), используется `cross/<task>` с отдельным явным OWNER DECISION, `owner-approved-cross-contour` review marker и проверкой всех affected routes; это не general escape hatch. При касании research catalogue дополнительно нужен `owner-approved-ledger-registration` и record-level QC. Маркер не заменяет реальное подтверждение владельца, applicable canon update и GitHub review; другие неклассифицированные пути остаются запрещёнными.

### 50.3 Merge gate / legacy / завершение

GitHub scope check выполняется в trusted base context без выполнения кода из PR. Проверяется полный список changed paths, включая удалённые/renamed; partial/truncated GitHub file list = BLOCKED. PR в `main` проходит CI и scope; **обязательность** обеспечивается только после включения required check и защищённого `main` (GitHub branch protection/ruleset). Пока эти настройки не проверены, `MERGE_ENFORCEMENT=NOT VERIFIED`; успешный workflow не доказывает, что GitHub физически запрещает обход. Прямой push в `main` должен быть заблокирован настройками репозитория, а не только документом.

Старые ветки, включая старые незавершённые PR, подчиняются новому gate при новой публикации: либо переводятся в scoped workflow через согласованную интеграцию, либо остаются на рассмотрении; **никакой автоматической очистки/force update**. После merge сохраняются Git SHA, результат QC, branch/run provenance и необходимые snapshots; удалять только по отдельному проверенному cleanup plan. Release identity и LIVE controls — DEVELOPMENT_RELEASE §26 и §35 без изменений.

### 50.4 Новый чат: task continuity и запрет дублирующих веток — OWNER DECISION 2026-10-22

При смене ChatGPT-чата незавершённая задача **НЕ становится новой задачей**. Новый исполнитель после mandatory/routed pre-read **до создания ветки, worktree, PR, server writer или повторного запуска job** обязан установить точную identity задачи и найти её предыдущее состояние. Этим пунктом владеет DEVELOPMENT_RELEASE; META/WORK/bootstrap дают только обязательные указатели.

Discovery в порядке:
1. Проверить актуальный GitHub `main`, открытые Issue/PR (включая draft и закрытые без merge), task ID/название/затрагиваемые пути, remote/local ветки и возможный уже опубликованный replacement/merge.
2. Проверить через одобренный read-only rail зарегистрированные серверные `git worktree list --porcelain`, точные branch/HEAD/base, `git status`, staged/modified/untracked, применимый `git diff`, наличие source snapshots и непубликовавшихся коммитов. GitHub **НЕ видит незакоммиченные байты** локального worktree. Mirror `/srv/cripta/source_checkout` не превращается в writer и не используется как рабочий checkout.
3. Проверить durable checkpoint задачи, реальный статус связанных research/replay/deploy jobs и возможного текущего writer/lock. Слова старого чата, память и имя ветки не доказывают, что job завершён либо worktree свободен. Известный `RUNNING` нельзя дублировать запуском.
4. Сопоставить *точное* намерение, scope, changed paths, source SHA, owner, предыдущий результат и следующий шаг. Одинаковая тема без совпадения task identity не даёт права присваивать чужую ветку.

Если найден **один доказанно совпадающий** незавершённый worktree и подтверждены writer/ownership/permission gates, продолжить **в нём же** с сохранением staged/unstaged/untracked файлов и точной source snapshot, не делать reset/rebase/checkout-clean/force push и не терять уже выполненную работу. Если есть только GitHub branch/PR, можно безопасно продолжить от неё в новом разрешённом isolated worktree только после проверки, что локальные dirty/untracked материалы отсутствуют либо отдельно сохранены и учтены. Для старой ветки с неправильным scope нужен отдельный owner-approved migration plan, а не скрытое переименование.

Если найдено несколько кандидатов, неизвестен активный writer/job, невозможно прочитать server state при подозрении на dirty worktree, расходятся ветка и снимок либо есть конкурирующий publisher: `TASK_RESUME=AMBIGUOUS`, `HARD_STOP=YES`, `OWNER_DECISION_REQUIRED=YES` **для создания/изменения веток и повторного запуска работы**. Дать владельцу найденные exact task/branch/worktree/commit/job IDs и безопасные варианты. Не объявлять отсутствие доступа доказательством отсутствия работы.

Только после `TASK_DISCOVERY=PASS` и `EXISTING_TASK_MATCH=NONE` (либо explicit owner decision о новой независимой работе после сохранения старого evidence) разрешено создать новую scoped branch по §50.1. Прежде чем начинать длительную работу или прерывать чат, оставить discoverable durable task checkpoint в связанном GitHub Issue либо существующем PR, с task ID, целью, контуром, branch, точным worktree path, base/HEAD SHA, staged/unstaged/untracked summary, writer/actor, job ID/status, evidence/backup location, проверенными результатами, blockers и **одним следующим действием**. Не класть secrets/raw большие datasets в GitHub. GitHub Issue/PR — указатель продолжения, **не** отдельный source authority и **не** резервная копия dirty files. При завершении закрыть task tracker и снять active status после независимого remote verification; cleanup отдельных worktrees/branches по §50.3.

Новый чат начинает ответ по незавершённой задаче с `RESUME_FOUND / NEW_TASK_CONFIRMED / BLOCKED` и exact evidence, а не с молчаливого `git checkout -b`. Это инструкция исполнителю, **не утверждение**, что CI проверяет все server worktrees или что действует автоматическая межчатовая блокировка: такие дополнительные tools/locks потребуют отдельной implementation/release проверки.

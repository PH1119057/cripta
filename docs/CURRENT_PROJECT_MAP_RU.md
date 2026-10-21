# CRIPTA — текущая карта проекта

**Версия:** 12.25
**Дата:** 2026-10-21
**Статус:** текущая карта реализации; не заменяет архитектурный контракт

# 1. Source of truth

Авторитетный source of truth:

```text
GitHub PH1119057/cripta:main
```

/srv/cripta/source_checkout — синхронизированное operational mirror GitHub
main, а не второй независимый authority.

Installed runtime, PostgreSQL и Exchange truth проверяются отдельно от source.

## 1.1 Автоматическая синхронизация server source mirror

IMPLEMENTED / DEPLOYED / RUNTIME VERIFIED 2026-09-27:

- `cripta-source-sync.timer` enabled/active;
- периодическая проверка GitHub `main` выполняется примерно каждые 5 минут;
- sync работает от repository owner `cripta`;
- разрешён только clean `main` -> exact remote `main` fast-forward;
- dirty worktree, unexpected branch, divergent/non-fast-forward history,
  fetch/remote mismatch блокируют изменение checkout;
- sync не выполняет deploy, не перезапускает trading services и не меняет
  PostgreSQL/Exchange/mainnet gate;
- clean fast-forward, dirty-worktree block и divergent-history block покрыты
  автоматическими тестами;
- первый production run завершён `SOURCE_SYNC=UP_TO_DATE`, а bootstrap
  installation не перезапустила ни одного уже работающего CRIPTA service.

GitHub `main` остаётся единственным authority. Автоматический mirror sync не
означает deploy: `SOURCE_HEAD`, `INSTALLED_COMMIT` и `LOADED_COMMIT`
по-прежнему проверяются раздельно.

## 1.2 Historical operational identity / delta — CHECKED HERE 2026-09-28

Exact pre-publication identity snapshot for this documentation revision:

```text
REMOTE_HEAD      = 52b5fc3e94a0052ab304cac79ed108734bf11c2a
SOURCE_HEAD      = 52b5fc3e94a0052ab304cac79ed108734bf11c2a
INSTALLED_COMMIT = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
LOADED_COMMIT    = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
                  [application runtime release]
```

Active operational deltas outside the last full package release:

```text
OPERATIONAL_DELTA_COMMITS =
  6cf5ae08917e3eb99124053a59766f155773a2ef
  109dd2fd7f33c584d5f2d0ed107d9aede8b1481d
```

- `6cf5ae0...` removed `cripta-private-runtime.service` from Dispatcher
  `Wants=`; live affected path:
  `/etc/systemd/system/cripta-dispatcher-v2.service`;
- `109dd2f...` added effective-actor permission preflight and hardened passive
  MAYAK report units; live affected paths:
  `/usr/local/sbin/cripta-permission-preflight`,
  `/etc/systemd/system/cripta-mayak-v2-report.service`,
  `/etc/systemd/system/cripta-mayak-v2-weekly-report.service`;
- live SHA256 этих четырёх files совпадает с current source bytes;
- Universal Entry observer/consumer и trade-lifecycle current symlinks всё ещё
  указывают на release `5a3ea5aba545d5fb97cef108562eac41d35bc47c`.

Следовательно full `INSTALLED_COMMIT` и application `LOADED_COMMIT` остаются
`5a3ea5a...`; точечные unit/helper changes не маскируются под новый full
release. До сведения operational deltas в один exact verified release
`SOURCE_LIVE_IDENTITY` для real-arm не может быть PASS.

Private-runtime finding:
- до `6cf5ae0...` private runtime был disabled, но Dispatcher запускал его
  через `Wants=`; из-за `Restart=always` и schema mismatch накопилось 68973
  restart attempts;
- fail-closed mismatch:
  `expected=runtime-schema-2026-09-20-slot-v1`,
  `actual=runtime-schema-2026-09-02-v1`;
- после fix private runtime остаётся `inactive/disabled`; schema contract
  намеренно не мигрировался.

Safety evidence at that historical checkpoint:
- Dispatcher, Universal Entry observer, Lifecycle Supervisor,
  Universal Exit shadow и dashboard — active;
- Universal Entry consumer и private runtime — inactive;
- mainnet gate=0;
- real execution permissions=0;
- open/reconciliation positions=0;
- active slot claims=0;
- active capital reservations=0;
- queued/running commands=0;
- pending Exchange orders=0;
- `runtime.position_mode_states=0`;
- `control.live_arm_evidence=0`;
- `control.live_arm_sessions=0`.

## 1.2.1 Current T3 operational identity — CHECKED HERE 2026-09-30

Exact pre-publication identity snapshot:

```text
REMOTE_HEAD      = 72797f38b20f2051a40de4669be80089f7575bdc
SOURCE_HEAD      = 72797f38b20f2051a40de4669be80089f7575bdc
INSTALLED_COMMIT = 72797f38b20f2051a40de4669be80089f7575bdc
LOADED_COMMIT    = 72797f38b20f2051a40de4669be80089f7575bdc
RESEARCH_TOOLING = 72797f38b20f2051a40de4669be80089f7575bdc
```

T3 deployment/liveness evidence:
- canonical installer завершился `DEPLOY_EXACT_VERIFIED_COMMIT=PASS`;
- `/srv/cripta/runtime/current` указывает на exact release `72797f38...`;
- `/data/cripta/research/tooling/current` указывает на tooling release того же commit;
- core runtime и Dispatcher import preflight PASS; locked runtime содержит SQLAlchemy 2.0.52 и greenlet 3.5.5;
- все 17 expected previously-active runtime/research-tooling services после deploy active;
- фактические process cmdline/WorkingDirectory там, где применимо, разрешаются через `/srv/cripta/runtime/current` либо `/data/cripta/research/tooling/current`;
- checked active unit definitions не содержат ссылок на legacy runtime code roots;
- Universal Entry consumer, private runtime, Strategy Dispatcher, Universal Entry shadow и causal context correlator остались inactive/disabled; Entry shadow scanner RETIRED by owner decision 2026-10-01;
- mainnet gate=0; real execution permissions=0; open/reconciliation positions=0; active slot claims/reservations=0; pending commands/orders=0; active LIVE-arm sessions=0.

Backup T3:
`/data/cripta/script_archive/release_backups/20260930_080922_d4800b4ab132e37f896c71801621d245f6af12c5_to_72797f38b20f2051a40de4669be80089f7575bdc`.

Historical-origin helper `/usr/local/sbin/cripta-permission-preflight` остаётся отдельно установленным operational artifact; CHECKED HERE его SHA256
`38cc16c08ba396e485bb72d03ec5b8bd96f743e66ce7fbe8b9bf338e6fd778a1` совпадает с current source bytes.

T3 liveness/loaded-path evidence не является T4 runtime-behavior verification и не даёт MICRO_LIVE/LIVE rights.

## 1.2.2 T4 runtime behavior + legacy-root cleanup — CHECKED HERE 2026-09-30

T4 runtime verification after T3:
- two independent post-deploy snapshots over 20 seconds showed stable PIDs and `NRestarts=0` for the checked active runtime services;
- connectivity message count and status timestamp advanced;
- MAYAK status advanced and `mayak_v2.snapshots` / `mayak_v2.coin_market_contexts` row counts advanced;
- Dispatcher status advanced and `dispatcher_v2.global_market_contexts` / `dispatcher_v2.coin_market_contexts` row counts advanced;
- Lifecycle Supervisor and Universal Exit heartbeat/status advanced with `trading_rights=NONE` / `execution_rights=NONE`;
- Universal Entry observer remained `observer_ready=true`, `state=IDLE`, `trading_effect=NONE`, reason=`no enabled StrategyActivation`;
- Exit Runtime heartbeat advanced;
- Safety observer remained `healthy`, open positions/orders = 0;
- mainnet gate=0 and real execution permissions=0 throughout.

Component provenance clarification:
- Dispatcher runtime code/package remained unchanged from component origin `ff259fdc173841a02cc6bb633af5ed5765614df1`;
- Dispatcher V2 context-correlator code remained unchanged from component origin `6d6bfd035128bac090809127d9c9e56e4cf2ce98`;
- therefore those `source_commit` fields are component-code provenance, not stale `LOADED_COMMIT`.

Pre-existing findings not caused by T3/T4 migration:
- `cripta-health-monitor` remains RED because it still treats intentionally disabled private/trade WS as unhealthy and its `/var/lib/cripta/backup/latest.json` points to an old verified backup; monitoring history shows the same RED state before T3;
- latest Dispatcher trading-capacity snapshot is correctly marked `STALE`; private runtime remains intentionally inactive/disabled, so this is not represented as fresh capacity.

Legacy runtime cleanup preconditions:
- live `/proc` cmdline/cwd/exe/maps scan: zero references to the legacy runtime roots;
- active systemd/cron/local-sbin scan: zero live references to the legacy runtime roots;
- current operational source has no runtime dependency on those roots; installer retains only conditional historical-link backup checks;
- exact archive root preflight PASS (`/data/cripta/script_archive`, root:cripta 750).

Verified archive before removal:
- archive: `/data/cripta/script_archive/runtime_legacy_cleanup_T4_20260930_0844/legacy_runtime_roots.tar`;
- tar SHA256: `aa7a7f8de0fa56645dffd39be56d030bbd92d008155c329ef4cef8b069046ab0`;
- source/restored manifests each contain 61,367 objects and are byte-identical;
- manifest SHA256: `fb952c92860c2098f721a6482bfa8129d3eea81f95809646e17993fd125c5fb1`;
- restore verification used tar ACL/xattr/numeric-owner preservation and completed PASS.

Removed only after the above gates:
`/srv/cripta/production`, `/srv/cripta/monitoring`, `/srv/cripta/connectivity`,
`/srv/cripta/dashboard`, `/srv/cripta/trade_lifecycle`,
`/srv/cripta/universal_entry_observer`, `/srv/cripta/universal_entry_consumer`,
`/srv/cripta/control`, `/srv/cripta/jobs`.

Post-removal verification:
- all 17 expected active runtime/research-tooling services remained active with unchanged PIDs and `NRestarts=0`;
- runtime heartbeats/status/DB rows continued to advance;
- mainnet gate=0, real execution permissions=0, hot positions=0, pending hot orders=0;
- R4 research compatibility bridges were not modified by T4.

T4 completion does not imply MICRO_LIVE/LIVE readiness or re-arm.

## 1.2.3 Health/backup operational repair — CHECKED HERE 2026-09-30

Post-T4 forensic выявил пропущенный operational debt:
- `cripta-health-monitor` считал намеренно disabled private/trade WS аварией;
- штатный `cripta-backup.timer` существовал, но был disabled, поэтому
  `/var/lib/cripta/backup/latest.json` оставался на verified backup 2026-09-03;
- `cripta-backup.service` исполнял `/srv/cripta/backup/backup.sh`, то есть
  один executable legacy root остался вне T1/T4 inventory.

Git-first repair:
- implementation commit: `23988f8f8f3bb69bd707359e41a0223cb3d02871`;
- runtime installer теперь упаковывает `research/server/backup` и устанавливает
  `cripta-backup.service` + `cripta-backup.timer` из exact runtime release;
- backup ExecStart:
  `/usr/bin/bash /srv/cripta/runtime/current/research/server/backup/backup.sh`;
- health unit явно задаёт `CRIPTA_EXPECT_PRIVATE_WS=0` и
  `CRIPTA_EXPECT_TRADE_WS=0` для current intentionally-disabled private runtime;
- strict backup freshness check сохранён;
- targeted tests 4/4 PASS, governance 21/21 PASS, full pytest
  1463 passed / 64 skipped;
- legacy health-monitor Ruff debt не расширен:
  baseline diagnostics=20, repaired diagnostics=16, `NEW_DIAGNOSTICS=0`.

Deployment/runtime evidence:
```text
REMOTE_HEAD      = 23988f8f8f3bb69bd707359e41a0223cb3d02871
SOURCE_HEAD      = 23988f8f8f3bb69bd707359e41a0223cb3d02871
INSTALLED_COMMIT = 23988f8f8f3bb69bd707359e41a0223cb3d02871
LOADED_COMMIT    = 23988f8f8f3bb69bd707359e41a0223cb3d02871
```

- canonical installer: `DEPLOY_EXACT_VERIFIED_COMMIT=PASS`;
- mainnet gate=0; real execution permissions=0;
- open/reconciliation positions, hot positions, pending commands/orders = 0;
- `cripta-backup.timer` enabled/active;
- `Persistent=true` immediately triggered the missed daily backup;
- verified backup: `/data/cripta/backups/system/20260930T094231Z`;
- backup service `Result=success / ExecMainStatus=0`;
- backup payload contains verified `cripta.pgdump`, `project.tar.gz`,
  `configuration.tar.gz` and `SHA256SUMS`;
- health monitor after backup: `state=green`, `issues=[]`;
- next timer trigger observed:
  2026-10-01 03:26:57 UTC (randomized daily 03:20 UTC schedule);
- old `/srv/cripta/backup` passed process/system/source zero-reference gate,
  was archive/restore verified and removed;
- legacy backup archive:
  `/data/cripta/script_archive/legacy_srv_backup_20260930_1000/legacy_srv_backup.tar`;
- archive SHA256:
  `f4ef2ac80434ce5e9ec7dfdb047a4244a927d4784e22e57abe56bf6d7315e220`.

Этот repair не меняет Strategy/trading policy и не даёт MICRO_LIVE/LIVE rights.

## 1.3 Filesystem permission hardening — CHECKED HERE 2026-09-28

FINDING был подтверждён реальными runtime failures, а не только static audit:

- research worker получил `PermissionError` на
  `/data/cripta/research_runs` 2026-09-26;
- MAYAK daily report получил `PermissionError` на
  `/srv/cripta-share/reports` 2026-09-26 и 2026-09-27;
- у report unit уже был `ReadWritePaths=/srv/cripta-share/reports`, но Unix
  owner/group/mode всё равно запрещали write actor'у `cripta`.

DEPLOYED / VERIFIED:
- `/data/cripta/research_runs = cripta:cripta 2770`; existing SentinelX ACL
  `user:sentinelx:rwx` сохранён;
- `/srv/cripta-share/reports = cripta:cripta-share 2750`;
- active research job services имели latent create-path defect:
  `/data/cripta/jobs` и `/data/cripta/jobs/intake` были `root:root 755`,
  хотя service user `cripta` по source contract может создавать в них state
  directories; оба exact parents приведены к `cripta:cripta 750`;
- read-only `cripta-permission-preflight` проверяет exact actor/path до
  mutation;
- create/delete smoke от Unix actor `cripta` PASS на research/report roots
  и на обоих job-state parents;
- daily и weekly MAYAK report service прошли real service-context preflight и
  завершились `Result=success / ExecMainStatus=0`;
- static audit всех current non-disabled CRIPTA services показал: каждый
  существующий `ReadWritePaths` доступен service `User` на write+traverse;
  disabled legacy units с отсутствующими state paths в этот PASS не включались.

Эта правка не меняет trading policy и не выдаёт real-execution rights.

## 1.4 Current server execution profile — CHECKED HERE 2026-09-28

Этот раздел — operational snapshot текущего production host, а не торговая
архитектура. Перед mutation фактическое состояние всё равно перепроверяется по
DEVELOPMENT_RELEASE §19.1–19.4.

### Actors

```text
root
  -> только OS-level mutation: systemd/unit install, daemon-reload,
     exact chown/chmod, privileged backup/restore where required
  -> НЕ normal runtime/research actor
  -> НЕ repository mutation actor

cripta
  -> основной CRIPTA runtime/service actor
  -> repository/source mirror owner
  -> runtime/research/job filesystem writer на явно разрешённых paths
  -> member of cripta-share

postgres
  -> отдельный PostgreSQL system/migration actor where required
  -> НЕ заменяется cripta/root по удобству

sentinelx
  -> management/tool rail
  -> его собственные filesystem capabilities НЕ доказывают права cripta,
     postgres или systemd service User
```

Credential/key/SSH details в этот snapshot не входят.

### Source checkout

CHECKED HERE:

```text
/srv/cripta/source_checkout       owner=cripta:cripta
/srv/cripta/source_checkout/.git  owner=cripta:cripta
.git/index                        owner=cripta:cripta
core.autocrlf=false
core.eol=lf
cripta-source-sync.timer          enabled/active
```

Правила:
- нормальная доставка опубликованного GitHub `main` на server checkout идёт
  через штатный source-sync, а не через ручной root checkout/reset;
- read-only Git forensic выполняется repository owner'ом с
  `GIT_OPTIONAL_LOCKS=0`;
- обычный root `git status`/checkout/add/commit в server checkout запрещён;
- source mirror не является live runtime и его обновление не является deploy;
- tool-generated `.bak.*`, temp/overlay files не допускаются в staged
  changeset и удаляются из isolated worktree до final status/staging.

### Current writable roots relevant to CRIPTA jobs/reports

```text
/var/lib/cripta                  = cripta:cripta 750
/data/cripta/research            = cripta:cripta 2770
/data/cripta/research/worktrees  = cripta:cripta 2770
/data/cripta/research/runs       = cripta:cripta 2770
/data/cripta/research/cache      = cripta:cripta 2770
/data/cripta/research/manifests  = cripta:cripta 2770
/data/cripta/research/tmp        = cripta:cripta 2770
/data/cripta/jobs                = cripta:cripta 750
/data/cripta/jobs/intake         = cripta:cripta 750
/srv/cripta-share/reports        = cripta:cripta-share 2750
/srv/cripta-share/reports/jobs   = cripta:cripta-share 2750
/srv/cripta-share/incoming/jobs  = cripta-sftp:cripta-share 2770
```

Эти modes/owners являются current operational facts, а не вечными constants.
Если service contract требует новый write path, сначала меняется/проверяется
его explicit ownership/ACL/sandbox contract; новый script не должен
самостоятельно «чинить весь сервер».

### Server-side запреты

На текущем host нельзя считать безопасным следующее:

- `ReadWritePaths=` без проверки Unix owner/group/mode/ACL;
- root-created work/output directory, который позже должен писать `cripta`
  или `postgres`;
- `chmod 777` или recursive `chown/chmod` как generic permission repair;
- запуск workload от root только потому, что intended actor получил DENIED;
- создание state/output child directory без проверки write+execute на parent;
- вывод `systemctl is-active` как доказательство runtime behavior;
- ручная mutation source checkout для «догоняния» уже опубликованного GitHub;
- смешивание `SOURCE_HEAD`, `INSTALLED_COMMIT`, `LOADED_COMMIT` и
  operational delta в одно слово «версия».



### Read-only access route — CHECKED HERE 2026-10-15

На host `robot` проверено через SentinelX:

- `sentinelx` — service principal управления (в проверенном контексте
  `uid=994`); его невозможность прочитать protected runtime path сама
  по себе не означает отказ Unix actor `cripta`.
- `sudo -u cripta python3 -c '...'` через отдельный `sentinel_exec`
  успешно работает от `cripta` (`uid=107`), если точная команда
  соответствует allowlist и tool security policy.
- `/srv/cripta/runtime/current` читается этим actor; snapshot symlink
  указывает на release `dbecd991566484fdfb0724f308fbd023ca6d455a`.
- `/var/lib/cripta/universal_entry_observer/status.json` читается от
  `cripta`; это liveness/status snapshot, не durable fault table.
- PostgreSQL socket smoke под Unix `cripta` с
  `dbname=cripta user=cripta` подтвердил `current_user=cripta`,
  `current_database=cripta`; `runtime.lifecycle_faults` доступна для
  SELECT (фактически прочитана).
- `/var/lib/cripta/release/INSTALLED_COMMIT` прочитан от `cripta`:
  `dbecd991566484fdfb0724f308fbd023ca6d455a`.
- `SOURCE_HEAD` на дату проверки =
  `29edfcaa693bd35c8f97e8ed81cadb325bead937`;
  этот SHA совпал с найденным GitHub `main`.
- SentinelX `exec` с chained shell `;` и неразрешённым
  `sudo -u cripta ls` возвращал `command_not_allowed`, а попытки
  `script_run` отклонялись отдельной защитой инструмента.
  Эти отказы не являются доказательством Unix permission defect.
- `journalctl` от `sentinelx` не получил доступ к system journal;
  journal-read как `cripta` / privileged reader **не проверен здесь**.

Операционный маршрут, с которого начинать следующие диагностики:

1. Прочитать `DEVELOPMENT_RELEASE §19.5` и current MAP §1.4.
2. Проверить `sentinel_capabilities`, actor и exact tool operation.
3. Для protected CRIPTA read-only facts использовать already approved
   `sudo -u cripta python3 -c '...'` — **одна команда на один вызов**.
4. Для PostgreSQL проверить `current_user` и SELECT scope; ничего не
   менять в БД без отдельной матрицы и approval.
5. Для write/deploy идти по §5.1 + §19.1–19.4, не переносить доказанные
   read permissions на write permissions.

Это датированное `CHECKED HERE`, а не вечная конфигурация.
Никаких новых sudo/Unix/DB прав данным documentation update не выдаётся.

## 1.5 Filesystem contour separation — OWNER DECISION / CHECKED HERE 2026-09-29

Target contract:

```text
SOURCE_ROOT        = /srv/cripta/source_checkout
RUNTIME_CODE_ROOT  = /srv/cripta/runtime
RESEARCH_ROOT      = /data/cripta/research
ARCHIVE_ROOT       = /data/cripta/script_archive
```

Final filesystem state, CHECKED HERE 2026-09-30:

```text
SOURCE_ROOT        = /srv/cripta/source_checkout   PRESENT / current
RUNTIME_CODE_ROOT  = /srv/cripta/runtime           PRESENT / current
RESEARCH_ROOT      = /data/cripta/research         PRESENT / current
ARCHIVE_ROOT       = /data/cripta/script_archive   PRESENT / current

/srv/cripta top-level project roots:
  source_checkout
  runtime

/data/cripta current operational/data roots:
  backups
  datasets
  jobs
  reports
  research
  script_archive
  lost+found [filesystem-owned, not CRIPTA contour]
```

Current shared report storage:
- `/srv/cripta-share/reports` and `/data/cripta/reports` resolve to the same
  backing directory on this host at this checkpoint;
- this is current operational report storage, not a legacy research root.

Final cleanup removed all transition-only roots, including:
`/srv/cripta/reports`, `/srv/cripta/patches`, `/srv/cripta/current`,
`/srv/cripta/releases`, `/srv/cripta/tests`, `/srv/cripta/u5_oi30s_source`,
`/srv/cripta/universal_entry_shadow`, `/srv/cripta/docs`,
`/srv/cripta/backups`, `/srv/cripta/operations`, `/srv/cripta/config`,
`/srv/cripta/dataset`, `/data/cripta/research_cache`,
`/data/cripta/research_stage`, `/data/cripta/system_offload_20260911`,
`/data/cripta/archive`, `/data/cripta/legacy`, `/data/cripta/lifecycle_v1`,
`/data/cripta/cache`, `/data/cripta/staging` and `/data/cripta/workspace`.

Migrated historical research evidence remains discoverable under canonical
Research rather than old absolute paths:
- `/data/cripta/research/runs/_legacy_srv_reports_20260911`;
- `/data/cripta/research/runs/_legacy_data_research_stage_20260909`;
- `/data/cripta/research/runs/_legacy_system_offload_research_runs_20260911`;
- `/data/cripta/research/cache/_legacy_data_research_cache_20260930`;
- earlier R1-R4 migrated legacy bundles already present under
  `/data/cripta/research/runs`, `/data/cripta/research/cache` and
  `/data/cripta/research/worktrees`.

Cold historical/offload material is consolidated under
`/data/cripta/script_archive`; notable final-cleanup bundles include:
- `legacy_patches_20260911`;
- `legacy_data_archive_20260930`;
- `legacy_data_legacy_20260930`;
- `legacy_data_lifecycle_v1_20260930`;
- `legacy_srv_residuals_20260930`;
- `final_legacy_srv_roots_20260930`;
- `legacy_srv_root_markers_20260930`.

Research internal cleanup / owner decision 2026-10-01:
- `cripta-entry-shadow-scanner.service` retired and removed from current release/systemd contract;
- completed NEW15/EO4 continuation launchers that depended on legacy
  `minute_entry_book_v1` / `universal_entry_v1` worktrees were removed from
  current source; Git history and research outputs remain evidence;
- after verified deploy the two legacy worktrees are re-homed to
  `ARCHIVE_ROOT`, not kept as current Research worktrees;
- current registered Research Git worktrees are limited to active feature work
  plus explicitly current research/data-maintenance bundles.

System backup retention / owner decision 2026-10-01:
- `/data/cripta/backups/system` keeps exactly the 2 latest verified generations;
- latest verified generation at this checkpoint:
  `/data/cripta/backups/system/20261001T032409Z`;
- deploy rollback backups are governed separately by
  `DEVELOPMENT_RELEASE_RULES §14.1`;
- heavy datasets and Research runs are not included in system backup and are
  not covered by this two-generation retention rule.

Bybit KZ quarantine data cleanup / owner decision 2026-10-01:
- current quarantined symbols: `1000PEPEUSDT`, `DOGEUSDT`, `NEARUSDT`,
  `XLMUSDT`;
- these symbols are excluded from Research acquisition/latency/opportunity
  defaults and from frozen dataset expansion;
- their symbol-specific payload under `/data/cripta` is retired/deleted;
- PostgreSQL historical trading/audit records are preserved;
- re-adding any quarantined symbol requires a new owner decision.

Reusable `L5-3 CLEAN` research baseline — OWNER DECISION / CHECKED HERE
2026-10-03:

```text
DATASET_ID = L5-3 CLEAN v1
ARTIFACT   = /data/cripta/research/runs/l53_clean_v1_20261003
FAST_QUERY = /data/cripta/research/runs/l53_clean_v1_20261003/results/l53_clean.sqlite
RAW_ROOT   = /data/cripta/datasets/raw/20260518_20260816
RANGE      = 2026-05-18 .. 2026-08-15
SYMBOLS    = 15
STATES     = 385813
TOUCHES    = 116867
```

Symbols:
`LINKUSDT, UNIUSDT, HBARUSDT, LTCUSDT, XRPUSDT, AVAXUSDT, DOTUSDT,
AAVEUSDT, SUIUSDT, ARBUSDT, BCHUSDT, ADAUSDT, APTUSDT, OPUSDT, INJUSDT`.

Dataset contract:
- geometry is exact canonical L5-3: 36 fully closed 5m bars,
  `range_low=min(low36)`, `range_high=max(high36)`, Wilder ATR200,
  `half_width=ATR200*0.5`, gap=0;
- `results/parts/states_<SYMBOL>.csv.gz` contains every causal L5-3 state
  available at `state_effective_ts`;
- `results/parts/touches_<SYMBOL>.csv.gz` contains the first raw public-trade
  touch per `state + side`: LONG = lower inner boundary, SHORT = upper inner
  boundary;
- `results/l53_clean.sqlite` is the primary fast analytical surface; schema,
  per-symbol counts and provenance are stored beside it;
- CLEAN contains no width threshold, STAND6, Entry/Exit rule, occupancy,
  TP/SL/outcome, approach, H3/H9 confluence, MAYAK/Dispatcher or market-regime
  filter;
- working width is preserved as absolute width plus explicitly named percentage
  metrics relative to lower inner boundary, upper inner boundary and midpoint.
  To reproduce the 2026-10-03 research convention called
  `working_width_pct`, use `working_width_pct_lower`; this is a research
  retrieval convention, not a Strategy default;
- pilot exact-equivalence against a known current L5-3 event = PASS;
  SQLite integrity = PASS; build stderr = empty.

This artifact is a reusable RESEARCH baseline/evidence. It does not itself
define Strategy policy, activate Strategy, change Entry/Exit behavior or grant
Execution rights. New research should prefer filtering this baseline over
re-reading multi-gigabyte raw public trades when its stored causal fields are
sufficient.

Current Research cleanup checkpoint, CHECKED HERE 2026-10-01:

```text
REMOTE_HEAD      = 8abc9155cfae487de00bd8f468a192eca6a83f8f
SOURCE_HEAD      = 8abc9155cfae487de00bd8f468a192eca6a83f8f
INSTALLED_COMMIT = 8abc9155cfae487de00bd8f468a192eca6a83f8f
LOADED_COMMIT    = 8abc9155cfae487de00bd8f468a192eca6a83f8f
RESEARCH_TOOLING = 8abc9155cfae487de00bd8f468a192eca6a83f8f
```

Research worktrees after internal cleanup:
- `entry_l53_reference` — active feature worktree;
- `exit_dynamic_l53` — active feature worktree;
- `legacy_srv_research` — intentionally retained data-maintenance bundle for
  enabled `cripta-download-expansion.service`;
- retired `minute_entry_book_v1` and `universal_entry_v1` are absent from
  `RESEARCH_ROOT/worktrees` and preserved under
  `/data/cripta/script_archive/retired_entry_shadow_scanner_worktrees_20261001`;
- `RESEARCH_ROOT/tmp` contains no payload objects;
- `RESEARCH_ROOT/tooling/releases` contains only the current tooling release;
- `cripta-entry-shadow-scanner.service` is absent from systemd and remains
  listed only as an installer retirement tombstone.

Final executable path repair/deploy:
- implementation commit:
  `44fac5628c741cbee8ba1f4d659d77560327fd77`;
- `operations/server_resources` and Strategy Dispatcher profile consumers now
  resolve through `/srv/cripta/runtime/current`;
- `cripta-dataset-manifest.service` is canonical-installer managed and executes
  `/data/cripta/research/tooling/current/research/server/dataset/build_manifest.py`;
- the dataset unit remains `static/inactive` until invoked by its existing
  workflow; `cripta-download-expansion.service` retains
  `OnSuccess=cripta-dataset-manifest.service`.

Exact pre-publication identity checkpoint:

```text
REMOTE_HEAD      = d3daa49892a57e45002d28866012f7847ce36611
SOURCE_HEAD      = d3daa49892a57e45002d28866012f7847ce36611
INSTALLED_COMMIT = d3daa49892a57e45002d28866012f7847ce36611
LOADED_COMMIT    = d3daa49892a57e45002d28866012f7847ce36611
RESEARCH_TOOLING = d3daa49892a57e45002d28866012f7847ce36611
```

Final zero-reference/runtime evidence:
- forbidden legacy project roots physically absent;
- current executable/config/system references to those roots = 0;
- live process references to those roots = 0;
- checked core services active;
- mainnet gate=0; real execution permissions=0; hot positions=0;
  queued/running trade commands=0; pending Exchange orders=0;
- `FILESYSTEM_RESTRUCTURING=COMPLETE`.

Historical Markdown/evidence may still quote an old absolute path as part of a
dated past checkpoint. Such text is not a current executable dependency and
must not be interpreted as a live path.

The migration checkpoints below are retained only as historical evidence; they
do not override the final state above.

Research PHASE R1 и R2 выполнены: target root и subroots созданы с
`cripta:cripta 2770`; зарегистрированные research worktrees перенесены в
`/data/cripta/research/worktrees`; завершённые/неактивные research runs
перенесены в `/data/cripta/research/runs`; legacy
`/data/cripta/research_runs` удалён после завершения последнего активного
расчёта.

Физические legacy research payloads из `/srv/cripta/research_runs`,
`/srv/cripta/research`, `/srv/cripta/research_cache` и
`/srv/cripta/research_inputs` перенесены на data-disk с byte/checksum
verification. R4 завершён: после Git-first path repair и zero-reference checks
все transition-only `/srv` compatibility names удалены. Новые research
worktrees/runs/cache/inputs используют только approved `/data` paths.

T2 staging evidence, historical checkpoint 2026-09-29:
- `/srv/cripta/runtime/releases/d4800b4ab132e37f896c71801621d245f6af12c5`
  присутствует как `cripta:cripta 750`;
- `/srv/cripta/runtime/current` указывает на этот exact release;
- runtime `.venv` import preflight PASS (`pydantic=2.13.5`, `psycopg`,
  `websocket`, Universal Entry/Exit/Lifecycle imports);
- `/data/cripta/research/tooling/current` указывает на tooling release того же
  source commit;
- это только staged release layout: loaded systemd services на этом checkpoint
  ещё используют legacy `/srv/cripta/...` paths, поэтому `DEPLOYED` и
  `LOADED_COMMIT=d4800b4...` до T3 не объявляются.

T3 CHECKED HERE 2026-09-30: active runtime consumers переключены на
`/srv/cripta/runtime/current`.

T4 CHECKED HERE 2026-09-30: broader runtime behavior подтверждено повторными
срезами, затем legacy runtime directories удалены после zero-reference и
archive/restore equivalence gate.


R4 CHECKED HERE 2026-09-30:
- GitHub/source path-repair commit:
  `64cb90d747d4168a1c1cd91144f058344a5052e8`;
- current operational source references to
  `/srv/cripta/research_runs`, `/srv/cripta/research`,
  `/srv/cripta/research_cache`, `/srv/cripta/research_inputs` and
  `/srv/cripta/test_gate_venv` = 0;
- governance regression test added; full gate:
  1459 passed, 64 skipped; Ruff PASS; shell syntax PASS;
- enabled `cripta-download-expansion.service` remains inactive but now executes
  directly from
  `/data/cripta/research/worktrees/legacy_srv_research/download_frozen_segment.py`;
- disabled untracked `cripta-minute-entry-book-v1.service` was archived and
  removed from systemd;
- live process/system-config/source zero-reference gates passed before bridge
  removal;
- `/srv/cripta/research_runs` contained symlinks only at deletion time;
- removed:
  `/srv/cripta/research_runs`, `/srv/cripta/research`,
  `/srv/cripta/research_cache`, `/srv/cripta/research_inputs`,
  `/srv/cripta/test_gate_venv`;
- target objects under `/data/cripta/research` remained present;
- job intake/runner stayed active with stable PIDs/restart counters;
- runtime safety remained unchanged: mainnet gate=0, real execution
  permissions=0;
- cleanup evidence archive:
  `/data/cripta/script_archive/r4_bridge_cleanup_20260930_0905`;
- bridge manifest SHA256:
  `3eb773005eaf9b3648ca45c4d591f079299be5a45efef95da0592d22e7fd0940`;
- stale unit backups are preserved in the same archive;
- three temporary T3/T4 worktrees accidentally created under
  `/srv/cripta/worktrees` were diff/status-archived and removed; new repair
  worktrees are under `/data/cripta/research/worktrees`.

Migration order / state:

```text
PHASE R1  COMPLETE  create /data/cripta/research contour + permissions
PHASE R2  COMPLETE  route all NEW research writes/worktrees/runs to target
PHASE R3  COMPLETE  finish exact active legacy job and remove legacy data root
PHASE R4  COMPLETE  compatibility bridges removed; research paths are direct /data
PHASE T1  COMPLETE  active runtime consumers inventoried
PHASE T2  COMPLETE  build /srv/cripta/runtime release layout in Git/release contract
PHASE T3  COMPLETE  exact verified release deployed; loaded-path/liveness cutover PASS
PHASE T4  COMPLETE  runtime behavior/repeat checks PASS; legacy runtime code roots archived+removed
```

T1 active-consumer inventory, historical checkpoint 2026-09-29:

- Dispatcher, MAYAK v2, M3 Analyst, Position Supervisor и Exit Runtime используют
  legacy `/srv/cripta/monitoring` / `/srv/cripta/production/src`;
- Dashboard использует `/srv/cripta/dashboard` + symlink на
  `trade_lifecycle/current`;
- Lifecycle Supervisor и Universal Exit shadow используют
  `/srv/cripta/trade_lifecycle/current`;
- Universal Entry observer использует
  `/srv/cripta/universal_entry_observer/current`;
- latency/safety paths используют `/srv/cripta/connectivity`;
- job intake/runner используют `/srv/cripta/jobs`;
- research watchdog всё ещё исполняется из `/srv/cripta/research_watchdog`;
- `/srv/cripta/test_gate_venv` физически перенесён в
  `/data/cripta/research/cache/test_gate_venv` и оставлен compatibility symlink,
  потому что active observer/lifecycle services всё ещё имеют его в `PYTHONPATH`;
- ни один active production service не импортирует и не исполняет код напрямую
  из `/data/cripta/research`.

Hard invariants during transition:
- no new research output is allowed on `/srv`;
- after PHASE R2 no new research output is allowed in
  `/data/cripta/research_runs`;
- runtime never imports/executes from `/data/cripta/research`;
- archive under `/data/cripta/script_archive` is restore/forensic-only;
- legacy path removal requires zero active refs + verified archived/restored
  copy where preservation is required.


# 2. Верхняя архитектура

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

# 3. Документационный контур

ChatGPT Project Source содержит все 11 current docs, перечисленных в
docs/DOCUMENTATION_INDEX_RU*.md.

Base pre-read состоит из META, WORK, ARCH, INDEX, GLOSSARY, MAP и SECURITY.
TRADING_CONTOUR, OBSERVATION_ANALYTICS, DEVELOPMENT_RELEASE и
RESEARCH_COMPUTE читаются по task route. Наличие routed doc в Project Source
не делает его mandatory every-chat reading.

TRADING_CONTOUR_RU*.md объединяет Strategy + Entry + Exit + Execution.
OBSERVATION_ANALYTICS_RU*.md объединяет MAYAK + Dispatcher + Monitoring +
Lifecycle Supervisor + Position Supervisor + Analyst/Research.

Historical payload/archive docs не являются текущим каноном.

# 4. MAYAK

MAYAK — strategy-agnostic объективное наблюдение внешнего рынка без trading
mutation rights.

# 5. Dispatcher

Текущая архитектура Dispatcher — strategy-agnostic:
- global market context;
- per-coin context;
- trading capacity snapshot;
- объективный rating только после отдельного утверждения формулы.

Dispatcher не создаёт Strategy profile/suitability и не принимает торговое
решение.

CHECKED HERE 2026-09-18:
- `cripta-dispatcher-v2.service` active/enabled;
- legacy `cripta-strategy-dispatcher.service` inactive/disabled;
- legacy source/config всё ещё содержит `M3_V1_*` identifiers и старый
  profile-based contour.

Последний пункт — `FINDING`, а не текущая архитектура. Перед разработкой
старого profile-кода требуется отдельная migration/cleanup задача.

# 6. Strategy / Entry / Exit

CANON после решения владельца 2026-09-18:

- StrategyCard остаётся passive immutable policy;
- Strategy layer включает Strategy Materializer;
- Materializer создаёт exact immutable EntryPlan + ExitPlan;
- Entry Engine универсально исполняет EntryPlan;
- после confirmed fill создаётся StrategyPosition;
- Exit Engine универсально исполняет exact ExitPlan этой StrategyPosition;
- Entry/Exit Engines не зависят от количества Strategy и не содержат скрытой
  Strategy-specific policy;
- Execution исполняет typed Entry/Exit requests.

Текущий source реализует основной универсальный contour этой модели:
- StrategyActivation;
- EntryPlan/ExitPlan materialization;
- ActivePlanRegistry;
- UniversalEntryEngine;
- ParameterizedCausalMarketWatch;
- StrategySignal -> strategy_attempt -> EntryDecision;
- atomic slot + capital admission;
- typed EntryExecutionRequest / request-state lifecycle;
- StrategyPosition exact lineage;
- Universal Exit Engine и typed Exit execution bridge;
- Lifecycle Supervisor;
- Analyst/counterfactual;
- PostgreSQL evidence/read-model;
- shadow recovery/restart contracts.

Real cutover при этом не выполнен: Universal Entry consumer и private runtime
остаются inactive, mainnet gate закрыт. Текущие enabled Strategy являются
monitoring Strategy, а их ExitPlan пока не содержит executable rules. Поэтому
наличие реализованного contour не даёт real execution rights.

# 7. Текущий первый Strategy Candidate

Текущая геометрическая идея остаётся `Strategy Candidate / Draft`, пока
владелец не утвердил immutable StrategyCard/version.

```text
H9 = 9 часов = 540 минут
5m component  = 108 закрытых 5m свечей
15m component = 36 закрытых 15m свечей
```

Это не универсальная константа Entry Engine.

## 7.1 Strategy settings authoring

Owner decision 2026-09-19:

- Strategy-specific settings остаются внутри явных StrategyCard policy-блоков;
- базовая защитная рамка отделяется от динамического Exit;
- новый authoring template содержит explicit disabled slots для hard stop, TP,
  break-even, trailing, geometry Exit, local-zone Exit и time Exit;
- numeric trading defaults в authoring template отсутствуют;
- `geometry_exit` зарезервирован, но его включение fail-closed до появления
  точного executable consumer contract;
- нестабильные H3/touch/trailing параметры остаются Strategy Candidate/Draft
  либо отдельной experimental Strategy version, а не global defaults;
- текущие активные Strategy records в PostgreSQL этой ревизией не меняются.

Классический исследовательский пример хранится только как non-canonical
implementation example и не получает StrategyActivation/execution rights.

# 8. H3

```text
H3 = 3 часа = 180 минут
```

H3 сейчас не является Entry condition текущего Strategy Candidate и относится к
сопровождению/Exit research.

Owner-confirmed baseline 2026-09-25: H9=108x5m+36x15m на 540m; H3=36x5m+12x15m на 180m; одна formula extrema/zones; Wilder ATR200 по каждому timeframe; zone half-width=ATR*0.5; gap=0; legacy shock/reset вне baseline. H3>=1% сейчас RESEARCH condition, не утверждённый Entry rule.

## 8.1 Geometry timeline

OWNER DECISION 2026-09-25: одинаковую Strategy-defined геометрию причинно рассчитывать один раз и сохранять versioned timeline для Entry/Exit/Position Supervisor/Analyst. Strategy сохраняет ownership параметров; observation получает exact GeometrySpec/fingerprint. `CANON=YES; IMPLEMENTED=NOT CHECKED/NOT CLAIMED; DEPLOYED=NO CLAIM`.

# 9. Стабилизация

Стабилизация задаётся Candidate/Strategy в минутах и не является global default.

# 10. Post-fill geometry

Entry price фиксируется как факт сделки.
Текущая geometry после Entry продолжает причинно пересчитываться.

# 11. Execution / Exchange

Execution исполняет уже принятое торговое решение. Bybit — текущий provider, но
не архитектурная константа.

Текущий approved real account contract остаётся:

```text
Bybit Unified linear
position_mode = ONE_WAY
positionIdx = 0
```

Это не считается вечным account state. Перед real arm и далее по freshness
contract требуется новое подтверждённое `position_mode_state`.

IMPLEMENTED / DEPLOYED 2026-09-21:
- durable `runtime.position_mode_states`;
- exclusive `runtime.exchange_position_slot_claims`;
- slot claim до `EntryDecision=ACCEPTED`;
- slot claim + capital reservation как один admission contract;
- `EXCHANGE_POSITION_OWNERSHIP_CONFLICT` как штатный EntryDecision outcome;
- `EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN` как отдельный lifecycle fault;
- `EXCHANGE_POSITION_MODE_MISMATCH` как fail-closed lifecycle/operational fault;
- separate request-state lifecycle;
- StrategyPosition binding/release exact slot + reservation lineage.

Controlled PostgreSQL scenarios проверили:
- два concurrent attempt не могут владеть одним physical slot;
- capital failure не оставляет durable slot claim;
- stale/incompatible position mode не создаёт claim/reservation;
- unknown/post-ack state не приводит к blind release;
- confirmed fill связывает exact StrategyPosition;
- final confirmed close освобождает reservation/slot;
- reconciliation state удерживает ownership до доказанного разрешения.

Production runtime сейчас не содержит fresh `position_mode_state`, потому что
private runtime выключен. Поэтому этот реализованный contract не является
доказательством текущей real account freshness.

# 12. Lifecycle / Position / Analytics

Каноническая lifecycle-chain определяется только ARCH §9.1. TC/OBS её не
дублируют.

Runtime evidence разделяется на:
- RUNTIME LIVENESS VERIFIED;
- RUNTIME BEHAVIOR VERIFIED.

Разделы §12.1, §12.2, §14, §15 и §20 ниже сохраняются как точное historical evidence
checkpoint 2026-09-21 и не должны читаться как current service-state snapshot.
Текущий проверенный operational delta находится в §1.2.

## 12.1 Status matrix — checkpoint 2026-09-21

| Компонент / contract | CANON | IMPLEMENTED | DEPLOYED | LIVENESS | BEHAVIOR | Evidence / режим |
| --- | --- | --- | --- | --- | --- | --- |
| StrategyCard authoring/materializer | YES | YES | YES | N/A | N/A | source/tests; runtime behavior dimension не применяется |
| Universal Entry observer / plan ACK | YES | YES | YES | YES | PARTIAL | service running, exact release loaded, facts advance; текущий observer после restart в WARMUP, post-restart evaluation ещё не наблюдался |
| Capital reservation admission | YES | YES | YES | N/A | YES (CONTROLLED) | concurrent/rollback/reconciliation PostgreSQL scenarios PASS; real consumer inactive |
| Durable physical slot claim + fresh mode contract | YES | YES | YES | N/A | YES (CONTROLLED) | atomic claim/reservation, conflict, stale/mismatch scenarios PASS; production fresh mode state сейчас отсутствует |
| Universal Exit Engine decision-only | YES | YES | YES | YES | YES (CONTROLLED) | service running; controlled ownership/restart/decision scenarios PASS; production open position sample=0 |
| Typed Exit execution bridge/consumer | YES | YES | YES | NO | YES (CONTROLLED) | execution path tested; real execution arm disabled |
| Lifecycle Supervisor full current contract | YES | YES | YES | YES | YES (CONTROLLED) | heartbeat advances; ownership/reconciliation/protection fault scenarios PASS |
| Critical fault durable delivery contract | YES | YES | YES | N/A | YES (CONTROLLED) | retry/ack/escalation and protection-fault delivery PASS; production owner channel currently not configured |
| Analyst counterfactual path | YES | YES | YES | N/A | YES (CONTROLLED) | capital + slot-conflict isolation/tests PASS |
| LIVE-arm evidence/session gate | YES | YES | YES | N/A | YES (CONTROLLED) | missing/stale/wrong-release fail-closed; active exact-release session required |
| Current private runtime source | YES | YES | YES | NO | NO | source/live exact; service inactive/disabled |

`YES (CONTROLLED)` означает специально проведённый воспроизводимый scenario на
disposable PostgreSQL/source exact текущего release. Это не означает, что такой
сценарий уже возникал на real Exchange.

## 12.2 Exact release identity — checkpoint 2026-09-21

Полностью проверенный historical implementation/runtime checkpoint
2026-09-21:

```text
REMOTE_HEAD      = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
SOURCE_HEAD      = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
INSTALLED_COMMIT = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
LOADED_COMMIT    = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
```

Ключевые live/source hashes совпали. После документационной ревизии Git SHA
может измениться только из-за документации; implementation evidence относится
к тем же исполняемым bytes до следующего implementation changeset.

# 13. ChatGPT Project Instructions

Каноническая схема Project Instructions — тонкий bootstrap по
`CHATGPT_INTERACTION_RULES_RU*.md`, с семействами имён через `*`.

Фактический текст Project Instructions в UI является отдельным ChatGPT-project
state и не подтверждается одним только GitHub.

# 14. Граница documentation/runtime revision — checkpoint 2026-09-21

Эта ревизия checkpoint 2026-09-21:
- синхронизирует карту с фактически проверенным code/DB/runtime checkpoint;
- не меняет production trading logic;
- не меняет Strategy records;
- не активирует real Execution;
- не включает mainnet;
- не создаёт fresh position-mode state;
- не создаёт LIVE-arm evidence/session;
- не назначает новые Strategy trading parameters;
- не переводит SHADOW в MICRO_LIVE/LIVE.

Research/исторические H3/H9/TP/SL/trailing результаты не становятся каноном из-за
этого обновления.

# 15. Проверенный runtime checkpoint 2026-09-21

Проверенные service states:

```text
cripta-universal-entry-observer.service = active/running
cripta-lifecycle-supervisor.service      = active/running
cripta-universal-exit-shadow.service     = active/running

cripta-universal-entry-consumer.service = inactive
cripta-private-runtime.service           = inactive
cripta-dashboard.service                 = inactive
```

Safety snapshot:

```text
mainnet execution gate = 0
shadow gate = 1
real Strategy execution permissions = 0
open/reconciliation StrategyPosition = 0
active physical slot claims = 0
active capital reservations = 0
queued/running trade_commands = 0
pending Exchange orders = 0
open lifecycle faults = 0
```

Двухсрезная runtime-проверка показала:
- heartbeat Entry observer / Lifecycle Supervisor / Universal Exit движется;
- PID трёх активных сервисов стабилен;
- Entry observer продолжает принимать market facts;
- за 4 секунды `facts_received` вырос с 105284 до 105649;
- `trading_effect=NONE`;
- faults/claims отсутствуют.

Entry observer после restart находится в штатном `WARMUP`:
- `observer_ready=true`;
- `state=WARMUP`;
- `evaluations=0`;
- `signals=0`;
- причина — ожидание plan-owned pre-start influence/sensor completeness.

Следовательно RUNTIME LIVENESS VERIFIED=YES, но фактический post-restart Entry
evaluation ещё не наблюдался.

# 16. Capital allocation / physical-slot admission

CANON и implementation теперь совпадают:

```text
Strategy attempt
-> required account / position-mode validation
-> physical slot claim
-> atomic capital reservation
-> EntryDecision
-> EntryExecutionRequest only for ACCEPTED
```

Фактически реализованы durable:
- `exchange_position_slot_claim_id`;
- `position_mode_state_ref`;
- `capital_reservation_id`;
- request-state events;
- StrategyPosition binding;
- reconciliation-aware release.

`EXCHANGE_POSITION_OWNERSHIP_CONFLICT` не является lifecycle fault.
Post-admission divergence классифицируется отдельно как
`EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN`.

# 17. Real protection / Lifecycle Supervisor / owner notification

IMPLEMENTED / DEPLOYED:
- StrategyPosition хранит exact ExitPlan/protection/emergency lineage;
- Supervisor выявляет `POSITION_WITHOUT_EXIT_OWNER`;
- Supervisor выявляет `CAPITAL_RESERVATION_STUCK`;
- Supervisor выявляет
  `POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION`;
- Supervisor выявляет `EXCHANGE_POSITION_MODE_MISMATCH`;
- Supervisor выявляет
  `EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN`;
- critical fault создаёт durable delivery;
- delivery поддерживает retry, separate owner acknowledgement и escalation;
- fault/recovery lifecycle restart-idempotent.

Отдельный controlled scenario 2026-09-21 подтвердил:

```text
POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION
-> severity=CRITICAL
-> state=OPEN
-> durable delivery state=PENDING
-> channel=OWNER_WEBHOOK
```

При этом production owner delivery channel сейчас не настроен
(`critical_delivery.configured=false`). Исторически это блокировало real arm.

OWNER DECISION 2026-10-04: для exact текущего R1 MICRO_LIVE
(`r1_aptusdt / r1_injusdt / r1_dotusdt / r1_ltcusdt / r1_arbusdt`,
по 10 USDT, 1x) owner разрешил запуск без owner webhook. В этом scope
`CRITICAL_FAULT_DELIVERY` фиксируется как
`OWNER_WAIVED_FOR_R1_MICRO_LIVE`, а не как `PASS`. Durable
fault/delivery/retry/ack/escalation contract остаётся включённым. Для других
Strategy, иного cohort/лимитов или полного LIVE waiver не действует.

# 18. LIVE / MICRO_LIVE readiness — CHECKED HERE 2026-09-28

Канонический checklist находится в TRADING_CONTOUR §4.7.

Current readiness checkpoint:

```text
CANON_CURRENT                         = PASS
REMOTE_COMMIT_VERIFIED                = PASS
SOURCE_LIVE_IDENTITY                  = NOT READY FOR ARM (operational deltas outside full release)
TESTS                                 = PASS
LIVE_EQUIVALENCE                      = NOT YET DECLARED PASS

EXCHANGE_ACCOUNT_IDENTITY             = NOT CURRENTLY PROVED FOR ARM
POSITION_MODE_FRESH                   = NOT READY
POSITION_IDX_EXPECTED                 = NOT READY
PHYSICAL_SLOT_CLAIM_CONTRACT          = PASS (CONTROLLED)
CAPITAL_RESERVATION_CONTRACT          = PASS (CONTROLLED)

EXACT_STRATEGY_ACTIVATION             = NOT READY FOR ARM
ENTRY_PLAN_EXECUTABLE                 = NOT READY FOR ARM
EXIT_PLAN_EXECUTABLE                  = NOT READY
INITIAL_PROTECTION_EXECUTABLE         = NOT READY
TERMINAL_LOSS_CONTAINMENT_PATH        = NOT CHECKED HERE
EMERGENCY_POLICY_SUPPORTED            = NOT CHECKED HERE

LIFECYCLE_SUPERVISOR_BEHAVIOR         = PASS (CONTROLLED)
CRITICAL_FAULT_DELIVERY               = NOT READY
RECONCILIATION_PATH                   = PASS (CONTROLLED)

MAINNET_GATE_EXPLICIT_OWNER_APPROVAL  = NOT GIVEN
MICRO_LIVE_LIMITS                     = NOT APPROVED
ROLLBACK_OR_KILL_PATH                 = NOT CHECKED HERE
```

Current repository gate for this revision:

```text
full pytest = 1454 passed / 64 skipped / 0 failed
```

Current Strategy activation DB check:

```text
enabled strategy_activations = 0
entry_v1_monitor_long  1.0 = disabled since 2026-09-21
entry_v1_monitor_short 1.0 = disabled since 2026-09-21
```

Experimental StrategyCard records существуют для research, но наличие
StrategyCard не является StrategyActivation и не даёт Entry/Execution rights.
Entry observer сейчас `state=IDLE`,
`reason=no enabled StrategyActivation`, `active_strategies=0`.
Поэтому `EXACT_STRATEGY_ACTIVATION` и `ENTRY_PLAN_EXECUTABLE` остаются
`NOT READY FOR ARM`.

В `runtime.position_mode_states` сейчас 0 rows.
В `control.live_arm_evidence` сейчас 0 rows.
В `control.live_arm_sessions` сейчас 0 rows.

## 18.1 R1 MICRO_LIVE readiness — CHECKED HERE 2026-10-04

Fresh runtime evidence перед arm:

```text
SOURCE_HEAD / INSTALLED_COMMIT / LOADED_COMMIT = 6e4deef20da7bcd1755456fd5139b965de7c6421
R1 StrategyActivation = 5 enabled, exact cohort only
other StrategyActivation = 0
private runtime = connected
trade websocket = authenticated-locked
fresh reconciliation = PASS, positions=0, orders=0
wallet account type = UNIFIED
wallet equity ≈ 58.17 USDT
position mode = ONE_WAY, positionIdx=0 on all 5 R1 symbols
Entry consumer = ENABLED / UNIVERSAL_ENTRY
Exit consumer = ENABLED
R1 reverse worker = ENABLED
R1 loaded-plan equivalence preflight = PASS 5/5
mainnet gate = DISARMED before final arm
execution permissions = 0 before final arm
critical_delivery.configured = false
CRITICAL_FAULT_DELIVERY = OWNER_WAIVED_FOR_R1_MICRO_LIVE
```

Owner approval for the exact R1 MICRO_LIVE scope is explicit in the current
conversation and mirrored into TRADING_CONTOUR §4.7. This checkpoint does not
itself claim that final gate/permissions have already been armed; those remain
runtime evidence after the corresponding mutation.

Следовательно:

```text
READY_FOR_LIVE = NO
READY_FOR_MICRO_LIVE = NO
MAINNET = DISARMED
```

Это fail-closed состояние является ожидаемым и не считается дефектом.

## 18.2 R1 MICRO_LIVE current runtime — CHECKED HERE 2026-10-05

This section supersedes §18.1 **for current runtime status only**. §18.1 is
retained as historical pre-arm evidence from 2026-10-04 and must not be read as
the current gate state.

CHECKED HERE against PostgreSQL/runtime after the owner-approved bugfix deploy
and exact R1 re-arm:

```text
SOURCE_HEAD / REMOTE_HEAD                  = 49a86e5996452ee79d17569ee24749f0eba9a8be
INSTALLED_COMMIT / LOADED_COMMIT           = 49a86e5996452ee79d17569ee24749f0eba9a8be
mainnet gate                               = ARMED / enabled=1
gate reason                                = R1 MICRO_LIVE explicitly armed by owner
enabled R1 execution permissions           = 5
active exact R1 LIVE-arm sessions          = 5
enabled exact R1 StrategyActivation        = 5
open real positions                        = 0
hot/pending exchange orders                = 0
```

Current exact R1 cohort remains:

```text
APTUSDT
INJUSDT
DOTUSDT
LTCUSDT
ARBUSDT
```

This is `MICRO_LIVE`, not unrestricted `LIVE`.

```text
READY_FOR_LIVE       = NO
READY_FOR_MICRO_LIVE = YES [current exact owner-approved R1 scope]
MAINNET              = ARMED [R1 MICRO_LIVE exact cohort only]
```

The MarketRegime prospective validator/research contour has no
Strategy/Entry/Exit/Execution mutation path and does not change this state.
Research observations must report this runtime truth but must not mutate gate,
execution permissions or LIVE-arm.

# 19. Repository / security checkpoint

CHECKED HERE 2026-09-21:
- GitHub repository `PH1119057/cripta` имеет visibility=public;
- full Git-history scan выполнен `gitleaks 8.30.1`;
- найдено 2 `generic-api-key` findings;
- оба вручную классифицированы как старые synthetic test fixtures:
  - `tests/test_dashboard_password_hash.py`;
  - `tests/test_mayak_component_resource_smoke.py`;
- findings принадлежат историческим commits `770a380...` и `7b366ea...`;
- actionable secret findings текущей implementation revision = 0.

Большое количество historical Pxx/EO/SE/ENTRY_BOT/PATCH artifacts в корне
repository остаётся отдельным cleanup finding. Их перенос в `archive/**`
должен быть отдельным exact repository-cleanup changeset после dependency
classification; текущая документационная синхронизация их не перемещает.

# 20. Verification results — checkpoint 2026-09-21

CHECKED HERE 2026-09-21:

```text
full pytest:
1430 passed
62 skipped
0 failed

controlled PostgreSQL lifecycle/admission/fault suite:
54 passed
0 failed

Ruff targeted current-release changes:
PASS
```

Дополнительно доказаны:
- exact remote/source/installed/loaded release identity;
- source/live equality ключевых исполняемых модулей;
- runtime-role ACL новых lifecycle tables;
- Git-first release identity/installer rail;
- exclusive installer lock;
- runtime heartbeat progression;
- no real Exchange mutation during verification;
- mainnet remained disarmed.

Старый documentation-first test debt из checkpoint 2026-09-19 закрыт и больше
не является текущим finding.

# 21. Current unresolved operational items

До real arm остаются именно operational/readiness задачи, а не недоказанная
реализация slot/lifecycle foundation:

1. current Entry observer находится `IDLE` с
   `reason=no enabled StrategyActivation`; до real arm требуется exact
   owner-approved StrategyActivation и post-activation runtime behavior evidence;
2. activated Strategy должна иметь executable EntryPlan + ExitPlan + required
   protection/lifecycle policy;
3. private account state поднимать только отдельным разрешённым readiness step:
   сначала совместить runtime schema contract, затем получить fresh
   `position_mode_state` / `positionIdx=0`; dependency crash-loop уже устранён;
4. настроить реальный durable owner-notification channel для critical faults;
5. сформировать canonical LIVE-arm evidence для exact Strategy/symbol/release;
6. owner-approved MICRO_LIVE limits;
7. отдельное explicit owner approval на real arm;
8. только после этого MICRO_LIVE; full LIVE не следует из MICRO_LIVE автоматически.
# 22. Observation contour completion — OWNER DECISION / CHECKED HERE 2026-10-02

OWNER DECISION 2026-10-02:

~~~text
DOCUMENTATION FIRST
-> IMPLEMENTATION IN SEPARATE STAGES
-> NO SILENT LIVE/TRADING EFFECT
~~~

Canonical target:
- MAYAK remains strategy-agnostic external-market observer;
- MAYAK distinguishes instant movement from persistent multi-horizon
  MarketRegime/MarketRegimeEpisode;
- observation continuity/coverage is an explicit quality dimension;
- significant regime/data-quality transitions can produce durable
  MarketObservationAlert without trading rights;
- Dispatcher remains strategy-agnostic but provides useful structured
  global/per-coin context rather than Strategy suitability;
- CoinMarketRating is objective-only and requires separate approved formula;
- trading interpretation belongs only to exact Strategy Context Policy;
- Strategy explicitly declares context usage per ENTRY/POSITION/EXIT phase;
- architecture changes use capability-conservation Hard Stop before old
  capability decommission.

CHECKED HERE against GitHub/source/runtime 2026-10-02:

~~~text
SOURCE_HEAD at audit        = cddf7e7449b449652a8ac1dd97807ae534ce2081
MAYAK primary MarketState   = current 5m-return panel classifier
MAYAK 15m/60m evidence      = exists, not primary persistent regime owner
snapshot persistence        = minute write depends on now.second < 2
observed DB snapshot gaps   = up to ~65 minutes in checked recent window
dispatcher_v2 rating status = NOT_IMPLEMENTED
current compatibility card  = mayak_context_policy []
                              dispatcher_context_policy []
current exact raw archive   = available research tree through 2026-08-16;
                              Sep/Oct equivalent public_trades archive
                              NOT FOUND in checked raw contour
~~~

Recent DB coverage audit for retained MAYAK coin contexts
2026-09-21 07:43 UTC -> 2026-10-02 16:50 UTC:

~~~text
OI                         ~99.21%
funding                    ~99.21%
spot 5m flow               ~99.04%
spot/derivatives books     ~99.21%
derivatives 5m flow        ~67.44%
liquidations VALID         ~45.16%
liquidations WARMUP        ~54.84%
~~~

These are FINDING / CHECKED HERE, not new trading policy.

Implementation status at publication of this documentation decision:

~~~text
persistent MarketRegime        = NOT IMPLEMENTED
MarketRegimeEpisode            = NOT IMPLEMENTED
durable MarketObservationAlert = NOT IMPLEMENTED
snapshot cadence repair        = NOT IMPLEMENTED
liquidation continuity repair  = NOT IMPLEMENTED
CoinMarketRating formula       = NOT IMPLEMENTED
Strategy phase context usage   = CANON UPDATED / IMPLEMENTATION PENDING
modern exact replay contour    = GAP / IMPLEMENTATION PENDING
~~~

No Strategy behavior, LIVE gate, Entry/Exit decision or Exchange mutation is
changed by this documentation revision.

## 22.1 MarketRegime multi-window research — OWNER DECISION / RESEARCH RESULT 2026-10-03

OWNER DECISION 2026-10-03:

~~~text
KEEP 1w / 2w / 4w EVIDENCE SEPARATELY
UTILITY_NOT_CONFIRMED
NO WINNER / NO CONSENSUS POLICY
NO TRADING EFFECT
CONTINUE OBSERVATION-CONTOUR ROADMAP
~~~

Research run:

~~~text
/data/cripta/research/runs/market_regime_multihorizon_focus15_20261003
~~~

CHECKED HERE research evidence:
- focus universe = 15 symbols, directionally separated UP/DOWN research
  populations corresponding to LONG/SHORT path analysis;
- horizons checked = 5m / 15m / 30m / 60m / 120m / 180m;
- adaptive trailing windows checked = 1w / 2w / 4w;
- fixed debounce checked = 0/1/2/3m;
- adaptive gap quantiles checked = P25/P50/P75;
- 495/495 Bybit daily public-trade gap archives for 2026-08-16..2026-09-17
  were causally streamed to minute data with 0 missing / 0 errors;
- exact source bytes checked = 5,330,890,143;
- long-horizon evidence was more persistent than short-only impulse across all
  checked 30 symbol×direction populations for Q60/Q70/Q80 on train and holdout;
- no single 1w/2w/4w window, no C1/C2/C3 consensus rule and no tested debounce
  value was strong enough to become canonical policy.

Current status after this owner decision:

~~~text
MarketRegimeEvidence 1w/2w/4w  = RESEARCH COMPLETE / KEEP AS EVIDENCE
evidence utility                  = UTILITY_NOT_CONFIRMED
persistent MarketRegime           = IMPLEMENTATION PENDING
MarketRegimeEpisode               = IMPLEMENTATION PENDING
debounce/hysteresis policy        = NOT APPROVED
automatic Strategy use            = FORBIDDEN
automatic CoinMarketRating use    = FORBIDDEN
automatic alert/regime promotion  = FORBIDDEN
~~~

This decision permits neutral evidence storage/transport only. It does not
promote the research result into Strategy policy, MarketRegime classification,
MarketRegimeEpisode boundaries or LIVE behavior.

## 22.2 Durable MarketObservationAlert infrastructure — CHECKED HERE 2026-10-03

Pre-publication implementation/deploy checkpoint, CHECKED HERE 2026-10-03:

~~~text
REMOTE_HEAD      = 458ebb173d729ef328149c2464166047c4dd32f3
SOURCE_HEAD      = 458ebb173d729ef328149c2464166047c4dd32f3
INSTALLED_COMMIT = 458ebb173d729ef328149c2464166047c4dd32f3
LOADED_COMMIT    = 458ebb173d729ef328149c2464166047c4dd32f3
MAINNET_GATE     = DISARMED
~~~

Implemented/deployed:
- immutable `mayak_v2.market_observation_alerts`;
- mutable `mayak_v2.market_observation_alert_deliveries`;
- canonical alert classes:
  `REGIME_CHANGE`, `MARKET_WIDE_STRESS`, `SYNCHRONIZATION_SPIKE`,
  `LIQUIDATION_CASCADE`, `DATA_QUALITY_DEGRADATION`, `SOURCE_OUTAGE`;
- delivery lifecycle:
  `PENDING -> DELIVERED -> ACKNOWLEDGED` with retry and
  `ESCALATION_REQUIRED`;
- `owner_notifiable=false` by default;
- provenance requires `trading_command=false`;
- alert facts are immutable; delivery rows have no DELETE privilege for
  runtime actor `cripta`.

Controlled DB behavior, CHECKED HERE:
- runtime actor / DB current_user = `cripta`;
- controlled `SOURCE_OUTAGE` alert completed
  `PENDING -> DELIVERED -> ACKNOWLEDGED`;
- scenario ran in one transaction and was rolled back;
- post-rollback production alert rows = 0;
- post-rollback production delivery rows = 0.

Runtime/liveness after deploy:
- MAYAK, Dispatcher V2 and Lifecycle Supervisor active;
- checked service restart counts = 0;
- execution permissions/open positions/hot positions/pending commands/
  pending orders = 0;
- no automatic market alert trigger was enabled.

Current observation-contour status:

~~~text
MarketRegimeEvidence 1w/2w/4w     = KEEP / UTILITY_NOT_CONFIRMED
persistent MarketRegime             = IMPLEMENTATION PENDING
MarketRegimeEpisode                 = IMPLEMENTATION PENDING
durable MarketObservationAlert infra= IMPLEMENTED / DEPLOYED / CONTROLLED VERIFIED
automatic alert generation          = NOT IMPLEMENTED / POLICY NOT APPROVED
Dispatcher persistent-regime enrich = BLOCKED BY UTILITY_NOT_CONFIRMED
CoinMarketRating formula             = OWNER DECISION REQUIRED
Strategy use of this regime evidence = FORBIDDEN BY CURRENT OWNER DECISION
modern exact replay contour          = TERM/BOUNDARY NOT CANONICALLY DEFINED
~~~

No trading behavior or Exchange state was changed.

## 22.3 Observation Replay Contour boundary — OWNER DECISION 2026-10-03

OWNER DECISION:

~~~text
MODERN EXACT REPLAY = FULL OBSERVATION CONTOUR
MAYAK
-> DISPATCHER
-> QUALITY / CONTINUITY
-> MarketObservationAlert STAGE
-> STOP BEFORE STRATEGY / TRADING
~~~

Exact boundary:
- production `LiveMayakEngine` semantics are reused by replay;
- production Dispatcher V2 builders are reused by replay;
- quality/continuity remain explicit causal evidence;
- alert stage is part of replay;
- current automatic alert-generation policy = `NO_POLICY`;
- no alert threshold, severity threshold, regime winner, CoinMarketRating,
  Strategy suitability or Strategy context policy is inferred;
- explicit historical MarketObservationAlert facts may be replayed;
- account/trading-capacity state is outside the mandatory market replay unless
  supplied as a separate causal technical input;
- Strategy/Entry/Exit/Execution/Exchange mutation are outside this contour.

Current implementation status at this owner decision:

~~~text
CausalMayakReplay / same LiveMayakEngine = IMPLEMENTED
Dispatcher V2 production builders        = IMPLEMENTED
quality/continuity in MAYAK/Dispatcher    = IMPLEMENTED
durable MarketObservationAlert infra      = IMPLEMENTED / DEPLOYED
full composed Observation Replay Contour  = IMPLEMENTATION PENDING
automatic alert generation                = NO_POLICY / NOT APPROVED
~~~

## 22.4 Observation Replay Contour implementation — CHECKED HERE 2026-10-03

Pre-publication implementation/runtime checkpoint:

~~~text
REMOTE_HEAD      = dd0416bb7844dfc1de26f1fc88ab749ca4f26bd4
SOURCE_HEAD      = dd0416bb7844dfc1de26f1fc88ab749ca4f26bd4
INSTALLED_COMMIT = dd0416bb7844dfc1de26f1fc88ab749ca4f26bd4
LOADED_COMMIT    = dd0416bb7844dfc1de26f1fc88ab749ca4f26bd4
MAINNET_GATE     = DISARMED
~~~

IMPLEMENTED / DEPLOYED:
- `ObservationContourReplay` composes the complete strategy-agnostic replay
  boundary selected in §22.3;
- MAYAK uses existing `CausalMayakReplay` and the same `LiveMayakEngine`;
- MAYAK live persistence and replay share exact pure source-record builders for
  `shared_market_contexts` / `coin_market_contexts`;
- minute continuity uses one shared `MinuteContinuityTracker` in live Collector
  and replay;
- replay loads the exact production `dispatcher_v2` package from the installed
  release and calls its production builders/serialization instead of copying
  formulas;
- quality/coverage/freshness and `trading_effect=NONE` are preserved;
- alert stage is always present with `NO_POLICY`; automatic generated alerts
  remain empty until a separate owner-approved alert-generation policy exists;
- explicit causal historical MarketObservationAlert facts use the same pure
  alert builder/content hash as durable live alert creation;
- Strategy/Entry/Exit/Execution/Exchange mutation are absent from replay.

TEST / RUNTIME EVIDENCE:

~~~text
targeted observation/replay suite = 24 passed
full pytest                        = 1499 passed / 64 skipped / 0 failed
Ruff changed code                  = PASS
mypy new/shared modules            = PASS
runtime ObservationReplay E2E      = PASS
runtime dispatcher package         = exact installed production package
alert policy                       = NO_POLICY
automatic generated alerts         = 0
3-minute replay continuity gap     = 2 missing snapshots [expected]
~~~

Post-deploy liveness:
- `cripta-mayak-v2.service` active, `NRestarts=0`;
- `cripta-dispatcher-v2.service` active, `NRestarts=0`;
- `cripta-lifecycle-supervisor.service` active, `NRestarts=0`;
- latest live MAYAK continuity retained `last_gap_minutes=1` after deploy;
- execution permissions/open positions/hot positions/pending commands/
  pending orders = 0;
- mainnet remained disarmed.

Observation-contour roadmap after this checkpoint:

~~~text
R10 multi-horizon evidence           = DONE / UTILITY_NOT_CONFIRMED
R11 durable alert infrastructure     = DONE / DEPLOYED / CONTROLLED VERIFIED
R12 Dispatcher persistent-regime use = BLOCKED BY UTILITY_NOT_CONFIRMED
R13 CoinMarketRating                  = OWNER DECISION REQUIRED / FORMULA ABSENT
R14 Strategy regime-context use       = FORBIDDEN BY CURRENT OWNER DECISION
R15 Observation Replay Contour        = DONE / DEPLOYED / RUNTIME BEHAVIOR VERIFIED
~~~

No trading behavior or Exchange state was changed by R15.


# 24. Dashboard UI release separation — owner decision 2026-10-05

OWNER DECISION:

```text
presentation-only Dashboard UI
!= trading/application runtime
```

Target physical/release identity:

```text
DASHBOARD_UI_ROOT   = /srv/cripta/dashboard-ui
DASHBOARD_UI_COMMIT = exact verified Git commit for current presentation asset
```

Presentation-only HTML/CSS/read-model rendering may be deployed while
`mainnet gate=OPEN` and Strategy `Execution ON`, provided the UI-only verifier
proves that API/auth/control/mutation and decision/execution-affecting semantics
did not change.

Such deploy:
- must not stop/restart Entry, Exit, private runtime, observer, lifecycle or
  other trading services;
- must not modify StrategyActivation, execution permissions or LIVE-arm;
- should not restart Dashboard itself when the static asset is read per request;
- records independent UI commit/hash evidence.

Current implementation migration from runtime-owned
`operations/dashboard/index.html` to the separate UI rail is part of the same
owner-approved change.

CHECKED HERE 2026-10-05 after first UI-only deploy:

```text
pre-publication SOURCE/REMOTE snapshot = 45e38252c786c093ebcb41ccce4091018ff20bb0
INSTALLED_COMMIT                       = ee7bf29cb5249151a90fc3ee8eeb1fdf2684a8b0
LOADED_COMMIT                          = ee7bf29cb5249151a90fc3ee8eeb1fdf2684a8b0
DASHBOARD_UI_COMMIT                    = 45e38252c786c093ebcb41ccce4091018ff20bb0
DASHBOARD_UI_ROOT                      = /srv/cripta/dashboard-ui
OPERATIONAL_DELTA_COMMIT               = 45e38252c786c093ebcb41ccce4091018ff20bb0
AFFECTED_OPERATIONAL_PATH              = /usr/local/sbin/cripta-deploy-dashboard-ui
```

Runtime evidence:
- presentation-only verifier: `PASS`;
- UI source/live SHA256 equal:
  `152cdd213a3ea1c32e865d930c87d6fa9dceb4d3fd854565c017ee94aa1fae61`;
- `mainnet gate` remained `1`;
- enabled execution permissions remained `5`;
- enabled StrategyActivation count remained `5`;
- active exact LIVE-arm sessions remained `5`;
- Universal Entry observer/consumer, private runtime, Universal Exit consumer,
  Lifecycle Supervisor and Dashboard kept the same PIDs with `NRestarts=0`;
- no trading service and no Dashboard service was restarted.

Status:

```text
CANON                         = YES
IMPLEMENTED                   = YES
DEPLOYED                      = YES
RUNTIME LIVENESS VERIFIED     = YES
RUNTIME BEHAVIOR VERIFIED     = YES [UI hot-swap + gate/permission invariance]
TRADING BEHAVIOR CHANGED      = NO
```

# 25. Real Entry account-state generation repair — OWNER DECISION 2026-10-06

OWNER DECISION:
- current R1 MICRO_LIVE scope не расширять;
- исправить non-deferrable slot-claim -> strategy_attempt FK, который ломал
  единый outer admission transaction до записи StrategyAttempt;
- заменить независимые real-entry wall-clock freshness gates
  reconciliation/wallet/capacity на current COMPLETE AccountStateGeneration;
- exact position-mode proof real Entry брать из того же generation;
- StrategySignal / EntryExecutionRequest expiry оставить без изменения.

SOURCE STATUS этого changeset до production deploy:
IMPLEMENTATION IN PROGRESS / DEPLOYED NOT CLAIMED HERE.
Runtime checkpoint после exact verified release обязан отдельно доказать
generation progression, deferred FK, automatic StrategySignal ->
EntryDecision -> EntryExecutionRequest path, gate/session identity и отсутствие
scope expansion.


BEFORE / AFTER CAPABILITY MATRIX:

| CAPABILITY | BEFORE_OWNER | BEFORE_STATUS | AFTER_OWNER | AFTER_STATUS | REPLACEMENT_IMPLEMENTED | MIGRATION_REQUIRED | TEST_EVIDENCE |
|---|---|---|---|---|---|---|---|
| private account truth for real Entry | private runtime + Entry readiness | fragmented wallet/reconciliation/capacity clocks | private runtime AccountStateGeneration + Entry admission | one COMPLETE/FAILED generation contract | YES | YES | generation/reconciliation tests |
| physical slot + capital admission | Entry admission | intended atomic transaction, broken by non-deferrable attempt FK | Entry admission | deferred FK validated at outer COMMIT, same referential integrity | YES | YES | migration + PostgreSQL integration gate |
| StrategySignal/ExecutionRequest expiry | Strategy/Entry | 30s Strategy-owned request validity | Strategy/Entry | unchanged | N/A | NO | architecture regression tests |
| position-mode proof for real Entry | private runtime + Entry admission | separate fresh_until clock | AccountStateGeneration + Entry admission | exact mode ref bound to current generation | YES | YES | generation/admission tests |

No capability loses an owner; no trading-policy owner moves between top-level layers.
# 26. PAPER / REAL execution-mode parity — OWNER DECISION / CHECKED HERE 2026-10-07

OWNER DECISION:
- `StrategyActivation` остаётся единым включателем Strategy;
- при выключенном real execution Strategy продолжает работать через PAPER;
- включение real execution выбирает REAL environment, а не другую
  Strategy/Entry/Exit implementation;
- для одного `strategy_attempt` запрещено одновременное PAPER + REAL execution;
- PAPER и REAL должны совпадать по Strategy/Entry/Exit policy и lifecycle;
- различаться разрешено только real-only admission/account/Exchange truth и
  фактическим execution result;
- real admission failure не даёт права silent fallback в PAPER.

## 26.1 Historical checkpoint — CHECKED HERE 2026-10-06

Historical source/loaded release:
`7a37b1976749d7d68c0be4d91b5167a6538ba0c1`.

На этом checkpoint target ещё не был реализован полностью:
- mode-neutral Entry payload ещё оставался физически привязан к PAPER naming;
- PAPER и REAL routing ещё не были доказаны как XOR;
- PAPER dynamic Exit и REAL ExitDecision физически расходились по decision path;
- restart-cold-history уже был исправлен, но full execution-mode parity ещё не
  была доказана.

Historical status:

```text
CANON                         = YES
OWNER_DECISION                = YES
IMPLEMENTED                   = NO
DEPLOYED                      = NO [Stage 4 target]
PAPER_REAL_DECISION_PARITY    = NOT YET PROVED
REAL_REARM                    = HARD_STOP
```

Этот блок сохраняется как historical evidence и не описывает current runtime.

## 26.2 Current parity checkpoint — CHECKED HERE 2026-10-07

Pre-publication source/runtime checkpoint:
`d6769ea574385dd4c1c7cbdd9e813b2509a2f44f`.

По INDEX §17.5 этот SHA является dated pre-publication snapshot: сам
documentation commit неизбежно изменит `REMOTE_HEAD/SOURCE_HEAD`, поэтому после
публикации exact release identity проверяется отдельно operational evidence.

Stage 4B / Stage 7B evidence:

- Entry:
  - `EntryExecutionIntent` является mode-neutral payload;
  - PAPER и REAL получают один exact Strategy/attempt/signal/EntryPlan/ExitPlan
    lineage;
  - REAL `ExecutionRequest` копирует payload `EntryExecutionIntent` без
    result-affecting policy rewrite;
  - observer выбирает PAPER XOR REAL; real admission failure не создаёт PAPER
    fallback.
- Exit:
  - PAPER `PaperTradeRuntime` вызывает тот же `UniversalExitEngine`, что и
    REAL path;
  - requested mutation проходит тот же `validate_exit_mutation`;
  - live PAPER `DYNAMIC_TP_MOVED` events несут exact
    `exit_decision_id / rule_id / action_kind / requested_mutation`.
- Reverse:
  - PAPER adapter и REAL observer/worker используют общий immutable
    `ReverseTransitionIntent`;
  - current R1 contract:
    `OPPOSITE_ENTRY_FORCED_FLIP -> MARKET close + MARKET open -> ONE_WAY /
    positionIdx=0 -> OPPOSITE_FLIP_TAKER`;
  - live opposite-entry flip во время Stage 7B soak не случился, поэтому
    рыночное occurrence не объявляется; parity доказана deterministic
    production-function evidence + adapter tests.
- Restart / causal continuity:
  - controlled restart PAPER Entry observer + Exit shadow сохранил три exact
    `paper_position_id`, direction и dynamic target state;
  - post-restart observer вернулся в `observer_ready=true`, history-ready
    state и продолжил causal market stream;
  - на следующей закрытой 5m свече ARB/DOT/INJ получили новые
    `DYNAMIC_TP_MOVED` с новыми ExitDecision IDs и тем же validated mutation
    contract;
  - unresolved `UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR` = 0.
- Tests:
  - exact current production parity gate: `113 PASS`;
  - full current repository suite: `1598 PASS / 65 SKIP`;
  - remaining 9 failures — тот же pre-existing Dashboard/U6 baseline, новых
    Stage 7 parity failures нет.
- Identity:
  - relevant Entry/Exit/reverse production modules byte-for-byte совпадали между
    source и loaded runtime на pre-publication checkpoint.

Current capability matrix:

| CAPABILITY | CURRENT OWNER/PATH | STATUS |
|---|---|---|
| Strategy/Entry trading meaning | StrategyCard + EntryPlan + UniversalEntryEngine | CONSERVED / VERIFIED |
| mode-neutral Entry execution semantics | `EntryExecutionIntent` before adapter selection | IMPLEMENTED / VERIFIED |
| real admission / slot / capital safety | REAL-only admission barrier | CONSERVED / VERIFIED CONTRACT |
| PAPER execution | PAPER adapter only when real execution permission OFF | IMPLEMENTED / VERIFIED |
| REAL execution | REAL adapter only when permission ON + admission/gates PASS | IMPLEMENTED / VERIFIED CONTRACT |
| Exit decision | one ExitPlan + UniversalExitEngine semantics for PAPER/REAL | IMPLEMENTED / RUNTIME VERIFIED |
| Reverse decision | common `ReverseTransitionIntent`, separate execution adapters | IMPLEMENTED / VERIFIED |
| Exchange mutation | private runtime / REAL consumers only | CONSERVED; not exercised by Stage 7B |

Current parity status at this checkpoint:

```text
CANON                         = YES
OWNER_DECISION                = YES
IMPLEMENTED                   = YES
DEPLOYED                      = YES
RUNTIME_LIVENESS_VERIFIED     = YES
RUNTIME_BEHAVIOR_VERIFIED     = YES
PAPER_REAL_DECISION_PARITY    = PASS

mainnet gate                  = 0
real execution permissions    = 0
real Entry/Exit/reverse/private services = intentionally inactive
PAPER observer                = active
PAPER Exit shadow             = active
```

`PAPER_REAL_DECISION_PARITY=PASS` закрывает только decision/policy parity gate.
Он **не** означает автоматический LIVE/MICRO_LIVE re-arm. Перед real arm
обязателен новый fresh check полного `TRADING_CONTOUR §4.7` checklist,
включая Exchange/account/mode/slot/capital/protection/reconciliation gates и
explicit owner approval. Любой `UNKNOWN/STALE/FAIL` остаётся fail-closed.

## 26.3 Stage 7D post-arm consumer stabilization — CHECKED HERE 2026-10-07

Этот раздел supersedes §26.2 **только для current R1 MICRO_LIVE runtime status**.
§26.2 сохраняется как historical parity/pre-rearm checkpoint.

После owner-approved Stage 7C re-arm был обнаружен production finding: post-arm
Universal Entry consumer повторно вызывал `evaluate_live_arm()` для каждого
REAL `EntryExecutionRequest`. Pre-arm TTL evidence имел
`valid_until = arm time + 90s`, а exact release-bound LIVE-arm session
продолжала оставаться ACTIVE. После TTL три `APTUSDT SHORT` REAL attempts на
release `e7f86a815569d872107b56b5802434572377cbc3` создали три
`EntryExecutionRequest`, но consumer завершил их `REQUEST_CANCELLED` с
`LIVE_ARM_NOT_READY` до Exchange mutation. PAPER fallback, trade command,
fill, position, pending order и open lifecycle fault не возникли; admission
resources были fail-closed освобождены.

Root cause: observer уже исполнял current contract — short-lived durable
LIVE-arm evidence использовался как pre-arm authorization, а после arm REAL
selection требовал exact active release-bound `live_arm_session` и текущую
per-entry technical readiness. Entry consumer дополнительно переоценивал тот же
expiring pre-arm evidence, поэтому корректная ACTIVE session становилась
unusable через 90 секунд.

Stage 7D repair:

~~~text
commit = bf3e31fdbb9a3b930518ee62dfa612454284d851
scope  = operations/connectivity/universal_entry_consumer.py
       + tests/test_r1_post_arm_admission.py
~~~

Consumer после repair:
- exact `LiveArmContext` всё ещё обязан соответствовать loaded release;
- exact ACTIVE release-bound LIVE-arm session обязательна;
- `_admission_pre_dispatch_status()` выполняется **до** session check и
  продолжает fail-closed проверять reservation/slot lineage, request-bound
  position-mode state/freshness, `ONE_WAY / positionIdx=0`, newer incompatible
  mode, ownership, hot position, pending Entry command и pending Exchange order;
- expired pre-arm evidence после уже успешного arm больше не является post-arm
  request gate;
- отсутствие ACTIVE session остаётся blocking.

Test evidence:

~~~text
py_compile changed files                           = PASS
Ruff changed files                                 = PASS
scoped release suite                               = 107 passed / 12 skipped
full pytest                                        = 1605 passed / 67 skipped
known pre-existing Dashboard/U6 baseline failures = 9
NEW_TEST_FAILURES                                  = 0
mypy baseline diagnostics                          = 6
NEW_MYPY_DIAGNOSTICS                               = 0
~~~

Exact application deploy checkpoint before this documentation publication:

~~~text
REMOTE_HEAD         = bf3e31fdbb9a3b930518ee62dfa612454284d851
SOURCE_HEAD         = bf3e31fdbb9a3b930518ee62dfa612454284d851
INSTALLED_COMMIT    = bf3e31fdbb9a3b930518ee62dfa612454284d851
LOADED_COMMIT       = bf3e31fdbb9a3b930518ee62dfa612454284d851
RESEARCH_TOOLING    = bf3e31fdbb9a3b930518ee62dfa612454284d851
DASHBOARD_UI_COMMIT = ed39c77b8985bdabd5552d37ebf0b5e2964118c2
~~~

Canonical installer verified exact GitHub release/tree/payload, created a
quiesced PostgreSQL rollback backup, replayed migrations/schema validation and
proved source/live consumer SHA256 equality. Critical restarted services were
stable with `NRestarts=0`. Installer left `GATE=DISARMED`, execution
permissions=0 and active LIVE-arm sessions=0 before explicit re-arm.

Explicit owner-approved R1 re-arm on the same application release then passed:

~~~text
pre-arm readiness                      = PASS
R1 StrategyActivation                  = 5/5 enabled
mainnet gate                           = ON
REAL execution permissions             = 5/5
ACTIVE release-bound LIVE-arm sessions = 5/5
requested_amount                       = 10 USDT per Strategy
leverage                               = 1x
cohort                                 = APT / INJ / DOT / LTC / ARB
open lifecycle faults                  = 0
hot positions / hot orders             = 0 / 0
~~~

Post-TTL runtime behavior verification:
- re-arm occurred at `2026-10-07 08:39:15 UTC`;
- last TTL-bound pre-arm evidence expired at `08:40:45 UTC`;
- after expiry all 20 TTL-bound evidence rows were expired;
- loaded Stage 7D Entry consumer returned post-arm readiness `TRUE` for all
  5/5 exact R1 contexts using their ACTIVE release-bound sessions;
- observer independently returned `REAL_SELECTED=5 / REAL_READY=5 / MATCH=True`;
- private runtime remained connected, trade runtime `authenticated-locked`,
  current AccountStateGeneration continued producing COMPLETE flat-account
  generations.

Status boundary:

~~~text
STAGE7D_POST_ARM_SESSION_CONTRACT         = RUNTIME BEHAVIOR VERIFIED
R1_MICRO_LIVE                             = ARMED
RUNTIME_LIVENESS                          = VERIFIED
FIRST_POST_FIX_STRATEGY_ATTEMPT           = NOT YET OCCURRED at this checkpoint
FIRST_POST_FIX_EXCHANGE_MUTATION          = NOT YET OCCURRED
FULL REAL ENTRY/FILL/PROTECTION/EXIT CYCLE = NOT YET RUNTIME BEHAVIOR VERIFIED
~~~

Отсутствие post-fix StrategySignal/attempt на этом checkpoint не является
ошибкой и не заменяется искусственным replay старых отменённых attempts.
Следующий естественный R1 signal должен проверяться causal end-to-end:
`StrategySignal -> attempt -> EntryExecutionRequest -> dispatch -> trade_command
-> Exchange order/fill -> initial protection -> ExitDecision/close ->
final economics`.

После публикации именно этой documentation revision `REMOTE_HEAD/SOURCE_HEAD`
неизбежно изменятся на documentation commit, тогда как application
`INSTALLED_COMMIT/LOADED_COMMIT` останутся `bf3e31f...` до следующего
application deploy. Это ожидаемая INDEX §17.5 time semantics и не должно
маскироваться под новый loaded release.

# 27. R1 Entry-time protection clarification / repair — OWNER DECISION 2026-10-07

OWNER DECISION:
- preserve catastrophic initial SL `-10.0%`;
- restore mandatory Entry-time TP as exact price of the causal opposite current
  5m L5-3 inner boundary (`LONG -> resistance_bottom`,
  `SHORT -> support_top`);
- initial TP is not a fixed percentage;
- both protection levels must be submitted to Bybit with the opening order;
- subsequent dynamic TP replacement remains Universal Exit-owned;
- immutable `1.0-micro-live` cards are not edited in place; corrected
  immutable version = `1.1-micro-live`.

FINDING before repair:
- current `1.0-micro-live` cards carried `take_profit_enabled=false`;
- market watch nevertheless produced `r1_opposite_inner_target`;
- REAL execution bridge already supported a price reference, but the active R1
  card did not request it;
- private runtime assumed `take_profit_pct` during opening-order protection and
  raised `TypeError: '<=' not supported between instances of 'NoneType' and
  'int'` before `/v5/order/create`;
- five post-Stage-7D REAL attempts reached private runtime and terminated
  pre-order with this same error; no real fill/position resulted.

Safety action:
- R1 MICRO_LIVE was explicitly DISARMED before repair work;
- re-arm is not part of patch/deploy and requires a separate explicit owner
  approval after exact release/readiness evidence.

Implementation/deploy/runtime evidence is recorded only after the corresponding
Git/release/runtime steps complete.


# 28. R1 1.1 post-arm first REAL order + cancellation-audit repair — CHECKED HERE 2026-10-07

After the `c4c0a8ed8515699991215bcd91c7725941423273` Stage 7E deployment,
owner-approved fresh pre-arm evidence initially failed closed because the
independent Dashboard read-model bundle still carried the previous
`PAPER_REAL_DECISION_PARITY` attestation. The canonical Dashboard scope verifier
accepted only `_u6_prepare_r1_prearm_evidence` plus
`R1_PARITY_ATTESTED_MODULE_SHA256`; the Dashboard bundle was then switched to
the same exact `c4c0a8ed...` source while trading-service PIDs/restarts, gate and
execution permissions remained unchanged.

Fresh R1 `1.1-micro-live` pre-arm then returned `PREARM_READY` for all five
owner-approved Strategy versions and owner re-arm created exactly five active
release-bound LIVE-arm sessions. The first natural REAL attempt occurred on
`DOTUSDT LONG` and proved:

```text
StrategyAttempt -> ACCEPTED
-> physical slot claim + capital reservation
-> REAL EntryExecutionRequest
-> private trade command
-> Bybit /v5/order/create retCode=0
-> Exchange order acknowledgement
```

The opening command carried the corrected Exchange-side protection:
- `stop_loss_enabled=true`, `stop_loss_pct=10.0`;
- `take_profit_enabled=true`;
- exact price-based `take_profit_price`;
- `take_profit_reference_path=fact.r1_opposite_inner_target`.

No fill occurred. While the PostOnly order was resting, causal L5-3 Entry
geometry changed and runtime cancelled it under the actual
`R1_EXACT_ENTRY_LEVEL_CHANGED` `SIGNAL_VALIDITY` reason. Reconciliation proved
`Cancelled + zero fill`; capital reservation and physical slot claim were
released, no real position remained and no lifecycle fault opened.

FINDING:
historical lifecycle release evidence mislabeled every confirmed zero-fill
Entry cancellation as `LIMIT_TTL_CANCEL_CONFIRMED_ZERO_FILL`, even when the
actual Strategy lifetime mode was `SIGNAL_VALIDITY` and
`entry_limit_ttl_seconds=null`. Trading behavior was correct; only causal audit
classification was misleading.

OWNER-APPROVED stabilization repair:
- propagate the exact cancellation cause from private runtime into the
  reservation lifecycle resolver;
- use `ENTRY_CANCEL_CONFIRMED_ZERO_FILL:<actual cause>` for reservation state
  reason, slot release reason and `REQUEST_CANCELLED` event;
- historical rows are not rewritten;
- this changes audit truth only and does not change R1 Entry/Exit policy.

Safety state before this repair/deploy:

```text
mainnet gate             = OFF
R1 execution permissions = 0
ACTIVE LIVE-arm sessions = 0
real positions           = 0
active Exchange orders   = 0
```

A new deploy requires a separate exact Git/runtime identity. Re-arm after that
deploy is a separate owner action. Full `Entry -> fill -> protected position ->
dynamic Exit -> close -> economics after fees` remains NOT YET RUNTIME BEHAVIOR
VERIFIED.


# 29. R1 causal cancellation repair deployed — pre-publication CHECKED HERE 2026-10-07

The stabilization repair described in §28 was published and deployed as exact
application release:

```text
APPLICATION_RELEASE = cb364c2944a13dffc68067c0a3c13c5f58999efc
SOURCE_HEAD         = cb364c2944a13dffc68067c0a3c13c5f58999efc
INSTALLED_COMMIT    = cb364c2944a13dffc68067c0a3c13c5f58999efc
LOADED_COMMIT       = cb364c2944a13dffc68067c0a3c13c5f58999efc
TOOLING_COMMIT      = cb364c2944a13dffc68067c0a3c13c5f58999efc
```

Installer/package identity, exact release payload, runtime/schema preflights and
`DEPLOY_EXACT_VERIFIED_COMMIT` all passed. Loaded code now propagates the actual
Strategy cancellation cause into
`ENTRY_CANCEL_CONFIRMED_ZERO_FILL:<actual cause>`. Historical evidence is not
rewritten. The new value has implementation/test/deploy evidence; a natural
post-deploy zero-fill event carrying it has not yet occurred.

Post-deploy safety snapshot:

```text
mainnet gate             = OFF
R1 execution permissions = 0
ACTIVE LIVE-arm sessions = 0
real positions           = 0
active Exchange orders   = 0
open lifecycle faults    = 0
```

The earlier R1 `1.1-micro-live` evidence remains valid: the first natural REAL
order reached Bybit with initial SL `-10%` and price-based opposite-inner TP,
then was cancelled under `R1_EXACT_ENTRY_LEVEL_CHANGED` before fill.

Current status:
```text
R1 1.1 implementation                  = IMPLEMENTED
causal zero-fill audit repair           = DEPLOYED
runtime liveness after repair            = VERIFIED
new audit reason natural runtime event   = NOT YET OBSERVED
full REAL fill/protection/Exit cycle      = NOT YET RUNTIME BEHAVIOR VERIFIED
LIVE                                     = DISARMED
```

This is explicitly a pre-publication checkpoint. Publishing this documentation
revision necessarily advances GitHub/source identity beyond `cb364c...`.
Before any subsequent real-arm, the final documentation commit must itself be
synchronized/deployed so REMOTE/SOURCE/INSTALLED/LOADED identity is exact; that
final equality is verified externally rather than self-referenced by an SHA in
this document.


# 30. Bybit time-calibration recurrence + operator alarm — CHECKED HERE 2026-10-07

Incident evidence before repair:

```text
host NTP                  = enabled / synchronized
mainnet gate              = OFF
gate reason               = restart: owner re-arm required
real positions            = 0
active Exchange orders    = 0
open CRITICAL fault       = ENTRY_EXECUTION_AMBIGUOUS
private runtime restart   = 1
```

The triggering Entry attempt was blocked before any Exchange mutation because
a single fresh midpoint observation reported approximately `502.5 ms` host
vs Bybit offset while the old mutation limit was `500 ms`. The old
`UnsafeBybitClock` class inherited `ExchangeMutationBarrier`, therefore a
pre-send clock refusal was incorrectly classified as ambiguous, disarmed Entry
and terminated the private runtime.

A separate earlier Bybit mutation was rejected with explicit `retCode=10002`
because the signed request timestamp drifted outside the Exchange receive
window. This exposed the class-wide defect: the old V32 implementation checked
Bybit offset before POST but then still signed the POST with uncorrected host
wall-clock time.

OWNER DECISION / canonical repair:
- authenticated mutation timestamp derives from fresh Bybit Time Calibration;
- local wall-clock offset is diagnostic, not the signed mutation authority;
- current 500 ms safety intent is preserved as midpoint uncertainty: accepted
  time-probe RTT must be <= 1000 ms;
- up to three read-only time probes may be used to obtain a sufficiently sharp
  calibration before any POST;
- POST itself is never automatically retried;
- timestamp-rejected signed GET may perform its existing safe retry using fresh
  calibration;
- private WebSocket authentication also uses Bybit-calibrated time;
- a failed pre-mutation calibration follows
  `PRE_MUTATION_SAFETY_BLOCK`, disarms Entry and does not create an ambiguous
  mutation or force process restart;
- only true post-send uncertainty uses `EXCHANGE_MUTATION_BARRIER`.

Operator UX repair in the same changeset:
- routine accepted/unfilled Entry orders remain in `Лог Bybit`;
- gate `OPEN -> OFF` and new open `CRITICAL` lifecycle fault queue one alarm
  sound if Dashboard audio is enabled;
- alerting works independently of which trading subpage is selected.

This changes transport safety and operator visibility only. Trading policy,
R1 Strategy version, Entry/Exit geometry, initial SL/TP, stake and leverage are
unchanged.

At authoring time LIVE remains DISARMED and the existing
`ENTRY_EXECUTION_AMBIGUOUS` fault remains open. Patch/deploy must not re-arm
LIVE as a side effect.

# 31. Bybit time-calibration deployment + safety-observer import repair — CHECKED HERE 2026-10-07

The canonical time-calibration changeset was published and deployed after exact
tests. Targeted clock/alarm/governance gate: `60/60 PASS`. Full pytest:
`1622 PASS / 65 SKIP / 7 exact baseline FAIL / NEW_FAILURES=0`.

Deployment finding:
the first `7b2eebc...` runtime cutover exposed a packaging/import defect in
`cripta-safety-observer.service`. The service executes
`research/server/connectivity/safety_observer.py` directly and does not receive
the application `PYTHONPATH`; therefore the newly shared
`bybit_workbench.exchange.bybit.time_calibration` module was not importable.
The repair makes the observer derive its own exact release root from
`__file__` and prepend only that release's `src/` directory before importing
the shared calibration module. A regression test executes the observer script
without `PYTHONPATH`, matching systemd import semantics.

Post-repair verified state before any new real-arm:

```text
GitHub/source/runtime/tooling/state/dashboard = exact release composition
mainnet gate                                = OFF
R1 execution permissions                    = 0
ACTIVE LIVE-arm sessions                    = 0
real positions                              = 0
active Exchange orders                      = 0
open lifecycle faults                       = 0
private runtime                             = connected / authenticated-locked
universal observer                          = RUNNING / observer_ready=true
```

The old clock incident's `ENTRY_EXECUTION_AMBIGUOUS` was reclassified only
after exact evidence proved that no Exchange mutation had been sent:
the command had no Exchange result/order id, no execution/order-history record,
and Exchange was flat. Its CapitalReservation and physical slot claim were
transitioned from `RECONCILIATION_REQUIRED` to `RELEASED` with reason
`PRE_MUTATION_CLOCK_BLOCK_RECLASSIFIED_NO_EXCHANGE_MUTATION:<release>`;
Lifecycle Supervisor then resolved the CRITICAL fault.

Five read-only post-deploy Bybit Time Calibration samples succeeded with
acceptable probe RTT and produced Exchange-calibrated timestamps even while the
diagnostic host-vs-Bybit offset varied materially. No new clock/timestamp error
was observed in private-runtime journal during this checkpoint.

Dashboard operational alert contract is loaded in the same exact release:
- one alarm on mainnet gate transition `OPEN -> OFF`;
- one alarm for each newly observed open CRITICAL lifecycle fault;
- routine accepted/unfilled Entry orders remain only in `Лог Bybit`;
- browser audio must already be enabled/unlocked by the operator.

LIVE remains deliberately DISARMED. Re-arm requires a fresh exact-release
readiness cycle and separate owner approval. Full
`Entry -> fill -> Exchange protection -> dynamic Exit -> close -> economics
after fees` behavior is still NOT YET VERIFIED.

# 32. R1 terminal Entry cancellation lifecycle repair — CHECKED HERE 2026-10-08

Pre-publication production finding on exact loaded release
`c5f3fc99fc894c5eddb40a53f87e8ea3ac6e999a`: after owner-approved R1 re-arm,
an `ARBUSDT LONG` PostOnly Entry was acknowledged by Bybit and then reached
`orderStatus=Cancelled` with `rejectReason=EC_PostOnlyWillTakeLiquidity`,
`cumExecQty=0` and no execution/position. Exchange was therefore flat, but the
Universal Entry CapitalReservation remained `PENDING_EXCHANGE_REFLECTION` and
the physical slot claim remained `CLAIMED`.

The stale claim caused subsequent ARBUSDT attempts to receive the canonical
`EXCHANGE_POSITION_OWNERSHIP_CONFLICT` EntryDecision. A second implementation
defect then converted that normal fail-closed outcome into an observer runtime
error: `NotificationKind` still exposed
`EXCHANGE_POSITION_OWNERSHIP_CONFLICT`, while the current
`strategy_entry.notifications.kind` storage CHECK intentionally does not allow
that token. Repeated attempts therefore produced durable
`UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR / CheckViolation` faults.

Canonical classification was already correct and is unchanged:
`EXCHANGE_POSITION_OWNERSHIP_CONFLICT` is an EntryDecision outcome, not a
lifecycle fault or operational notification kind. Analyst counterfactual
capture remains allowed for that decision.

Implementation repair prepared in an isolated worktree:
- terminal private Bybit order events are persisted to
  `runtime.exchange_order_history` before removal from `runtime.hot_orders`;
- `Cancelled / Rejected / Deactivated` Universal Entry terminal events are fed
  through the existing reservation reconciliation helper;
- exact `Cancelled + cumExecQty=0 + no execution` releases both reservation and
  slot with `ENTRY_CANCEL_CONFIRMED_ZERO_FILL:<actual Exchange cause>`;
- partial/uncertain terminal outcomes remain fail-closed in reconciliation;
- `EXCHANGE_POSITION_OWNERSHIP_CONFLICT` is removed from `NotificationKind` and
  is no longer written to `strategy_entry.notifications`;
- Analyst counterfactual for ownership conflict is built directly from the
  canonical EntryDecision and exact Strategy/plan lineage;
- PAPER/REAL parity attestation is re-bound only after the updated deterministic
  Entry/Exit/reverse contour passed.

Pre-publication test evidence:
```text
targeted ownership/counterfactual/lifecycle/schema = 92 PASS
R1 Entry/Exit/reverse parity contour               = 161 PASS
post-attestation targeted gate                     = 50 PASS
disposable PostgreSQL exact zero-fill/partial gate = 3 PASS
full pytest                                        = 1623 PASS / 67 SKIP
pre-existing Dashboard/U6 baseline failures        = 7
NEW_TEST_FAILURES                                   = 0
Ruff NEW_DIAGNOSTICS                               = 0
mypy baseline diagnostics                          = patch diagnostics
```

Safety state during authoring:
```text
mainnet gate              = OFF
R1 execution permissions  = 0
ACTIVE LIVE-arm sessions  = 0
real positions            = 0
active Exchange orders    = 0
stale ARBUSDT slot        = still present pending post-deploy cleanup
observer CheckViolation faults = still open pending post-deploy cleanup
```

`DEPLOYED` and post-repair `RUNTIME BEHAVIOR VERIFIED` are NOT claimed by this
pre-publication checkpoint. The approved completion path is exact GitHub
publication -> verified deploy while DISARMED -> deterministic stale-slot/fault
cleanup using Exchange-flat evidence -> fresh readiness -> explicit owner-
approved re-arm.


# 33. R1 pending Entry ATR parity / causal cancel-intent repair — CHECKED HERE 2026-10-08

FINDING on exact production release `5448939da34d1219abd938043b2601e8296bc560`:
- natural INJUSDT SHORT signal at `2026-10-08 07:22:58.945 UTC` was ACCEPTED, dispatched and acknowledged by Bybit (retCode=0);
- zero-fill Entry was cancelled at `07:23:16 UTC` without a 5m closed-candle structural transition;
- observer's rolling R1 history had 207 closed 5m bars and ATR200
  `0.03140903399481686819609375`; private REST validity used up to 239 closed
  bars from a 240-kline response and got another ATR200 seed;
- both computations preserved `range_low=7.227` and `range_high=7.45`,
  but exact inner Entry differed and caused false `R1_EXACT_ENTRY_LEVEL_CHANGED`;
- the terminal WebSocket event resolved the reservation/slot as
  `ENTRY_CANCEL_CONFIRMED_ZERO_FILL:BYBIT:EC_PerCancelRequest`, losing
  the originating Strategy cause because of the async cancel/reconcile race.

Prepared in isolated GitHub branch
`fix/r1-entry-atr-cancel-20261008` (not current `main`):

1. Entry command now carries the exact observer-derived closed-candle
   `history_limit` from the immutable R1 watch policy (207 for current R1);
   private validity computes Wilder ATR on that same trailing history window,
   and fails closed if required history is absent.
2. `runtime.entry_cancel_intents` records exact Strategy cancel intent and
   commits it before the Bybit cancel request. Terminal private WS resolution
   uses that durable reason if present, otherwise the actual `BYBIT:*` cause.
3. New idempotent `20261008_r1_entry_cancel_intents.sql` migration is
   registered in the canonical exact-release installer.
4. R1 Entry/Exit/initial protection/offset/SL/TP/ping-pong Strategy policy
   and immutable StrategyCards are unchanged.

Isolated test evidence:
- causal ATR REST/observer regression and fail-closed tests PASS;
- direct cancel-intent-before-Exchange mutation test PASS;
- targeted R1/bridge/private-runtime suite: `51 PASS`;
- full suite: `1629 PASS / 68 SKIP / 7 existing Dashboard/U6 baseline FAIL`;
- Ruff `src tests`: `25` diagnostics, equal to current `main`;
- patched legacy operations/tests Ruff diagnostics: `94`, equal to `main`;
- mypy 1.19.1: `46` diagnostics, equal to `main` in isolated reduced dependency environment;
- standalone disposable PostgreSQL migration parse/create/grant PASS;
  disposable test DB cleaned afterward;
- exact production deployment, full PostgreSQL lifecycle behavior,
  post-deploy loaded runtime and natural new order: NOT CHECKED HERE.

Safety/deployment status at this checkpoint:

```text
BRANCH_PATCH_IMPLEMENTED      = YES
PATCH_TESTED_IN_ISOLATION     = YES (with baseline caveats)
REMOTE_MAIN_UPDATED           = NO
INSTALLED_COMMIT_UPDATED      = NO
LOADED_COMMIT_UPDATED         = NO
RUNTIME_BEHAVIOR_VERIFIED     = NO
MAINNET_GATE                  = ON [read-only check]
EXCHANGE_HOT_POSITIONS        = 0  [read-only check]
EXCHANGE_HOT_ORDERS           = 0  [read-only check]
OPEN_LIFECYCLE_FAULTS         = 0  [read-only check]
DEPLOY                         = BLOCKED while armed
RE_ARM                         = FORBIDDEN without separate owner approval
```

The owner requested stabilization; no new trading Strategy version is
authorized, and no code is promoted to `main` or deployed while the live
gate remains armed. Complete release only under the canonical disarm ->
exact commit/test/package/backup/deploy/runtime evidence chain, with no
automatic real re-arm.


## 34. R1 Observer timestamp / Entry structural-lifetime repair (2026-10-08)

OWNER DECISION: восстановить исходный структурный критерий valid resting R1 Entry;
ATR200 отвечает за ширину зоны, но не является отдельным условием отмены.
В отдельной ветке `fix/r1-observer-capture-structural-entry-20261008`
исправлено время counterfactual capture и сравнение frozen L5-3 structural extrema.
Первичные регрессионные tests: 28 PASS в изолированном checkout;
production runtime по этому changeset **NOT DEPLOYED**.
При проверке серверного preflight наблюдались биржевая позиция (1),
условные ордера (2), queued commands (3), execution permissions ON (5),
mainnet gate OFF. Это blocker штатного installer; все pending operational
объекты должны быть сверены независимо от заявления о ручном управлении.


## 35. Prepared release-rail independence (2026-10-08)

Isolated PR #50 branch prepares an installer that does not require flat Bybit,
zero pending commands, disabled Strategy execution permissions, or disarmed
mainnet gate. Installer does not close active live-arm sessions and observes
post-start private runtime reconciliation (Exchange -> DB). Full production
installation remains **NOT DEPLOYED** pending exact release verification.

## 36. Release-bound LIVE-arm mismatch and missing operator banner — OWNER DECISION 2026-10-09

CHECKED HERE (pre-implementation forensic): production checkpoint showed loaded
application release `78ac93a1fa67c5ab6842cd6552ae1547c5ba2aea` but five
ACTIVE LIVE-arm sessions bound to
`1c78c722f38c78d27b16822480daa48839853e39`, while mainnet gate
was OPEN. R1 Entry decisions repeatedly returned
`REAL_EXECUTION_SELECTED_BUT_NOT_ARM_READY`. Systemd services were active,
but this was not real Entry readiness. The existing Dashboard alarm was wired
to gate OPEN->OFF and newly opened CRITICAL lifecycle faults, not to
release-bound consumer admission mismatch; therefore neither visual persistent
release CRITICAL nor an audible gate-transition alarm was generated.

Owner-approved repair semantics: see DEVELOPMENT_RELEASE §46. Inhibit new REAL
Entry during application release switch, without denying deployment for any
positions/orders/queued commands; preserve Exit and Bybit-to-DB reconciliation.
After release change, reject stale-session Entry until fresh owner-approved
exact-release arm. Add durable independent admission-health CRITICAL and
persistent red portal banner, not sound-only and not dependent on new signals.

This is a dated decision/evidence checkpoint, **not** proof of implementation,
Git publication to main, deployment or successful runtime behavior.

# 34. Per-symbol self-normalized CoinMarketContext — OWNER DECISION 2026-10-15 / CHECKED HERE 2026-10-16

OWNER DECISION:
- состояние монеты оценивается относительно причинной истории **этой же монеты**;
- absolute USD liquidation/price thresholds не являются универсальным
  cross-symbol критерием;
- MAYAK сохраняет objective physical facts, Dispatcher публикует структурированный
  per-symbol context, а торговая интерпретация остаётся только в exact Strategy
  Context Policy;
- vector dimensions имеют приоритет; scalar `CoinMarketRating` до отдельного
  research/formula approval отсутствует;
- первая цель — observation/replay/research. Эта ревизия не меняет R1 Entry/Exit,
  H9, capital/leverage, protection, LIVE-arm или Exchange mutation.

Pre-publication baseline, CHECKED HERE 2026-10-16:

~~~text
GitHub main / SOURCE_HEAD    = 0125f7119569e59331c85ec01644f55337239858
source checkout              = clean main, same SHA
INSTALLED_COMMIT             = 4a9795be8fa67e18e79cedd81190e7db422e43ac
runtime/current target       = /srv/cripta/runtime/releases/4a9795be8fa67e18e79cedd81190e7db422e43ac
LOADED_COMMIT                = NOT CHECKED HERE
DASHBOARD_UI_COMMIT          = 0125f7119569e59331c85ec01644f55337239858
integration worktree branch  = feature/per-symbol-coin-market-context-integration-20261016
production deploy            = NOT PERFORMED
~~~

The integration branch is based directly on current `0125f711...` main, so all
current Dashboard changes are inherited unchanged. `operations/dashboard/**`
has zero diff in this changeset.

Implementation findings/evidence:
- `LiveMayakEngine` already emitted per-symbol price, flow, OI/funding/mark/index,
  order-book and exact-liquidation facts where source was available;
- existing per-symbol liquidation context already had a narrow current
  60-minute nonzero self-baseline for total liquidation intensity
  (`p50/p90/p99` + normalization to p50), but it was not a general versioned
  multi-dimension `CoinSelfBaselineEvidence` contract;
- this changeset adds scale-aware per-symbol `stress_inputs` while preserving
  raw facts: liquidation/OI ratios, liquidation/derivatives-turnover ratios,
  directional price response, OI clearance and side-specific liquidation
  speed/acceleration;
- exact Bybit `allLiquidation.S` semantics were rechecked against the official
  provider contract: current `Buy -> LONG_LIQUIDATED`,
  `Sell -> SHORT_LIQUIDATED`; collector passes the raw provider side into MAYAK;
- MAYAK coin context schema advances to `coin-market-context-v2`,
  engine/feature versions to `mayak-v2.3` / `objective-coin-context-v3`;
- Dispatcher preserves the new objective vector and advances only its per-coin
  context version to `dispatcher-coin-context-v2.2`; scalar
  `CoinMarketRating` remains `NOT_IMPLEMENTED`;
- current implementation intentionally leaves `self_baseline` as
  `RESEARCH_NOT_APPROVED` and multi-venue confirmation as `NO_DATA` until
  exact research/source support exists;
- prior remote feature commit `cd1427ed1877e9bf014f3f0d35bf48f5659a2b31`
  is **SUPERSEDED / NOT A RELEASE BASELINE** because its published
  `live.py` was truncated after pre-publication tests. The current integration
  was reconstructed from exact `0125f711...` main and revalidated.

Capability-conservation matrix:

| CAPABILITY | BEFORE OWNER/STATUS | AFTER OWNER/STATUS | MIGRATION |
| --- | --- | --- | --- |
| objective market observation | MAYAK / IMPLEMENTED | MAYAK / CONSERVED + enriched | additive |
| per-symbol structured context | Dispatcher / IMPLEMENTED | Dispatcher / CONSERVED + enriched | schema/versioned |
| Strategy-specific interpretation | Strategy Context Policy / CANON | unchanged | none |
| Entry/Exit mutation rights | Strategy/Engines/Execution | unchanged | none |
| scalar CoinMarketRating | no approved formula | still NOT APPROVED | research required |

Changeset status before GitHub publication:

~~~text
CURRENT MAIN BASELINE               = PASS / 0125f711...
DASHBOARD DELTA IN CHANGESET        = NONE
CANON UPDATE                        = PREPARED IN CHANGESET
MAYAK OBJECTIVE ENRICHMENT          = IMPLEMENTED IN CHANGESET
DISPATCHER CONTEXT ENRICHMENT       = IMPLEMENTED IN CHANGESET
OBSERVATION REPLAY PARITY           = TARGETED TEST PASS
TARGETED MAYAK/DISPATCHER TESTS     = 44 passed
TARGETED + GOVERNANCE FINAL GATE    = 73 passed
RUFF CHANGED CODE                   = PASS
MYPY CHANGED SOURCE                 = PASS / 0 issues
FULL ISOLATED TEST SUITE            = 1794 passed / 70 skipped / 0 failed
SELF-BASELINE RESEARCH              = NOT YET RUN
COINMARKETRATING FORMULA            = NOT APPROVED
STRATEGY CONTEXT POLICY TRADING USE = NOT AUTHORIZED
DEPLOY / RUNTIME CHANGE             = NONE
~~~

Publication of this changeset may advance GitHub/source identity. Per INDEX
§17.5 the final commit SHA is verified externally and is not self-referenced
inside this document. No deploy or LIVE/re-arm follows from source publication.

# 37. Multi-account / multi-venue identity gap — OWNER DECISION / CHECKED HERE 2026-10-16

Pre-publication source authority checked against GitHub main:
f3a84de0682c2902463b230e057ea4e02fab3877.

Owner-approved target model is now documented by ARCH §49, TRADING_CONTOUR §7,
OBSERVATION_ANALYTICS §7 and GLOSSARY §33.

## 37.1 Current production/source findings — READ-ONLY forensic 2026-10-16

Current implementation remains single-account oriented and is **not**
multi-account ready.

Verified PostgreSQL/source findings:

- runtime AccountStateGeneration currently uses account_ref BYBIT:UNIFIED;
- runtime.position_mode_states likewise scopes current mode rows to
  exchange=BYBIT, account_ref=BYBIT:UNIFIED;
- dispatcher_v2.trading_capacity_snapshots currently reports one
  source_account_ref runtime.wallet_latest:1;
- current position-key builder emits
  BYBIT:UNIFIED:LINEAR:USDT:<symbol>:<positionIdx>;
- universal Entry consumer explicitly rejects an account_ref different from the
  single CURRENT_ACCOUNT_REF;
- runtime.hot_positions primary identity is symbol + position_idx and contains
  no explicit account identity;
- runtime.hot_orders primary identity is order_id and current rows contain no
  explicit trading_account_id;
- runtime.executions primary identity is exec_id and current rows contain no
  explicit trading_account_id;
- runtime.position_ownership has account_ref for newer lifecycle rows, but
  historical rows may be NULL and must not be silently assigned to a future
  subaccount;
- no durable StrategyExecutionBinding object currently maps a
  StrategyActivation to an exact TradingAccount before admission.

These are FINDING / CURRENT LIMITATION, not permission to change schema.

## 37.2 Current market/account separation finding

MAYAK/Dispatcher public market context is already conceptually
strategy-agnostic and must not be multiplied by future subaccounts. Current
large market tables and research links have real FK/consumer dependencies, so
they cannot be purged before replacement analytics/replay consumers are proven.

Private/account tables form a separate admission/recovery chain including
AccountStateGeneration, position-mode evidence, capital reservations, physical
slot claims, position ownership, private orders/executions and protection.

## 37.3 Implementation status after this documentation cycle

~~~text
IDENTITY_MODEL_CANON              = YES
MULTI_ACCOUNT_IMPLEMENTED         = NO
MULTI_VENUE_EXECUTION_IMPLEMENTED = NO
STRATEGY_ACCOUNT_BINDING          = NOT IMPLEMENTED
DB_SCHEMA_MIGRATION               = NOT STARTED
DATA_RETENTION_CUTOVER            = NOT STARTED
DATA_DELETE                       = NO
LIVE_BEHAVIOR_CHANGED             = NO
~~~

Implementation must follow DEVELOPMENT_RELEASE §48. No legacy identity is
rewritten by this documentation revision.

## 37.4 Historical R1 deployed / R2-R4 source-prepared checkpoint — CHECKED HERE 2026-10-16

Pre-publication source/runtime snapshot:

~~~text
REMOTE_HEAD                     = c4a4f1bbf2ff4bef3ab1ed93edf0b8d1ce05d677
SOURCE_HEAD                     = c4a4f1bbf2ff4bef3ab1ed93edf0b8d1ce05d677
INSTALLED_COMMIT                = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
LOADED_COMMIT                   = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
OPERATIONAL_DELTA_COMMIT(S)     = NONE
~~~

`LOADED_COMMIT` is evidenced by active service process paths under
`/srv/cripta/runtime/current` resolving to release `88216254...`, together with
successful post-restart reconciliation and runtime schema validation.

R1 exact status:

~~~text
IDENTITY_REGISTRY_SCHEMA        = DEPLOYED
RUNTIME_SCHEMA_CONTRACT         = PASS / runtime-schema-2026-10-16-identity-r1
identity.venues                 = PRESENT / EMPTY
identity.market_sources         = PRESENT / EMPTY
identity.instruments            = PRESENT / EMPTY
identity.trading_accounts       = PRESENT / EMPTY
identity.account_connectors     = PRESENT / EMPTY
R1 BRIDGE COLUMNS               = PRESENT / ALL NULL
HISTORICAL BACKFILL             = NONE
DATA DELETE                     = NONE
~~~

The R1 release was installed through the exact verified release installer with
a major-structure CONTROL_CHECKPOINT. The rollback backup is recorded by
`/var/lib/cripta/release/LAST_BACKUP`. Runtime services recovered and the latest
Exchange-to-DB reconciliation was successful.

Release-bound trading safety after deployment:

~~~text
MAINNET_GATE                    = 0
GATE_REASON                     = RELEASE_BOUND_LIVE_ARM_NOT_READY
OWNER RE-ARM                    = NOT PERFORMED
~~~

Two stale old-release sessions (`DOTUSDT`, `LTCUSDT`) were automatically retired
with reason `EXACT_SCOPE_FLAT_AFTER_RELEASE_CHANGE`. Three old-release sessions
for currently non-flat scopes (`APTUSDT`, `ARBUSDT`, `INJUSDT`) remained ACTIVE
for lifecycle/Exit continuity while the global mainnet gate stayed closed.
This is fail-closed release behavior, not a new LIVE approval.

Source-only follow-up status:

~~~text
R2 CURRENT IDENTITY SEEDS       = IMPLEMENTED IN SOURCE / NOT DEPLOYED
R3 PROVEN NEW TELEMETRY IDS     = IMPLEMENTED IN SOURCE / NOT DEPLOYED
R4 LEGACY_UNKNOWN BOUNDARY      = IMPLEMENTED IN SOURCE / NOT DEPLOYED
R5 ACCOUNT-SCOPED CURRENT STATE = NOT IMPLEMENTED
R6 STRATEGY EXECUTION BINDING   = NOT IMPLEMENTED
R7 MARKET/INSTRUMENT CUTOVER    = NOT IMPLEMENTED
R8 COMPACT ANALYTICS CUTOVER    = NOT IMPLEMENTED
RETENTION / DELETE              = NOT STARTED
~~~

R2 intentionally registers no InstrumentIdentity yet. Current MAYAK coin context
combines spot and linear evidence under one symbol, while private position truth
belongs to a concrete LINEAR product. Product/instrument semantics must be
resolved in R7 rather than collapsed into one incorrect identity.

R3/R4 source contract:
- exact current TradingAccount/AccountConnector IDs are injected by systemd;
- private runtime validates the pair against the registry before normal startup;
- new AccountStateGeneration and position-mode rows are written as `PROVEN`;
- Dispatcher capacity rows carry the same exact account/connector IDs;
- old rows become explicit `LEGACY_UNKNOWN` through additive metadata-default
  semantics; no historical UPDATE/backfill is performed.

Current source gate after R3/R4 preparation:

~~~text
IDENTITY CONTRACT ASSERTS       = 13 PASS
GOVERNANCE ASSERTS              = 24 PASS
TRADING ARCH ASSERTS            = 5 PASS
PYTHON COMPILE                  = PASS (private_runtime/dispatcher/runtime_schema)
INSTALLER BASH SYNTAX           = PASS
FULL PYTEST FOR R3/R4           = NOT RUN / pytest unavailable in server runtime toolchain
~~~

The current account model remains single-account in production until the
account-scoped wallet/hot-state/execution keys and StrategyExecutionBinding are
implemented and verified. Presence of identity registry tables alone is not
multi-account readiness.

## 37.5 Current identity/account migration source checkpoint — CHECKED HERE 2026-10-16

This section supersedes §37.4 only for **current source implementation status**.
§37.4 remains historical evidence of the earlier R1 deploy checkpoint.

Pre-publication identity verified immediately before this documentation update:

~~~text
REMOTE_HEAD                     = 9b089c4530c700c95ce5061739ef26c6f61e112a
SOURCE_HEAD                     = 9b089c4530c700c95ce5061739ef26c6f61e112a
SOURCE_WORKTREE                 = CLEAN
INSTALLED_COMMIT                = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
LOADED_COMMIT                   = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
OPERATIONAL_DELTA_COMMIT(S)     = NONE CHECKED FOR THIS SOURCE-ONLY CYCLE
~~~

The source mirror and GitHub `main` were equal at this checkpoint. Production
application runtime remains on the earlier R1 release `88216254...`; the
following R2-R6 work is **not deployed** and is not runtime-verified.

Current implementation status:

~~~text
R1 IDENTITY REGISTRY            = DEPLOYED / RUNTIME VERIFIED

R2 CURRENT IDENTITY SEEDS       = IMPLEMENTED IN SOURCE / NOT DEPLOYED
R3 PROVEN NEW TELEMETRY IDS     = IMPLEMENTED IN SOURCE / NOT DEPLOYED
R4 LEGACY_UNKNOWN BOUNDARY      = IMPLEMENTED IN SOURCE / NOT DEPLOYED

R5 ACCOUNT-SCOPED CURRENT STATE = PARTIALLY IMPLEMENTED IN SOURCE
  REAL ENTRY SLOT KEY           = DONE IN SOURCE
  CRITICAL PRIVATE READERS      = DONE IN SOURCE
  RESTART/READINESS SCOPE       = DONE IN SOURCE
  HOT PRIVATE PROJECTIONS       = DONE IN SOURCE
  OWNERSHIP/ORPHAN/WORKER SCOPE = NOT YET IMPLEMENTED

R6 STRATEGY EXECUTION BINDING   = IMPLEMENTED IN SOURCE THROUGH R6b2
  R6a BINDING GATE              = DONE IN SOURCE
  R6b1 ATOMIC ENTRY LINEAGE     = DONE IN SOURCE
  R6b2 COMMAND/POSITION LINEAGE = DONE IN SOURCE
  DEPLOYED                      = NO
  RUNTIME VERIFIED              = NO

R7 MARKET/INSTRUMENT CUTOVER    = NOT IMPLEMENTED
R8 COMPACT ANALYTICS CUTOVER    = NOT IMPLEMENTED
RETENTION / DELETE              = NOT STARTED
PHYSICAL RECLAIM                = NOT STARTED
MULTI_ACCOUNT LIVE READY        = NO
~~~

Source evidence already published before this checkpoint includes:

~~~text
0dd822d6  capture exact account identity in private current state
5ef88d8f  bind durable exchange evidence to account identity
b85298b5  require Strategy execution binding before real admission
7ceb0123  carry Strategy binding through atomic Entry admission
ccb1fe67  carry Strategy binding into command and position lineage
9844fbf9  scope real Entry slots to TradingAccount
5f12d4c8  scope critical private readers to TradingAccount
916ec24e  scope private restart evidence to TradingAccount
9b089c45  scope private projections to TradingAccount
~~~

R5 transition rule at this checkpoint:

- new `PROVEN` private/account state is scoped by exact `TradingAccount`;
- old `LEGACY_UNKNOWN` command/execution/ownership evidence is retained only
  as an explicit transition path where required to recover or accompany
  already-existing legacy trade cycles;
- `LEGACY_UNKNOWN` is not multi-account proof and must not be treated as a
  permanent shared-account fallback;
- before legacy fallback retirement, R10/parity must prove that no active
  lifecycle/recovery dependency still requires it;
- historical rows are not rewritten merely to make them appear `PROVEN`.

No PostgreSQL migration after the deployed R1 release, data backfill, retention
cutover, DELETE, physical reclaim, service restart, Exchange mutation or
LIVE re-arm is performed by this documentation update.

Documentation maintenance gate for this revision:

~~~text
ACTIVE DOCUMENT SET             = UNCHANGED
DOCUMENT PATHS                  = UNCHANGED
BASE PRE-READ                   = UNCHANGED
ROUTING                         = UNCHANGED
PROJECT SOURCE FILE COUNT       = 11 / UNCHANGED
README / AGENTS                 = NO TOPOLOGY CHANGE / NO UPDATE REQUIRED
PROJECT INSTRUCTIONS TEMPLATE   = NO BOOTSTRAP CHANGE / NO UPDATE REQUIRED
SECTION RENUMBER                = NONE
OPEN ARCHITECTURE DECISIONS     = NOT SILENTLY CLOSED
TRADING BEHAVIOR                = UNCHANGED
~~~

## 37.6 Current R5/R6 source progress checkpoint — CHECKED HERE 2026-10-16

This checkpoint supersedes §37.5 only for current source implementation status.
No production deploy, PostgreSQL mutation, cleanup, retention cutover or LIVE
re-arm is performed by this documentation update.

Verified identities immediately before this checkpoint:

~~~text
REMOTE_HEAD                     = 760070cc6e0454db3704531f221e6f482aa6418f
SOURCE_HEAD                     = 760070cc6e0454db3704531f221e6f482aa6418f
SOURCE_WORKTREE                 = CLEAN
INSTALLED_COMMIT                = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
LOADED_COMMIT                   = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
DB / FILESYSTEM MIGRATION       = NOT PERFORMED IN THIS SOURCE CYCLE
DEPLOY                          = NOT PERFORMED
~~~

Current source progress:

~~~text
R5 ACCOUNT-SCOPED CURRENT STATE = SOURCE IMPLEMENTED THROUGH KEY PREPARATION / NOT DEPLOYED

DONE IN SOURCE:
  REAL ENTRY SLOT SCOPE
  CRITICAL PRIVATE READERS
  PRIVATE RESTART / READINESS EVIDENCE
  HOT PRIVATE PROJECTIONS
  OWNERSHIP / ORPHAN / WORKER PRIVATE EVIDENCE
  ENTRY RECONCILIATION EVIDENCE
  LEGACY EXIT PRIVATE EVIDENCE
  LIFECYCLE SUPERVISOR PRIVATE FACT SCOPE
  PROTECTION SUPERVISOR PRIVATE EVIDENCE
  DASHBOARD CONTROL / PRE-ARM PRIVATE SCOPE
  DASHBOARD LIVE READ-MODEL PRIVATE SCOPE
  ACTIVE PRIVATE CONSUMER / WRITER PARITY AUDIT
  REPORT / ARCHIVE / REPLAY PRIVATE PROVENANCE
  PHYSICAL ACCOUNT-SCOPED PK / UNIQUE PREPARATION

DEFERRED / LATER GATES:
  INACTIVE LEGACY WRITER RETIREMENT PROOF = R10
  OLD PHYSICAL PK / UNIQUE RETIREMENT = NOT YET / AFTER R9 PARITY + R10 RETIREMENT
  WRITER ON CONFLICT COMPOSITE-KEY CUTOVER = NOT YET
  R9/R10 PARITY AND LEGACY PATH RETIREMENT GATES

R6 STRATEGY EXECUTION BINDING   = SOURCE IMPLEMENTED THROUGH R6d / NOT DEPLOYED
  R6a REAL BINDING GATE         = DONE IN SOURCE
  R6b1 ATOMIC ENTRY LINEAGE     = DONE IN SOURCE
  R6b2 COMMAND/POSITION LINEAGE = DONE IN SOURCE
  R6c UNIVERSAL EXIT LINEAGE    = DONE IN SOURCE
  R6d COMMAND ACCOUNT PROOF     = DONE IN SOURCE

R7 MARKET/INSTRUMENT CUTOVER    = SOURCE IMPLEMENTED THROUGH R7b / NOT DEPLOYED
  R7a EXACT CURRENT INSTRUMENT REGISTRY = DONE IN SOURCE
  R7a MAYAK/DISPATCHER IDENTITY LINEAGE = DONE IN SOURCE
  R7a POSITION-MODE INSTRUMENT LINEAGE  = DONE IN SOURCE
  R7b ACTIVE/SCHEDULED READER CUTOVER    = DONE IN SOURCE
  R7b PUBLIC ARCHIVE PROVENANCE          = DONE IN SOURCE
  HISTORICAL PUBLIC ID BACKFILL         = NOT PERFORMED
  INACTIVE LEGACY CORRELATOR RETIREMENT = DEFERRED TO R10
  DB NON-NULL / LEGACY FIELD RETIREMENT = NOT YET / AFTER R9-R10 GATES
R8 COMPACT ANALYTICS CUTOVER    = DEPLOYED / RUNTIME VERIFIED THROUGH R8c
  R8a EXACT INSTRUMENT-MINUTE TABLE = DEPLOYED
  R8a MAYAK COMPACT DUAL-WRITE      = DEPLOYED
  R8a ARCHIVE / SCHEMA CONTRACT     = DEPLOYED
  R8b FEATURE COVERAGE / PARITY     = RUNTIME VERIFIED
  R8b SCHEDULED REPORT COMPACT READ = RUNTIME VERIFIED
  R8c MISSING COMPACT PRIMITIVES    = DEPLOYED
  R8c ARCHIVE RESEARCH READER       = SOURCE TESTED / PROD PARITY VERIFIED
  R8c SIGNAL ANALYSIS COMPACT READ  = DEPLOYED
  KNOWN ANALYTICAL PATH COVERAGE    = 233 / 233
  COMPACT REPLACEMENT READY         = YES FOR KNOWN ANALYTICAL PATHS
  HOT CURRENT JSONB CONTEXTS        = RETAINED BY DESIGN
  OLD JSONB / COIN_MINUTES RETIREMENT = NOT AUTHORIZED / R10-R11 GATES
R9 DUAL-READ / PARITY / RESTART = PASS / DEPLOYED / RUNTIME VERIFIED
  R9a READ-ONLY MIGRATION VERIFIER   = DEPLOYED
  NEW WRITER PATH                    = PASS
  NEW READER PATH                    = PASS
  ACCOUNT SCOPE PARITY               = PASS
  MARKET SOURCE PARITY               = PASS
  COMPACT ANALYTICS PARITY           = PASS / 13,980 PATH CHECKS
  RESTART / RECOVERY                 = PASS
  REPORT / REPLAY / RESEARCH         = PASS
  BACKUP / ROLLBACK                  = PASS
  LEGACY REQUIRED READERS            = NOT YET / R10 GATE
  LIVE RE-ARM                        = NO
R10 CONSTRAINTS / LEGACY RETIRE = PARTIAL / R10a SOURCE IMPLEMENTED
  R10a NEW COIN_MINUTES WRITER       = RETIRED IN SOURCE / NOT DEPLOYED
  R10a REPORT LEGACY FALLBACK        = RETIRED IN SOURCE / NOT DEPLOYED
  R10a LEGACY_V1 ENTRY WRITER        = RETIRED IN SOURCE / NOT DEPLOYED
  R10a ACCOUNT-SCOPED CONFLICT KEYS  = CUT OVER IN SOURCE / NOT DEPLOYED
  OLD GLOBAL PRIMARY KEYS            = RETAINED
  LEGACY REQUIRED READERS            = >0 / R10b REQUIRED
R11 RETENTION / DELETE          = NOT STARTED
R12 PHYSICAL RECLAIM            = NOT STARTED
MULTI_ACCOUNT LIVE READY        = NO
~~~

Recent source commits represented by this checkpoint:

~~~text
34588f84  scope private ownership evidence to TradingAccount
e17a0a28  carry TradingAccount through Universal Exit lineage
b2f9b1a0  separate command account proof from Strategy binding
46848c8b  scope Entry reconciliation evidence to TradingAccount
558a8516  scope legacy Exit evidence to TradingAccount
3a99cb77  scope Lifecycle Supervisor facts to TradingAccount
4d54a226  scope protection evidence to TradingAccount
760070cc  scope Dashboard controls to TradingAccount
63679739  scope Dashboard live state to TradingAccount
bf065c95  scope active private consumers to TradingAccount
c34f4102  scope stale-arm retirement to TradingAccount
9e867593  scope report/replay private provenance
cb1423df  scope residual private report readers
99278812  finish active private reader account scope
bee199d7  prepare account-scoped physical keys
4ae7e138  add R7 market/instrument identity lineage
ae54cfec  cut active market readers to exact identity
8b7c6b93  add compact market analytics foundation
aab41e26  add compact analytics coverage parity
3a0a93b4  complete compact analytics primitives
f76743bf  add R9 migration verification gates
768620d6  recover R9b runtime services
c705ae43  restore R9b report runtime imports
f65ca9b6  begin R10 legacy path retirement
~~~

R9b is deployed and runtime verified. R10a is now implemented in source only:
the new duplicate coin-minute writer, scheduled-report fallback and inactive
LEGACY_V1 Entry writer are retired, and private writer conflict targets use the
already prepared account-scoped unique indexes. R10 is still incomplete because
residual LEGACY_UNKNOWN readers/fallbacks and archive/Dashboard historical
references remain. R10b must prove and reduce that inventory before old primary
keys or fallback paths can be retired. R9/R10a source publication does not
authorize R10 production mutation, R11 deletion or LIVE re-arm.

Documentation maintenance gate:

~~~text
ACTIVE DOCUMENT SET             = UNCHANGED
DOCUMENT PATHS                  = UNCHANGED
BASE PRE-READ                   = UNCHANGED
ROUTING                         = UNCHANGED
PROJECT SOURCE FILE COUNT       = 11 / UNCHANGED
README / AGENTS                 = NO TOPOLOGY CHANGE / NO UPDATE REQUIRED
PROJECT INSTRUCTIONS TEMPLATE   = NO BOOTSTRAP CHANGE / NO UPDATE REQUIRED
TRADING BEHAVIOR                = UNCHANGED
~~~

## 37.7 Residual R5 audit findings — CHECKED HERE 2026-10-16

Current source audit after `bf065c95...` confirms:

- production `private_runtime` is pinned by its systemd drop-in to
  `CRIPTA_ENTRY_COMMAND_SOURCE=UNIVERSAL_ENTRY`; the old `LEGACY_V1`
  writer code remains present but is not the active production Entry writer;
- future R10 must still prove and retire that inactive legacy writer before
  `LEGACY_UNKNOWN` fallback removal and multi-account readiness;
- Universal Entry consumer request selection and pre-dispatch expiry cleanup
  are now limited to exact `trading_account_id` with
  `identity_status='PROVEN'`;
- private-runtime and Dashboard execution-slippage evidence now read only
  exact current-account `runtime.private_events` with `PROVEN` identity;
- the residual audit still has separate stale-arm/control and
  report/archive/replay provenance work; therefore R5 is not complete.

Separate pre-existing implementation finding discovered by a broad SQL static
audit:

~~~text
FINDING:
operations/connectivity/universal_entry_consumer.py
entry_plans lookup contains four SQL placeholders with a two-value parameter tuple.
~~~

This mismatch predates the account-scope changes above and is not silently
repaired inside the R5 identity changeset. It requires a separate bounded
implementation fix/test.

No deploy, PostgreSQL migration, data rewrite/delete, Exchange mutation or LIVE
re-arm is performed by this documentation checkpoint.


## 37.8 Report/archive/replay provenance checkpoint — CHECKED HERE 2026-10-16

Pre-publication source/runtime identity checked for this documentation cycle:

~~~text
REMOTE_HEAD             = 9927881213d4a547de89378147a95446e5548f54
SOURCE_HEAD             = 9927881213d4a547de89378147a95446e5548f54
SOURCE_WORKTREE         = CLEAN
INSTALLED_COMMIT        = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
LOADED_COMMIT           = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
DB MIGRATION APPLY      = NO
DEPLOY                  = NO
DELETE                  = NO
LIVE RE-ARM             = NO
~~~

Current source evidence after `9e867593...`, `cb1423df...` and `99278812...`:

- report/archive/replay consumers preserve private account provenance instead
  of silently attributing historical rows to the current TradingAccount;
- derived `research_context.dispatcher_v2_event_links` receives additive
  `trading_account_id` / `identity_status` source schema only; existing rows
  remain `LEGACY_UNKNOWN`, with no invented account backfill;
- `archive_v2` exports the current identity-bearing private evidence and records
  provenance counts while rejecting inconsistent `PROVEN` /
  `LEGACY_UNKNOWN` rows;
- active M3 / Dispatcher correlation reads keep exact private identity scope;
- historical `live_v1_session_audit` is explicitly
  `LEGACY_UNKNOWN_ONLY`;
- exact-close read-only audit partitions/matches private execution evidence by
  `identity_status + trading_account_id` and therefore cannot join one proven
  account's fills into another account's position;
- Universal Entry observer capacity and Dispatcher status fallback read only
  exact `PROVEN` capacity for the configured TradingAccount;
- reverse-transition position lookup prefers the exact current
  `PROVEN` TradingAccount and preserves an explicit `LEGACY_UNKNOWN` fallback
  without assigning legacy history to that account.

Universal Exit shadow remains a decision-only position consumer, not a
single-account private-state adapter. Its current global position scan is not
reclassified as an account-scope defect solely because more than one
TradingAccount may exist: exact StrategyPosition / Exit lineage already carries
or resolves `trading_account_id` and `ExecutionLeg`; Exchange
mutation/reconciliation must remain exact-account scoped under
`TRADING_CONTOUR §7.4`.

Residual private references under `research/**` were re-scanned. The only
remaining legacy writer is
`research/server/connectivity/private_runtime.py`; it is historical/inactive
and remains deliberately deferred to R10 retirement proof. It is not treated as
current writer authority and is not modified by this R5 cycle.

Verification evidence for the implementation commits:

~~~text
PY_COMPILE                         = PASS
TARGETED R5 CONTRACTS              = PASS
LATEST TARGETED SET                = 67 / 67 PASS
FULL PYTEST                        = 1875 PASS / 70 SKIPPED / 12 FAIL
BASELINE SAME 12 FAILURES          = CONFIRMED
NEW TEST FAILURES                  = 0
DIFF CHECK                         = PASS
NEW DESTRUCTIVE SQL                = NONE
OLD PK / UNIQUE RETIREMENT         = NOT PERFORMED
~~~

The 12 full-suite failures are pre-existing source/baseline findings in
unrelated R6/pre-arm/source-assertion tests and are not converted to PASS by
this checkpoint.

Separate pre-existing finding remains open and unchanged:

~~~text
operations/connectivity/universal_entry_consumer.py
entry_plans lookup contains four SQL placeholders with a two-value parameter tuple.
~~~

That finding is not an identity-policy decision and is not silently repaired
inside this provenance changeset.

Status of this R5 sub-gate:

~~~text
REPORT / ARCHIVE / REPLAY PRIVATE PROVENANCE = DONE IN SOURCE
ACTIVE PRIVATE READER / WRITER SCOPE AUDIT    = DONE IN SOURCE
INACTIVE LEGACY WRITER RETIREMENT             = DEFERRED TO R10
PHYSICAL ACCOUNT-SCOPED KEY PREPARATION       = NEXT
DEPLOYED                                      = NO
RUNTIME BEHAVIOR VERIFIED FOR NEW SOURCE      = NO
~~~

No deploy, PostgreSQL mutation, data rewrite/delete, Exchange mutation or LIVE
re-arm is performed by this documentation checkpoint.


## 37.9 Physical account-scoped key preparation checkpoint — CHECKED HERE 2026-10-17

Recovery after delivery timeout confirmed that the implementation publication
completed before the interruption:

~~~text
REMOTE_HEAD             = bee199d7c3c38cdc9bbe0b9fccea7cdd5382cf74
SOURCE_HEAD             = bee199d7c3c38cdc9bbe0b9fccea7cdd5382cf74
SOURCE_WORKTREE         = CLEAN
INSTALLED_COMMIT        = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
LOADED_COMMIT           = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
DB MIGRATION APPLY      = NO
DEPLOY                  = NO
DELETE                  = NO
LIVE RE-ARM             = NO
~~~

Read-only production PostgreSQL inspection confirms that the installed database
still has the legacy physical keys and does not yet contain the R5 identity
columns on these five current private tables:

~~~text
runtime.wallet_latest          PRIMARY KEY (singleton)
runtime.hot_positions          PRIMARY KEY (symbol, position_idx)
runtime.hot_orders             PRIMARY KEY (order_id)
runtime.executions             PRIMARY KEY (exec_id)
runtime.exchange_order_history PRIMARY KEY (order_id)

trading_account_id / account_connector_id / identity_status on those tables:
NOT INSTALLED IN CURRENT PRODUCTION DB
~~~

Source commit `bee199d7...` prepares, but does not activate, the later
multi-account physical-key cutover:

- adds additive account-scoped UNIQUE indexes for
  `wallet_latest`, `hot_positions`, `hot_orders`, `executions` and
  `exchange_order_history`;
- uses PostgreSQL `NULLS NOT DISTINCT` so the unresolved
  `LEGACY_UNKNOWN` physical identity remains singular instead of being
  multiplied by nullable account scope;
- adds fail-closed identity-consistency checks for new rows:
  `PROVEN` requires both TradingAccount and AccountConnector, while
  `LEGACY_UNKNOWN` requires both to remain null;
- keeps every legacy PRIMARY KEY intact;
- deliberately keeps current writers on the legacy
  `ON CONFLICT(singleton|symbol,position_idx|order_id|exec_id)` targets;
- extends runtime schema validation so the prepared account-scoped indexes must
  exist with the exact unique/null semantics before the new schema contract can
  validate;
- adds the preparation migration to the installer after R5 identity columns are
  introduced and before later StrategyExecutionBinding migrations.

Therefore this checkpoint does **not** claim that production is multi-account
physical-write ready. The old keys remain the final collision blocker until
R9 parity/restart/recovery evidence and R10 legacy-path retirement allow the
writer conflict-target and physical-key cutover.

Verification evidence for `bee199d7...`:

~~~text
TARGETED CONTRACT TESTS           = 34 / 34 PASS
PY_COMPILE                        = PASS
RUFF                              = PASS
INSTALLER BASH SYNTAX             = PASS
DIFF CHECK                        = PASS
FULL PYTEST                       = 1881 PASS / 70 SKIPPED / 12 FAIL
BASELINE SAME 12 FAILURES         = CONFIRMED
NEW TEST FAILURES                 = 0
MYPY                              = 6 PRE-EXISTING ERRORS / 0 NEW
DESTRUCTIVE SQL                   = NONE
OLD PK / UNIQUE RETIREMENT        = NOT PERFORMED
WRITER COMPOSITE CONFLICT CUTOVER = NOT PERFORMED
~~~

The separate pre-existing `universal_entry_consumer.py` SQL placeholder finding
remains open and unchanged; it was not mixed into this R5 physical-key
preparation.

Status after this checkpoint:

~~~text
R5 ACTIVE PRIVATE ACCOUNT SCOPE           = DONE IN SOURCE
R5 REPORT / REPLAY PROVENANCE             = DONE IN SOURCE
R5 PHYSICAL KEY PREPARATION               = DONE IN SOURCE
R5 PRODUCTION DB APPLY                    = NO
R6 STRATEGY EXECUTION BINDING             = SOURCE IMPLEMENTED THROUGH R6d
NEXT BOUNDED SOURCE STAGE                 = R7 MARKET/INSTRUMENT CUTOVER
MULTI_ACCOUNT LIVE READY                  = NO
~~~

No deploy, PostgreSQL mutation, data rewrite/delete, Exchange mutation or LIVE
re-arm is performed by this documentation checkpoint.


## 37.10 R7a MarketSource / InstrumentIdentity foundation — CHECKED HERE 2026-10-17

Pre-publication implementation/runtime identity for this checkpoint:

~~~text
REMOTE_HEAD             = 4ae7e138ade90247c2fb1d8e8d25a9aaf10c0f3e
SOURCE_HEAD             = 4ae7e138ade90247c2fb1d8e8d25a9aaf10c0f3e
SOURCE_WORKTREE         = CLEAN
INSTALLED_COMMIT        = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
LOADED_COMMIT           = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
DB MIGRATION APPLY      = NO
DEPLOY                  = NO
DELETE                  = NO
LIVE RE-ARM             = NO
~~~

Read-only current-source/runtime evidence used for R7a:

- current MAYAK consumes both Bybit public SPOT and LINEAR streams;
- all 20 currently configured MAYAK symbols were observed as supported on both
  SPOT and LINEAR;
- read-only Bybit instrument metadata reported the same 20 SPOT instruments as
  Trading and the same 20 LINEAR instruments as Trading / LinearPerpetual;
- current MAYAK CoinMarketContext intentionally combines SPOT and LINEAR facts,
  so R7a does not invent one singular InstrumentIdentity for that derived
  context.

Source commit `4ae7e138...` implements the bounded R7a foundation:

- preserves the owner-approved composite MarketSource
  `ms-bybit-mainnet-public-v1`; it is not silently split or redefined;
- registers 40 exact current InstrumentIdentity rows in source migration:
  20 SPOT + 20 LINEAR;
- stable current IDs distinguish product semantics, for example
  `inst-bybit-spot-btcusdt` and `inst-bybit-linear-btcusdt`;
- adds nullable additive MarketSource/InstrumentIdentity lineage to current
  MAYAK and Dispatcher public/derived tables;
- new MAYAK writes carry exact source identity and, for mixed coin contexts,
  explicit SPOT + LINEAR contributor identities;
- exact linear liquidations carry the LINEAR InstrumentIdentity;
- the canonical MAYAK -> Dispatcher handoff schema now requires
  `market_source_id` and contributor `instrument_lineage` for new source
  records;
- Dispatcher builders fail closed when the new source identity is absent and
  include it in immutable content identity/provenance;
- Dispatcher bootstrap consumes only new shared/coin source rows that actually
  carry the required identity lineage, while downstream symbol fields remain
  present for the staged cutover;
- new PROVEN `runtime.position_mode_states` source rows carry the exact
  LINEAR `instrument_id` in addition to the legacy symbol/product fields;
- historical rows are not assigned invented identities and receive no UPDATE
  backfill.

Rollback/cutover safety is intentionally staged:

- R7a identity columns remain nullable in PostgreSQL;
- FOREIGN KEY constraints are additive / NOT VALID for historical rows;
- R7a does **not** add new-row NOT-NULL CHECK constraints, because even a
  `CHECK ... NOT VALID` would still reject writes from the previous runtime
  during rollback;
- current `symbol` columns and symbol-based downstream consumers are not
  retired in R7a;
- DB non-null enforcement, residual reader cutover and legacy-field retirement
  require later parity/restart/recovery evidence.

Verification evidence:

~~~text
TARGETED R7a CONTRACT SET        = 49 / 49 PASS
MAYAK CANONICAL SCHEMA SET       = 21 / 21 PASS
PY_COMPILE                       = PASS
RUFF TARGETED                    = PASS
PRIVATE_RUNTIME RUFF BASELINE    = 66 FINDINGS / SAME RULE COUNTS / 0 NEW
SQL PLACEHOLDER STATIC CHECK     = PASS
INSTALLER BASH SYNTAX            = PASS
DIFF CHECK                       = PASS
FULL PYTEST                      = 1889 PASS / 70 SKIPPED / 12 FAIL
BASELINE SAME 12 FAILURES        = CONFIRMED
NEW TEST FAILURES                = 0
HISTORICAL PUBLIC ID BACKFILL    = NONE
DESTRUCTIVE SQL                  = NONE
~~~

The same separate pre-existing `universal_entry_consumer.py` SQL placeholder
finding remains open and unchanged; it was not mixed into R7a.

Status after R7a:

~~~text
R7a IDENTITY FOUNDATION / DUAL-WRITE LINEAGE = DONE IN SOURCE
R7 PRODUCTION DB APPLY                       = NO
R7 RUNTIME BEHAVIOR VERIFIED FOR NEW SOURCE  = NO
R7b READER CUTOVER / RESIDUAL PUBLIC AUDIT   = NEXT
R8 COMPACT ANALYTICS                         = NOT IMPLEMENTED
MULTI_ACCOUNT / MULTI-VENUE LIVE READY       = NO
~~~

Documentation maintenance gate remains unchanged:

~~~text
ACTIVE DOCUMENT SET             = UNCHANGED
DOCUMENT PATHS                  = UNCHANGED
BASE PRE-READ                   = UNCHANGED
ROUTING                         = UNCHANGED
PROJECT SOURCE FILE COUNT       = 11 / UNCHANGED
README / AGENTS                 = NO TOPOLOGY CHANGE / NO UPDATE REQUIRED
PROJECT INSTRUCTIONS TEMPLATE   = NO BOOTSTRAP CHANGE / NO UPDATE REQUIRED
TRADING BEHAVIOR                = UNCHANGED
~~~

No deploy, PostgreSQL mutation, historical rewrite/delete, Exchange mutation or
LIVE re-arm is performed by this documentation checkpoint.


## 37.11 R7b active/scheduled reader cutover checkpoint — CHECKED HERE 2026-10-17

Pre-publication implementation/runtime identity for this checkpoint:

~~~text
REMOTE_HEAD             = ae54cfecb2f3fb345ed08880ffdddb07504e720f
SOURCE_HEAD             = ae54cfecb2f3fb345ed08880ffdddb07504e720f
SOURCE_WORKTREE         = CLEAN
INSTALLED_COMMIT        = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
LOADED_COMMIT           = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
DB MIGRATION APPLY      = NO
DEPLOY                  = NO
DELETE                  = NO
LIVE RE-ARM             = NO
~~~

Source commit `ae54cfec...` completes the bounded R7b source reader cutover:

- Universal Entry objective-context reads now require the exact current
  `MarketSource` and exact LINEAR `InstrumentIdentity`; returned mixed
  CoinMarketContext lineage is verified against both expected SPOT and LINEAR
  identities before use;
- Universal Entry source_refs now preserve MarketSource and InstrumentIdentity
  provenance instead of exposing only legacy context IDs;
- Dispatcher V2 context correlator creates new immutable event links only from
  exact R7 market-source rows and resolves SPOT/LINEAR identities through the
  registry with an ambiguity fail-closed `HAVING count(*)=1` condition;
- existing immutable historical event links are not rewritten;
- MAYAK restart recovery restores only liquidation rows whose
  `market_source_id + instrument_id` resolve to the exact active LINEAR
  instrument metadata; legacy/no-identity liquidation rows are not injected
  into a new runtime context;
- Dashboard current shared-market context and causal signal exports use exact
  MarketSource/InstrumentIdentity lineage; old rows without identity are not
  silently treated as current exact context;
- archive_v2 now exports the Venue/MarketSource/InstrumentIdentity registry and
  records public-market provenance counts; complete exact rows are recorded as
  PROVEN, all-null historical rows as LEGACY_UNKNOWN, while partial identity is
  rejected;
- the active daily and weekly MAYAK report timers remain enabled, and their
  source now separates PROVEN and LEGACY_UNKNOWN coin-minute rows instead of
  grouping them together under one raw symbol; partial public identity is
  fail-closed.

Runtime/residual audit:

~~~text
cripta-mayak-v2.service                       = ACTIVE
cripta-dispatcher-v2.service                  = ACTIVE
cripta-dispatcher-v2-context-correlator       = ACTIVE
cripta-universal-entry-observer               = ACTIVE
cripta-mayak-v2-report.timer                  = ACTIVE / ENABLED
cripta-mayak-v2-weekly-report.timer           = ACTIVE / ENABLED
cripta-causal-context-correlator.service      = INACTIVE
~~~

The inactive legacy `operations/monitoring/causal_context_correlator.py`
remains a historical path and is not converted into current R7 authority.
Its retirement proof is deferred to R10 together with other legacy-path
retirement. This does not authorize reactivation.

Historical public rows remain unchanged:

~~~text
HISTORICAL PUBLIC ID UPDATE/BACKFILL = NONE
LEGACY_UNKNOWN PRESERVED             = YES
OLD SYMBOL COLUMNS RETIRED           = NO
DB NON-NULL ENFORCEMENT              = NO
~~~

Verification evidence:

~~~text
TARGETED R7b SET                     = 81 / 81 PASS
PY_COMPILE                           = PASS
RUFF TARGETED                        = PASS
UNIVERSAL ENTRY RUFF BASELINE        = SAME 2 FINDINGS / 0 NEW
DASHBOARD RUFF BASELINE              = SAME 42 FINDINGS / 0 NEW
SQL PARAMETER STATIC CHECK           = PASS
DIFF CHECK                           = PASS
FULL PYTEST                          = 1897 PASS / 70 SKIPPED / 12 FAIL
BASELINE SAME 12 FAILURES            = CONFIRMED
NEW TEST FAILURES                    = 0
DESTRUCTIVE SQL                      = NONE
~~~

Status after R7b:

~~~text
R7 SOURCE IMPLEMENTATION             = DONE THROUGH R7b
R7 PRODUCTION DB APPLY               = NO
R7 DEPLOY                            = NO
R7 RUNTIME BEHAVIOR VERIFIED         = NO
R9 PARITY / RESTART / RECOVERY       = NOT STARTED
R10 LEGACY RETIREMENT / ENFORCEMENT  = NOT STARTED
NEXT BOUNDED SOURCE STAGE            = R8 COMPACT ANALYTICS
MULTI_ACCOUNT / MULTI-VENUE LIVE READY = NO
~~~

Documentation maintenance gate remains unchanged:

~~~text
ACTIVE DOCUMENT SET             = UNCHANGED
DOCUMENT PATHS                  = UNCHANGED
BASE PRE-READ                   = UNCHANGED
ROUTING                         = UNCHANGED
PROJECT SOURCE FILE COUNT       = 11 / UNCHANGED
README / AGENTS                 = NO TOPOLOGY CHANGE / NO UPDATE REQUIRED
PROJECT INSTRUCTIONS TEMPLATE   = NO BOOTSTRAP CHANGE / NO UPDATE REQUIRED
TRADING BEHAVIOR                = UNCHANGED
~~~

No deploy, PostgreSQL mutation, historical rewrite/delete, Exchange mutation or
LIVE re-arm is performed by this documentation checkpoint.


## 37.12 R8a compact analytical dual-write foundation — CHECKED HERE 2026-10-17

Pre-publication implementation/runtime identity:

~~~text
REMOTE_HEAD             = 8b7c6b934b38eb424b37f15489b8ae208df81f4f
SOURCE_HEAD             = 8b7c6b934b38eb424b37f15489b8ae208df81f4f
SOURCE_WORKTREE         = CLEAN
INSTALLED_COMMIT        = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
LOADED_COMMIT           = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
DB MIGRATION APPLY      = NO
DEPLOY                  = NO
DELETE                  = NO
LIVE RE-ARM             = NO
~~~

Read-only current PostgreSQL sizing used to bound R8:

~~~text
dispatcher_v2.coin_market_contexts  ~= 3624 MB
mayak_v2.snapshots                  ~= 3536 MB
mayak_v2.coin_market_contexts       ~= 3286 MB
mayak_v2.coin_minutes               ~=  915 MB
~~~

These current large tables are evidence for the storage problem; R8a does not
delete, rewrite or backfill them.

Source commit `8b7c6b93...` adds the first compact analytical path:

- new canonical candidate table `analytics.market_minutes`;
- physical grain is exactly
  `MarketSource + InstrumentIdentity + observed_minute`;
- one mixed symbol row is split into exact SPOT and LINEAR instrument rows;
- no TradingAccount or Strategy dimension is present in the compact market
  grain;
- SPOT rows carry only spot flow/book/return facts and cannot carry LINEAR-only
  OI/funding/mark/index/positioning fields;
- LINEAR rows carry derivatives flow/book/return plus available OI, funding,
  last/mark/index and long/short ratio facts;
- quality/freshness/availability plus exact source versions and
  `config_fingerprint` are persisted without full JSONB payload duplication;
- denormalized `product_category + external_symbol` are DB-constrained to the
  exact InstrumentIdentity, so they cannot silently disagree with the identity
  registry;
- MAYAK dual-writes the new compact rows after the existing
  `mayak_v2.coin_minutes` write;
- write identity is idempotent on
  `market_source_id + instrument_id + observed_minute`;
- existing exact `mayak_v2.liquidations` remains the compact exact
  liquidation event family and is not duplicated into another R8 table;
- archive export and runtime schema validation now know the compact table;
- installer ordering applies the R8a migration after the R7 identity registry
  bridge and before service start.

R8a deliberately does not claim full analytical replacement. Current
CoinMarketContext/flow_context exposes additional rolling-horizon and
book-dynamics features. Before old JSONB/coin-minute paths can be retired, R8b
must inventory the exact long-horizon feature set required for replay/research,
prove compact-reader equivalence, and identify what is recomputable from minute
facts versus what must be added to the canonical compact representation.

Verification evidence:

~~~text
TARGETED R8a SET                  = 72 / 72 PASS
PY_COMPILE                        = PASS
RUFF TARGETED                     = PASS
WRITER SQL/ROW KEY PARITY         = 41 / 41 PASS
INSTALLER BASH SYNTAX             = PASS
DIFF CHECK                        = PASS
DESTRUCTIVE R8 SQL                = NONE
FULL PYTEST                       = 1902 PASS / 70 SKIPPED / 12 FAIL
BASELINE SAME 12 FAILURES         = CONFIRMED
NEW TEST FAILURES                 = 0
~~~

Status after R8a:

~~~text
R8a COMPACT DUAL-WRITE FOUNDATION = DONE IN SOURCE
R8 PRODUCTION DB APPLY             = NO
R8 DEPLOY                          = NO
R8 RUNTIME BEHAVIOR VERIFIED       = NO
R8b FEATURE COVERAGE / EQUIVALENCE = NEXT
R9 PARITY / RESTART / RECOVERY     = NOT STARTED
R11 RETENTION / DELETE             = NOT STARTED
OLD JSONB / COIN_MINUTE DELETE     = NOT AUTHORIZED
~~~

Documentation topology is unchanged: active document set, paths, base pre-read,
routing, README/AGENTS bootstrap and Project Source count remain unchanged.

No deploy, PostgreSQL mutation, historical backfill/rewrite/delete, Exchange
mutation or LIVE re-arm is performed by this checkpoint.


## 37.13 R8b compact feature coverage / parity checkpoint — CHECKED HERE 2026-10-17

Pre-publication implementation/runtime identity:

~~~text
REMOTE_HEAD             = aab41e267fcfab9925768ad11994176a60335914
SOURCE_HEAD             = aab41e267fcfab9925768ad11994176a60335914
SOURCE_WORKTREE         = CLEAN
INSTALLED_COMMIT        = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
LOADED_COMMIT           = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
DB MIGRATION APPLY      = NO
DEPLOY                  = NO
DELETE                  = NO
LIVE RE-ARM             = NO
~~~

Source commit `aab41e26...` establishes a machine-readable fail-closed R8b
coverage/equivalence contract rather than claiming premature analytical
replacement.

Current research/replay feature inventory represented by that contract:

~~~text
REQUIRED ANALYTICAL PATHS = 233
DIRECTLY STORED IN R8a    = 33
RECOMPUTABLE FROM R8a     = 18
UNCOVERED                  = 182
COMPACT REPLACEMENT READY = NO
~~~

Uncovered path families are explicit:

~~~text
price / returns             = 4
money / flow                = 118
liquidity / order-book      = 38
positioning                 = 7
relative strength           = 15
~~~

Exact liquidations remain a separate compact exact-event family in
`mayak_v2.liquidations`; they are not counted as missing merely because they
are not duplicated into `analytics.market_minutes`.

R8b source behavior:

- compact minute rows are now materialized from the already frozen
  `coin_market_contexts[*].payload`, not from an earlier mutable
  `snapshot["coins"]` view; this removes an intra-snapshot race/equivalence
  ambiguity between the compact writer and the canonical CoinMarketContext;
- compact schema-version marker advances to `compact-market-minute-v2`;
- a pure compact reader reconstructs every currently provable R8a field and
  deterministic derived value, including 5m net share/speed/large-trade share,
  current book imbalance and mark/index/last premium ratios;
- parity comparison checks the compact projection against the exact frozen
  CoinMarketContext payload and reports both mismatches and uncovered fields;
- replacement readiness is fail-closed while any required analytical path
  remains uncovered;
- current scheduled daily/weekly MAYAK report uses
  `analytics.market_minutes` for PROVEN public-market rows;
- the old `mayak_v2.coin_minutes` reader remains only as explicit
  `LEGACY_UNKNOWN` fallback for pre-cutover rows with all identity columns
  null;
- scheduled-report source coverage and coin statistics therefore no longer
  double-read new PROVEN rows from the legacy mixed-symbol table.

R8b does not fill the 182 gaps by approximation. In particular, higher-horizon
flow windows, prior-window flow needed for acceleration/turnover ratios, deep
book/depth dynamics, long-horizon OI dynamics and non-5m relative-strength
inputs remain explicit next-stage requirements.

Verification evidence:

~~~text
TARGETED R8b SET                  = 47 / 47 PASS
RUFF TARGETED                     = PASS
PY_COMPILE                        = PASS
REPORT SQL PARAMETER STATIC       = PASS
DIFF CHECK                        = PASS
FULL PYTEST                       = 1907 PASS / 70 SKIPPED / 12 FAIL
BASELINE SAME 12 FAILURES         = CONFIRMED
NEW TEST FAILURES                 = 0
~~~

Status after R8b:

~~~text
R8a FOUNDATION                    = DONE IN SOURCE
R8b COVERAGE / PARITY             = DONE IN SOURCE
R8 SCHEDULED REPORT PROVEN READER = COMPACT IN SOURCE
R8 COMPACT REPLACEMENT READY      = NO
R8c MISSING PRIMITIVES            = NEXT
R8c RESEARCH / REPLAY READERS     = NEXT
R9 DUAL-READ / RESTART / RECOVERY = NOT STARTED
R11 RETENTION / DELETE            = NOT STARTED
OLD JSONB / COIN_MINUTE DELETE    = NOT AUTHORIZED
~~~

Documentation topology is unchanged: active document set, paths, base pre-read,
routing, README/AGENTS bootstrap and Project Source count remain unchanged.

No deploy, PostgreSQL mutation, historical backfill/rewrite/delete, Exchange
mutation or LIVE re-arm is performed by this checkpoint.


## 37.14 R8c compact primitive coverage / research-reader checkpoint — CHECKED HERE 2026-10-17

Pre-publication implementation/runtime identity:

~~~text
REMOTE_HEAD             = 3a0a93b4c61728186f40f73d4ec77cc616b61148
SOURCE_HEAD             = 3a0a93b4c61728186f40f73d4ec77cc616b61148
SOURCE_WORKTREE         = CLEAN
INSTALLED_COMMIT        = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
LOADED_COMMIT           = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
DB MIGRATION APPLY      = NO
DEPLOY                  = NO
DELETE                  = NO
LIVE RE-ARM             = NO
~~~

Source commit `3a0a93b4...` completes the bounded R8c source representation for
the **known current research/replay analytical path set** without expanding the
compact grain beyond `MarketSource + InstrumentIdentity + observed_minute`.

R8c adds 64 compact numeric primitives/features required because exact live
semantics cannot always be reconstructed from once-per-minute samples alone.
The added families cover:

- 1m / 15m / 30m / 60m executed-flow windows for SPOT and LINEAR;
- exact prior-turnover and acceleration evidence, including 5m;
- best bid/ask, 5/10/25/50 bps compact depth and 1m/5m/15m book dynamics;
- OI value, 15m/30m/60m OI change, 5m OI speed/acceleration and funding change.

Derived features are not physically duplicated when exact recomputation is
sufficient. The compact reader recomputes net share, speed, large-trade share,
turnover-to-prior ratios, book imbalance, mark/index/last premiums and
panel-relative strength. Exact liquidation events remain the separate
`mayak_v2.liquidations` event family.

Coverage after R8c:

~~~text
KNOWN REQUIRED ANALYTICAL PATHS = 233
DIRECT COMPACT PATHS            = 158
DETERMINISTICALLY RECOMPUTABLE  = 75
UNCOVERED                       = 0
SOURCE COMPACT REPLACEMENT      = READY FOR THIS KNOWN PATH SET
~~~

A source-level full-panel parity test reconstructs all 233 paths for each symbol
from the compact representation and compares them with the frozen
CoinMarketContext payload. The non-trivial test panel contains BTC/ETH/ADA and
therefore checks 699 path values, including 60m flow, depth/book dynamics,
long-horizon OI and relative-strength derivation.

The compact writer is also tightened:

- rows are sourced from the already frozen `coin_market_contexts[*]`, not an
  earlier mutable view;
- exact symbol, MarketSource, SPOT/LINEAR InstrumentIdentity and observed time
  are validated before row materialization;
- the compact schema marker advances to `compact-market-minute-v3`;
- writer SQL is generated from one static 105-column contract, and row/SQL key
  parity is tested exactly;
- the R8c migration is additive only and introduces no UPDATE/backfill, DELETE,
  DROP or TRUNCATE.

A canonical archive research reader now streams
`analytics/market_minutes.jsonl`, keeps MarketSource partitions separate,
fails closed on incompatible compact schema versions and rebuilds analytical
panel payloads from R8c rows.

Consumer boundary audit:

- daily/weekly MAYAK report PROVEN rows already use compact analytics from R8b;
- Dashboard signal-analysis causal context now reads exact paired SPOT+LINEAR
  `analytics.market_minutes` instead of the mixed
  `mayak_v2.coin_minutes` row;
- Dashboard raw-period/archive evidence may still include old tables during the
  staged migration;
- Dispatcher, Universal Entry and current-context Dashboard reads that consume
  MAYAK/Dispatcher JSON objects are HOT/current-runtime consumers and are not
  reclassified as long-horizon analytics solely to force an R8 migration;
- historical raw-source research jobs that rebuild older periods from external
  source files are not readers of the current PostgreSQL duplicate-history path
  and remain valid source-specific replay tools.

Static capacity envelope for the new R8c additions at the current 20-symbol,
two-product panel:

~~~text
NEW NUMERIC PRIMITIVES              = 64 FLOAT8 MAX / ROW
MAX NEW NUMERIC PAYLOAD             = 512 BYTES / ROW
ROWS / DAY AT 20 x SPOT+LINEAR      = 57,600
MAX NEW NUMERIC PAYLOAD / DAY       ~= 28.12 MiB
~~~

This is a source-level numeric payload bound only; it excludes PostgreSQL
row/index overhead and is **not** a measured production growth rate. R11/R12
still own retention and physical reclaim.

Verification evidence:

~~~text
TARGETED R8c + CONSUMER SET         = 61 / 61 PASS
FULL PANEL PARITY                   = 699 / 699 PATH CHECKS PASS
COMPACT INSERT COLUMNS              = 105
SQL / ROW KEY PARITY                = 105 / 105 PASS
DASHBOARD SQL PARAMETER STATIC      = PASS
DASHBOARD RUFF BASELINE             = 42 -> 42 / 0 NEW
RUFF TARGETED                       = PASS
PY_COMPILE                          = PASS
INSTALLER BASH SYNTAX               = PASS
DIFF CHECK                          = PASS
DESTRUCTIVE R8c SQL                 = NONE
FULL PYTEST                         = 1915 PASS / 70 SKIPPED / 12 FAIL
BASELINE SAME 12 FAILURES           = CONFIRMED
NEW TEST FAILURES                   = 0
~~~

Status after R8c:

~~~text
R8 SOURCE IMPLEMENTATION            = DONE THROUGH R8c
R8 PRODUCTION DB APPLY              = NO
R8 DEPLOY                           = NO
R8 RUNTIME BEHAVIOR VERIFIED        = NO
R8 KNOWN ANALYTICAL COVERAGE        = 233 / 233 IN SOURCE
R9 DUAL-READ / PARITY / RESTART     = NEXT
R10 LEGACY-PATH RETIREMENT          = NOT STARTED
R11 RETENTION / DELETE              = NOT STARTED
R12 PHYSICAL RECLAIM                = NOT STARTED
OLD JSONB / COIN_MINUTE DELETE      = NOT AUTHORIZED
~~~

Documentation topology is unchanged: active document set, paths, base pre-read,
routing, README/AGENTS bootstrap and Project Source count remain unchanged.

No deploy, PostgreSQL mutation, historical backfill/rewrite/delete, Exchange
mutation or LIVE re-arm is performed by this checkpoint.


## 37.15 R9a migration verifier / pre-deploy checkpoint — CHECKED HERE 2026-10-17

Source/runtime identity after publication of the bounded R9a verifier:

~~~text
REMOTE_HEAD             = f76743bfcaaff4518f64dd8080a82b2a016ae823
SOURCE_HEAD             = f76743bfcaaff4518f64dd8080a82b2a016ae823
SOURCE_WORKTREE         = CLEAN
INSTALLED_COMMIT        = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
RELEASE_ENV_COMMIT      = 88216254e5c0b1cca3c8edf3db9ee0f0ac11f101
DEPLOY                  = NO
DB MIGRATION APPLY      = NO
SERVICE RESTART         = NO
LIVE RE-ARM             = NO
~~~

Source commit `f76743bf...` adds two read-only R9 tools:

1. `operations/monitoring/r9_migration_verifier.py`
   - starts every DB check with `SET TRANSACTION READ ONLY`;
   - can capture restart watermarks without requiring the new schema;
   - verifies the R8c runtime schema contract after deploy;
   - verifies exact active TradingAccount / AccountConnector registry state;
   - checks current private-state account scope;
   - checks exact MarketSource / InstrumentIdentity lineage;
   - compares recent compact panels against frozen
     `mayak_v2.coin_market_contexts` through the existing 233-path compact
     parity contract;
   - with an explicit post-restart watermark, requires both public MAYAK/compact
     progress and new PROVEN private account-state/reconciliation evidence;
   - fails closed and performs no repair/backfill/mutation.

2. `operations/infrastructure/r9_backup_preflight.py`
   - is read-only;
   - resolves the exact release-backup filesystem rather than assuming that
     `/data` and `/data/cripta` share one device;
   - compares current free space with the size of the latest verified
     `cripta_before.dump`;
   - is a minimum headroom check only; PASS does not replace the required actual
     deploy backup creation/verification.

Read-only current production baseline from the new verifier:

~~~text
MAYAK_SNAPSHOT_ID                    = 36984
MAYAK_OBSERVED_AT                    = 2026-10-17T07:47:04.600948+00:00
ACCOUNT_GENERATION_STARTED_AT        = 2026-10-17T07:47:58.161000+00:00
RECONCILIATION_STARTED_AT            = 2026-10-17T07:47:51.260000+00:00
DATABASE_TIME                        = 2026-10-17T07:48:01.827537+00:00
~~~

The pre-deploy verifier intentionally fails on the currently installed old
release:

~~~text
SCHEMA_CONTRACT = FAIL
missing =
  strategy_entry.strategy_execution_bindings
  analytics.market_minutes
~~~

This is expected evidence that source sync did not silently become deployment.
It is not repaired in-place and is not relabelled as runtime parity.

Filesystem clarification from exact mount evidence:

~~~text
/data/cripta/release-backup filesystem = /dev/sdb1
release-backup free bytes              = 17,780,494,336
latest verified cripta_before.dump     = 4,450,675,555 bytes
R9 MINIMUM BACKUP HEADROOM PRECHECK    = PASS

/srv + PostgreSQL filesystem           = /dev/sda1
current free bytes                     ~= 357,318,656
current installed runtime release      ~= 58 MiB
~~~

An earlier coarse `df /data` observation referred to the parent/root
filesystem and is not used as evidence for release-backup headroom. The
separate root filesystem remains tight and must be rechecked before an approved
deploy, but no undocumented minimum-margin threshold is invented here.

Current R9 gate state:

~~~text
NEW_WRITER_PATH                 = IMPLEMENTED IN SOURCE / NOT RUNTIME VERIFIED
NEW_READER_PATH                 = IMPLEMENTED IN SOURCE / NOT RUNTIME VERIFIED
ACCOUNT_SCOPE_PARITY            = NOT CHECKED POST-DEPLOY
MARKET_SOURCE_PARITY            = NOT CHECKED POST-DEPLOY
RESTART_RECOVERY                = NOT CHECKED POST-DEPLOY
REPORT_REPLAY_RESEARCH          = SOURCE TESTED / NOT POST-DEPLOY VERIFIED
LEGACY_REQUIRED_READERS         = NOT YET R10 GATE
BACKUP_ROLLBACK                 = HEADROOM PRECHECK PASS / ACTUAL NEW BACKUP NOT RUN
~~~

Verification evidence for the R9a source changeset:

~~~text
TARGETED R9a SET                  = 24 / 24 PASS
READ-ONLY PRODUCTION BASELINE     = PASS
OLD-SCHEMA FAIL-CLOSED SMOKE      = PASS
BACKUP HEADROOM MINIMUM PRECHECK  = PASS
RUFF TARGETED                     = PASS
PY_COMPILE                        = PASS
DIFF CHECK                        = PASS
FULL PYTEST                       = 1925 PASS / 70 SKIPPED / 12 FAIL
BASELINE SAME 12 FAILURES         = CONFIRMED
NEW TEST FAILURES                 = 0
~~~

Production boundary after R9a:

~~~text
CANON_CONFLICT                  = NO
R9a SOURCE PREPARATION          = PASS
R9 RUNTIME VERIFICATION         = NOT PERFORMED
EXPLICIT DEPLOY APPROVAL        = REQUIRED BEFORE R9b
HARD_STOP_FOR_PRODUCTION_MUTATION = YES UNTIL THAT APPROVAL
R10 / R11 / R12                 = NOT AUTHORIZED
~~~

Documentation topology is unchanged: active document set, paths, base pre-read,
routing, README/AGENTS bootstrap and Project Source count remain unchanged.

No deploy, PostgreSQL mutation, service restart, historical backfill/rewrite/
delete, Exchange mutation or LIVE re-arm is performed by this checkpoint.


## 37.16 R9b deployed parity / restart / recovery checkpoint — CHECKED HERE 2026-10-17

Owner authorization for the production stage was explicit:

~~~text
OWNER APPROVAL                    = "Разрешаю deploy R9b"
LIVE RE-ARM APPROVAL              = NO
~~~

Final exact production identity:

~~~text
REMOTE_HEAD                       = c705ae435fd777e8836161e8fe8dd1a84f4d64a5
SOURCE_HEAD                       = c705ae435fd777e8836161e8fe8dd1a84f4d64a5
SOURCE_WORKTREE                   = CLEAN
INSTALLED_COMMIT                  = c705ae435fd777e8836161e8fe8dd1a84f4d64a5
RELEASE_ENV_COMMIT                = c705ae435fd777e8836161e8fe8dd1a84f4d64a5
DASHBOARD_UI_COMMIT               = c705ae435fd777e8836161e8fe8dd1a84f4d64a5
RUNTIME CURRENT                   = release c705ae435fd777e8836161e8fe8dd1a84f4d64a5
MAINNET GATE                      = 0
ACTIVE LIVE-ARM SESSIONS          = 0
LIVE RE-ARM                       = NO
~~~

The R9b rollout required two bounded recovery fixes discovered by real
post-deploy evidence rather than hidden by source-only tests.

1. Runtime-service recovery commit `768620d6...`
   - added the runtime source PYTHONPATH to the Dispatcher V2 context correlator;
   - corrected Lifecycle Supervisor PostgreSQL account-scope NULL typing and
     dispatch identity joins;
   - added regression coverage for those runtime failures.

2. Report-runtime recovery commit `c705ae43...`
   - real daily report smoke exposed
     `ModuleNotFoundError: No module named 'bybit_workbench'`;
   - both daily and weekly MAYAK report units now carry
     `PYTHONPATH=/srv/cripta/runtime/current/src`;
   - exact report actor/path/sandbox contract remains unchanged;
   - regression tests cover both units.

The final `c705ae43...` deploy used the canonical serialized installer. Its
post-deploy evidence includes:

~~~text
RUNTIME_SCHEMA_MIGRATION          = PASS
RUNTIME_SCHEMA_CONTRACT           = PASS
TRADING_CONTROL_UNCHANGED         = PASS
POST_DEPLOY_EXCHANGE_TO_DB_RECONCILIATION = PASS
DEPLOY_EXACT_VERIFIED_COMMIT      = PASS
GATE                              = 0
~~~

Final R9 verifier result on the loaded release, with a post-follow-up-deploy
watermark:

~~~text
SCHEMA_CONTRACT                   = PASS
IDENTITY_REGISTRY                 = PASS
ACCOUNT_SCOPE_PARITY              = PASS
MARKET_SOURCE_PARITY              = PASS
COMPACT_ANALYTICS_PARITY          = PASS
PARITY SNAPSHOTS                  = 3
PARITY SYMBOLS                    = 60
PARITY PATH CHECKS                = 13,980
PATHS PER SYMBOL                  = 233
RESTART_RECOVERY                  = PASS
OVERALL                           = PASS
~~~

Runtime writer evidence after cutover:

~~~text
analytics.market_minutes v3 rows  = 1,600 at checked snapshot
latest compact source_observed_at = 2026-10-17 09:03:00.199061+00
runtime.wallet_latest PROVEN      = 1
account_state_generations PROVEN  = 333
reconciliation_runs PROVEN        = 333
~~~

Scheduled report/read-path verification:

~~~text
DAILY MAYAK REPORT                = PASS / ExecMainStatus=0
WEEKLY MAYAK REPORT               = PASS / ExecMainStatus=0
PROVEN SOURCE                     = analytics.market_minutes
LEGACY FALLBACK SOURCE            = mayak_v2.coin_minutes
COMPACT REPLACEMENT READY         = true
REPORT UNIT SOURCE/LIVE           = PASS
~~~

The legacy table remains an explicit historical fallback; this R9 checkpoint
does not authorize its removal.

Exact source/live payload verification after the final deploy:

~~~text
RUNTIME TRACKED FILES CHECKED     = 437
RUNTIME TRACKED MISMATCHES        = 0
TOOLING TRACKED FILES CHECKED     = 12
TOOLING TRACKED MISMATCHES        = 0
SOURCE_LIVE_PAYLOAD               = PASS
~~~

Key runtime services were sampled twice six seconds apart after the final
cutover. Private runtime, MAYAK, Dispatcher, context correlator, Dashboard,
Lifecycle Supervisor and Universal Entry consumer/observer all remained active
with the same PIDs and `NRestarts=0`. No Traceback/ERROR/CRITICAL/Exception
was found in those key service journals after final cutover.

The only currently failed CRIPTA unit observed is
`cripta-u5-oi30s-source-soak.service`; its failure timestamp is
2026-10-09 14:50:58 UTC, so it predates R9b and is not classified as an R9b
regression.

Backup / rollback evidence:

~~~text
LATEST DEPLOY BACKUP
  768620d6... -> c705ae43...
  cripta_before.dump = 4,665,296,714 bytes
  pg_restore --list  = PASS

R9 CONTROL CHECKPOINT RETAINED
  99608ce2... -> 768620d6...
  cripta_before.dump = 4,663,675,617 bytes
  pg_restore --list  = PASS

PRE-R9 VERIFIED SYSTEM BACKUP
  generation         = 20261017T032535Z
  cripta-backup.service Result / status = success / 0
  current pg_restore --list             = PASS
~~~

The canonical system-backup script creates `SHA256SUMS` and does not publish
the generation until `sha256sum --check`, `pg_restore --list` and both tar
listing checks pass. A later ad-hoc full SHA256 re-read lost its management
channel result and is therefore **not** counted as new evidence; the canonical
backup-job success plus current restore-list check are the evidence used here.

Latest source verification for the final report-recovery changeset:

~~~text
TARGETED REPORT RECOVERY SET      = 20 / 20 PASS
SYSTEMD UNIT VERIFY               = PASS
RUFF TARGETED                     = PASS
DIFF CHECK                        = PASS
FULL PYTEST                       = 1931 PASS / 70 SKIPPED / 12 FAIL
BASELINE SAME 12 FAILURES         = CONFIRMED
NEW TEST FAILURES                 = 0
~~~

R9 completion gate:

~~~text
NEW_WRITER_PATH                   = PASS
NEW_READER_PATH                   = PASS
ACCOUNT_SCOPE_PARITY              = PASS
MARKET_SOURCE_PARITY              = PASS
RESTART_RECOVERY                  = PASS
REPORT_REPLAY_RESEARCH            = PASS
BACKUP_ROLLBACK                   = PASS
LEGACY_REQUIRED_READERS           = NOT YET / R10 GATE
~~~

`LEGACY_REQUIRED_READERS=0` is deliberately not fabricated by R9 completion.
It is the next R10 retirement proof before legacy constraints/path removal.

No data deletion, old JSONB/coin-minute retirement, physical PostgreSQL reclaim,
Exchange mutation or LIVE re-arm is authorized or performed by this R9b
checkpoint.


## 37.17 R10a legacy writer / fallback retirement source checkpoint — CHECKED HERE 2026-10-17

R10a is a bounded **source-only** cutover. No production deploy, PostgreSQL
mutation, service restart, data deletion or LIVE re-arm is performed by this
checkpoint.

Source/runtime separation at implementation publication:

~~~text
IMPLEMENTATION COMMIT            = f65ca9b6ecaccc50e1a7b05808cc4cb90b5f6878
REMOTE / SOURCE AFTER CODE SYNC  = f65ca9b6ecaccc50e1a7b05808cc4cb90b5f6878
SOURCE WORKTREE                  = CLEAN
INSTALLED / LOADED APPLICATION   = c705ae435fd777e8836161e8fe8dd1a84f4d64a5
DEPLOY                           = NO
DB MUTATION                      = NO
SERVICE RESTART                  = NO
LIVE RE-ARM                      = NO
~~~

R10a retires the first set of unambiguous legacy paths in source:

1. New duplicate public analytical writes to `mayak_v2.coin_minutes`
   are stopped. MAYAK continues to write the canonical
   `analytics.market_minutes` representation and current HOT context tables.
   Historical `coin_minutes` schema/data are deliberately retained for R11.

2. Daily/weekly MAYAK report source no longer reads
   `mayak_v2.coin_minutes`. Its public analytical rows are compact-only and
   the summary now reports:

~~~text
proven_source              = analytics.market_minutes
legacy_fallback_source     = null
compact_replacement_ready  = true
~~~

A source-smoke of that R10a report against the current production DB completed
successfully in the research run root and reported
`legacy_unknown_rows=0`.

3. The inactive `LEGACY_V1` Entry command-source path is retired from the
   active private runtime and Universal Entry consumer source. Both now default
   to `UNIVERSAL_ENTRY`; private runtime fails closed on any non-Universal
   source with `R10_LEGACY_ENTRY_COMMAND_SOURCE_RETIRED`. The Universal Entry
   consumer remains hard-disabled unless its separate mainnet consumer arm is
   explicitly enabled; R10a does not arm it.

4. The old private-runtime legacy signal-to-`runtime.trade_commands` Entry
   writer block is removed. Its operational cleanup for already existing
   pending Entry limits remains.

5. Current private upsert writers now target the account-scoped unique indexes
   prepared in R5:

~~~text
wallet_latest
  ON CONFLICT(trading_account_id,singleton)

hot_positions
  ON CONFLICT(trading_account_id,symbol,position_idx)

hot_orders
  ON CONFLICT(trading_account_id,order_id)

executions
  ON CONFLICT(trading_account_id,exec_id)

exchange_order_history
  ON CONFLICT(trading_account_id,order_id)
~~~

Read-only production inspection confirms all five corresponding
`NULLS NOT DISTINCT` unique indexes already exist. The old global primary keys
are **not** dropped in R10a.

The legacy Entry safety contracts were not discarded mechanically. Ten tests
that referenced the removed writer were moved to the replacement Universal
Entry authority only after source proof of the replacement invariants:

- `OBSERVE` context is linked but is not a decision mode;
- decision-affecting context is Strategy-owned through explicit
  `CONDITION` / `RANKING` policy;
- the execution request carries immutable causal `signal_fact`, source refs
  and attributes;
- execution reference paths are exact `fact.*` paths;
- real dispatch requires StrategyExecutionBinding, TradingAccount,
  exchange-position slot claim and position-mode lineage;
- mainnet execution gate remains an independent pre-dispatch gate.

Those ten replacement contract tests pass 10/10.

R10a source verification:

~~~text
PY_COMPILE MODIFIED RUNTIME FILES = PASS
R10a REPLACEMENT CONTRACTS        = 10 / 10 PASS
R10a TARGETED SET                 = 61 PASS / SAME 7 UE BASELINE FAILURES
COMPACT-ONLY REPORT SOURCE SMOKE  = PASS
ACCOUNT UNIQUE INDEX EVIDENCE     = 5 / 5 PRESENT
RUFF DELTA private_runtime        = 66 -> 55 / 0 NEW FINDINGS
RUFF DELTA other runtime files    = 0 NEW FINDINGS
DIFF CHECK                        = PASS
FULL PYTEST                       = 1937 PASS / 70 SKIPPED / 12 FAIL
BASELINE SAME 12 FAILURES         = CONFIRMED
NEW TEST FAILURES                 = 0
~~~

R10 is **not complete**. A residual source inventory still contains legacy
history/fallback references in runtime, Dashboard/archive and lifecycle/exit
components. Examples include:

- Dashboard raw-period and `archive_v2` export of historical
  `mayak_v2.coin_minutes`;
- private/runtime and lifecycle/exit `LEGACY_UNKNOWN` compatibility reads;
- Dispatcher context correlator historical compatibility;
- Universal Exit / protection / stale-arm historical compatibility.

The inventory also includes schema-preservation references such as the retained
`coin_minutes` table creation. A raw source-reference count is therefore not
equivalent to a required-reader count.

Current production evidence also proves why R11 cannot start yet:

~~~text
historical coin_minutes LEGACY_UNKNOWN rows = 730,760 at checked snapshot
historical private LEGACY_UNKNOWN evidence  = retained
current HOT private identity                = PROVEN
R10a historical rewrite/delete             = NONE
~~~

R10 gate after this checkpoint:

~~~text
NEW_WRITER_PATH                   = SOURCE CUTOVER PREPARED / NOT DEPLOYED
NEW_READER_PATH                   = R9 PASS
ACCOUNT_SCOPE_PARITY              = R9 PASS
MARKET_SOURCE_PARITY              = R9 PASS
RESTART_RECOVERY                  = R9 PASS
REPORT_REPLAY_RESEARCH            = R10a COMPACT-ONLY SOURCE SMOKE PASS
BACKUP_ROLLBACK                   = R9 PASS
LEGACY_REQUIRED_READERS           = NOT ZERO / R10b REQUIRED
OLD GLOBAL PK RETIREMENT          = NOT AUTHORIZED
R11 RETENTION / DELETE            = NOT AUTHORIZED
R12 PHYSICAL RECLAIM              = NOT AUTHORIZED
~~~

The next bounded stage is R10b: classify each remaining legacy reference as
active required reader, historical preservation/export, or dead compatibility;
migrate/remove only the active fallback dependencies; then prove
`LEGACY_REQUIRED_READERS=0` before any old physical key or historical-path
retirement.

Documentation topology is unchanged: active document set, paths, mandatory
pre-read/routing and Project Source count remain unchanged.

## 2026-10-18 PostgreSQL physical offload — OWNER DECISION, NOT IMPLEMENTED

ARCH §50 and DEVELOPMENT_RELEASE §49 govern the one-time exception. Last observed: production PostgreSQL 16 runs from /var/lib/postgresql/16/main on System SSD; isolated restored test cluster at /data/cripta/pg_restore_validation_20261018 is STOPPED, not a physical rollback. Verified logical dump is under /data/cripta/script_archive/postgresql_preclean_20261018. Data filesystem mount is /data/cripta; /data itself is System SSD. Last free space approx. 90–100 MB System and 13 GB Data; refresh before action. CRIPTA production services remain running; mainnet gate=0 and active LIVE arm=0; independent Bybit open position/order check NOT CHECKED HERE. Full physical rollback NOT VERIFIED. All owner/path, available space and consumer gates require current evidence.

MIGRATION PREPARED=NO; MIGRATION IMPLEMENTED=NO; CUTOVER=NO. R10b/R11/R12 remain open. LIVE UNCHANGED.


## 2026-10-20 ChatGPT scheduled CI control not executed — CHECKED HERE

Operator-reported absence of the 16:01 Asia/Almaty notification for GitHub
PR #115 (head `54e766d2be72d226c3c33bbab90f0cb40952fa50`).
ChatGPT automation `Проверка CI PR 115`, scheduled
`2026-10-20T11:01:45Z` (16:01:45 Asia/Almaty), was verified as
`is_enabled=true`, `last_run_time=null`, `notifications_enabled=false`,
`email_enabled=false` in its task state at the time of inspection.
This is evidence of **no recorded execution**, not proof of the underlying
scheduler failure or root cause; no cause has been confirmed.

Operational follow-up: scheduling success must NOT be reported as confirmed
notification delivery. Always inspect reminder task state, specifically
notification capability and actual `last_run_time`; if unavailable, report
`NOTIFICATION_NOT_CONFIRMED`. A missed scheduled check must be verified
independently against GitHub CI when the operator returns. Do not promise
push delivery when notifications are disabled. Exact job/check and terminal
state must be reported distinctly. Do not create unbounded polling tasks.

Follow-up checked: PR #115 `quality=success` and
`trade-lifecycle-postgres=success`; safely merged into GitHub main as
`fda12d5d99c5bba9aad7e8410567ac9a174da9ec`. No production deploy,
database mutation or LIVE-arm is claimed by this checkpoint.


## 2026-10-21 superseded LIVE-arm retirement dependency — SOURCE REPAIR PROPOSED, NOT DEPLOYED

Owner reiterated independent deploy and continued Exit for already opened
positions. Root cause in prior source: `retire_stale_release_arms()` incorrectly
required a current `StrategyExecutionBinding` and flat, complete authenticated
Exchange inventory to close an old-release Entry-only authorization record.
Observed checkpoint before change (2026-10-20): installed/loaded application
`3ef144ce58a058ba43d8d1cee23d89e33d8e6a15`, five historical ACTIVE
arms on `e238c71cbe19e2e89669c946e3b2117ef4ed7d54`, mainnet gate OFF,
zero effective execution bindings, two unresolved release-arm incidents.
These historical figures must NOT be represented as fresh current runtime
without verification. GitHub documentation-only/research HEAD can differ from
installed application identity under DEVELOPMENT_RELEASE §26.2 / INDEX §17.5.

Target repair: with mainnet new-entry gate already OFF, independent Lifecycle
retires only superseded Entry authorization by exact release identity and
durable event; no Exchange inventory, order, Exit, position ownership, or
historical binding is modified. Post-restart Execution/Lifecycle/Universal Exit
reconciliation remains independently required; OWNER ARM remains manual.
No production SQL, deploy, LIVE or Exchange mutation was performed in this
source-repair preparation. Release/runtime behavior after deployment remains
NOT VERIFIED until a later explicit checkpoint.


## 2026-10-21 R1 one-Strategy/five-instrument alignment — SOURCE ONLY

OWNER DECISION: R1 represents ONE Strategy for APTUSDT, ARBUSDT, DOTUSDT,
INJUSDT and LTCUSDT (LONG and SHORT), with ONE StrategyActivation and ONE
dedicated TradingAccount. The whole-plan EntryPlan yields ten causal
`EntryPlan × symbol × direction` monitoring lanes, not ten independent
Strategies or ten persisted EntryPlans.

CHECKED HERE at GitHub `6ae7a6982e78232aa433dfb3aaeefd60a1abdf3a`
and read-only production snapshot:
- installed application runtime remained `6d931baf1a8eeb323704df36cb765f0c67d477f9`;
- five enabled, separately named legacy R1 StrategyCards have exact
  single-symbol scope, identical result-affecting policy blocks and distinct
  per-symbol immutable identities;
- one ACTIVE TradingAccount and one account connector; no
  `strategy_execution_bindings` rows as inspected before this change;
- existing `paper_positions` and `paper_orders` records carry historical
  Strategy/Activation/EntryPlan/ExitPlan IDs; no historical rewrite is allowed;
- global mainnet new Entry gate remained OFF at last checked snapshot.

SOURCE-PREPARED: an explicitly gated one-time transactional monitoring cutover
`operations/implementation/transition_r1_five_symbols.py` is in source, with
`--apply` required, stopped-Observer proof, mainnet OFF, exact five legacy
fingerprints, fresh authenticated flat account, no unsettled PAPER/REAL
resources or pre-existing account binding. Its same transaction retires old
activations and five enabled legacy ExecutionPermissions and materializes
one new unified StrategyCard/EntryPlan/ExitPlan; it leaves new execution OFF.
Separate negative-path source tests cover its hard stops. No production
execution of this tool is implied.

SOURCE-PREPARED: new inert `build_r1_unified_card()` preserves all R1
result-affecting policy blocks while selecting all five symbols under one new
Strategy identity. Added immutable policy parity and ten-lane materialization
tests. Historic `build_r1_cards()` and original per-symbol records are retained.

NOT APPLIED / NOT DEPLOYED: transactional production transition of five
active activations to the one unified activation, exact account binding, R1
legacy owner-arm route replacement, application release and runtime continuity.
Do not claim one active production Strategy from this source-only step.
No automatic ExecutionPermission ON, mainnet opening, new LIVE-arm, Exchange
mutations, historical PAPERS/REAL rewrite or change to existing ExitPlan.
Pending PAPER and REAL Exit continuity is a mandatory gate before the eventual
production transition. Independent Observer continuity faults remain open and
are not auto-resolved by this configuration change.

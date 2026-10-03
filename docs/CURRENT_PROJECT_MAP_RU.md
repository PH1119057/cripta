# CRIPTA — текущая карта проекта

**Версия:** 11.2
**Дата:** 2026-10-03
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
(`critical_delivery.configured=false`). Поэтому
`CRITICAL_FAULT_DELIVERY=PASS` для real arm пока ставить нельзя, хотя сам
delivery contract реализован и controlled behavior verified.

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

Следовательно:

```text
READY_FOR_LIVE = NO
READY_FOR_MICRO_LIVE = NO
MAINNET = DISARMED
```

Это fail-closed состояние является ожидаемым и не считается дефектом.

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
automatic alert generation                = NO_PO
#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE="${CRIPTA_SOURCE_CHECKOUT:-/srv/cripta/source_checkout}"
RELEASE_COMMIT="${CRIPTA_RELEASE_COMMIT:-}"
EXPECTED_BASELINE="${CRIPTA_EXPECTED_BASELINE_COMMIT:-}"
EXPECTED_TREE="${CRIPTA_RELEASE_TREE_SHA:-}"
RUNTIME_ROOT="${CRIPTA_RUNTIME_ROOT:-/srv/cripta/runtime}"
DASHBOARD_UI_ROOT="${CRIPTA_DASHBOARD_UI_ROOT:-/srv/cripta/dashboard-ui}"
RESEARCH_TOOLING_ROOT="${CRIPTA_RESEARCH_TOOLING_ROOT:-/data/cripta/research/tooling}"
BACKUP_ROOT="${CRIPTA_RELEASE_BACKUP_ROOT:-/data/cripta/script_archive/release_backups}"
STATE_ROOT="${CRIPTA_RELEASE_STATE_ROOT:-/var/lib/cripta/release}"
CONTROL_CHECKPOINT="${CRIPTA_RELEASE_CONTROL_CHECKPOINT:-0}"
CONTROL_REASON="${CRIPTA_RELEASE_CONTROL_REASON:-}"
STAGED_OFFLINE="${CRIPTA_STAGED_OFFLINE_CUTOVER:-0}"
CONTROL_MAX_AGE_SECONDS=604800

die() { echo "ERROR: $*" >&2; exit 1; }
as_repo_owner() { runuser -u cripta -- env GIT_OPTIONAL_LOCKS=0 "$@"; }
sql_scalar() { runuser -u postgres -- psql -X -Atqc "$1" cripta; }

prune_release_backups() {
  local now dir marker expires
  local control_kept=0
  local -a release_backups=()

  now="$(date -u +%s)"
  mapfile -t release_backups < <(
    find "$BACKUP_ROOT" -regextype posix-extended       -mindepth 1 -maxdepth 1 -type d       -regex '.*/20[0-9]{6}_[0-9]{6}_[0-9a-f]{40}_to_[0-9a-f]{40}'       -print | sort -r
  )

  for dir in "${release_backups[@]}"; do
    if [[ "$dir" == "$backup" ]]; then
      [[ -f "$dir/CONTROL_CHECKPOINT" ]] && control_kept=1
      echo "RELEASE_BACKUP_RETAINED=$dir reason=latest"
      continue
    fi

    marker="$dir/CONTROL_CHECKPOINT"
    if [[ -f "$marker" && "$control_kept" == "0" ]]; then
      expires="$(awk -F= '$1=="expires_at_epoch" {print $2; exit}' "$marker")"
      if [[ "$expires" =~ ^[0-9]+$ ]] && (( expires >= now )); then
        control_kept=1
        echo "RELEASE_BACKUP_RETAINED=$dir reason=control_checkpoint"
        continue
      fi
    fi

    rm -rf -- "$dir"
    echo "RELEASE_BACKUP_PRUNED=$dir"
  done
}

[[ "$(id -u)" -eq 0 ]] || die "installer must run as root"
exec 9>/run/lock/cripta-install-verified-release.lock
flock -n 9 || die "another release installer is already running"
[[ "$RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "CRIPTA_RELEASE_COMMIT is invalid"
[[ "$EXPECTED_BASELINE" =~ ^[0-9a-f]{40}$ ]] || die "CRIPTA_EXPECTED_BASELINE_COMMIT is invalid"
[[ -d "$SOURCE/.git" ]] || die "source checkout missing"
[[ "$CONTROL_CHECKPOINT" == "0" || "$CONTROL_CHECKPOINT" == "1" ]] || die "CRIPTA_RELEASE_CONTROL_CHECKPOINT must be 0 or 1"
[[ "$STAGED_OFFLINE" == "0" || "$STAGED_OFFLINE" == "1" ]] || die "CRIPTA_STAGED_OFFLINE_CUTOVER must be 0 or 1"
if [[ "$STAGED_OFFLINE" == "1" ]]; then
  [[ "$CONTROL_CHECKPOINT" == "1" ]] || die "STAGED_OFFLINE_REQUIRES_CONTROL_CHECKPOINT"
fi
if [[ "$CONTROL_CHECKPOINT" == "1" ]]; then
  [[ -n "$CONTROL_REASON" ]] || die "CRIPTA_RELEASE_CONTROL_REASON is required for a control checkpoint"
fi

remote="$(as_repo_owner git -C "$SOURCE" ls-remote origin refs/heads/main | awk 'NR==1{print $1}')"
[[ "$remote" == "$RELEASE_COMMIT" ]] || die "remote main differs from requested release commit"
as_repo_owner git -C "$SOURCE" fetch --no-tags origin "$RELEASE_COMMIT" >/dev/null
tree="$(as_repo_owner git -C "$SOURCE" rev-parse "${RELEASE_COMMIT}^{tree}")"
if [[ -n "$EXPECTED_TREE" && "$tree" != "$EXPECTED_TREE" ]]; then die "release tree mismatch"; fi

# Release installation is independent of all Exchange and trading inventory.
# The installer only observes; Execution and Lifecycle own mutation/recovery.
pre_gate="$(sql_scalar "SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'")"
pre_permissions="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
pre_sessions="$(sql_scalar "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'")"
echo "TRADING_STATE_INFORMATIONAL=gate:$pre_gate permissions:$pre_permissions sessions:$pre_sessions"
if [[ "$STAGED_OFFLINE" == "1" ]]; then
  # Local facts are necessary, NOT sufficient: approved preflight must also
  # independently verify Exchange positions/orders and restore/rollback.
  [[ "$pre_gate" == "0" && "$pre_permissions" == "0" && "$pre_sessions" == "0" ]] || die "STAGED_OFFLINE_TRADING_NOT_DISARMED"
  pre_hot_positions="$(sql_scalar "SELECT count(*) FROM runtime.hot_positions")"
  pre_hot_orders="$(sql_scalar "SELECT count(*) FROM runtime.hot_orders")"
  [[ "$pre_hot_positions" == "0" && "$pre_hot_orders" == "0" ]] || die "STAGED_OFFLINE_LOCAL_HOT_INVENTORY_NOT_EMPTY"
  echo "STAGED_OFFLINE_LOCAL_PREFLIGHT=PASS EXCHANGE_EVIDENCE=EXTERNAL_REQUIRED"
fi

current_runtime="$(readlink -f "$RUNTIME_ROOT/current" 2>/dev/null || true)"
current_tooling="$(readlink -f "$RESEARCH_TOOLING_ROOT/current" 2>/dev/null || true)"
[[ -n "$current_runtime" && -f "$current_runtime/INSTALLED_COMMIT" ]] || die "current runtime identity missing"
[[ -n "$current_tooling" && -f "$current_tooling/INSTALLED_COMMIT" ]] || die "current tooling identity missing"
actual_runtime_baseline="$(runuser -u cripta -- cat "$current_runtime/INSTALLED_COMMIT")"
actual_tooling_baseline="$(runuser -u cripta -- cat "$current_tooling/INSTALLED_COMMIT")"
[[ "$actual_runtime_baseline" == "$EXPECTED_BASELINE" ]] || die "runtime baseline mismatch: actual=$actual_runtime_baseline expected=$EXPECTED_BASELINE"
[[ "$actual_tooling_baseline" == "$EXPECTED_BASELINE" ]] || die "tooling baseline mismatch: actual=$actual_tooling_baseline expected=$EXPECTED_BASELINE"

install -d -o cripta -g cripta -m 0750 "$RUNTIME_ROOT" "$RUNTIME_ROOT/releases"
install -d -o root -g cripta -m 0755 "$DASHBOARD_UI_ROOT" "$DASHBOARD_UI_ROOT/releases"
install -d -o cripta -g cripta -m 2770 "$RESEARCH_TOOLING_ROOT" "$RESEARCH_TOOLING_ROOT/releases"
install -d -o cripta -g cripta -m 0750 /data/cripta/datasets/raw/bybit_public_trades_daily_v1
install -d -o root -g root -m 0700 "$BACKUP_ROOT"
install -d -o root -g cripta -m 0750 "$STATE_ROOT"

runtime_release="$RUNTIME_ROOT/releases/$RELEASE_COMMIT"
tooling_release="$RESEARCH_TOOLING_ROOT/releases/$RELEASE_COMMIT"

if [[ ! -d "$runtime_release" ]]; then
  as_repo_owner install -d -m 0750 "$runtime_release"
  as_repo_owner git -C "$SOURCE" archive --format=tar "$RELEASE_COMMIT" -- src production/src/bybit_workbench/dispatcher_v2 operations config research/server/backup research/server/connectivity research/server/monitoring research/server/control pyproject.toml     | runuser -u cripta -- tar -xf - -C "$runtime_release"
fi
if [[ ! -d "$tooling_release" ]]; then
  as_repo_owner install -d -m 0750 "$tooling_release"
  as_repo_owner git -C "$SOURCE" archive --format=tar "$RELEASE_COMMIT" -- research/server/jobs research/server/dataset research/server/cripta-download-expansion.service     | runuser -u cripta -- tar -xf - -C "$tooling_release"
fi

# Prepare the separately identified Dashboard bundle from the same approved
# Git release. Its active symlink switches only during the coordinated cutover.
ui_release="$DASHBOARD_UI_ROOT/releases/$RELEASE_COMMIT"
if [[ ! -d "$ui_release" ]]; then
  install -d -o root -g cripta -m 0755 "$ui_release"
  install -o root -g cripta -m 0644 "$runtime_release/operations/dashboard/index.html" "$ui_release/index.html"
  install -o root -g cripta -m 0644 "$runtime_release/operations/dashboard/app.py" "$ui_release/app.py"
  printf '%s\n' "$RELEASE_COMMIT" > "$ui_release/DASHBOARD_UI_COMMIT"
  chown root:cripta "$ui_release/DASHBOARD_UI_COMMIT"
  chmod 0644 "$ui_release/DASHBOARD_UI_COMMIT"
fi
# The runtime candidate is intentionally overlaid below with the independently
# deployed Dashboard identity. A retry after a pre-cutover failure must therefore
# validate the Dashboard release against immutable Git bytes, not against a
# runtime candidate that a prior attempt may already have overlaid.
cmp -s <(as_repo_owner git -C "$SOURCE" show "$RELEASE_COMMIT:operations/dashboard/index.html") "$ui_release/index.html" ||
  die "candidate Dashboard HTML differs from exact Git release"
cmp -s <(as_repo_owner git -C "$SOURCE" show "$RELEASE_COMMIT:operations/dashboard/app.py") "$ui_release/app.py" ||
  die "candidate Dashboard app differs from exact Git release"
[[ "$(cat "$ui_release/DASHBOARD_UI_COMMIT")" == "$RELEASE_COMMIT" ]] ||
  die "candidate Dashboard identity mismatch"

# Dashboard presentation/read-model is a separate release identity. Bootstrap it only when the
# independent Dashboard rail has not been initialized yet; otherwise preserve the
# current Dashboard bundle across full application-runtime deploys.
if [[ ! -f "$DASHBOARD_UI_ROOT/current/index.html" || ! -f "$DASHBOARD_UI_ROOT/current/app.py" ]]; then
  ui_bootstrap="$DASHBOARD_UI_ROOT/releases/$RELEASE_COMMIT"
  install -d -o root -g cripta -m 0755 "$ui_bootstrap"
  install -o root -g cripta -m 0644 "$runtime_release/operations/dashboard/index.html" "$ui_bootstrap/index.html"
  install -o root -g cripta -m 0644 "$runtime_release/operations/dashboard/app.py" "$ui_bootstrap/app.py"
  printf '%s\n' "$RELEASE_COMMIT" > "$ui_bootstrap/DASHBOARD_UI_COMMIT"
  chown root:cripta "$ui_bootstrap/DASHBOARD_UI_COMMIT"
  chmod 0644 "$ui_bootstrap/DASHBOARD_UI_COMMIT"
  next_ui="$DASHBOARD_UI_ROOT/.current-$RELEASE_COMMIT"
  rm -f "$next_ui"
  ln -s "$ui_bootstrap" "$next_ui"
  mv -Tf "$next_ui" "$DASHBOARD_UI_ROOT/current"
fi
[[ -f "$DASHBOARD_UI_ROOT/current/index.html" ]] || die "current Dashboard HTML asset missing"
[[ -f "$DASHBOARD_UI_ROOT/current/app.py" ]] || die "current Dashboard read-model app missing"
rm -f "$runtime_release/operations/dashboard/index.html" "$runtime_release/operations/dashboard/app.py"
ln -s "$DASHBOARD_UI_ROOT/current/index.html" "$runtime_release/operations/dashboard/index.html"
install -o cripta -g cripta -m 0644 "$DASHBOARD_UI_ROOT/current/app.py" "$runtime_release/operations/dashboard/app.py"
[[ -f "$runtime_release/operations/dashboard/app.py" && ! -L "$runtime_release/operations/dashboard/app.py" ]] ||
  die "Dashboard app must remain a regular in-directory file"

runtime_requirements="$runtime_release/operations/runtime/runtime_requirements.lock"
[[ -f "$runtime_requirements" ]] || die "runtime dependency lock missing"
if [[ ! -x "$runtime_release/.venv/bin/python" ]]; then
  as_repo_owner /usr/bin/python3 -m venv --system-site-packages "$runtime_release/.venv"
  as_repo_owner "$runtime_release/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: --require-hashes -r "$runtime_requirements"
fi

PYTHONPATH="$runtime_release/src" "$runtime_release/.venv/bin/python" - <<'PY'
import pydantic
import psycopg
import sqlalchemy
import websocket
import bybit_workbench.universal_entry.shadow_runtime
import bybit_workbench.universal_exit.engine
import bybit_workbench.lifecycle_supervisor
assert pydantic.__version__ == "2.13.5"
print("RUNTIME_IMPORT_PREFLIGHT=PASS")
PY

PYTHONPATH="$runtime_release/production/src" "$runtime_release/.venv/bin/python" - <<'PY'
import bybit_workbench.dispatcher_v2
print("DISPATCHER_IMPORT_PREFLIGHT=PASS")
PY

printf '%s\n' "$RELEASE_COMMIT" > "$runtime_release/INSTALLED_COMMIT"
printf '%s\n' "$RELEASE_COMMIT" > "$tooling_release/INSTALLED_COMMIT"
chown cripta:cripta "$runtime_release/INSTALLED_COMMIT" "$tooling_release/INSTALLED_COMMIT"
chmod 0640 "$runtime_release/INSTALLED_COMMIT" "$tooling_release/INSTALLED_COMMIT"

next_runtime="$RUNTIME_ROOT/.current-$RELEASE_COMMIT"
next_tooling="$RESEARCH_TOOLING_ROOT/.current-$RELEASE_COMMIT"
rm -f "$next_runtime" "$next_tooling"
ln -s "$runtime_release" "$next_runtime"
ln -s "$tooling_release" "$next_tooling"

unit_specs=(
  "research/server/connectivity/cripta-bybit-latency.service|cripta-bybit-latency.service|runtime"
  "research/server/backup/cripta-backup.service|cripta-backup.service|runtime"
  "research/server/backup/cripta-backup.timer|cripta-backup.timer|runtime"
  "operations/systemd/cripta-dashboard.service|cripta-dashboard.service|runtime"
  "operations/systemd/cripta-dns-override.service|cripta-dns-override.service|runtime"
  "operations/dispatcher_v2/cripta-dispatcher-v2-context-correlator.service|cripta-dispatcher-v2-context-correlator.service|runtime"
  "operations/dispatcher_v2/cripta-dispatcher-v2.service|cripta-dispatcher-v2.service|runtime"
  "operations/monitoring/cripta-exit-runtime.service|cripta-exit-runtime.service|runtime"
  "research/server/monitoring/cripta-health-monitor.service|cripta-health-monitor.service|runtime"
  "research/server/jobs/cripta-job-intake.service|cripta-job-intake.service|tooling"
  "research/server/jobs/cripta-job-runner.service|cripta-job-runner.service|tooling"
  "research/server/dataset/cripta-dataset-manifest.service|cripta-dataset-manifest.service|tooling"
  "research/server/dataset/cripta-public-trade-archive.service|cripta-public-trade-archive.service|tooling"
  "research/server/dataset/cripta-public-trade-archive.timer|cripta-public-trade-archive.timer|tooling"
  "research/server/cripta-download-expansion.service|cripta-download-expansion.service|tooling"
  "operations/systemd/cripta-lifecycle-supervisor.service|cripta-lifecycle-supervisor.service|runtime"
  "operations/systemd/cripta-m3-trade-analyst.service|cripta-m3-trade-analyst.service|runtime"
  "operations/systemd/cripta-mayak-v2.service|cripta-mayak-v2.service|runtime"
  "operations/systemd/cripta-mayak-v2-report.service|cripta-mayak-v2-report.service|runtime"
  "operations/systemd/cripta-mayak-v2-weekly-report.service|cripta-mayak-v2-weekly-report.service|runtime"
  "research/server/monitoring/cripta-opportunity-tracker.service|cripta-opportunity-tracker.service|runtime"
  "operations/monitoring/cripta-position-supervisor.service|cripta-position-supervisor.service|runtime"
  "research/server/connectivity/cripta-private-runtime.service|cripta-private-runtime.service|runtime"
  # Source-only templates are installed but never enabled or added to managed_services.
  "operations/systemd/cripta-private-runtime@.service|cripta-private-runtime@.service|runtime"
  # Inert, owner-triggered GET-only identity check. Never auto-start/enable.
  "operations/systemd/cripta-account-enrollment-probe@.service|cripta-account-enrollment-probe@.service|runtime"
  # Inert SUB-only Lifecycle worker; no auto-enable or implicit started service.
  "operations/systemd/cripta-lifecycle-account@.service|cripta-lifecycle-account@.service|runtime"
  "operations/systemd/cripta-account-position@.service|cripta-account-position@.service|runtime"
  "operations/systemd/cripta-universal-entry-consumer@.service|cripta-universal-entry-consumer@.service|runtime"
  "research/server/connectivity/cripta-safety-observer.service|cripta-safety-observer.service|runtime"
  "research/server/control/cripta-shadow-command-worker.service|cripta-shadow-command-worker.service|runtime"
  "operations/systemd/cripta-u5-oi30s-source-soak.service|cripta-u5-oi30s-source-soak.service|runtime"
  "operations/systemd/cripta-universal-entry-consumer.service|cripta-universal-entry-consumer.service|runtime"
  "operations/systemd/cripta-universal-entry-observer.service|cripta-universal-entry-observer.service|runtime"
  "operations/systemd/cripta-universal-entry-shadow.service|cripta-universal-entry-shadow.service|runtime"
  "operations/systemd/cripta-universal-exit-shadow.service|cripta-universal-exit-shadow.service|runtime"
  "operations/systemd/cripta-universal-exit-consumer.service|cripta-universal-exit-consumer.service|runtime"
  "operations/systemd/cripta-r1-reverse-worker.service|cripta-r1-reverse-worker.service|runtime"
  "operations/monitoring/cripta-causal-context-correlator.service|cripta-causal-context-correlator.service|runtime"
  "operations/strategy_dispatcher/cripta-strategy-dispatcher.service|cripta-strategy-dispatcher.service|runtime"
)

for spec in "${unit_specs[@]}"; do
  IFS='|' read -r src_rel unit scope <<<"$spec"
  if [[ "$scope" == "tooling" ]]; then
    source_unit="$tooling_release/$src_rel"
  else
    source_unit="$runtime_release/$src_rel"
  fi
  [[ -f "$source_unit" ]] || die "unit source missing before mutation: $source_unit"
done
echo "UNIT_SOURCE_PREFLIGHT=PASS"

stamp="$(date -u +%Y%m%d_%H%M%S)"
backup="$BACKUP_ROOT/${stamp}_${EXPECTED_BASELINE}_to_${RELEASE_COMMIT}"
install -d -o root -g root -m 0700 "$backup"
{
  echo "release_commit=$RELEASE_COMMIT"
  echo "staged_offline=$STAGED_OFFLINE"
  echo "release_tree_sha=$tree"
  echo "expected_baseline_commit=$EXPECTED_BASELINE"
  echo "remote_main=$remote"
  echo "runtime_release=$runtime_release"
  echo "research_tooling_release=$tooling_release"
  echo "created_at=$stamp"
} > "$backup/release_identity.txt"
if [[ "$CONTROL_CHECKPOINT" == "1" ]]; then
  control_created_epoch="$(date -u +%s)"
  control_expires_epoch="$((control_created_epoch + CONTROL_MAX_AGE_SECONDS))"
  {
    echo "created_at_epoch=$control_created_epoch"
    echo "expires_at_epoch=$control_expires_epoch"
    echo "reason=$CONTROL_REASON"
  } > "$backup/CONTROL_CHECKPOINT"
fi

for spec in "${unit_specs[@]}"; do
  IFS='|' read -r src_rel unit scope <<<"$spec"
  if [[ -e "/etc/systemd/system/$unit" || -L "/etc/systemd/system/$unit" ]]; then
    install -d -m 0700 "$backup/files/etc/systemd/system"
    cp -a "/etc/systemd/system/$unit" "$backup/files/etc/systemd/system/$unit"
  fi
done
retired_units=(
  cripta-entry-shadow-scanner.service
)
for unit in "${retired_units[@]}"; do
  if [[ -e "/etc/systemd/system/$unit" || -L "/etc/systemd/system/$unit" ]]; then
    install -d -m 0700 "$backup/files/etc/systemd/system"
    cp -a "/etc/systemd/system/$unit" "$backup/files/etc/systemd/system/$unit"
  fi
done
if [[ -d /etc/systemd/system/cripta-private-runtime.service.d ]]; then
  install -d -m 0700 "$backup/files/etc/systemd/system"
  cp -a /etc/systemd/system/cripta-private-runtime.service.d "$backup/files/etc/systemd/system/"
fi
for path in /usr/local/sbin/cripta-apply-incoming /usr/local/sbin/cripta-deploy-dashboard-ui /etc/cripta/release.env; do
  if [[ -e "$path" || -L "$path" ]]; then
    safe="${path#/}"
    install -d -m 0700 "$backup/files/$(dirname "$safe")"
    cp -a "$path" "$backup/files/$safe"
  fi
done
for link in "$RUNTIME_ROOT/current" "$RESEARCH_TOOLING_ROOT/current" /srv/cripta/universal_entry_observer/current /srv/cripta/universal_entry_consumer/current /srv/cripta/trade_lifecycle/current /srv/cripta/dashboard/universal_entry_source; do
  if [[ -e "$link" || -L "$link" ]]; then
    printf '%s\t%s\n' "$link" "$(readlink -f "$link" 2>/dev/null || printf REGULAR)" >> "$backup/live_links.tsv"
  fi
done

managed_services=(
  cripta-universal-entry-shadow.service
  cripta-strategy-dispatcher.service
  cripta-causal-context-correlator.service
  cripta-u5-oi30s-source-soak.service
  cripta-mayak-v2-weekly-report.service
  cripta-mayak-v2-report.service
  cripta-bybit-latency.service
  cripta-public-trade-archive.timer
  cripta-dashboard.service
  cripta-download-expansion.service
  cripta-dispatcher-v2-context-correlator.service
  cripta-dispatcher-v2.service
  cripta-exit-runtime.service
  cripta-health-monitor.service
  cripta-job-intake.service
  cripta-job-runner.service
  cripta-lifecycle-supervisor.service
  cripta-m3-trade-analyst.service
  cripta-mayak-v2.service
  cripta-opportunity-tracker.service
  cripta-position-supervisor.service
  cripta-private-runtime.service
  cripta-safety-observer.service
  cripta-shadow-command-worker.service
  cripta-universal-entry-consumer.service
  cripta-universal-entry-observer.service
  cripta-universal-exit-shadow.service
  cripta-universal-exit-consumer.service
)

# The templates above are inert for an unprovisioned SUB. If an instance was
# already loaded for a previously approved account, it MUST join the same
# release stop/start and exact-loaded-commit lifecycle as MAIN and Exit.
# Never leave an active SUB process running old code after source cutover.
# Capture the command status (including pipeline errors) before consuming units.
# Process substitution hides list-units failures from mapfile under set -e.
discovered_sub_text="$(
  systemctl list-units --all --type=service --plain --no-legend \
    'cripta-private-runtime@*.service' \
    'cripta-universal-entry-consumer@*.service' \
    'cripta-lifecycle-account@*.service' \
    'cripta-account-position@*.service' | awk '{print $1}' | sort -u
)" || die "SUB unit enumeration failed; release identity cannot be verified"
discovered_sub_units=()
if [[ -n "$discovered_sub_text" ]]; then
  mapfile -t discovered_sub_units <<< "$discovered_sub_text"
fi
for unit in "${discovered_sub_units[@]}"; do
  [[ "$unit" =~ ^cripta-(private-runtime|universal-entry-consumer|lifecycle-account|account-position)@[A-Za-z0-9_.-]+\.service$ ]] ||
    die "unexpected SUB unit identity while preparing release: $unit"
  managed_services+=("$unit")
done

declare -A was_active=()
services_stopped=0
db_mutation_steps=0
cutover_started=0
deploy_succeeded=0

restore_pre_cutover_services() {
  local recovery_failed=0
  systemctl daemon-reload || recovery_failed=1
  for service in "${managed_services[@]}"; do
    if [[ "${was_active[$service]:-0}" == "1" ]]; then
      systemctl start "$service" || recovery_failed=1
    elif systemctl is-active --quiet "$service"; then
      systemctl stop "$service" || recovery_failed=1
    fi
  done
  for service in "${managed_services[@]}"; do
    if [[ "${was_active[$service]:-0}" == "1" ]]; then
      systemctl is-active --quiet "$service" || recovery_failed=1
    else
      systemctl is-active --quiet "$service" && recovery_failed=1
    fi
  done
  if [[ "$recovery_failed" == "0" ]]; then
    echo "PRE_CUTOVER_SERVICE_RECOVERY=PASS"
  else
    echo "PRE_CUTOVER_SERVICE_RECOVERY=FAIL" >&2
  fi
  return "$recovery_failed"
}

release_exit_handler() {
  local rc=$?
  if [[ "$deploy_succeeded" == "1" || "$services_stopped" == "0" ]]; then
    return
  fi
  echo "DEPLOY_FAILED=YES" >&2
  echo "FAILED_RELEASE_COMMIT=$RELEASE_COMMIT" >&2
  echo "DB_MUTATION_STEPS_COMMITTED=$db_mutation_steps" >&2
  echo "CUTOVER_STARTED=$cutover_started" >&2
  if [[ "$cutover_started" == "0" && "$db_mutation_steps" == "0" ]]; then
    if restore_pre_cutover_services; then
      echo "DEPLOY_RECOVERY=PRE_CUTOVER_BASELINE_RESTORED" >&2
    else
      echo "HARD_STOP=YES" >&2
      echo "DEPLOY_RECOVERY=PRE_CUTOVER_SERVICE_RESTORE_FAILED" >&2
    fi
  else
    echo "HARD_STOP=YES" >&2
    echo "DEPLOY_RECOVERY=MANUAL_REQUIRED_DB_OR_CUTOVER_MUTATION" >&2
  fi
  return "$rc"
}

for service in "${managed_services[@]}"; do
  if systemctl is-active --quiet "$service"; then
    was_active["$service"]=1
  else
    was_active["$service"]=0
  fi
done

trap release_exit_handler EXIT
services_stopped=1
for service in "${managed_services[@]}"; do
  if [[ "${was_active[$service]}" == "1" ]]; then
    systemctl stop "$service"
  fi
done

# The rollback dump must describe the quiesced pre-migration baseline.
runuser -u postgres -- pg_dump -Fc -d cripta > "$backup/cripta_before.dump"

migration_files=(
  operations/sql/20260920_slot_admission_v1.sql
  operations/sql/20261006_account_state_generation_v1.sql
  operations/sql/20261006_observer_runtime_fault_v1.sql
  operations/sql/20261015_observer_dashboard_delivery_v1.sql
  operations/sql/20261015_live_arm_retirement_events_v1.sql
  operations/sql/20261003_market_observation_alert_v1.sql
  operations/sql/20261004_r1_reverse_transitions.sql
  operations/sql/20261004_r1_live_arm_waiver.sql
  operations/sql/20261030_p90_portal_micro_live_waiver.sql
  operations/sql/20261007_live_arm_parity_gate.sql
  operations/sql/20261008_r1_entry_cancel_intents.sql
  operations/sql/20261008_exchange_closed_entry_recovery.sql
  operations/sql/20261009_release_arm_incidents.sql
  operations/sql/20261012_effective_protection_evidence_v1.sql
  operations/sql/20261016_identity_registry_r1.sql
  operations/sql/20261016_identity_registry_r2_seed.sql
  operations/sql/20261016_identity_bridge_r3_r4.sql
  operations/sql/20261017_market_instrument_identity_r7a.sql
  operations/sql/20261017_compact_analytics_r8a.sql
  operations/sql/20261017_compact_analytics_r8c.sql
  operations/sql/20261016_private_current_state_r5a.sql
  operations/sql/20261016_execution_evidence_r5b.sql
  operations/sql/20261016_account_scoped_key_preparation_r5.sql
  operations/sql/20261016_strategy_execution_binding_r6a.sql
  operations/sql/20261018_strategy_execution_binding_lock_v1.sql
  operations/sql/20261016_binding_lineage_r6b1.sql
  operations/sql/20261016_command_position_lineage_r6b2.sql
  operations/sql/20261016_exit_account_lineage_r6c.sql
  operations/sql/20261016_trade_command_account_identity_r6d.sql
  operations/sql/20261016_report_replay_provenance_r5.sql
  # Additive account-scoped schema. No arm sessions, accounts or safety-block clears.
  operations/sql/20261024_account_scoped_live_arm.sql
  operations/sql/20261029_p90l90_real_account_arm_control.sql
  operations/sql/20261025_account_entry_safety_blocks.sql
  operations/sql/20261025_account_attempt_fanout.sql
  # Cut over all five physical current-state keys after additive account indexes
  # and complete writer/reader parity gates; no pre-existing rows are rewritten.
  operations/sql/20261026_account_scoped_physical_key_cutover.sql
  # Independent account-scoped Lifecycle/Safety and partial Position observations.
  # Additive only; no past MAIN safety or M3 observer rows are rewritten.
  operations/sql/20261027_account_safety_observation.sql
  operations/sql/20261027_account_position_observer.sql
  # Scoped SECURITY DEFINER writer for two PASSIVE P90L90 bindings only.
  operations/sql/20261028_p90l90_legacy_passive_binding_writer.sql
  # New independent P90 v2 passive binding and PAPER-only causal event buffer.
  operations/sql/20261028_p90l90_strategy_v2_passive_binding_writer.sql
  operations/sql/20261028_p90l90_causal_transport_checkpoints.sql
  operations/sql/20261028_p90l90_atomic_entry_checkpoint.sql
  # Additive provenance split: no historical bar/checkpoint UPDATE.
  operations/sql/20261028_p90l90_data_lineage_compatibility.sql
  # One strategy-agnostic cache owner for verified closed OHLCV, independent of TradingAccount.
  operations/sql/20261028_shared_closed_candle_source.sql
  # Owner-approved exact P90L90 REAL v3 INACTIVE two-account writer only.
  # Never creates enabled Strategy or bypasses per-account LIVE arm.
  operations/sql/20261029_p90l90_real_v3_inactive_binding_writer.sql
  # Additive independent REAL-only source-epoch state; never resets old PAPER evidence.
  operations/sql/20261029_p90l90_real_source_epochs.sql
)
for migration in "${migration_files[@]}"; do
  runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d cripta < "$runtime_release/$migration"
  db_mutation_steps=$((db_mutation_steps + 1))
done

runuser -u cripta -- env \
  PYTHONPATH="$runtime_release/src:$runtime_release/operations/connectivity:$runtime_release/research/server/connectivity:$runtime_release/.venv/lib/python3.12/site-packages" \
  "$runtime_release/.venv/bin/python" \
  "$runtime_release/operations/connectivity/runtime_schema.py" migrate
db_mutation_steps=$((db_mutation_steps + 1))
runuser -u cripta -- env \
  PYTHONPATH="$runtime_release/src:$runtime_release/operations/connectivity:$runtime_release/research/server/connectivity:$runtime_release/.venv/lib/python3.12/site-packages" \
  "$runtime_release/.venv/bin/python" \
  "$runtime_release/operations/connectivity/runtime_schema.py" validate
# Installer does not close LIVE-arm sessions or change execution permissions.
# Post-restart Exchange reconciliation is performed by the private runtime.

# Filesystem/unit cutover starts only after every DB migration and schema
# validation succeeded against the candidate release.
cutover_started=1
mv -Tf "$next_runtime" "$RUNTIME_ROOT/current"
mv -Tf "$next_tooling" "$RESEARCH_TOOLING_ROOT/current"
# A single cutover of the independently versioned UI bundle accompanies the
# database schema and observer runtime. Dashboard app is a regular runtime file.
next_dashboard="$DASHBOARD_UI_ROOT/.current-$RELEASE_COMMIT"
rm -f "$next_dashboard"
ln -s "$ui_release" "$next_dashboard"
mv -Tf "$next_dashboard" "$DASHBOARD_UI_ROOT/current"
install -o cripta -g cripta -m 0644 "$ui_release/app.py" "$runtime_release/operations/dashboard/app.py"
[[ "$(readlink -f "$DASHBOARD_UI_ROOT/current")" == "$ui_release" ]] ||
  die "Dashboard UI cutover identity mismatch"


for spec in "${unit_specs[@]}"; do
  IFS='|' read -r src_rel unit scope <<<"$spec"
  if [[ "$scope" == "tooling" ]]; then source_unit="$tooling_release/$src_rel"; else source_unit="$runtime_release/$src_rel"; fi
  [[ -f "$source_unit" ]] || die "unit source missing: $source_unit"
  install -o root -g root -m 0644 "$source_unit" "/etc/systemd/system/$unit"
done
install -d -o root -g root -m 0755 /etc/systemd/system/cripta-private-runtime.service.d
install -o root -g root -m 0644 "$runtime_release/operations/systemd/cripta-private-runtime.service.d/10-pythonpath.conf" /etc/systemd/system/cripta-private-runtime.service.d/10-pythonpath.conf
install -o root -g root -m 0755 "$runtime_release/operations/infrastructure/cripta-apply-incoming" /usr/local/sbin/cripta-apply-incoming
install -o root -g root -m 0755 "$runtime_release/operations/infrastructure/deploy_dashboard_ui.sh" /usr/local/sbin/cripta-deploy-dashboard-ui

install -d -o root -g cripta -m 0750 /etc/cripta
cat > /etc/cripta/release.env <<EOF
CRIPTA_RELEASE_COMMIT=$RELEASE_COMMIT
CRIPTA_RUNTIME_ROOT=$RUNTIME_ROOT/current
CRIPTA_DASHBOARD_UI_ROOT=$DASHBOARD_UI_ROOT
CRIPTA_RESEARCH_TOOLING_ROOT=$RESEARCH_TOOLING_ROOT/current
EOF
chown root:cripta /etc/cripta/release.env
chmod 0640 /etc/cripta/release.env

for unit in "${retired_units[@]}"; do
  systemctl disable --now "$unit" >/dev/null 2>&1 || true
  rm -f "/etc/systemd/system/$unit"
done

systemctl daemon-reload
for service in "${managed_services[@]}"; do
  if [[ "${was_active[$service]}" == "1" ]]; then systemctl start "$service"; fi
done
for service in "${managed_services[@]}"; do
  if [[ "${was_active[$service]}" == "1" ]]; then
    systemctl is-active --quiet "$service" || die "service failed after deploy: $service"
  else
    systemctl is-active --quiet "$service" && die "previously inactive service became active: $service"
  fi
done

gate_after="$(sql_scalar "SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'")"
permissions_after="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
sessions_after="$(sql_scalar "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'")"
# Trading controls are reported, never used as installer go/no-go decisions.
# A supervisor/runtime may legitimately change trading state during deployment.
if [[ "$gate_after" == "$pre_gate" && "$permissions_after" == "$pre_permissions" && "$sessions_after" == "$pre_sessions" ]]; then
  echo "TRADING_CONTROL_UNCHANGED=PASS"
else
  echo "TRADING_CONTROL_CHANGED_EXTERNALLY=WARNING before:$pre_gate/$pre_permissions/$pre_sessions after:$gate_after/$permissions_after/$sessions_after" >&2
fi
# Exchange -> DB synchronization belongs to private runtime reconciliation.
# Never send Exchange mutations from the installer.
echo "POST_DEPLOY_RECONCILIATION_OWNER=cripta-private-runtime.service"
if [[ "${was_active[cripta-private-runtime.service]:-0}" == "1" ]]; then
  # Wait for the new process to read the authoritative Exchange snapshot.
  # A failed/stale sync is an operational alert, not an Exchange mutation.
  sync_started_ms="$(( $(date -u +%s) * 1000 ))"
  sync_verified=0
  for attempt in {1..30}; do
    successful_ms="$(sql_scalar "SELECT coalesce((SELECT finished_at_epoch_ms FROM runtime.reconciliation_runs WHERE ok=1 ORDER BY id DESC LIMIT 1),0)")"
    if [[ "$successful_ms" =~ ^[0-9]+$ ]] && (( successful_ms >= sync_started_ms )); then
      sync_verified=1
      break
    fi
    sleep 2
  done
  if [[ "$sync_verified" == "1" ]]; then
    echo "POST_DEPLOY_EXCHANGE_TO_DB_RECONCILIATION=PASS"
  else
    echo "POST_DEPLOY_EXCHANGE_TO_DB_RECONCILIATION=NOT_VERIFIED_REQUIRES_RUNTIME_ATTENTION" >&2
  fi
else
  echo "POST_DEPLOY_EXCHANGE_TO_DB_RECONCILIATION=NOT_APPLICABLE_PRIVATE_RUNTIME_INACTIVE"
fi

printf '%s\n' "$RELEASE_COMMIT" > "$STATE_ROOT/INSTALLED_COMMIT"
chown root:cripta "$STATE_ROOT/INSTALLED_COMMIT"
chmod 0640 "$STATE_ROOT/INSTALLED_COMMIT"
printf '%s\n' "$backup" > "$STATE_ROOT/LAST_BACKUP"
chown root:cripta "$STATE_ROOT/LAST_BACKUP"
chmod 0640 "$STATE_ROOT/LAST_BACKUP"

if [[ "$STAGED_OFFLINE" == "1" ]]; then
  # Protect all historic archives and release backups during breaking R10.
  # Pruning may resume only under a separately reviewed retention decision.
  echo "STAGED_OFFLINE_BACKUP_RETENTION=KEEP_ALL"
else
  prune_release_backups
fi

deploy_succeeded=1
trap - EXIT

echo "DEPLOY_EXACT_VERIFIED_COMMIT=PASS"
echo "INSTALLED_COMMIT=$RELEASE_COMMIT"
echo "RUNTIME_CURRENT=$(readlink -f "$RUNTIME_ROOT/current")"
echo "DASHBOARD_UI_CURRENT=$(readlink -f "$DASHBOARD_UI_ROOT/current")"
echo "RESEARCH_TOOLING_CURRENT=$(readlink -f "$RESEARCH_TOOLING_ROOT/current")"
echo "BACKUP=$backup"
echo "GATE=$gate_after"
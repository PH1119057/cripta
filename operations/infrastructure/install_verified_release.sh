#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE="${CRIPTA_SOURCE_CHECKOUT:-/srv/cripta/source_checkout}"
RELEASE_COMMIT="${CRIPTA_RELEASE_COMMIT:-}"
EXPECTED_BASELINE="${CRIPTA_EXPECTED_BASELINE_COMMIT:-}"
EXPECTED_TREE="${CRIPTA_RELEASE_TREE_SHA:-}"
RUNTIME_ROOT="${CRIPTA_RUNTIME_ROOT:-/srv/cripta/runtime}"
RESEARCH_TOOLING_ROOT="${CRIPTA_RESEARCH_TOOLING_ROOT:-/data/cripta/research/tooling}"
BACKUP_ROOT="${CRIPTA_RELEASE_BACKUP_ROOT:-/data/cripta/script_archive/release_backups}"
STATE_ROOT="${CRIPTA_RELEASE_STATE_ROOT:-/var/lib/cripta/release}"

die() { echo "ERROR: $*" >&2; exit 1; }
as_repo_owner() { runuser -u cripta -- env GIT_OPTIONAL_LOCKS=0 "$@"; }
sql_scalar() { runuser -u postgres -- psql -X -Atqc "$1" cripta; }

[[ "$(id -u)" -eq 0 ]] || die "installer must run as root"
exec 9>/run/lock/cripta-install-verified-release.lock
flock -n 9 || die "another release installer is already running"
[[ "$RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "CRIPTA_RELEASE_COMMIT is invalid"
[[ "$EXPECTED_BASELINE" =~ ^[0-9a-f]{40}$ ]] || die "CRIPTA_EXPECTED_BASELINE_COMMIT is invalid"
[[ -d "$SOURCE/.git" ]] || die "source checkout missing"

remote="$(as_repo_owner git -C "$SOURCE" ls-remote origin refs/heads/main | awk 'NR==1{print $1}')"
[[ "$remote" == "$RELEASE_COMMIT" ]] || die "remote main differs from requested release commit"
as_repo_owner git -C "$SOURCE" fetch --no-tags origin "$RELEASE_COMMIT" >/dev/null
tree="$(as_repo_owner git -C "$SOURCE" rev-parse "${RELEASE_COMMIT}^{tree}")"
if [[ -n "$EXPECTED_TREE" && "$tree" != "$EXPECTED_TREE" ]]; then die "release tree mismatch"; fi

gate="$(sql_scalar "SELECT coalesce((SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'),-1)")"
[[ "$gate" == "0" ]] || die "mainnet gate must be disarmed before deploy"
permissions="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
[[ "$permissions" == "0" ]] || die "real Strategy execution permissions must be zero before deploy"
owned_positions="$(sql_scalar "SELECT count(*) FROM runtime.position_ownership WHERE state IN ('OPEN','RECONCILIATION_REQUIRED')")"
[[ "$owned_positions" == "0" ]] || die "open/reconciliation StrategyPosition exists"
hot_positions="$(sql_scalar "SELECT count(*) FROM runtime.hot_positions")"
[[ "$hot_positions" == "0" ]] || die "Exchange hot position exists"
pending_commands="$(sql_scalar "SELECT count(*) FROM runtime.trade_commands WHERE state IN ('queued','running')")"
[[ "$pending_commands" == "0" ]] || die "pending runtime trade command exists"
pending_orders="$(sql_scalar "SELECT count(*) FROM runtime.hot_orders WHERE order_status IN ('New','PartiallyFilled','Untriggered')")"
[[ "$pending_orders" == "0" ]] || die "pending Exchange order exists"

install -d -o cripta -g cripta -m 0750 "$RUNTIME_ROOT" "$RUNTIME_ROOT/releases"
install -d -o cripta -g cripta -m 2770 "$RESEARCH_TOOLING_ROOT" "$RESEARCH_TOOLING_ROOT/releases"
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
  as_repo_owner git -C "$SOURCE" archive --format=tar "$RELEASE_COMMIT" -- research/server/jobs research/server/dataset     | runuser -u cripta -- tar -xf - -C "$tooling_release"
fi

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

stamp="$(date -u +%Y%m%d_%H%M%S)"
backup="$BACKUP_ROOT/${stamp}_${EXPECTED_BASELINE}_to_${RELEASE_COMMIT}"
install -d -o root -g root -m 0700 "$backup"
{
  echo "release_commit=$RELEASE_COMMIT"
  echo "release_tree_sha=$tree"
  echo "expected_baseline_commit=$EXPECTED_BASELINE"
  echo "remote_main=$remote"
  echo "runtime_release=$runtime_release"
  echo "research_tooling_release=$tooling_release"
  echo "created_at=$stamp"
} > "$backup/release_identity.txt"
runuser -u postgres -- pg_dump -Fc -d cripta > "$backup/cripta_before.dump"

unit_specs=(
  "research/server/connectivity/cripta-bybit-latency.service|cripta-bybit-latency.service|runtime"
  "research/server/backup/cripta-backup.service|cripta-backup.service|runtime"
  "research/server/backup/cripta-backup.timer|cripta-backup.timer|runtime"
  "operations/systemd/cripta-dashboard.service|cripta-dashboard.service|runtime"
  "operations/dispatcher_v2/cripta-dispatcher-v2-context-correlator.service|cripta-dispatcher-v2-context-correlator.service|runtime"
  "operations/dispatcher_v2/cripta-dispatcher-v2.service|cripta-dispatcher-v2.service|runtime"
  "operations/monitoring/cripta-exit-runtime.service|cripta-exit-runtime.service|runtime"
  "research/server/monitoring/cripta-health-monitor.service|cripta-health-monitor.service|runtime"
  "research/server/jobs/cripta-job-intake.service|cripta-job-intake.service|tooling"
  "research/server/jobs/cripta-job-runner.service|cripta-job-runner.service|tooling"
  "research/server/dataset/cripta-dataset-manifest.service|cripta-dataset-manifest.service|tooling"
  "operations/systemd/cripta-lifecycle-supervisor.service|cripta-lifecycle-supervisor.service|runtime"
  "operations/systemd/cripta-m3-trade-analyst.service|cripta-m3-trade-analyst.service|runtime"
  "operations/systemd/cripta-mayak-v2.service|cripta-mayak-v2.service|runtime"
  "operations/systemd/cripta-mayak-v2-report.service|cripta-mayak-v2-report.service|runtime"
  "operations/systemd/cripta-mayak-v2-weekly-report.service|cripta-mayak-v2-weekly-report.service|runtime"
  "research/server/monitoring/cripta-opportunity-tracker.service|cripta-opportunity-tracker.service|runtime"
  "operations/monitoring/cripta-position-supervisor.service|cripta-position-supervisor.service|runtime"
  "research/server/connectivity/cripta-private-runtime.service|cripta-private-runtime.service|runtime"
  "research/server/connectivity/cripta-safety-observer.service|cripta-safety-observer.service|runtime"
  "research/server/control/cripta-shadow-command-worker.service|cripta-shadow-command-worker.service|runtime"
  "operations/systemd/cripta-u5-oi30s-source-soak.service|cripta-u5-oi30s-source-soak.service|runtime"
  "operations/systemd/cripta-universal-entry-consumer.service|cripta-universal-entry-consumer.service|runtime"
  "operations/systemd/cripta-universal-entry-observer.service|cripta-universal-entry-observer.service|runtime"
  "operations/systemd/cripta-universal-entry-shadow.service|cripta-universal-entry-shadow.service|runtime"
  "operations/systemd/cripta-universal-exit-shadow.service|cripta-universal-exit-shadow.service|runtime"
  "operations/monitoring/cripta-causal-context-correlator.service|cripta-causal-context-correlator.service|runtime"
  "operations/strategy_dispatcher/cripta-strategy-dispatcher.service|cripta-strategy-dispatcher.service|runtime"
  "operations/systemd/cripta-entry-shadow-scanner.service|cripta-entry-shadow-scanner.service|runtime"
)

for spec in "${unit_specs[@]}"; do
  IFS='|' read -r src_rel unit scope <<<"$spec"
  if [[ -e "/etc/systemd/system/$unit" || -L "/etc/systemd/system/$unit" ]]; then
    install -d -m 0700 "$backup/files/etc/systemd/system"
    cp -a "/etc/systemd/system/$unit" "$backup/files/etc/systemd/system/$unit"
  fi
done
if [[ -d /etc/systemd/system/cripta-private-runtime.service.d ]]; then
  install -d -m 0700 "$backup/files/etc/systemd/system"
  cp -a /etc/systemd/system/cripta-private-runtime.service.d "$backup/files/etc/systemd/system/"
fi
for path in /usr/local/sbin/cripta-apply-incoming /etc/cripta/release.env; do
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
  cripta-entry-shadow-scanner.service
  cripta-strategy-dispatcher.service
  cripta-causal-context-correlator.service
  cripta-u5-oi30s-source-soak.service
  cripta-mayak-v2-weekly-report.service
  cripta-mayak-v2-report.service
  cripta-bybit-latency.service
  cripta-dashboard.service
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
)

declare -A was_active=()
for service in "${managed_services[@]}"; do
  if systemctl is-active --quiet "$service"; then
    was_active["$service"]=1
    systemctl stop "$service"
  else
    was_active["$service"]=0
  fi
done

mv -Tf "$next_runtime" "$RUNTIME_ROOT/current"
mv -Tf "$next_tooling" "$RESEARCH_TOOLING_ROOT/current"

for spec in "${unit_specs[@]}"; do
  IFS='|' read -r src_rel unit scope <<<"$spec"
  if [[ "$scope" == "tooling" ]]; then source_unit="$tooling_release/$src_rel"; else source_unit="$runtime_release/$src_rel"; fi
  [[ -f "$source_unit" ]] || die "unit source missing: $source_unit"
  install -o root -g root -m 0644 "$source_unit" "/etc/systemd/system/$unit"
done
install -d -o root -g root -m 0755 /etc/systemd/system/cripta-private-runtime.service.d
install -o root -g root -m 0644 "$runtime_release/operations/systemd/cripta-private-runtime.service.d/10-pythonpath.conf" /etc/systemd/system/cripta-private-runtime.service.d/10-pythonpath.conf
install -o root -g root -m 0755 "$runtime_release/operations/infrastructure/cripta-apply-incoming" /usr/local/sbin/cripta-apply-incoming

install -d -o root -g cripta -m 0750 /etc/cripta
cat > /etc/cripta/release.env <<EOF
CRIPTA_RELEASE_COMMIT=$RELEASE_COMMIT
CRIPTA_RUNTIME_ROOT=$RUNTIME_ROOT/current
CRIPTA_RESEARCH_TOOLING_ROOT=$RESEARCH_TOOLING_ROOT/current
EOF
chown root:cripta /etc/cripta/release.env
chmod 0640 /etc/cripta/release.env

runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d cripta < "$RUNTIME_ROOT/current/operations/sql/20260920_slot_admission_v1.sql"
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d cripta <<'SQL'
UPDATE control.live_arm_sessions
   SET state='CLOSED',
       deactivated_at=clock_timestamp(),
       updated_at=clock_timestamp()
 WHERE state='ACTIVE';
SQL

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
[[ "$gate_after" == "0" ]] || die "deploy changed mainnet gate"
permissions_after="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
[[ "$permissions_after" == "0" ]] || die "deploy changed real execution permissions"

printf '%s\n' "$RELEASE_COMMIT" > "$STATE_ROOT/INSTALLED_COMMIT"
chown root:cripta "$STATE_ROOT/INSTALLED_COMMIT"
chmod 0640 "$STATE_ROOT/INSTALLED_COMMIT"
printf '%s\n' "$backup" > "$STATE_ROOT/LAST_BACKUP"
chown root:cripta "$STATE_ROOT/LAST_BACKUP"
chmod 0640 "$STATE_ROOT/LAST_BACKUP"

echo "DEPLOY_EXACT_VERIFIED_COMMIT=PASS"
echo "INSTALLED_COMMIT=$RELEASE_COMMIT"
echo "RUNTIME_CURRENT=$(readlink -f "$RUNTIME_ROOT/current")"
echo "RESEARCH_TOOLING_CURRENT=$(readlink -f "$RESEARCH_TOOLING_ROOT/current")"
echo "BACKUP=$backup"
echo "GATE=DISARMED"
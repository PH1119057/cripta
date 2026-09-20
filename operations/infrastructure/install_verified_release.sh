#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE="${CRIPTA_SOURCE_CHECKOUT:-/srv/cripta/source_checkout}"
RELEASE_COMMIT="${CRIPTA_RELEASE_COMMIT:-}"
EXPECTED_BASELINE="${CRIPTA_EXPECTED_BASELINE_COMMIT:-}"
EXPECTED_TREE="${CRIPTA_RELEASE_TREE_SHA:-}"
BACKUP_ROOT="${CRIPTA_RELEASE_BACKUP_ROOT:-/srv/cripta/backups/releases}"
STATE_ROOT="${CRIPTA_RELEASE_STATE_ROOT:-/var/lib/cripta/release}"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

as_repo_owner() {
  runuser -u cripta -- env GIT_OPTIONAL_LOCKS=0 "$@"
}

sql_scalar() {
  runuser -u postgres -- psql -X -Atqc "$1" cripta
}

[[ "$(id -u)" -eq 0 ]] || die "installer must run as root"
exec 9>/run/lock/cripta-install-verified-release.lock
flock -n 9 || die "another release installer is already running"
[[ "$RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "CRIPTA_RELEASE_COMMIT is invalid"
[[ "$EXPECTED_BASELINE" =~ ^[0-9a-f]{40}$ ]]   || die "CRIPTA_EXPECTED_BASELINE_COMMIT is invalid"
[[ -d "$SOURCE/.git" ]] || die "source checkout missing"

remote="$(
  as_repo_owner git -C "$SOURCE" ls-remote origin refs/heads/main | awk 'NR==1{print $1}'
)"
[[ "$remote" == "$RELEASE_COMMIT" ]]   || die "remote main differs from requested release commit"

as_repo_owner git -C "$SOURCE" fetch --no-tags origin "$RELEASE_COMMIT" >/dev/null
tree="$(as_repo_owner git -C "$SOURCE" rev-parse "${RELEASE_COMMIT}^{tree}")"
if [[ -n "$EXPECTED_TREE" && "$tree" != "$EXPECTED_TREE" ]]; then
  die "release tree mismatch"
fi

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

stamp="$(date -u +%Y%m%d_%H%M%S)"
backup="$BACKUP_ROOT/${stamp}_${EXPECTED_BASELINE}_to_${RELEASE_COMMIT}"
install -d -o root -g root -m 0700 "$backup"

{
  echo "release_commit=$RELEASE_COMMIT"
  echo "release_tree_sha=$tree"
  echo "expected_baseline_commit=$EXPECTED_BASELINE"
  echo "remote_main=$remote"
  echo "created_at=$stamp"
} > "$backup/release_identity.txt"

runuser -u postgres -- pg_dump -Fc -d cripta > "$backup/cripta_before.dump"

for path in   /etc/systemd/system/cripta-universal-entry-observer.service   /etc/systemd/system/cripta-universal-entry-consumer.service   /etc/systemd/system/cripta-lifecycle-supervisor.service   /etc/systemd/system/cripta-universal-exit-shadow.service   /etc/systemd/system/cripta-dashboard.service   /usr/local/sbin/cripta-apply-incoming   /srv/cripta/dashboard/app.py   /srv/cripta/dashboard/universal_entry_source   /srv/cripta/connectivity/private_runtime.py   /srv/cripta/connectivity/runtime_schema.py   /etc/cripta/release.env
do
  if [[ -e "$path" || -L "$path" ]]; then
    safe="${path#/}"
    install -d -m 0700 "$backup/files/$(dirname "$safe")"
    cp -a "$path" "$backup/files/$safe"
  fi
done

for link in   /srv/cripta/universal_entry_observer/current   /srv/cripta/universal_entry_consumer/current   /srv/cripta/trade_lifecycle/current   /srv/cripta/dashboard/universal_entry_source
do
  if [[ -e "$link" || -L "$link" ]]; then
    printf '%s\t%s\n' "$link" "$(readlink -f "$link" 2>/dev/null || printf REGULAR)"       >> "$backup/live_links.tsv"
  fi
done

declare -A was_active=()
services=(
  cripta-universal-entry-observer.service
  cripta-universal-entry-consumer.service
  cripta-lifecycle-supervisor.service
  cripta-universal-exit-shadow.service
  cripta-dashboard.service
  cripta-private-runtime.service
)
for service in "${services[@]}"; do
  if systemctl is-active --quiet "$service"; then
    was_active["$service"]=1
    systemctl stop "$service"
  else
    was_active["$service"]=0
  fi
done

make_release() {
  local root="$1"
  local release="$root/releases/$RELEASE_COMMIT"
  if [[ ! -d "$release" ]]; then
    install -d -o cripta -g cripta -m 0750 "$release"
    as_repo_owner git -C "$SOURCE" archive --format=tar "$RELEASE_COMMIT"       | runuser -u cripta -- tar -xf - -C "$release"
  fi
  printf '%s\n' "$RELEASE_COMMIT" > "$release/INSTALLED_COMMIT"
  chown cripta:cripta "$release/INSTALLED_COMMIT"
  chmod 0640 "$release/INSTALLED_COMMIT"
  local next="$root/.current-${RELEASE_COMMIT}"
  rm -f "$next"
  ln -s "$release" "$next"
  mv -Tf "$next" "$root/current"
}

make_release /srv/cripta/universal_entry_observer
make_release /srv/cripta/universal_entry_consumer
make_release /srv/cripta/trade_lifecycle

rm -rf /srv/cripta/dashboard/universal_entry_source
ln -s /srv/cripta/trade_lifecycle/current /srv/cripta/dashboard/universal_entry_source

rm -f /srv/cripta/dashboard/app.py
ln -s /srv/cripta/trade_lifecycle/current/operations/dashboard/app.py   /srv/cripta/dashboard/app.py

rm -f /srv/cripta/connectivity/private_runtime.py
ln -s /srv/cripta/trade_lifecycle/current/operations/connectivity/private_runtime.py   /srv/cripta/connectivity/private_runtime.py
rm -f /srv/cripta/connectivity/runtime_schema.py
ln -s /srv/cripta/trade_lifecycle/current/operations/connectivity/runtime_schema.py   /srv/cripta/connectivity/runtime_schema.py

install -o root -g root -m 0644   /srv/cripta/trade_lifecycle/current/operations/systemd/cripta-universal-entry-observer.service   /etc/systemd/system/cripta-universal-entry-observer.service
install -o root -g root -m 0644   /srv/cripta/trade_lifecycle/current/operations/systemd/cripta-universal-entry-consumer.service   /etc/systemd/system/cripta-universal-entry-consumer.service
install -o root -g root -m 0644   /srv/cripta/trade_lifecycle/current/operations/systemd/cripta-lifecycle-supervisor.service   /etc/systemd/system/cripta-lifecycle-supervisor.service
install -o root -g root -m 0644   /srv/cripta/trade_lifecycle/current/operations/systemd/cripta-universal-exit-shadow.service   /etc/systemd/system/cripta-universal-exit-shadow.service
install -o root -g root -m 0644   /srv/cripta/trade_lifecycle/current/operations/systemd/cripta-dashboard.service   /etc/systemd/system/cripta-dashboard.service
install -o root -g root -m 0755   /srv/cripta/trade_lifecycle/current/operations/infrastructure/cripta-apply-incoming   /usr/local/sbin/cripta-apply-incoming

install -d -o root -g cripta -m 0750 /etc/cripta
printf 'CRIPTA_RELEASE_COMMIT=%s\n' "$RELEASE_COMMIT"   > /etc/cripta/release.env
chown root:cripta /etc/cripta/release.env
chmod 0640 /etc/cripta/release.env

runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d cripta \
  < /srv/cripta/trade_lifecycle/current/operations/sql/20260920_slot_admission_v1.sql

runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d cripta <<'SQL'
UPDATE control.live_arm_sessions
   SET state='CLOSED',
       deactivated_at=clock_timestamp(),
       updated_at=clock_timestamp()
 WHERE state='ACTIVE';
SQL

systemctl daemon-reload

for service in "${services[@]}"; do
  if [[ "${was_active[$service]}" == "1" ]]; then
    systemctl start "$service"
  fi
done

for service in "${services[@]}"; do
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

install -d -o root -g cripta -m 0750 "$STATE_ROOT"
printf '%s\n' "$RELEASE_COMMIT" > "$STATE_ROOT/INSTALLED_COMMIT"
chown root:cripta "$STATE_ROOT/INSTALLED_COMMIT"
chmod 0640 "$STATE_ROOT/INSTALLED_COMMIT"

printf '%s\n' "$backup" > "$STATE_ROOT/LAST_BACKUP"
chown root:cripta "$STATE_ROOT/LAST_BACKUP"
chmod 0640 "$STATE_ROOT/LAST_BACKUP"

echo "DEPLOY_EXACT_VERIFIED_COMMIT=PASS"
echo "INSTALLED_COMMIT=$RELEASE_COMMIT"
echo "BACKUP=$backup"
echo "GATE=DISARMED"

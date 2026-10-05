#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE="${CRIPTA_SOURCE_CHECKOUT:-/srv/cripta/source_checkout}"
UI_COMMIT="${CRIPTA_DASHBOARD_UI_COMMIT:-}"
UI_ROOT="${CRIPTA_DASHBOARD_UI_ROOT:-/srv/cripta/dashboard-ui}"
RUNTIME_ROOT="${CRIPTA_RUNTIME_ROOT:-/srv/cripta/runtime}"
STATE_ROOT="${CRIPTA_RELEASE_STATE_ROOT:-/var/lib/cripta/release}"

die() { echo "ERROR: $*" >&2; exit 1; }
as_repo_owner() { runuser -u cripta -- env GIT_OPTIONAL_LOCKS=0 "$@"; }
sql_scalar() { runuser -u postgres -- psql -X -Atqc "$1" cripta; }

[[ "$(id -u)" -eq 0 ]] || die "dashboard UI deploy must run as root"
[[ "$UI_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "CRIPTA_DASHBOARD_UI_COMMIT is invalid"
[[ -d "$SOURCE/.git" ]] || die "source checkout missing"

exec 9>/run/lock/cripta-dashboard-ui-deploy.lock
flock -n 9 || die "another dashboard UI deploy is running"

remote_main="$(as_repo_owner git -C "$SOURCE" ls-remote origin refs/heads/main | awk 'NR==1{print $1}')"
[[ "$remote_main" =~ ^[0-9a-f]{40}$ ]] || die "cannot resolve remote main"
as_repo_owner git -C "$SOURCE" fetch --no-tags origin main >/dev/null
as_repo_owner git -C "$SOURCE" cat-file -e "$UI_COMMIT^{commit}" || die "UI commit missing locally"
as_repo_owner git -C "$SOURCE" merge-base --is-ancestor "$UI_COMMIT" "$remote_main" ||
  die "UI commit is not reachable from approved remote main"

current_runtime="$(readlink -f "$RUNTIME_ROOT/current" 2>/dev/null || true)"
[[ -n "$current_runtime" && -d "$current_runtime/operations/dashboard" ]] ||
  die "current runtime dashboard path missing"
live_path="$current_runtime/operations/dashboard/index.html"

previous_commit=""
if [[ -r "$STATE_ROOT/DASHBOARD_UI_COMMIT" ]]; then
  previous_commit="$(cat "$STATE_ROOT/DASHBOARD_UI_COMMIT")"
elif [[ -r "$current_runtime/INSTALLED_COMMIT" ]]; then
  previous_commit="$(cat "$current_runtime/INSTALLED_COMMIT")"
fi
[[ "$previous_commit" =~ ^[0-9a-f]{40}$ ]] || die "cannot resolve previous UI baseline commit"
as_repo_owner git -C "$SOURCE" cat-file -e "$previous_commit^{commit}" ||
  die "previous UI baseline commit missing"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
as_repo_owner git -C "$SOURCE" show "$previous_commit:operations/dashboard/index.html" > "$tmp/old.html"
as_repo_owner git -C "$SOURCE" show "$UI_COMMIT:operations/dashboard/index.html" > "$tmp/new.html"
[[ -s "$tmp/new.html" ]] || die "new UI asset is empty"

python3 - "$tmp/old.html" "$tmp/new.html" <<'PY'
from pathlib import Path
import re
import sys

old = Path(sys.argv[1]).read_text(encoding="utf-8")
new = Path(sys.argv[2]).read_text(encoding="utf-8")

# Presentation-only rail is intentionally strict. Any change touching network
# mutation/control/auth semantics must fall back to the full runtime release.
sensitive_tokens = (
    "/api/",
    "fetch(",
    "livePost(",
    "tradeCommand(",
    "changeTradeGate(",
    "toggleStrategyExecution(",
    "toggleStrategyState(",
    "setTrailing(",
    "Set-Cookie",
    "cripta_session",
)

def protected_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if any(token in line for token in sensitive_tokens)
    ]

if protected_lines(old) != protected_lines(new):
    raise SystemExit("presentation-only verification failed: control/API/auth lines changed")

old_endpoints = sorted(set(re.findall(r"/api/[A-Za-z0-9_./?-]+", old)))
new_endpoints = sorted(set(re.findall(r"/api/[A-Za-z0-9_./?-]+", new)))
if old_endpoints != new_endpoints:
    raise SystemExit("presentation-only verification failed: API endpoint set changed")

print("PRESENTATION_ONLY_SCOPE=PASS")
PY

gate_before="$(sql_scalar "SELECT coalesce((SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'),-1)")"
permissions_before="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
activations_before="$(sql_scalar "SELECT count(*) FROM strategy_entry.strategy_activations WHERE enabled=true")"

release_dir="$UI_ROOT/releases/$UI_COMMIT"
install -d -o root -g cripta -m 0755 "$UI_ROOT" "$UI_ROOT/releases"
if [[ ! -d "$release_dir" ]]; then
  install -d -o root -g cripta -m 0755 "$release_dir"
fi
install -o root -g cripta -m 0644 "$tmp/new.html" "$release_dir/index.html"
printf '%s\n' "$UI_COMMIT" > "$release_dir/DASHBOARD_UI_COMMIT"
chown root:cripta "$release_dir/DASHBOARD_UI_COMMIT"
chmod 0644 "$release_dir/DASHBOARD_UI_COMMIT"

next_ui="$UI_ROOT/.current-$UI_COMMIT"
rm -f "$next_ui"
ln -s "$release_dir" "$next_ui"
mv -Tf "$next_ui" "$UI_ROOT/current"

# Dashboard app reads index.html on every request. Point only this presentation
# asset at the independent UI release. No service restart is needed.
next_live="$current_runtime/operations/dashboard/.index.html-$UI_COMMIT"
rm -f "$next_live"
ln -s "$UI_ROOT/current/index.html" "$next_live"
mv -Tf "$next_live" "$live_path"

install -d -o root -g cripta -m 0750 "$STATE_ROOT"
printf '%s\n' "$UI_COMMIT" > "$STATE_ROOT/DASHBOARD_UI_COMMIT"
chown root:cripta "$STATE_ROOT/DASHBOARD_UI_COMMIT"
chmod 0640 "$STATE_ROOT/DASHBOARD_UI_COMMIT"

source_sha="$(sha256sum "$release_dir/index.html" | awk '{print $1}')"
live_sha="$(sha256sum "$live_path" | awk '{print $1}')"
[[ "$source_sha" == "$live_sha" ]] || die "UI source/live hash mismatch"

gate_after="$(sql_scalar "SELECT coalesce((SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'),-1)")"
permissions_after="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
activations_after="$(sql_scalar "SELECT count(*) FROM strategy_entry.strategy_activations WHERE enabled=true")"

[[ "$gate_after" == "$gate_before" ]] || die "UI deploy changed mainnet gate"
[[ "$permissions_after" == "$permissions_before" ]] || die "UI deploy changed execution permissions"
[[ "$activations_after" == "$activations_before" ]] || die "UI deploy changed StrategyActivation count"

cat > "$STATE_ROOT/DASHBOARD_UI_DEPLOY.json" <<EOF
{
  "dashboard_ui_commit": "$UI_COMMIT",
  "previous_dashboard_ui_commit": "$previous_commit",
  "live_path": "$live_path",
  "source_sha256": "$source_sha",
  "live_sha256": "$live_sha",
  "mainnet_gate_before": $gate_before,
  "mainnet_gate_after": $gate_after,
  "execution_permissions_before": $permissions_before,
  "execution_permissions_after": $permissions_after,
  "strategy_activations_before": $activations_before,
  "strategy_activations_after": $activations_after
}
EOF
chown root:cripta "$STATE_ROOT/DASHBOARD_UI_DEPLOY.json"
chmod 0640 "$STATE_ROOT/DASHBOARD_UI_DEPLOY.json"

echo "DASHBOARD_UI_DEPLOY=PASS"
echo "DASHBOARD_UI_COMMIT=$UI_COMMIT"
echo "LIVE_PATH=$live_path"
echo "LIVE_SHA256=$live_sha"
echo "GATE_UNCHANGED=$gate_after"
echo "EXECUTION_PERMISSIONS_UNCHANGED=$permissions_after"
echo "TRADING_SERVICES_RESTARTED=0"

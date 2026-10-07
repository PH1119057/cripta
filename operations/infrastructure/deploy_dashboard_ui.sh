#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE="${CRIPTA_SOURCE_CHECKOUT:-/srv/cripta/source_checkout}"
UI_COMMIT="${CRIPTA_DASHBOARD_UI_COMMIT:-}"
UI_ROOT="${CRIPTA_DASHBOARD_UI_ROOT:-/srv/cripta/dashboard-ui}"
RUNTIME_ROOT="${CRIPTA_RUNTIME_ROOT:-/srv/cripta/runtime}"
STATE_ROOT="${CRIPTA_RELEASE_STATE_ROOT:-/var/lib/cripta/release}"
VERIFY_ONLY="${CRIPTA_DASHBOARD_UI_VERIFY_ONLY:-0}"

die() { echo "ERROR: $*" >&2; exit 1; }
as_repo_owner() { runuser -u cripta -- env GIT_OPTIONAL_LOCKS=0 "$@"; }
sql_scalar() { runuser -u postgres -- psql -X -Atqc "$1" cripta; }

[[ "$(id -u)" -eq 0 ]] || die "dashboard UI deploy must run as root"
[[ "$UI_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "CRIPTA_DASHBOARD_UI_COMMIT is invalid"
[[ "$VERIFY_ONLY" == "0" || "$VERIFY_ONLY" == "1" ]] ||
  die "CRIPTA_DASHBOARD_UI_VERIFY_ONLY must be 0 or 1"
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
as_repo_owner git -C "$SOURCE" show "$previous_commit:operations/dashboard/app.py" > "$tmp/old_app.py"
as_repo_owner git -C "$SOURCE" show "$UI_COMMIT:operations/dashboard/app.py" > "$tmp/new_app.py"
[[ -s "$tmp/new.html" && -s "$tmp/new_app.py" ]] || die "Dashboard bundle is incomplete"

python3 - "$tmp/old.html" "$tmp/new.html" "$tmp/old_app.py" "$tmp/new_app.py" <<'PY'
from pathlib import Path
import ast
import difflib
import re
import sys

old_html = Path(sys.argv[1]).read_text(encoding="utf-8")
new_html = Path(sys.argv[2]).read_text(encoding="utf-8")
old_app = Path(sys.argv[3]).read_text(encoding="utf-8")
new_app = Path(sys.argv[4]).read_text(encoding="utf-8")

control_function_names = {
    "botAction",
    "packageProject",
    "livePost",
    "livePostTimed",
    "changeTradeGate",
    "tradeCommand",
    "setTrailing",
    "saveStrategyVersion",
    "toggleStrategyState",
    "toggleStrategyExecution",
}

def protected_control_functions(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    pattern = re.compile(r"^\\s*(?:async\\s+)?function\\s+([A-Za-z0-9_]+)\\b")
    for line in text.splitlines():
        match = pattern.match(line)
        if match and match.group(1) in control_function_names:
            out[match.group(1)] = line.strip()
    return out

if protected_control_functions(old_html) != protected_control_functions(new_html):
    raise SystemExit("Dashboard verifier failed: HTML control function changed")

old_endpoints = set(re.findall(r"/api/[A-Za-z0-9_./?-]+", old_html))
new_endpoints = set(re.findall(r"/api/[A-Za-z0-9_./?-]+", new_html))
removed_endpoints = old_endpoints - new_endpoints
added_endpoints = new_endpoints - old_endpoints
allowed_added_endpoints = {"/api/observer-faults/resolve"}
if removed_endpoints:
    raise SystemExit(
        "Dashboard verifier failed: API endpoint removed: "
        + ",".join(sorted(removed_endpoints))
    )
if added_endpoints - allowed_added_endpoints:
    raise SystemExit(
        "Dashboard verifier failed: unauthorized API endpoint added: "
        + ",".join(sorted(added_endpoints - allowed_added_endpoints))
    )

for token in ("Set-Cookie", "cripta_session"):
    if old_html.count(token) != new_html.count(token):
        raise SystemExit(f"Dashboard verifier failed: auth marker changed: {token}")

allowed_functions = {
    "_paper_entry_fee_rate",
    "_paper_exit_fee_rate",
    "_paper_net_pnl_usdt",
    "_signal_monitor_summary",
    "strategy_signal_monitor_state",
    "strategy_paper_state",
    "_live_trading_state",
    "export_trading_table",
    "observer_runtime_fault_state",
    "merge_observer_fault_health",
    "resolve_observer_runtime_fault",
    "_r1_paper_real_parity_attestation",
    "_u6_prepare_r1_prearm_evidence",
    "snapshot",
}
allowed_assignments = {
    "PAPER_MAKER_FEE_RATE",
    "PAPER_TAKER_FEE_RATE",
    "REAL_IMMEDIATE_CLOSE_FEE_RATE",
    "OBSERVER_RUNTIME_FAULT_CODE",
    "R1_PARITY_ATTESTED_MODULE_SHA256",
}
forbidden_call_tokens = (
    "arm_r1_micro_live",
    "disarm_r1_micro_live",
    "set_execution_permission",
)
mutation_sql = re.compile(r"\\b(INSERT\\s+INTO|UPDATE\\s+|DELETE\\s+FROM|ALTER\\s+TABLE|DROP\\s+|TRUNCATE\\s+)\\b", re.I)

def top_level(tree: ast.Module) -> dict[tuple[str, str], ast.AST]:
    out: dict[tuple[str, str], ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[(type(node).__name__, node.name)] = node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out[("import", ast.dump(node, include_attributes=False))] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = []
            for target in targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
            key = ",".join(names) if names else ast.dump(node, include_attributes=False)
            out[("assign", key)] = node
        else:
            out[(type(node).__name__, ast.dump(node, include_attributes=False))] = node
    return out

old_tree = ast.parse(old_app)
new_tree = ast.parse(new_app)
old_nodes = top_level(old_tree)
new_nodes = top_level(new_tree)

all_keys = set(old_nodes) | set(new_nodes)
changed: list[tuple[str, str]] = []
for key in sorted(all_keys):
    a = old_nodes.get(key)
    b = new_nodes.get(key)
    if a is None or b is None or ast.dump(a, include_attributes=False) != ast.dump(b, include_attributes=False):
        changed.append(key)

for kind, name in changed:
    if kind in {"FunctionDef", "AsyncFunctionDef"} and name in allowed_functions:
        node = new_nodes.get((kind, name))
        if node is not None:
            src = ast.get_source_segment(new_app, node) or ""
            if any(token in src for token in forbidden_call_tokens):
                raise SystemExit(f"Dashboard verifier failed: forbidden control call in {name}")
            mutation_strings: list[str] = []
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and mutation_sql.search(child.value)
                ):
                    mutation_strings.append(child.value)
            if name == "resolve_observer_runtime_fault":
                if (
                    "UPDATE runtime.lifecycle_faults" not in src
                    or "state='RESOLVED'" not in src
                    or "state='OPEN'" not in src
                    or "fault_code=%s" not in src
                ):
                    raise SystemExit(
                        "Dashboard verifier failed: observer fault resolution contract changed"
                    )
                if any(
                    "UPDATE runtime.lifecycle_faults" not in value
                    for value in mutation_strings
                ):
                    raise SystemExit(
                        "Dashboard verifier failed: unauthorized mutation SQL in "
                        "resolve_observer_runtime_fault"
                    )
            elif name == "_u6_prepare_r1_prearm_evidence":
                if any(
                    "INSERT INTO control.live_arm_evidence" not in value
                    for value in mutation_strings
                ):
                    raise SystemExit(
                        "Dashboard verifier failed: unauthorized mutation SQL in "
                        "_u6_prepare_r1_prearm_evidence"
                    )
                if "PAPER_REAL_DECISION_PARITY" not in src:
                    raise SystemExit(
                        "Dashboard verifier failed: parity evidence missing from prearm"
                    )
            elif mutation_strings:
                raise SystemExit(f"Dashboard verifier failed: mutation SQL in {name}")
        continue
    if kind == "ClassDef" and name == "Handler":
        old_class = old_nodes[(kind, name)]
        new_class = new_nodes[(kind, name)]
        old_methods = {
            child.name: child
            for child in old_class.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        new_methods = {
            child.name: child
            for child in new_class.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if set(old_methods) != set(new_methods):
            raise SystemExit("Dashboard verifier failed: Handler method set changed")
        changed_methods = [
            method_name
            for method_name in sorted(old_methods)
            if ast.dump(old_methods[method_name], include_attributes=False)
            != ast.dump(new_methods[method_name], include_attributes=False)
        ]
        if changed_methods != ["do_POST"]:
            raise SystemExit(
                "Dashboard verifier failed: unexpected Handler method change: "
                + ",".join(changed_methods)
            )
        old_post = ast.get_source_segment(old_app, old_methods["do_POST"]) or ""
        new_post = ast.get_source_segment(new_app, new_methods["do_POST"]) or ""
        diff_lines = list(difflib.ndiff(old_post.splitlines(), new_post.splitlines()))
        removed = [
            line[2:]
            for line in diff_lines
            if line.startswith("- ") and line[2:].strip()
        ]
        added = "\n".join(
            line[2:]
            for line in diff_lines
            if line.startswith("+ ") and line[2:].strip()
        )
        if removed:
            raise SystemExit(
                "Dashboard verifier failed: existing Handler.do_POST code removed"
            )
        if '/api/observer-faults/resolve' not in added:
            raise SystemExit(
                "Dashboard verifier failed: observer fault resolve endpoint missing"
            )
        forbidden_added = (
            "/api/live/",
            "runtime.trade_commands",
            "execution_permissions",
            "arm_r1_micro_live",
            "disarm_r1_micro_live",
            "set_execution_permission",
        )
        if any(token in added for token in forbidden_added):
            raise SystemExit(
                "Dashboard verifier failed: trading control added to Handler.do_POST"
            )
        continue
    if kind == "assign" and name in allowed_assignments:
        continue
    raise SystemExit(f"Dashboard verifier failed: non-read-model app.py change: {kind} {name}")

print("DASHBOARD_PRESENTATION_READ_MODEL_SCOPE=PASS")
print("APP_CHANGED_NODES=" + ",".join(f"{k}:{n}" for k,n in changed))
PY

if [[ "$VERIFY_ONLY" == "1" ]]; then
  echo "DASHBOARD_UI_VERIFY_ONLY=PASS"
  echo "DASHBOARD_UI_BASELINE_COMMIT=$previous_commit"
  echo "DASHBOARD_UI_CANDIDATE_COMMIT=$UI_COMMIT"
  exit 0
fi

# Capture trading-service identities before any Dashboard switch.
trading_units=(
  cripta-universal-entry-observer.service
  cripta-universal-entry-consumer.service
  cripta-private-runtime.service
  cripta-universal-exit-consumer.service
  cripta-lifecycle-supervisor.service
  cripta-r1-reverse-worker.service
)
: > "$tmp/trading_before.tsv"
for unit in "${trading_units[@]}"; do
  printf '%s\t%s\t%s\n' "$unit"     "$(systemctl show "$unit" -p MainPID --value 2>/dev/null || true)"     "$(systemctl show "$unit" -p NRestarts --value 2>/dev/null || true)"     >> "$tmp/trading_before.tsv"
done

gate_before="$(sql_scalar "SELECT coalesce((SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'),-1)")"
permissions_before="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
activations_before="$(sql_scalar "SELECT count(*) FROM strategy_entry.strategy_activations WHERE enabled=true")"

release_dir="$UI_ROOT/releases/$UI_COMMIT"
install -d -o root -g cripta -m 0755 "$UI_ROOT" "$UI_ROOT/releases"
if [[ ! -d "$release_dir" ]]; then
  install -d -o root -g cripta -m 0755 "$release_dir"
fi
install -o root -g cripta -m 0644 "$tmp/new.html" "$release_dir/index.html"
install -o root -g cripta -m 0644 "$tmp/new_app.py" "$release_dir/app.py"
printf '%s\n' "$UI_COMMIT" > "$release_dir/DASHBOARD_UI_COMMIT"
chown root:cripta "$release_dir/DASHBOARD_UI_COMMIT"
chmod 0644 "$release_dir/DASHBOARD_UI_COMMIT"

next_ui="$UI_ROOT/.current-$UI_COMMIT"
rm -f "$next_ui"
ln -s "$release_dir" "$next_ui"
mv -Tf "$next_ui" "$UI_ROOT/current"

# Switch the independent presentation/read-model bundle atomically per path.
next_live="$current_runtime/operations/dashboard/.index.html-$UI_COMMIT"
rm -f "$next_live"
ln -s "$UI_ROOT/current/index.html" "$next_live"
mv -Tf "$next_live" "$live_path"

live_app="$current_runtime/operations/dashboard/app.py"
next_app="$current_runtime/operations/dashboard/.app.py-$UI_COMMIT"
rm -f "$next_app"
install -o cripta -g cripta -m 0644 "$UI_ROOT/current/app.py" "$next_app"
mv -Tf "$next_app" "$live_app"

# Keep app.py physically inside operations/dashboard so sibling imports such as
# archive_v2 remain resolvable through Python's script-directory sys.path.
[[ -f "$live_app" && ! -L "$live_app" ]] || die "Dashboard app must be a regular in-directory file"

# Static-only changes need no restart. A read-model backend change restarts only
# the Dashboard process; no trading service may be touched.
old_app_sha="$(sha256sum "$tmp/old_app.py" | awk '{print $1}')"
new_app_sha="$(sha256sum "$tmp/new_app.py" | awk '{print $1}')"
dashboard_restarted=0
if [[ "$old_app_sha" != "$new_app_sha" ]]; then
  systemctl restart cripta-dashboard.service
  systemctl is-active --quiet cripta-dashboard.service || die "Dashboard restart failed"
  dashboard_restarted=1
fi

install -d -o root -g cripta -m 0750 "$STATE_ROOT"
printf '%s\n' "$UI_COMMIT" > "$STATE_ROOT/DASHBOARD_UI_COMMIT"
chown root:cripta "$STATE_ROOT/DASHBOARD_UI_COMMIT"
chmod 0640 "$STATE_ROOT/DASHBOARD_UI_COMMIT"

html_source_sha="$(sha256sum "$release_dir/index.html" | awk '{print $1}')"
html_live_sha="$(sha256sum "$live_path" | awk '{print $1}')"
app_source_sha="$(sha256sum "$release_dir/app.py" | awk '{print $1}')"
app_live_sha="$(sha256sum "$live_app" | awk '{print $1}')"
[[ "$html_source_sha" == "$html_live_sha" ]] || die "Dashboard HTML source/live hash mismatch"
[[ "$app_source_sha" == "$app_live_sha" ]] || die "Dashboard app source/live hash mismatch"

gate_after="$(sql_scalar "SELECT coalesce((SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'),-1)")"
permissions_after="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
activations_after="$(sql_scalar "SELECT count(*) FROM strategy_entry.strategy_activations WHERE enabled=true")"

[[ "$gate_after" == "$gate_before" ]] || die "Dashboard deploy changed mainnet gate"
[[ "$permissions_after" == "$permissions_before" ]] || die "Dashboard deploy changed execution permissions"
[[ "$activations_after" == "$activations_before" ]] || die "Dashboard deploy changed StrategyActivation count"

while IFS="$(printf '\t')" read -r unit pid_before restarts_before; do
  pid_after="$(systemctl show "$unit" -p MainPID --value 2>/dev/null || true)"
  restarts_after="$(systemctl show "$unit" -p NRestarts --value 2>/dev/null || true)"
  [[ "$pid_after" == "$pid_before" ]] || die "Dashboard deploy changed trading service PID: $unit"
  [[ "$restarts_after" == "$restarts_before" ]] || die "Dashboard deploy restarted trading service: $unit"
done < "$tmp/trading_before.tsv"

cat > "$STATE_ROOT/DASHBOARD_UI_DEPLOY.json" <<EOF
{
  "dashboard_ui_commit": "$UI_COMMIT",
  "previous_dashboard_ui_commit": "$previous_commit",
  "live_path": "$live_path",
  "html_source_sha256": "$html_source_sha",
  "html_live_sha256": "$html_live_sha",
  "app_source_sha256": "$app_source_sha",
  "app_live_sha256": "$app_live_sha",
  "dashboard_restarted": $dashboard_restarted,
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
echo "HTML_LIVE_SHA256=$html_live_sha"
echo "APP_LIVE_SHA256=$app_live_sha"
echo "DASHBOARD_RESTARTED=$dashboard_restarted"
echo "GATE_UNCHANGED=$gate_after"
echo "EXECUTION_PERMISSIONS_UNCHANGED=$permissions_after"
echo "TRADING_SERVICES_RESTARTED=0"

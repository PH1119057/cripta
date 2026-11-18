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
RELEASE_CONTROLLER_STATE_ROOT="${CRIPTA_RELEASE_CONTROLLER_STATE_ROOT:-/var/lib/cripta/release-controller}"
CONTROL_CHECKPOINT="${CRIPTA_RELEASE_CONTROL_CHECKPOINT:-0}"
CONTROL_REASON="${CRIPTA_RELEASE_CONTROL_REASON:-}"
STAGED_OFFLINE="${CRIPTA_STAGED_OFFLINE_CUTOVER:-0}"
# Only the separately owner-approved controller purpose supplies this switch.
# Ordinary exact releases preserve the independent native producer's exact
# active/inactive state. Only an approved producer-ABI cutover may start it.
NATIVE_SOURCE_CUTOVER="${CRIPTA_NATIVE_SOURCE_CUTOVER:-0}"
# One-off #470 staged physical acceptance. The value is never caller-selected
# freely: the dedicated no-argument roadmap rail derives it from INSTALLED_COMMIT.
ROADMAP470_STAGE="${CRIPTA_ROADMAP470_STAGE:-}"
CONTROL_MAX_AGE_SECONDS=604800
controller_stage=""
last_error_line=""
last_error_command=""

record_err() {
  last_error_line="$1"
  last_error_command="$2"
}

cleanup_controller_stage() {
  if [[ -n "$controller_stage" && -d "$controller_stage" ]]; then
    rm -rf -- "$controller_stage"
  fi
}
trap cleanup_controller_stage EXIT
trap 'record_err "$LINENO" "$BASH_COMMAND"' ERR

die() { echo "ERROR: $*" >&2; exit 1; }
as_repo_owner() { runuser -u cripta -- env GIT_OPTIONAL_LOCKS=0 "$@"; }
sql_scalar() { runuser -u postgres -- psql -X -Atqc "$1" cripta; }

roadmap470_pair() {
  case "$1" in
    R07) printf '%s %s\n' \
      99a1ba4c16edbe5cb32022addf6c52888574716d \
      bbc21196fc7233d199f1ff016ea8553450e55532 ;;
    R08) printf '%s %s\n' \
      bbc21196fc7233d199f1ff016ea8553450e55532 \
      a18a78b849ea0c6a18dfb6dce422e757466f5ab8 ;;
    R09) printf '%s %s\n' \
      a18a78b849ea0c6a18dfb6dce422e757466f5ab8 \
      1e29f9d5105ffc99a91af01c3c500cad062534ea ;;
    R09_REPAIR) printf '%s %s\n' \
      1e29f9d5105ffc99a91af01c3c500cad062534ea \
      c65b385fdd3365f404b4ccfce3583b51b3e3476d ;;
    R09_REPAIR2) printf '%s %s\n' \
      c65b385fdd3365f404b4ccfce3583b51b3e3476d \
      0e0a661695ab920a2b64ad11d5bea56bd6163302 ;;
    R10) printf '%s %s\n' \
      0e0a661695ab920a2b64ad11d5bea56bd6163302 \
      14a4fc65857d2a3158bdb3fda73578dd238057d2 ;;
    R10_ACTIVATE) printf '%s %s\n' \
      14a4fc65857d2a3158bdb3fda73578dd238057d2 \
      0cdf4ce0ca80ee4dcc5c7cc5c2595223fd4208d4 ;;
    R11) printf '%s %s\n' \
      0cdf4ce0ca80ee4dcc5c7cc5c2595223fd4208d4 \
      29cbe0229c137112e3f4e9d3b5b778ebdbc05738 ;;
    R11_REPAIR) printf '%s %s\n' \
      29cbe0229c137112e3f4e9d3b5b778ebdbc05738 \
      103ac4dc7ce0d7d06b413847938a22e356cc26b9 ;;
    R11_REPAIR2) printf '%s %s\n' \
      103ac4dc7ce0d7d06b413847938a22e356cc26b9 \
      f16601924f6623260cbe45d96547c6e9fc71067a ;;
    R11_REPAIR3) printf '%s %s\n' \
      f16601924f6623260cbe45d96547c6e9fc71067a \
      35923cfb7a8c1149284d2ba327a8d5ae306f5490 ;;
    R11_REPAIR4) printf '%s %s\n' \
      35923cfb7a8c1149284d2ba327a8d5ae306f5490 \
      5fc00daf43676c30c494f871efb1a69e0eac33b4 ;;
    R11_REPAIR5) printf '%s %s\n' \
      5fc00daf43676c30c494f871efb1a69e0eac33b4 \
      b9caae2843db344067412a5fb4b42ae15c244022 ;;
    R11_REPAIR6) printf '%s %s\n' \
      b9caae2843db344067412a5fb4b42ae15c244022 \
      84f7fd5583daed5559a251284ffd90b8847fa496 ;;
    R11_REPAIR7) printf '%s %s\n' \
      84f7fd5583daed5559a251284ffd90b8847fa496 \
      3f368fe88d72d6e10f0af0902d6205fc141d8447 ;;
    R11_REPAIR8) printf '%s %s\n' \
      3f368fe88d72d6e10f0af0902d6205fc141d8447 \
      edc6363293b6f6dc64972f01684619a313936ef5 ;;
    R11_REPAIR9) printf '%s %s\n' \
      edc6363293b6f6dc64972f01684619a313936ef5 \
      0d440f1c2e401417fe11c182c2e38f57872ec5b4 ;;
    R12) printf '%s %s\n' \
      0d440f1c2e401417fe11c182c2e38f57872ec5b4 \
      9b95351d4d0d55a835005f99dd6738a3022dd6e2 ;;
    R13) printf '%s %s\n' \
      9b95351d4d0d55a835005f99dd6738a3022dd6e2 \
      b3b939f7e9234fd9304f60e7fa14db650713481c ;;
    R13_REPAIR) printf '%s %s\n' \
      b3b939f7e9234fd9304f60e7fa14db650713481c \
      b485e97c3d601011459d235460a975da4794c5d1 ;;
    *) return 1 ;;
  esac
}

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
[[ "$NATIVE_SOURCE_CUTOVER" == "0" || "$NATIVE_SOURCE_CUTOVER" == "1" ]] ||
  die "CRIPTA_NATIVE_SOURCE_CUTOVER must be 0 or 1"
[[ "$NATIVE_SOURCE_CUTOVER" == "0" || "$STAGED_OFFLINE" == "0" ]] ||
  die "NATIVE_SOURCE_CUTOVER_CANNOT_COMBINE_STAGED_OFFLINE"
if [[ -n "$ROADMAP470_STAGE" ]]; then
  [[ "$STAGED_OFFLINE" == "1" ]] ||
    die "ROADMAP470_STAGE_REQUIRES_STAGED_OFFLINE"
  [[ "$CONTROL_CHECKPOINT" == "1" ]] ||
    die "ROADMAP470_STAGE_REQUIRES_CONTROL_CHECKPOINT"
  [[ "$NATIVE_SOURCE_CUTOVER" == "0" ]] ||
    die "ROADMAP470_STAGE_FORBIDS_NATIVE_CUTOVER"
  read -r roadmap470_expected_baseline roadmap470_expected_target < <(
    roadmap470_pair "$ROADMAP470_STAGE"
  ) || die "ROADMAP470_STAGE_INVALID"
  [[ "$EXPECTED_BASELINE" == "$roadmap470_expected_baseline" ]] ||
    die "ROADMAP470_BASELINE_NOT_EXACT_STAGE_PREDECESSOR"
  [[ "$RELEASE_COMMIT" == "$roadmap470_expected_target" ]] ||
    die "ROADMAP470_TARGET_NOT_EXACT_STAGE_COMMIT"
fi
if [[ "$STAGED_OFFLINE" == "1" ]]; then
  [[ "$CONTROL_CHECKPOINT" == "1" ]] || die "STAGED_OFFLINE_REQUIRES_CONTROL_CHECKPOINT"
fi
if [[ "$CONTROL_CHECKPOINT" == "1" ]]; then
  [[ -n "$CONTROL_REASON" ]] || die "CRIPTA_RELEASE_CONTROL_REASON is required for a control checkpoint"
fi

remote="$(as_repo_owner git -C "$SOURCE" ls-remote origin refs/heads/main | awk 'NR==1{print $1}')"
if [[ -n "$ROADMAP470_STAGE" ]]; then
  source_branch="$(as_repo_owner git -C "$SOURCE" symbolic-ref --quiet --short HEAD || true)"
  source_head="$(as_repo_owner git -C "$SOURCE" rev-parse HEAD)"
  source_dirty="$(as_repo_owner git -C "$SOURCE" status --porcelain=v1 --untracked-files=all)"
  [[ "$source_branch" == "main" ]] || die "ROADMAP470_SOURCE_BRANCH_NOT_MAIN"
  [[ -z "$source_dirty" ]] || die "ROADMAP470_SOURCE_NOT_CLEAN"
  [[ "$source_head" == "$remote" ]] || die "ROADMAP470_SOURCE_NOT_CURRENT_REMOTE_MAIN"
  as_repo_owner git -C "$SOURCE" cat-file -e "${RELEASE_COMMIT}^{commit}" ||
    die "ROADMAP470_TARGET_NOT_IN_LOCAL_VERIFIED_GIT"
  as_repo_owner git -C "$SOURCE" merge-base --is-ancestor "$RELEASE_COMMIT" "$remote" ||
    die "ROADMAP470_TARGET_NOT_ANCESTOR_OF_CURRENT_MAIN"
  echo "ROADMAP470_AUTHORITY=PASS stage:$ROADMAP470_STAGE remote:$remote target:$RELEASE_COMMIT"
else
  [[ "$remote" == "$RELEASE_COMMIT" ]] ||
    die "remote main differs from requested release commit"
  as_repo_owner git -C "$SOURCE" fetch --no-tags origin "$RELEASE_COMMIT" >/dev/null
fi
tree="$(as_repo_owner git -C "$SOURCE" rev-parse "${RELEASE_COMMIT}^{tree}")"
if [[ -n "$EXPECTED_TREE" && "$tree" != "$EXPECTED_TREE" ]]; then die "release tree mismatch"; fi

# Release installation is independent of all Exchange and trading inventory.
# The installer only observes; Execution and Lifecycle own mutation/recovery.
pre_gate="$(sql_scalar "SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'")"
pre_permissions="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
pre_sessions="$(sql_scalar "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'")"
pre_account_sessions="$(sql_scalar "SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'")"
echo "TRADING_STATE_INFORMATIONAL=gate:$pre_gate permissions:$pre_permissions sessions:$pre_sessions account_sessions:$pre_account_sessions"
if [[ "$STAGED_OFFLINE" == "1" ]]; then
  # Local facts are necessary, NOT sufficient: approved preflight must also
  # independently verify Exchange positions/orders and restore/rollback.
  [[ "$pre_gate" == "0" && "$pre_permissions" == "0" && "$pre_sessions" == "0" && "$pre_account_sessions" == "0" ]] || die "STAGED_OFFLINE_TRADING_NOT_DISARMED"
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

# The native source is NOT stopped by normal trading releases. If already
# running, preserve its exact loaded source code across the new release.
# Changing native producer, its journal/identity contract or systemd unit
# requires a separately reviewed source upgrade/cutover, not a covert
# in-place module swap while a long-lived process still records evidence.
native_code_changed=0
native_source_offline_cutover=0
producer_loaded_commit=""
producer_old_session=""
producer_old_pid=""
if systemctl is-active --quiet cripta-native-public-trade-source.service; then
  producer_status="/var/lib/cripta/native_public_trade_source/status.json"
  [[ -f "$producer_status" ]] ||
    die "live native recorder has no physical source status proof"
  producer_loaded_commit="$(runuser -u cripta -- python3 -c '
import json,sys
try:
    value=json.load(open(sys.argv[1],encoding="utf-8"))["source_commit"]
except Exception:
    value=""
print(value if isinstance(value,str) else "")
' "$producer_status")"
  [[ "$producer_loaded_commit" =~ ^[0-9a-f]{40}$ ]] ||
    die "live native source release identity invalid"
  as_repo_owner git -C "$SOURCE" cat-file -e "${producer_loaded_commit}^{commit}" ||
    die "live native source origin not in verified git"
  producer_old_session="$(runuser -u cripta -- python3 -c '
import json,sys
try:
    v=json.load(open(sys.argv[1],encoding="utf-8"))
    print(v.get("session","") if v.get("state")=="ACTIVE" else "")
except Exception:
    print("")
' "$producer_status")"
  [[ "$producer_old_session" =~ ^[0-9a-f]{32}$ ]] ||
    die "NATIVE_SOURCE_OLD_SESSION_UNVERIFIED"
  producer_old_pid="$(systemctl show cripta-native-public-trade-source.service --value --property=MainPID)"
  [[ "$producer_old_pid" =~ ^[1-9][0-9]*$ ]] ||
    die "NATIVE_SOURCE_OLD_PID_UNVERIFIED"
  if ! as_repo_owner git -C "$SOURCE" diff --quiet \
    "$producer_loaded_commit" "$RELEASE_COMMIT" -- \
    operations/monitoring/native_public_trade_source.py \
    operations/monitoring/public_market_source_union_capture.py \
    src/bybit_workbench/market_data/public_source_event_codec.py \
    src/bybit_workbench/market_data/public_source_second_store.py \
    src/bybit_workbench/market_data/public_source_quality.py \
    src/bybit_workbench/market_data/native_public_trade_journal.py \
    src/bybit_workbench/market_identity.py \
    operations/systemd/cripta-native-public-trade-source.service; then
    native_code_changed=1
    [[ "$NATIVE_SOURCE_CUTOVER" == "1" ]] ||
      die "NATIVE_SOURCE_PRODUCER_CODE_CHANGED_SEPARATE_CUTOVER_REQUIRED"
    # Old loaded code must be the exact installed baseline ABI, even when
    # source_commit originated in an earlier but byte-identical release.
    as_repo_owner git -C "$SOURCE" diff --quiet \
      "$producer_loaded_commit" "$EXPECTED_BASELINE" -- \
      operations/monitoring/native_public_trade_source.py \
      operations/monitoring/public_market_source_union_capture.py \
      src/bybit_workbench/market_data/public_source_event_codec.py \
      src/bybit_workbench/market_data/public_source_second_store.py \
      src/bybit_workbench/market_data/public_source_quality.py \
      src/bybit_workbench/market_data/native_public_trade_journal.py \
      src/bybit_workbench/market_identity.py \
      operations/systemd/cripta-native-public-trade-source.service ||
      die "NATIVE_SOURCE_INSTALLED_BASELINE_ABI_UNVERIFIED"
    echo "NATIVE_SOURCE_EXACT_CUTOVER_PREPARED=PASS"
  else
    [[ "$NATIVE_SOURCE_CUTOVER" == "0" ]] ||
      die "NATIVE_SOURCE_CUTOVER_NO_CODE_CHANGE"
    echo "NATIVE_SOURCE_CONTINUOUS_CODE_IDENTITY=PASS"
  fi
elif [[ "$NATIVE_SOURCE_CUTOVER" == "1" ]]; then
  # #642 scoped offline producer cutover candidate: the last writer was
  # deliberately stopped for disk safety. NEVER boot the legacy seven-symbol
  # producer just to satisfy the active-writer cutover precondition.
  [[ "$(systemctl show cripta-native-public-trade-source.service --value --property=ActiveState)" == "inactive" ]] ||
    die "NATIVE_OFFLINE_SOURCE_NOT_INACTIVE"
  [[ "$(systemctl show cripta-native-public-trade-source.service --value --property=MainPID)" == "0" ]] ||
    die "NATIVE_OFFLINE_SOURCE_PID_NOT_ZERO"
  [[ "$pre_gate" == "0" && "$pre_permissions" == "0" &&
     "$pre_sessions" == "0" && "$pre_account_sessions" == "0" ]] ||
    die "NATIVE_OFFLINE_FINANCIAL_GATE_NOT_CLOSED"
  [[ "$(sql_scalar 'SELECT count(*) FROM runtime.hot_positions')" == "0" &&
     "$(sql_scalar 'SELECT count(*) FROM runtime.hot_orders')" == "0" ]] ||
    die "NATIVE_OFFLINE_HOT_EXPOSURE_NOT_FLAT"
  [[ "$(sql_scalar "SELECT count(*) FROM runtime.position_ownership
    WHERE state IN ('OPEN','RECONCILIATION_REQUIRED')")" == "0" ]] ||
    die "NATIVE_OFFLINE_POSITION_OWNERSHIP_NOT_FLAT"
  # A Git-reviewed source profile is mandatory; never accept an arbitrary
  # stopped old writer / profile as authorization for offline upgrade.
  target_native_unit="$(as_repo_owner git -C "$SOURCE" show \
    "$RELEASE_COMMIT:operations/systemd/cripta-native-public-trade-source.service")" ||
    die "NATIVE_OFFLINE_TARGET_UNIT_UNVERIFIED"
  grep -Fxq 'Environment=CRIPTA_SOURCE_PROFILE=P90_MINIMAL' <<<"$target_native_unit" ||
    die "NATIVE_OFFLINE_PROFILE_NOT_P90_MINIMAL"
  grep -Fxq 'Environment=CRIPTA_R11_MAYAK_ANCHORS=0' <<<"$target_native_unit" ||
    die "NATIVE_OFFLINE_R11_ANCHORS_NOT_OFF"
  grep -Fxq 'Environment=CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES=0' <<<"$target_native_unit" ||
    die "NATIVE_OFFLINE_R11_ORIGINALS_NOT_OFF"
  producer_status="/var/lib/cripta/native_public_trade_source/status.json"
  [[ -f "$producer_status" && ! -L "$producer_status" ]] ||
    die "NATIVE_OFFLINE_SOURCE_STATUS_MISSING"
  old_status_and_commit="$(runuser -u cripta -- python3 -c '
import json,sys
try:
    d=json.load(open(sys.argv[1],encoding="utf-8"))
    print(str(d.get("state","")) + "|" + str(d.get("source_commit","")))
except Exception:
    print("")
' "$producer_status")" ||
    die "NATIVE_OFFLINE_SOURCE_STATUS_UNREADABLE"
  [[ "${old_status_and_commit%%|*}" == "STOPPED" ]] ||
    die "NATIVE_OFFLINE_SOURCE_STATUS_OR_COMMIT_INVALID"
  producer_loaded_commit="${old_status_and_commit#STOPPED|}"
  [[ "$producer_loaded_commit" =~ ^[0-9a-f]{40}$ ]] ||
    die "NATIVE_OFFLINE_ORIGIN_COMMIT_FORMAT_INVALID"
  as_repo_owner git -C "$SOURCE" cat-file -e "${producer_loaded_commit}^{commit}" ||
    die "NATIVE_OFFLINE_ORIGIN_COMMIT_UNVERIFIED"
  as_repo_owner git -C "$SOURCE" merge-base --is-ancestor \
    "$producer_loaded_commit" "$EXPECTED_BASELINE" ||
    die "NATIVE_OFFLINE_ORIGIN_NOT_BASELINE_ANCESTOR"
  # The controller-only bootstrap advanced INSTALLED_COMMIT, but the actual
  # stopped producer status and its durable GAP must retain the OLD commit.
  # Prove every reviewed producer ABI file byte-identical in that interval.
  as_repo_owner git -C "$SOURCE" diff --quiet \
    "$producer_loaded_commit" "$EXPECTED_BASELINE" -- \
    operations/monitoring/native_public_trade_source.py \
    operations/monitoring/public_market_source_union_capture.py \
    src/bybit_workbench/market_data/public_source_event_codec.py \
    src/bybit_workbench/market_data/public_source_second_store.py \
    src/bybit_workbench/market_data/public_source_quality.py \
    src/bybit_workbench/market_data/native_public_trade_journal.py \
    src/bybit_workbench/market_identity.py \
    operations/systemd/cripta-native-public-trade-source.service ||
    die "NATIVE_OFFLINE_ORIGIN_BASELINE_ABI_DRIFT"
  [[ "$(sql_scalar "SELECT count(*) FROM analytics.native_public_trade_source_sessions
    WHERE state='ACTIVE'")" == "0" ]] ||
    die "NATIVE_OFFLINE_ACTIVE_SOURCE_SESSION_PRESENT"
  producer_old_session="$(sql_scalar "SELECT source_session_id
    FROM analytics.native_public_trade_source_sessions
    WHERE market_source_id='ms-bybit-mainnet-public-v1'
    ORDER BY subscribed_at DESC, source_session_id DESC LIMIT 1")"
  [[ "$producer_old_session" =~ ^[0-9a-f]{32}$ ]] ||
    die "NATIVE_OFFLINE_LAST_SESSION_MISSING"
  [[ "$(sql_scalar "SELECT state || '|' || reason
    FROM analytics.native_public_trade_source_sessions
    WHERE source_session_id='$producer_old_session'
      AND producer_release_commit='$producer_loaded_commit'")" == "GAP|OWNER_CONTROLLED_STOP" ]] ||
    die "NATIVE_OFFLINE_OLD_SESSION_NOT_OWNER_STOPPED_GAP"
  if as_repo_owner git -C "$SOURCE" diff --quiet \
    "$EXPECTED_BASELINE" "$RELEASE_COMMIT" -- \
    operations/monitoring/native_public_trade_source.py \
    operations/monitoring/public_market_source_union_capture.py \
    operations/systemd/cripta-native-public-trade-source.service; then
    die "NATIVE_OFFLINE_NO_PRODUCER_ABI_DELTA"
  fi
  producer_old_pid="0"
  native_source_offline_cutover=1
  native_code_changed=1
  echo "NATIVE_SOURCE_OFFLINE_CUTOVER_PREPARED=PASS"
fi

install -d -o cripta -g cripta -m 0750 "$RUNTIME_ROOT" "$RUNTIME_ROOT/releases"
install -d -o root -g cripta -m 0755 "$DASHBOARD_UI_ROOT" "$DASHBOARD_UI_ROOT/releases"
install -d -o cripta -g cripta -m 2770 "$RESEARCH_TOOLING_ROOT" "$RESEARCH_TOOLING_ROOT/releases"
install -d -o cripta -g cripta -m 0750 /data/cripta/datasets/raw/bybit_public_trades_daily_v1
install -d -o root -g root -m 0700 "$BACKUP_ROOT"
install -d -o root -g cripta -m 0750 "$STATE_ROOT"
install -d -o root -g root -m 0700 "$RELEASE_CONTROLLER_STATE_ROOT"
install -d -o root -g root -m 0700 "$RELEASE_CONTROLLER_STATE_ROOT/used"

runtime_release="$RUNTIME_ROOT/releases/$RELEASE_COMMIT"
tooling_release="$RESEARCH_TOOLING_ROOT/releases/$RELEASE_COMMIT"

# A release directory name or marker is not proof of its bytes. Any target
# that is not the current immutable runtime/tooling baseline is an expendable
# build candidate and is rebuilt from exact Git on every attempt.
reset_release_candidate() {
  local candidate="$1"
  local current_link="$2"
  local expected_parent="$3"
  local label="$4"
  local current_target=""

  [[ "$candidate" == "$expected_parent/$RELEASE_COMMIT" ]] ||
    die "RELEASE_TARGET_CANDIDATE_SCOPE_INVALID:$label"
  if [[ -e "$candidate" || -L "$candidate" ]]; then
    [[ -d "$candidate" && ! -L "$candidate" ]] ||
      die "RELEASE_TARGET_CANDIDATE_TYPE_INVALID:$label"
    current_target="$(readlink -f "$current_link" 2>/dev/null || true)"
    if [[ "$current_target" == "$candidate" ]]; then
      if [[ -n "$ROADMAP470_STAGE" ]]; then
        die "ROADMAP470_INCOMPLETE_CANDIDATE_IS_CURRENT:$label"
      fi
      die "RELEASE_TARGET_CANDIDATE_IS_CURRENT:$label"
    fi
    rm -rf --one-file-system -- "$candidate"
    echo "RELEASE_TARGET_CANDIDATE_REBUILT=PASS label:$label target:$RELEASE_COMMIT"
    if [[ -n "$ROADMAP470_STAGE" ]]; then
      echo "ROADMAP470_INCOMPLETE_CANDIDATE_RESET=PASS label:$label target:$RELEASE_COMMIT"
    fi
  fi
}

reset_release_candidate \
  "$runtime_release" "$RUNTIME_ROOT/current" "$RUNTIME_ROOT/releases" runtime
reset_release_candidate \
  "$tooling_release" "$RESEARCH_TOOLING_ROOT/current" \
  "$RESEARCH_TOOLING_ROOT/releases" tooling

as_repo_owner install -d -m 0750 "$runtime_release"
as_repo_owner git -C "$SOURCE" archive --format=tar "$RELEASE_COMMIT" -- src operations config \
  research/server/backup research/server/connectivity \
  research/server/monitoring research/server/control pyproject.toml \
  | runuser -u cripta -- tar -xf - -C "$runtime_release"
as_repo_owner install -d -m 0750 "$tooling_release"
as_repo_owner git -C "$SOURCE" archive --format=tar "$RELEASE_COMMIT" -- research/server/jobs research/server/dataset research/server/cripta-download-expansion.service \
  | runuser -u cripta -- tar -xf - -C "$tooling_release"

# Verify every selected tracked blob and Git executable/symlink mode before
# .venv, Dashboard overlay, INSTALLED_COMMIT, DB, service, or symlink cutover.
runuser -u cripta -- /usr/bin/python3 \
  "$SOURCE/operations/infrastructure/verify_release_candidate_git.py" \
  --repo "$SOURCE" --commit "$RELEASE_COMMIT" \
  --candidate "$runtime_release" --label runtime \
  --path src --path operations --path config \
  --path research/server/backup --path research/server/connectivity \
  --path research/server/monitoring --path research/server/control \
  --path pyproject.toml || die "RELEASE_RUNTIME_GIT_BLOB_VERIFY_FAILED"
echo "RELEASE_RUNTIME_GIT_BLOB_VERIFY=PASS target:$RELEASE_COMMIT"

runuser -u cripta -- /usr/bin/python3 \
  "$SOURCE/operations/infrastructure/verify_release_candidate_git.py" \
  --repo "$SOURCE" --commit "$RELEASE_COMMIT" \
  --candidate "$tooling_release" --label tooling \
  --path research/server/jobs --path research/server/dataset \
  --path research/server/cripta-download-expansion.service ||
  die "RELEASE_TOOLING_GIT_BLOB_VERIFY_FAILED"
echo "RELEASE_TOOLING_GIT_BLOB_VERIFY=PASS target:$RELEASE_COMMIT"

# Fail closed before service/DB mutation if the runtime release retention root
# contains any unexpected direct child or if current does not resolve to the
# exact expected baseline. The helper is taken from the exact candidate release.
runuser -u root -- /usr/bin/python3 \
  "$runtime_release/operations/infrastructure/runtime_release_retention.py" \
  --root "$RUNTIME_ROOT/releases" \
  --current-link "$RUNTIME_ROOT/current" \
  validate \
  --expected-sha "$EXPECTED_BASELINE"

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

case "$ROADMAP470_STAGE" in
  R07|R08|R09|R09_REPAIR|R09_REPAIR2|R10|R10_ACTIVATE)
    # Dispatcher was consolidated under src/bybit_workbench only at R11.
    # Historical staged acceptance must validate the code that actually existed
    # at the immutable target, not impose a later package on an earlier release.
    echo "DISPATCHER_IMPORT_PREFLIGHT=DEFERRED_NOT_IN_STAGE stage:$ROADMAP470_STAGE"
    ;;
  *)
    PYTHONPATH="$runtime_release/src" "$runtime_release/.venv/bin/python" - <<'PY'
import bybit_workbench.dispatcher_v2
print("DISPATCHER_IMPORT_PREFLIGHT=PASS")
PY
    ;;
esac

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
  # Installed for reviewed retention cutover, but never enabled automatically.
  "operations/systemd/cripta-market-hot-retention.service|cripta-market-hot-retention.service|runtime"
  "operations/systemd/cripta-market-hot-retention.timer|cripta-market-hot-retention.timer|runtime"
  "operations/systemd/cripta-mayak-v2-report.service|cripta-mayak-v2-report.service|runtime"
  "operations/systemd/cripta-mayak-v2-weekly-report.service|cripta-mayak-v2-weekly-report.service|runtime"
  "research/server/monitoring/cripta-opportunity-tracker.service|cripta-opportunity-tracker.service|runtime"
  "operations/monitoring/cripta-position-supervisor.service|cripta-position-supervisor.service|runtime"
  "research/server/connectivity/cripta-private-runtime.service|cripta-private-runtime.service|runtime"
  # Source-only templates are installed but never enabled or added to managed_services.
  "operations/systemd/cripta-private-runtime@.service|cripta-private-runtime@.service|runtime"
  # Inert, owner-triggered GET-only identity check. Never auto-start/enable.
  "operations/systemd/cripta-account-enrollment-probe@.service|cripta-account-enrollment-probe@.service|runtime"
  # Inert owner-triggered Release Controller; never auto-enabled or managed.
  "operations/systemd/cripta-release-controller.service|cripta-release-controller.service|runtime"
  # Inert explicit P90 source-compatibility registration; DB append only, no Exchange/ARM.
  "operations/systemd/cripta-p90-source-compatibility-register.service|cripta-p90-source-compatibility-register.service|runtime"
  # Inert exact development owner-delegation rails. Never enable/manage automatically.
  "operations/systemd/cripta-p90-development-owner-arm.service|cripta-p90-development-owner-arm.service|runtime"
  "operations/systemd/cripta-p90-development-owner-disarm.service|cripta-p90-development-owner-disarm.service|runtime"
  # Generic management-plane broker. It may start only source-allowlisted CRIPTA units.
  "operations/systemd/cripta-real-service-broker.service|cripta-real-service-broker.service|runtime"
  # Exact SUB WS technical recovery, no restart broker and no OWNER/LIVE rights.
  "operations/systemd/cripta-runtime-recovery-supervisor.service|cripta-runtime-recovery-supervisor.service|runtime"
  # Inert SUB-only Lifecycle worker; no auto-enable or implicit started service.
  "operations/systemd/cripta-lifecycle-account@.service|cripta-lifecycle-account@.service|runtime"
  "operations/systemd/cripta-account-position@.service|cripta-account-position@.service|runtime"
  "operations/systemd/cripta-universal-entry-consumer@.service|cripta-universal-entry-consumer@.service|runtime"
  "research/server/connectivity/cripta-safety-observer.service|cripta-safety-observer.service|runtime"
  "research/server/control/cripta-shadow-command-worker.service|cripta-shadow-command-worker.service|runtime"
  "operations/systemd/cripta-u5-oi30s-source-soak.service|cripta-u5-oi30s-source-soak.service|runtime"
  "operations/systemd/cripta-universal-entry-consumer.service|cripta-universal-entry-consumer.service|runtime"
  # Credential-free native MarketSource must own the journal before consumers.
  "operations/systemd/cripta-native-public-trade-source.service|cripta-native-public-trade-source.service|runtime"
  # Inert exact root-managed R06 switch/recovery units; never auto-enable.
  "operations/systemd/cripta-r06-entry-cutover.service|cripta-r06-entry-cutover.service|runtime"
  "operations/systemd/cripta-r06-entry-rollback.service|cripta-r06-entry-rollback.service|runtime"
  "operations/systemd/cripta-r06-entry-recovery.service|cripta-r06-entry-recovery.service|runtime"
  # Immutable-source cripta-only signed read-only probe under PID 1; no root UID handoff.
  "operations/systemd/cripta-r06-entry-preflight@.service|cripta-r06-entry-preflight@.service|runtime"
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

release_controller_files=(
  operations/infrastructure/cripta-release-controller
  operations/infrastructure/release_controller_gate.py
  operations/infrastructure/release_controller_github.py
  operations/systemd/cripta-release-controller.service
)
controller_stage="$(mktemp -d /run/lock/cripta-release-controller-stage.XXXXXX)"
chmod 0700 "$controller_stage"
for relative in "${release_controller_files[@]}"; do
  candidate="$runtime_release/$relative"
  [[ -f "$candidate" ]] || die "release controller source missing: $relative"
  staged="$controller_stage/$(basename "$relative")"
  as_repo_owner git -C "$SOURCE" show "$RELEASE_COMMIT:$relative" > "$staged"
  chmod 0600 "$staged"
  cmp -s "$staged" "$candidate" || die "release controller candidate differs from exact Git: $relative"
done
echo "RELEASE_CONTROLLER_SOURCE_PREFLIGHT=PASS"

# Exact root-owned R06 management bytes must be compared to the reviewed
# Git object BEFORE stopping any managed service or writing the filesystem.
r06_switch_rel="operations/management/r06_pg_mode_switch.py"
r06_switch_stage="$controller_stage/r06_pg_mode_switch.py"
as_repo_owner git -C "$SOURCE" show "$RELEASE_COMMIT:$r06_switch_rel" > "$r06_switch_stage"
chmod 0600 "$r06_switch_stage"
cmp -s "$r06_switch_stage" "$runtime_release/$r06_switch_rel" ||
  die "R06 management root helper differs from exact protected Git bytes"
[[ -f "$runtime_release/operations/management/r06_pg_switch_probe.py" ]] ||
  die "R06 signed preflight source absent from release"
echo "R06_MANAGEMENT_ROOT_BINARY_GIT_BYTES=PASS"


broker_relative="operations/management/real_service_broker.py"
broker_candidate="$runtime_release/$broker_relative"
[[ -f "$broker_candidate" ]] || die "REAL service broker source missing: $broker_relative"
broker_staged="$controller_stage/real_service_broker.py"
as_repo_owner git -C "$SOURCE" show "$RELEASE_COMMIT:$broker_relative" > "$broker_staged"
chmod 0600 "$broker_staged"
cmp -s "$broker_staged" "$broker_candidate" ||
  die "REAL service broker candidate differs from exact Git"
echo "REAL_SERVICE_BROKER_SOURCE_PREFLIGHT=PASS"

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
for path in /usr/local/sbin/cripta-apply-incoming /usr/local/sbin/cripta-deploy-dashboard-ui /usr/local/libexec/cripta-release-controller /usr/local/libexec/cripta-real-service-broker /usr/local/libexec/cripta-r06-switch /usr/local/libexec/cripta-market-hot-retention.py /usr/local/libexec/cripta-market-source-hot-retention.py /usr/local/libexec/cripta-native-public-trade-hot-retention.py /etc/cripta/release.env /etc/cripta/market-source/required_public_feeds.json; do
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
  # Preserve exact active/inactive state across releases; new timer defaults OFF.
  cripta-market-hot-retention.service
  cripta-market-hot-retention.timer
  cripta-opportunity-tracker.service
  cripta-position-supervisor.service
  cripta-private-runtime.service
  cripta-real-service-broker.service
  cripta-runtime-recovery-supervisor.service
  cripta-safety-observer.service
  cripta-shadow-command-worker.service
  cripta-universal-entry-consumer.service
  # MarketSource is NOT stopped for ordinary trading release/backup.
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
native_source_was_active=0
native_source_stopped_for_cutover=0
if systemctl is-active --quiet cripta-native-public-trade-source.service; then
  native_source_was_active=1
fi
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
  # On failed first cutover restore the original inactive producer state;
  # otherwise preserve the previous independently running native recorder.
  if [[ "$native_source_was_active" == "0" ]]; then
    systemctl stop cripta-native-public-trade-source.service >/dev/null 2>&1 ||
      recovery_failed=1
    systemctl disable cripta-native-public-trade-source.service >/dev/null 2>&1 ||
      recovery_failed=1
  fi
  if [[ "$recovery_failed" == "0" ]]; then
    echo "PRE_CUTOVER_SERVICE_RECOVERY=PASS"
  else
    echo "PRE_CUTOVER_SERVICE_RECOVERY=FAIL" >&2
  fi
  return "$recovery_failed"
}

release_exit_handler() {
  local rc=$?
  cleanup_controller_stage
  if [[ "$deploy_succeeded" == "1" ]]; then
    return
  fi
  if [[ "$rc" != "0" ]]; then
    local failure_tmp="$STATE_ROOT/.LAST_FAILED_RELEASE.$"
    {
      printf 'release=%s\n' "$RELEASE_COMMIT"
      printf 'exit_code=%s\n' "$rc"
      printf 'line=%s\n' "${last_error_line:-unknown}"
      printf 'command=%q\n' "${last_error_command:-unknown}"
      printf 'recorded_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$failure_tmp" || true
    chown root:cripta "$failure_tmp" 2>/dev/null || true
    chmod 0640 "$failure_tmp" 2>/dev/null || true
    mv -f "$failure_tmp" "$STATE_ROOT/LAST_FAILED_RELEASE" 2>/dev/null || true
  fi
  if [[ -n "$ROADMAP470_STAGE" ]]; then
    echo "DEPLOY_FAILED=YES" >&2
    echo "FAILED_RELEASE_COMMIT=$RELEASE_COMMIT" >&2
    echo "ROADMAP470_FAILED_STAGE=$ROADMAP470_STAGE" >&2
    echo "DB_MUTATION_STEPS_COMMITTED=$db_mutation_steps" >&2
    echo "CUTOVER_STARTED=$cutover_started" >&2
    if [[ "$db_mutation_steps" != "0" || "$cutover_started" != "0" ]]; then
      echo "HARD_STOP=YES" >&2
      echo "DEPLOY_RECOVERY=ROADMAP470_RECONCILE_BEFORE_RETRY" >&2
    fi
    return
  fi
  if [[ "$services_stopped" == "0" ]]; then
    return
  fi
  echo "DEPLOY_FAILED=YES" >&2
  echo "FAILED_RELEASE_COMMIT=$RELEASE_COMMIT" >&2
  echo "DB_MUTATION_STEPS_COMMITTED=$db_mutation_steps" >&2
  echo "CUTOVER_STARTED=$cutover_started" >&2
  # If no release symlink changed, the OLD immutable binary remains loaded.
  # Restore its recorder even if the additive DB migration already committed.
  if [[ "$native_source_stopped_for_cutover" == "1" &&
        "$cutover_started" == "0" ]]; then
    if systemctl start cripta-native-public-trade-source.service; then
      echo "NATIVE_SOURCE_PRE_SWITCH_RECOVERY=STARTED_OLD_CODE" >&2
    else
      echo "NATIVE_SOURCE_PRE_SWITCH_RECOVERY=FAILED HARD_STOP=YES" >&2
    fi
  fi
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

# Fail BEFORE stopping any owner/consumer if this scope claims MAYAK was
# already paused while it actually remains active. Releasing P90_MINIMAL is
# not authorization to kill a live seven-symbol observation workflow.
if [[ -z "$ROADMAP470_STAGE" && "$native_code_changed" == "1" &&
      "${was_active[cripta-mayak-v2.service]:-0}" == "1" ]]; then
  target_source_unit="$(as_repo_owner git -C "$SOURCE" show \
    "$RELEASE_COMMIT:operations/systemd/cripta-native-public-trade-source.service")"
  if grep -Fxq 'Environment=CRIPTA_SOURCE_PROFILE=P90_MINIMAL' <<<"$target_source_unit"; then
    die "P90_MINIMAL_MAYAK_MUST_BE_PREPAUSED"
  fi
fi

trap release_exit_handler EXIT
if [[ -n "$ROADMAP470_STAGE" ]]; then
  # This rail installs code/schema only. It does not disturb unrelated
  # research infrastructure, and it refuses to proceed if any trading/market
  # runtime that could observe a partial stage is active.
  roadmap470_core_services=(
    cripta-native-public-trade-source.service
    cripta-universal-entry-observer.service
    cripta-universal-entry-consumer.service
    cripta-universal-exit-shadow.service
    cripta-universal-exit-consumer.service
    cripta-private-runtime.service
    cripta-lifecycle-supervisor.service
    cripta-safety-observer.service
    cripta-mayak-v2.service
    cripta-dispatcher-v2.service
    cripta-dispatcher-v2-context-correlator.service
    cripta-position-supervisor.service
    cripta-opportunity-tracker.service
    cripta-m3-trade-analyst.service
    cripta-dashboard.service
    cripta-real-service-broker.service
    cripta-runtime-recovery-supervisor.service
    cripta-shadow-command-worker.service
  )
  for service in "${roadmap470_core_services[@]}"; do
    systemctl is-active --quiet "$service" &&
      die "ROADMAP470_CORE_SERVICE_MUST_BE_STOPPED:$service"
  done
  for service in "${discovered_sub_units[@]}"; do
    systemctl is-active --quiet "$service" &&
      die "ROADMAP470_SUB_SERVICE_MUST_BE_STOPPED:$service"
  done
  [[ "$native_source_was_active" == "0" ]] ||
    die "ROADMAP470_NATIVE_SOURCE_MUST_BE_STOPPED"
  services_stopped=0
  echo "ROADMAP470_NO_START_PREFLIGHT=PASS stage:$ROADMAP470_STAGE"
else
  services_stopped=1
  for service in "${managed_services[@]}"; do
    if [[ "${was_active[$service]}" == "1" ]]; then
      systemctl stop "$service"
    fi
  done
fi

# The rollback dump must describe the pre-migration baseline. In roadmap staged
# mode all trading services are already stopped and unrelated research services
# remain untouched.
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
  operations/sql/20261031_entry_consumer_health.sql
  operations/sql/20261101_owner_full_contour_diagnostic.sql
  operations/sql/20261101_subordinate_diagnostic_binding.sql
  operations/sql/20261101_owner_full_contour_diagnostic_v2.sql
  operations/sql/20261101_owner_full_contour_diagnostic_v3.sql
  # Durable technical readiness state; no Strategy/LIVE/Exchange effect.
  operations/sql/20261102_real_readiness_operations.sql
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
  # Least-privilege recovery for exact PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN.
  # Additive control only; never opens gate, grants permissions/arms or mutates Exchange.
  operations/sql/20261102_p90_account_ws_safety_recovery.sql
  # Account-specific technical resume; atomic relatch and no OWNER/LIVE effect.
  operations/sql/20261104_sub_ws_technical_resume.sql
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
  operations/sql/20261105_gateway_closed_candle_corrections.sql
  operations/sql/20261105_resolved_mutation_recovery.sql
  # Exact pre-send/no-mutation latch recovery; signed-flat proof + immutable audit.
  operations/sql/20261117_p90_pre_send_account_recovery.sql
  # Owner-approved exact P90L90 REAL v3 INACTIVE two-account writer only.
  # Never creates enabled Strategy or bypasses per-account LIVE arm.
  operations/sql/20261029_p90l90_real_v3_inactive_binding_writer.sql
  # Additive independent REAL-only source-epoch state; never resets old PAPER evidence.
  operations/sql/20261029_p90l90_real_source_epochs.sql
  operations/sql/20261106_p90_real_source_release_compatibility.sql
  operations/sql/20261106_p90_real_source_release_compatibility_v2.sql
  # Same source checkpoint owner: exact rolling trade-ID journal and V2 JSON
  # accepted atomically; the migration never opens any financial gate.
  operations/sql/20261106_p90_native_sequence_exec_ids.sql
  # Shared native MarketSource evidence. Required before Observer code cutover.
  # Additive only: no source cursor reset or financial gate mutation.
  operations/sql/20261107_native_public_trade_evidence.sql
  # One independently recorded WS session/cursor per original provider stream.
  operations/sql/20261108_native_public_trade_source_sessions.sql
  # Immutable provider WS frame order: required for scoped native P90 reading.
  operations/sql/20261109_native_public_trade_source_event_order.sql
  # Reorder the existing five-column UNIQUE index (no second permanent copy)
  # so its parent-identity prefix also serves FK enforcement during retention.
  operations/sql/20261116_native_public_trade_parent_fk_index_order.sql
  operations/sql/20261109_p90_scoped_native_consumer_checkpoint.sql
  # Inert shared public-data publication schema: no consumers or writer
  # switch until independently approved MarketSource source/capability cutover.
  operations/sql/20261110_market_source_publication.sql
  # Least-privilege row lock for R06 Exit; runtime cripta keeps publication UPDATE revoked.
  operations/sql/20261117_p90_r06_market_publication_lock.sql
  # Additive compact SPOT+LINEAR public feed facts, inactive until exact
  # producer/retention cutover and no duplicate raw archive.
  operations/sql/20261110_public_source_compact_seconds.sql
  # Link the compact source-second PK in the same immutable public snapshot.
  # The read-model stays WATERMARKS_ONLY until full source capability proof.
  operations/sql/20261110_market_publication_compact_links.sql
  # Native MarketSource public-only REST facts; additive/insert-only.
  operations/sql/20261110_public_market_rest_observations.sql
  operations/sql/20261110_market_publication_rest_links.sql
  # Source-only unconfirmed one-minute evidence. Not canonical closed OHLCV.
  operations/sql/20261110_public_forming_minute_observations.sql
  operations/sql/20261111_public_market_source_quality_events.sql
  operations/sql/20261110_market_publication_forming_links.sql
  # Shared source closed candle IDs for SPOT/LINEAR; legacy symbol projection
  # remains LINEAR-only until all consumers migrate.
  operations/sql/20261110_market_publication_instrument_candle_links.sql
  # Technical Observer delivery only: no P90 Exit/Entry or SOURCE_LIVE_IDENTITY.
  operations/sql/20261110_native_public_trade_observer_delivery.sql
  # R06 additive provenance on the SAME P90 decision checkpoint; no source reset.
  operations/sql/20261112_r06_entry_publication_checkpoint_lineage.sql
  operations/sql/20261112_r06_checkpoint_release_bridge.sql
  operations/sql/20261112_r06_fresh_epoch_transition.sql
  # R07 DB-enforced source-epoch transaction guard; postgres actor before code cutover.
  operations/sql/20261114_r07_source_epoch_transaction_guard.sql
)
# Later #470 migrations are appended only when the staged target actually
# contains that roadmap stage. Normal releases always include every current
# migration. This prevents R07-R10 staged installs from reaching forward into
# files that do not exist in their immutable historical target.
case "$ROADMAP470_STAGE" in
  ""|R11|R11_REPAIR|R11_REPAIR2|R11_REPAIR3|R11_REPAIR4|R11_REPAIR5|R11_REPAIR6|R11_REPAIR7|R11_REPAIR8|R11_REPAIR9|R12|R13|R13_REPAIR)
    migration_files+=(
      operations/sql/20261114_r11_optional_original_public_event_frames.sql
      operations/sql/20261114_r11_optional_publication_frame_refs.sql
      operations/sql/20261115_r11_mayak_consumer_commit.sql
    )
    ;;
esac
case "$ROADMAP470_STAGE" in
  ""|R12|R13|R13_REPAIR)
    # #642 account-scoped Universal Entry financial promotion after R06
    # SOURCE_ONLY. Additive only; no gate/permission/LIVE-arm mutation.
    migration_files+=(operations/sql/20261116_r12_r06_financial_promotion.sql)
    ;;
esac
case "$ROADMAP470_STAGE" in
  ""|R13|R13_REPAIR)
    migration_files+=(operations/sql/20261115_r13_opportunity_source_checkpoint.sql)
    ;;
esac
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

# Register the owner-approved diagnostic Strategy pair only while the exact
# production baseline is fully DISARMED.  The installer creates immutable
# Strategy/plan/binding rows only; ExecutionPermission remains OFF and no
# LIVE-arm session or Exchange mutation is created.
runuser -u cripta -- env \
  PYTHONPATH="$runtime_release/src:$runtime_release/operations/connectivity:$runtime_release/research/server/connectivity:$runtime_release/.venv/lib/python3.12/site-packages" \
  "$runtime_release/.venv/bin/python" \
  "$runtime_release/operations/implementation/install_owner_full_contour_diagnostic.py" install
db_mutation_steps=$((db_mutation_steps + 1))
runuser -u cripta -- env \
  PYTHONPATH="$runtime_release/src:$runtime_release/operations/connectivity:$runtime_release/research/server/connectivity:$runtime_release/.venv/lib/python3.12/site-packages" \
  "$runtime_release/.venv/bin/python" \
  "$runtime_release/operations/implementation/install_owner_full_contour_diagnostic.py" check

# Prove the candidate's exact consumer bytes against the ACTIVE physical
# source epoch BEFORE any filesystem/service cutover. This evidence grants no
# financial permission; failure is fatal so the old runtime/Exit remains current.
runuser -u cripta -- env \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$runtime_release/src:$runtime_release/operations/connectivity:$runtime_release/research/server/connectivity:$runtime_release/.venv/lib/python3.12/site-packages" \
  "$runtime_release/.venv/bin/python" \
  "$runtime_release/operations/implementation/register_p90_real_source_compatibility.py" \
  --release-commit "$RELEASE_COMMIT" --active-epoch --register --release-preparation
db_mutation_steps=$((db_mutation_steps + 1))
echo "P90_SOURCE_COMPATIBILITY_RELEASE_PREPARATION=PASS_PRE_CUTOVER"

# Installer does not close LIVE-arm sessions or change execution permissions.
# Post-restart Exchange reconciliation is performed by the private runtime.

# The separately approved producer cutover declares a real source GAP.
# Stop exactly one old PID AFTER backups+migrations+candidate validation and
# BEFORE changing the runtime symlink. A normal release never stops this worker.
if [[ "$native_code_changed" == "1" ]]; then
  [[ "$NATIVE_SOURCE_CUTOVER" == "1" ]] ||
    die "NATIVE_SOURCE_CUTOVER_AUTH_OR_OLD_PROCESS_INVALID"
  if [[ "$native_source_offline_cutover" == "1" ]]; then
    # Recheck immediately before symlink mutation: no surprise second writer.
    [[ "$native_source_was_active" == "0" &&
       "$(systemctl show cripta-native-public-trade-source.service --value --property=ActiveState)" == "inactive" &&
       "$(systemctl show cripta-native-public-trade-source.service --value --property=MainPID)" == "0" ]] ||
      die "NATIVE_OFFLINE_SOURCE_STARTED_DURING_RELEASE"
    [[ "$(sql_scalar "SELECT count(*) FROM analytics.native_public_trade_source_sessions
      WHERE state='ACTIVE'")" == "0" ]] ||
      die "NATIVE_OFFLINE_SOURCE_EPOCH_STARTED_DURING_RELEASE"
    old_source_session_state="$(sql_scalar "SELECT state FROM
      analytics.native_public_trade_source_sessions
      WHERE source_session_id='$producer_old_session'")"
    [[ "$old_source_session_state" == "GAP" ]] ||
      die "NATIVE_OFFLINE_OLD_GAP_CHANGED"
    echo "NATIVE_SOURCE_OLD_PRODUCER=ALREADY_INACTIVE_WITH_EXPLICIT_GAP"
  else
    [[ "$native_source_was_active" == "1" ]] ||
      die "NATIVE_SOURCE_CUTOVER_AUTH_OR_OLD_PROCESS_INVALID"
    systemctl stop cripta-native-public-trade-source.service
    native_source_stopped_for_cutover=1
    ! systemctl is-active --quiet cripta-native-public-trade-source.service ||
      die "NATIVE_SOURCE_OLD_WRITER_STILL_ACTIVE"
    old_source_session_state="$(sql_scalar "SELECT state FROM
      analytics.native_public_trade_source_sessions
      WHERE source_session_id='$producer_old_session'")"
    [[ "$old_source_session_state" == "GAP" ]] ||
      die "NATIVE_SOURCE_OLD_SESSION_NOT_EXPLICIT_GAP"
    echo "NATIVE_SOURCE_OLD_PRODUCER=STOPPED_WITH_EXPLICIT_GAP"
  fi
fi

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
# Persist Dashboard identity at the same physical cutover point. A later
# installer failure must not leave STATE_ROOT pointing at an older UI bundle.
printf '%s\n' "$RELEASE_COMMIT" > "$STATE_ROOT/DASHBOARD_UI_COMMIT"
chown root:cripta "$STATE_ROOT/DASHBOARD_UI_COMMIT"
chmod 0640 "$STATE_ROOT/DASHBOARD_UI_COMMIT"


# The postgres migration actor cannot traverse the cripta-owned /srv/cripta/runtime
# parent (0750). Install one exact root-owned, world-readable, immutable-source
# script in the approved /usr/local/libexec path instead of expanding privileges.
install -o root -g root -m 0644 "$runtime_release/operations/monitoring/market_hot_retention.py" /usr/local/libexec/cripta-market-hot-retention.py
cmp -s "$runtime_release/operations/monitoring/market_hot_retention.py" /usr/local/libexec/cripta-market-hot-retention.py ||
  die "retention actor path bytes differ from exact installed release"

install -o root -g root -m 0644 "$runtime_release/operations/monitoring/market_source_hot_retention.py" /usr/local/libexec/cripta-market-source-hot-retention.py
cmp -s "$runtime_release/operations/monitoring/market_source_hot_retention.py" /usr/local/libexec/cripta-market-source-hot-retention.py ||
  die "market source retention actor path bytes differ from exact installed release"

install -o root -g root -m 0644 "$runtime_release/operations/monitoring/native_public_trade_hot_retention.py" /usr/local/libexec/cripta-native-public-trade-hot-retention.py
cmp -s "$runtime_release/operations/monitoring/native_public_trade_hot_retention.py" /usr/local/libexec/cripta-native-public-trade-hot-retention.py ||
  die "native publicTrade retention actor path bytes differ from exact installed release"

for spec in "${unit_specs[@]}"; do
  IFS='|' read -r src_rel unit scope <<<"$spec"
  if [[ "$scope" == "tooling" ]]; then source_unit="$tooling_release/$src_rel"; else source_unit="$runtime_release/$src_rel"; fi
  [[ -f "$source_unit" ]] || die "unit source missing: $source_unit"
  if [[ "$unit" == "cripta-release-controller.service" ]]; then
    source_unit="$controller_stage/cripta-release-controller.service"
  fi
  install -o root -g root -m 0644 "$source_unit" "/etc/systemd/system/$unit"
done
install -d -o root -g root -m 0755 /etc/systemd/system/cripta-private-runtime.service.d
install -o root -g root -m 0644 "$runtime_release/operations/systemd/cripta-private-runtime.service.d/10-pythonpath.conf" /etc/systemd/system/cripta-private-runtime.service.d/10-pythonpath.conf

# The exact Git-reviewed native unit selects its matching source manifest.
# P90_MINIMAL removes speculative SPOT/R11 topics but preserves the full R11
# manifest and code in Git; rollback returns to that prior exact profile.
case "$ROADMAP470_STAGE" in
  ""|R11|R11_REPAIR|R11_REPAIR2|R11_REPAIR3|R11_REPAIR4|R11_REPAIR5|R11_REPAIR6|R11_REPAIR7|R11_REPAIR8|R11_REPAIR9|R12|R13|R13_REPAIR)
    install -d -o root -g cripta -m 0750 /etc/cripta/market-source
    source_profile_unit="$runtime_release/operations/systemd/cripta-native-public-trade-source.service"
    if grep -qx 'Environment=CRIPTA_SOURCE_PROFILE=P90_MINIMAL' "$source_profile_unit"; then
      grep -qx 'Environment=CRIPTA_R11_MAYAK_ANCHORS=0' "$source_profile_unit" ||
        die "P90_MINIMAL_ANCHORS_MUST_BE_OFF"
      grep -qx 'Environment=CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES=0' "$source_profile_unit" ||
        die "P90_MINIMAL_ORIGINALS_MUST_BE_OFF"
      source_manifest="$runtime_release/operations/monitoring/p90_minimal_public_feeds.json"
      source_profile=P90_MINIMAL
    else
      source_manifest="$runtime_release/operations/monitoring/r11_seven_public_feeds.json"
      source_profile=R11_SEVEN
    fi
    [[ -f "$source_manifest" && ! -L "$source_manifest" ]] ||
      die "SOURCE_EXACT_MANIFEST_NOT_REGULAR"
    install -o root -g cripta -m 0640 "$source_manifest" \
      /etc/cripta/market-source/required_public_feeds.json
    cmp -s "$source_manifest" /etc/cripta/market-source/required_public_feeds.json ||
      die "SOURCE_EXACT_MANIFEST_INSTALL_BYTE_MISMATCH"
    echo "SOURCE_FEED_PROFILE=$source_profile MANIFEST=INSTALLED_EXACT"
    ;;
esac
install -o root -g root -m 0755 "$runtime_release/operations/infrastructure/cripta-apply-incoming" /usr/local/sbin/cripta-apply-incoming
install -o root -g root -m 0755 "$runtime_release/operations/infrastructure/deploy_dashboard_ui.sh" /usr/local/sbin/cripta-deploy-dashboard-ui
install -d -o root -g root -m 0755 /usr/local/libexec/cripta-release-controller
install -o root -g root -m 0755 \
  "$controller_stage/cripta-release-controller" \
  /usr/local/libexec/cripta-release-controller/cripta-release-controller
install -o root -g root -m 0644 \
  "$controller_stage/release_controller_gate.py" \
  /usr/local/libexec/cripta-release-controller/release_controller_gate.py
install -o root -g root -m 0644 \
  "$controller_stage/release_controller_github.py" \
  /usr/local/libexec/cripta-release-controller/release_controller_github.py
install -d -o root -g root -m 0755 /usr/local/libexec/cripta-real-service-broker
install -o root -g root -m 0755 \
  "$broker_staged" \
  /usr/local/libexec/cripta-real-service-broker/real_service_broker.py


# One exact root-owned unit entrypoint; never run the privileged helper from
# cripta-writable runtime code. Root state retains secret preimage 0600 only.
install -d -o root -g root -m 0755 /usr/local/libexec/cripta-r06-switch
install -o root -g root -m 0755 "$r06_switch_stage" \
  /usr/local/libexec/cripta-r06-switch/r06_pg_mode_switch.py
cmp -s "$r06_switch_stage" \
  /usr/local/libexec/cripta-r06-switch/r06_pg_mode_switch.py ||
  die "R06 root management helper install-byte mismatch"
install -d -o root -g root -m 0700 \
  /var/lib/cripta/release-controller/r06-pg-switch
echo "R06_ROOT_ONLY_SWITCH_RAIL=INSTALLED_INERT"

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

# P90_MINIMAL deliberately pauses MAYAK. Removing a single runtime PID is
# insufficient: enabled Mayak can be pulled back by Dispatcher/reboot.
# The reviewed Dispatcher unit has no Wants= for MAYAK in this source scope.
if [[ -z "$ROADMAP470_STAGE" && "$native_code_changed" == "1" &&
      "${source_profile:-}" == "P90_MINIMAL" ]]; then
  dispatcher_source_unit="$runtime_release/operations/dispatcher_v2/cripta-dispatcher-v2.service"
  if grep -Eq '^Wants=.*cripta-mayak-v2\.service' "$dispatcher_source_unit"; then
    die "P90_MINIMAL_DISPATCHER_STILL_WANTS_MAYAK"
  fi
  systemctl stop cripta-mayak-v2.service
  systemctl disable cripta-mayak-v2.service >/dev/null
  mayak_boot_state="$(systemctl is-enabled cripta-mayak-v2.service 2>/dev/null || true)"
  [[ "$mayak_boot_state" == "disabled" ]] ||
    die "P90_MINIMAL_MAYAK_BOOT_NOT_DISABLED"
  ! systemctl is-active --quiet cripta-mayak-v2.service ||
    die "P90_MINIMAL_MAYAK_NOT_PAUSED"
  echo "P90_MINIMAL_MAYAK_PAUSED_BY_DATA_SCOPE=PASS"
fi

systemctl disable cripta-release-controller.service >/dev/null
controller_enabled_state="$(systemctl is-enabled cripta-release-controller.service 2>/dev/null || true)"
[[ "$controller_enabled_state" == "disabled" ]] ||
  die "release controller must remain disabled/manual-only"

if [[ -n "$ROADMAP470_STAGE" ]]; then
  # Installation-only checkpoint: no MarketSource, broker, private runtime,
  # consumer, observer or analytics service is started here. Preserve every
  # pre-existing service active/inactive state, including unrelated research.
  for service in "${managed_services[@]}"; do
    if [[ "${was_active[$service]}" == "1" ]]; then
      systemctl is-active --quiet "$service" ||
        die "ROADMAP470_UNRELATED_ACTIVE_SERVICE_CHANGED:$service"
    else
      ! systemctl is-active --quiet "$service" ||
        die "ROADMAP470_INACTIVE_SERVICE_BECAME_ACTIVE:$service"
    fi
  done
  ! systemctl is-active --quiet cripta-native-public-trade-source.service ||
    die "ROADMAP470_NATIVE_SOURCE_STARTED_UNEXPECTEDLY"
  gate_after="$(sql_scalar "SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'")"
  permissions_after="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
  sessions_after="$(sql_scalar "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'")"
  account_sessions_after="$(sql_scalar "SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'")"
  [[ "$gate_after" == "$pre_gate" &&
     "$permissions_after" == "$pre_permissions" &&
     "$sessions_after" == "$pre_sessions" &&
     "$account_sessions_after" == "$pre_account_sessions" ]] ||
    die "ROADMAP470_TRADING_CONTROL_CHANGED_DURING_NO_START_INSTALL"
  echo "ROADMAP470_NO_START_INSTALL=PASS stage:$ROADMAP470_STAGE"
  echo "POST_DEPLOY_EXCHANGE_TO_DB_RECONCILIATION=DEFERRED_NO_START"
else
  # The management-plane broker must be available before Dashboard accepts an
  # owner preparation click. It has no trading authority and cannot open gate/arms.
  systemctl enable cripta-real-service-broker.service >/dev/null
  systemctl start cripta-real-service-broker.service
  systemctl is-active --quiet cripta-real-service-broker.service ||
    die "REAL service broker failed after deploy"
  echo "REAL_SERVICE_BROKER=ACTIVE"
  
  # The supervisor only requests narrow, signed-proof account WS technical
  # clearance. It never issues broker restarts, gate changes, or owner re-arms.
  systemctl enable cripta-runtime-recovery-supervisor.service >/dev/null
  systemctl start cripta-runtime-recovery-supervisor.service
  systemctl is-active --quiet cripta-runtime-recovery-supervisor.service ||
    die "SUB WS technical recovery supervisor failed after deploy"
  echo "SUB_WS_TECHNICAL_RECOVERY_SUPERVISOR=ACTIVE"
  
  # Candidate/source compatibility was already proven and, when applicable,
  # registered before cutover. Do not create post-cutover compatibility state.
  echo "P90_SOURCE_COMPATIBILITY_RELEASE_PREPARATION=PRE_CUTOVER_VERIFIED"
  
  # Preserve exact pre-release Source liveness on ordinary releases.
  # A real producer ABI cutover must start a fresh writer; an already-running
  # byte-identical writer stays running and is re-proved; an inactive writer
  # stays inactive. Release must never turn Source ON merely to install code.
  if [[ "$native_code_changed" == "1" ||
        "$native_source_was_active" == "1" ]]; then
    native_probe_started_at="$(sql_scalar "SELECT clock_timestamp()")"
    if [[ "$native_code_changed" == "1" ]]; then
      systemctl enable cripta-native-public-trade-source.service >/dev/null
      systemctl start cripta-native-public-trade-source.service
    else
      systemctl is-active --quiet cripta-native-public-trade-source.service ||
        die "native MarketSource changed state during ordinary release"
    fi

    # A source that never records accepted provider cursors cannot be considered
    # healthy merely because systemd says active. A stale ACTIVE status/session
    # is not a fresh five-symbol source proof.
    native_source_ready=0
    for _attempt in {1..60}; do
      source_active_state="$(systemctl show cripta-native-public-trade-source.service --value --property=ActiveState 2>/dev/null || true)"
      case "$source_active_state" in
        active) ;;
        activating|reloading)
          sleep 1
          continue
          ;;
        inactive|failed|deactivating|"")
          die "native MarketSource writer not active during release proof: ${source_active_state:-unknown}"
          ;;
        *)
          sleep 1
          continue
          ;;
      esac
      # Read only the current source process's exact advertised ACTIVE session.
      # A stale ACTIVE row left by a killed old process cannot prove readiness.
      # A transient runuser/PAM wrapper failure is retryable only inside this
      # bounded 60-second readiness loop.
      source_session=""
      if ! source_session="$(runuser -u cripta -- python3 -c '
import json,sys
try:
    v=json.load(open(sys.argv[1],encoding="utf-8"))
    sid=v.get("session") if v.get("state")=="ACTIVE" else ""
except Exception:
    sid=""
print(sid if isinstance(sid,str) else "")
  ' /var/lib/cripta/native_public_trade_source/status.json)"; then
        source_session=""
      fi
      if [[ ! "$source_session" =~ ^[0-9a-f]{32}$ ]]; then
        sleep 1
        continue
      fi
      if [[ "$native_code_changed" == "1" ]]; then
        # A reused status file, stale PID or old session cannot approve the new
        # source. Exact loaded release must match the installed candidate.
        source_pid="$(systemctl show cripta-native-public-trade-source.service --value --property=MainPID)"
        source_commit_seen=""
        if ! source_commit_seen="$(runuser -u cripta -- python3 -c '
import json,sys
try:
    v=json.load(open(sys.argv[1],encoding="utf-8"))
    print(v.get("source_commit","") if v.get("state")=="ACTIVE" else "")
except Exception:
    print("")
  ' /var/lib/cripta/native_public_trade_source/status.json)"; then
          source_commit_seen=""
        fi
        if [[ "$source_pid" == "$producer_old_pid" ||
              "$source_commit_seen" != "$RELEASE_COMMIT" ||
              "$source_session" == "$producer_old_session" ]]; then
          sleep 1
          continue
        fi
      fi
      source_cursor_count="$(sql_scalar "SELECT count(DISTINCT c.external_symbol)
        FROM analytics.native_public_trade_source_cursors c
        JOIN analytics.native_public_trade_source_sessions s
          ON s.source_session_id=c.source_session_id
        WHERE s.source_session_id='$source_session'
          AND s.state='ACTIVE'
          AND s.market_source_id='ms-bybit-mainnet-public-v1'
          AND s.last_accepted_at >= now() - interval '10 seconds'
          AND c.external_symbol IN (
            'APTUSDT','ARBUSDT','DOTUSDT','INJUSDT','LTCUSDT'
          )
          AND c.accepted_at >= '$native_probe_started_at'::timestamptz
          AND c.event_at >= '$native_probe_started_at'::timestamptz
                                - interval '5 seconds'
          AND c.event_at >= now() - interval '3 minutes'")"
      if [[ "$source_cursor_count" == "5" ]]; then
        native_source_ready=1
        break
      fi
      sleep 1
    done
    [[ "$native_source_ready" == "1" ]] ||
      die "independent native source five-symbol fresh cursor proof missing"
    echo "INDEPENDENT_NATIVE_MARKET_SOURCE=ACTIVE_FIVE_SYMBOLS"

    if [[ "$native_code_changed" == "1" ]]; then
      new_source_status="$(sql_scalar "SELECT state FROM
        analytics.native_public_trade_source_sessions
        WHERE source_session_id='$source_session'
          AND producer_release_commit='$RELEASE_COMMIT'")"
      [[ "$new_source_status" == "ACTIVE" ]] ||
        die "NATIVE_SOURCE_NEW_SESSION_IDENTITY_UNVERIFIED"
      old_source_status="$(sql_scalar "SELECT state FROM
        analytics.native_public_trade_source_sessions
        WHERE source_session_id='$producer_old_session'")"
      [[ "$old_source_status" == "GAP" ]] ||
        die "NATIVE_SOURCE_OLD_GAP_LOST"
      echo "NATIVE_SOURCE_EXACT_CODE_CUTOVER=PASS old:$producer_loaded_commit new:$RELEASE_COMMIT"
    else
      echo "INDEPENDENT_NATIVE_MARKET_SOURCE=PRESERVED_ACTIVE"
    fi
  else
    ! systemctl is-active --quiet cripta-native-public-trade-source.service ||
      die "ordinary release unexpectedly started inactive native MarketSource"
    echo "INDEPENDENT_NATIVE_MARKET_SOURCE=PRESERVED_INACTIVE"
  fi

  p90_release_private_barrier=0
  if [[ "$pre_gate" == "0" &&
        "$pre_permissions" == "0" &&
        "$pre_sessions" == "0" &&
        "$pre_account_sessions" == "0" &&
        "${was_active[cripta-universal-entry-observer.service]:-0}" == "1" &&
        "${was_active[cripta-private-runtime.service]:-0}" == "1" ]]; then
    p90_release_private_barrier=1
  fi

  private_runtime_started_at=""
  if [[ "$p90_release_private_barrier" == "1" ]]; then
    private_runtime_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for service in "${managed_services[@]}"; do
      if [[ "$service" == "cripta-private-runtime.service" ||
            "$service" =~ ^cripta-private-runtime@[A-Za-z0-9_.-]+\.service$ ]]; then
        if [[ "${was_active[$service]}" == "1" ]]; then
          systemctl start "$service"
        fi
      fi
    done
    runuser -u cripta -- env \
      PYTHONPATH="$RUNTIME_ROOT/current/src:$RUNTIME_ROOT/current" \
      CRIPTA_RELEASE_COMMIT="$RELEASE_COMMIT" \
      CRIPTA_RELEASE_PRIVATE_RUNTIME_NOT_BEFORE="$private_runtime_started_at" \
      "$RUNTIME_ROOT/current/.venv/bin/python" \
      "$RUNTIME_ROOT/current/operations/implementation/wait_p90_private_runtime_ready.py" ||
      die "P90_RELEASE_PRIVATE_RUNTIME_BARRIER_FAILED"
  fi

  for service in "${managed_services[@]}"; do
    if [[ "$service" == "cripta-real-service-broker.service" ||
          "$service" == "cripta-runtime-recovery-supervisor.service" ]]; then
      continue
    fi
    if [[ "$p90_release_private_barrier" == "1" &&
          ( "$service" == "cripta-private-runtime.service" ||
            "$service" =~ ^cripta-private-runtime@[A-Za-z0-9_.-]+\.service$ ) ]]; then
      continue
    fi
    if [[ "${was_active[$service]}" == "1" ]]; then
      systemctl start "$service"
    fi
  done
  if [[ "$p90_release_private_barrier" == "1" ]]; then
    echo "P90_RELEASE_PRIVATE_RUNTIME_START_ORDER=PASS"
  fi

  # systemd may pull an intentionally inactive managed unit through Wants=/Requires=
  # while restoring another unit that was active before the release. On an
  # ordinary byte-compatible release the contract is exact pre-release liveness,
  # so normalize those dependency pulls back to OFF before strict verification.
  # Native producer ABI cutover has separate source/timer semantics below.
  if [[ "$native_code_changed" == "0" ]]; then
    for service in "${managed_services[@]}"; do
      if [[ "$service" == "cripta-real-service-broker.service" ||
            "$service" == "cripta-runtime-recovery-supervisor.service" ]]; then
        continue
      fi
      if [[ "${was_active[$service]}" != "1" ]] &&
         systemctl is-active --quiet "$service"; then
        systemctl stop "$service"
      fi
    done
    echo "ORDINARY_RELEASE_DEPENDENCY_STATE_NORMALIZED=PASS"
  fi

  for service in "${managed_services[@]}"; do
    if [[ "$service" == "cripta-real-service-broker.service" ||
          "$service" == "cripta-runtime-recovery-supervisor.service" ]]; then
      systemctl is-active --quiet "$service" ||
        die "required control-plane service inactive after deploy: $service"
    elif [[ "$native_code_changed" == "1" &&
            "$service" == "cripta-market-hot-retention.timer" ]]; then
      systemctl is-active --quiet cripta-native-public-trade-source.service ||
        die "native source inactive while source-coupled retention timer checked"
      systemctl is-active --quiet "$service" ||
        die "source-coupled market retention timer inactive after native cutover"
    elif [[ "$native_code_changed" == "1" &&
            "$service" == "cripta-market-hot-retention.service" ]]; then
      ! systemctl is-failed --quiet "$service" ||
        die "source-coupled market retention service failed after native cutover"
    elif [[ "${was_active[$service]}" == "1" ]]; then
      systemctl is-active --quiet "$service" || die "service failed after deploy: $service"
    else
      systemctl is-active --quiet "$service" && die "previously inactive service became active: $service"
    fi
  done
  
  gate_after="$(sql_scalar "SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'")"
  permissions_after="$(sql_scalar "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true")"
  sessions_after="$(sql_scalar "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'")"
  account_sessions_after="$(sql_scalar "SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'")"
  # Trading controls are reported, never used as installer go/no-go decisions.
  # A supervisor/runtime may legitimately change trading state during deployment.
  if [[ "$gate_after" == "$pre_gate" && "$permissions_after" == "$pre_permissions" && "$sessions_after" == "$pre_sessions" && "$account_sessions_after" == "$pre_account_sessions" ]]; then
    echo "TRADING_CONTROL_UNCHANGED=PASS"
  else
    echo "TRADING_CONTROL_CHANGED_EXTERNALLY=WARNING before:$pre_gate/$pre_permissions/$pre_sessions/$pre_account_sessions after:$gate_after/$permissions_after/$sessions_after/$account_sessions_after" >&2
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
  
  
fi

printf '%s\n' "$RELEASE_COMMIT" > "$STATE_ROOT/INSTALLED_COMMIT"
chown root:cripta "$STATE_ROOT/INSTALLED_COMMIT"
chmod 0640 "$STATE_ROOT/INSTALLED_COMMIT"
printf '%s\n' "$backup" > "$STATE_ROOT/LAST_BACKUP"
chown root:cripta "$STATE_ROOT/LAST_BACKUP"
chmod 0640 "$STATE_ROOT/LAST_BACKUP"

if [[ "$STAGED_OFFLINE" == "1" && -z "$ROADMAP470_STAGE" ]]; then
  # Generic staged-offline behavior keeps every backup.
  echo "STAGED_OFFLINE_BACKUP_RETENTION=KEEP_ALL"
else
  # #470 incremental acceptance keeps the newest rollback backup plus at most
  # one still-valid control checkpoint. Seven full pg_dump copies would exceed
  # the currently measured /data capacity before R13.
  prune_release_backups
fi

# Owner decision 2026-10-31: immutable runtime artifacts retain only the exact
# current release plus the exact immediate previous deployed baseline. This
# runs after cutover, managed-service liveness checks, reconciliation handling
# and INSTALLED_COMMIT persistence. It never touches other artifact/data roots.
runuser -u root -- /usr/bin/python3 \
  "$runtime_release/operations/infrastructure/runtime_release_retention.py" \
  --root "$RUNTIME_ROOT/releases" \
  --current-link "$RUNTIME_ROOT/current" \
  prune \
  --current-sha "$RELEASE_COMMIT" \
  --previous-sha "$EXPECTED_BASELINE"

rm -f "$STATE_ROOT/LAST_FAILED_RELEASE"
deploy_succeeded=1
cleanup_controller_stage
trap - EXIT
trap - ERR

echo "DEPLOY_EXACT_VERIFIED_COMMIT=PASS"
if [[ -n "$ROADMAP470_STAGE" ]]; then
  echo "ROADMAP470_STAGED_INSTALL=PASS stage:$ROADMAP470_STAGE"
fi
echo "INSTALLED_COMMIT=$RELEASE_COMMIT"
echo "RUNTIME_CURRENT=$(readlink -f "$RUNTIME_ROOT/current")"
echo "DASHBOARD_UI_CURRENT=$(readlink -f "$DASHBOARD_UI_ROOT/current")"
echo "RESEARCH_TOOLING_CURRENT=$(readlink -f "$RESEARCH_TOOLING_ROOT/current")"
echo "BACKUP=$backup"
echo "GATE=$gate_after"

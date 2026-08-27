#!/usr/bin/env bash
set -euo pipefail

root=/data/cripta/datasets/raw/20260518_20260816
sealed=/srv/cripta/reports/holdout_new15/archive_validation_20260824
mkdir -p "$sealed"

old='^(1000PEPE|ADA|BTC|DOGE|ETH|LINK|SOL|UNI|XRP)USDT$'
find "$root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' \
  | grep -Ev "$old" \
  | sort > "$sealed/symbols.txt"

export root
check_symbol() {
  symbol=$1
  find "$root/$symbol/public_trades" -maxdepth 1 -type f -name '*.csv.gz' -print0 \
    | xargs -0 -r -n1 gzip -t
  find "$root/$symbol/orderbook" -maxdepth 1 -type f -name '*.zip' -print0 \
    | xargs -0 -r -n1 unzip -tqq
  printf '%s OK\n' "$symbol"
}
export -f check_symbol

xargs -r -n1 -P10 bash -c 'check_symbol "$1"' _ < "$sealed/symbols.txt" \
  > "$sealed/status.txt"
sha256sum "$sealed/symbols.txt" "$sealed/status.txt" > "$sealed/fingerprints.sha256"
printf 'validated=%s failed=0\n' "$(wc -l < "$sealed/status.txt")"

#!/usr/bin/env bash
set -euo pipefail

root=/data/cripta/datasets/raw/20260518_20260816
for d in "$root"/*USDT; do
  [[ -d "$d" ]] || continue
  symbol=${d##*/}
  trades=$(find "$d/public_trades" -maxdepth 1 -type f 2>/dev/null | wc -l)
  orderbook=$(find "$d/orderbook" -maxdepth 1 -type f 2>/dev/null | wc -l)
  bytes=$(du -sb "$d" | cut -f1)
  printf '%s %s %s %s\n' "$symbol" "$trades" "$orderbook" "$bytes"
done

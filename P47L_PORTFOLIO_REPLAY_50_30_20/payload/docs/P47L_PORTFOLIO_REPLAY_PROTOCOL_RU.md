# P47L - chronological portfolio replay

Purpose: convert the already-computed P47K signal outcomes into a chronological portfolio
replay without re-reading raw market data or changing frozen Entry V1/P46.

Primary fixed benchmark:

- reference bank: 100 USD;
- leverage: 10x;
- slot budgets: 50% / 30% / 20% of the capped reference bank; maker entry fee reserve is included in each budget;
- sizing bank is capped at 100 USD: it scales down after realized losses but never scales above 100 USD;
- one active position per symbol;
- maximum three simultaneous positions;
- when a high-priority slot is released, the next eligible Entry can reuse it;
- successful P47K continuation exits at +1.10%, maker exit fee;
- initial/pre-0.50 stop exits at -1.00%, taker exit fee;
- after-0.50 protective stop exits at -0.50%, taker exit fee;
- Entry fee is maker;
- default fee rates: maker 0.020%, taker 0.055%; both are CLI parameters.

Policies compared without tuning:

1. NO_CAP_50_30_20 - only symbol/capacity constraints.
2. CAP2_15M_50_30_20 - same portfolio plus a diagnostic market-burst sensitivity:
   no more than two new executed portfolio entries in a rolling 15-minute window.

Input truth:

- latest `reports/early_protection_plus05_minus05_v1/ALL9_*/event_results.csv`;
- latest `reports/early_failure_puncture_v1/ALL9_*/early_failure_events.csv` only to recover
  the exact first -1% timestamps for the 66 baseline early failures.

The script validates frozen P47K counts and P47G count before replay. The earliest P47K
censored Entry is used as the new-entry cutoff so the tail is not treated as complete.

Outputs:

- `policy_summary.csv`;
- `executed_trades.csv`;
- `skipped_signals.csv`;
- `daily_summary.csv`;
- `occupancy_summary.csv`;
- `stop_clusters_15m.csv`;
- `sources.csv` with SHA256;
- `summary.json` machine truth;
- `summary.md` presentation.

Important limitations:

- this is capped-size portfolio economics, not a 90-day compounding/account-growth forecast;
- realized drawdown does not include mark-to-market movement of still-open positions;
- P47K outcomes remain signal-path outcomes, now filtered by chronology/capacity;
- no new Exit parameter is tuned here;
- CAP2_15M is a sensitivity, not a production rule;
- exit fee is approximated using entry notional as the fee base because P47K compact results
  do not carry side/exit quantity-value detail.

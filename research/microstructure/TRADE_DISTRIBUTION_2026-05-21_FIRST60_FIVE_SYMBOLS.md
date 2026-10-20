# CRIPTA — new first-hour #27/#28 physical pilot (2026-05-21)

{
  "status": "RESEARCH_RESULT_NOT_CANON_NOT_READY",
  "date_utc": "2026-05-21",
  "window": "00:00:00Z to 01:00:00Z; first 60 min only",
  "repo_main_checked_before_run": "40f361a95061eff7a2ef8ce5d5ae3c2de60e19e2",
  "execution": "SentinelX read-only exec under approved cripta actor, python3 -B from /srv/cripta/source_checkout/research/microstructure/experiments/trade_distribution_v1.py, --minutes 60",
  "source_head_identity": "SERVER_MIRROR_SCRIPT_COMMIT_NOT_ATTESTED; DO NOT CLAIM VERIFIED EXECUTED SOURCE_HEAD",
  "parameters": "--date 2026-05-21 --minutes 60, five symbols one by one",
  "host_status_before": "LOAD 5.72/6.06/6.00, available RAM 2401MB, 4 OLD90 workers active; all five independent light scoped runs returned rc0",
  "source": "/data/cripta/datasets/raw/20260518_20260816/{symbol}/public_trades/{symbol}2026-05-21.csv.gz",
  "raw_sha256": "NOT_CHECKED",
  "data": [
    {
      "symbol": "APTUSDT",
      "fills": 2875,
      "buy_fills": 1486,
      "sell_fills": 1389,
      "median_positive_timestamp_gap_ms": 1275.800048828125,
      "fraction_adjacent_same_timestamp": 0.6503131524008351,
      "fill_p95_ex_post_usdt": 462.3091,
      "fill_p99_ex_post_usdt": 922.372062,
      "notional_fraction_ge_p99": 0.15882805837374,
      "exit_code": 0
    },
    {
      "symbol": "INJUSDT",
      "fills": 8539,
      "buy_fills": 5055,
      "sell_fills": 3484,
      "median_positive_timestamp_gap_ms": 357.6500244140625,
      "fraction_adjacent_same_timestamp": 0.7200749590067932,
      "fill_p95_ex_post_usdt": 518.6832,
      "fill_p99_ex_post_usdt": 1495.5072,
      "notional_fraction_ge_p99": 0.2264023676274873,
      "exit_code": 0
    },
    {
      "symbol": "DOTUSDT",
      "fills": 7034,
      "buy_fills": 3838,
      "sell_fills": 3196,
      "median_positive_timestamp_gap_ms": 279.25,
      "fraction_adjacent_same_timestamp": 0.7662448457272857,
      "fill_p95_ex_post_usdt": 449.12142,
      "fill_p99_ex_post_usdt": 916.22838,
      "notional_fraction_ge_p99": 0.17231064165714824,
      "exit_code": 0
    },
    {
      "symbol": "LTCUSDT",
      "fills": 2407,
      "buy_fills": 1178,
      "sell_fills": 1229,
      "median_positive_timestamp_gap_ms": 2573.9998779296875,
      "fraction_adjacent_same_timestamp": 0.770573566084788,
      "fill_p95_ex_post_usdt": 1175.02,
      "fill_p99_ex_post_usdt": 2296.275,
      "notional_fraction_ge_p99": 0.21784447552998384,
      "exit_code": 0
    },
    {
      "symbol": "ARBUSDT",
      "fills": 11438,
      "buy_fills": 6509,
      "sell_fills": 4929,
      "median_positive_timestamp_gap_ms": 79.300048828125,
      "fraction_adjacent_same_timestamp": 0.7606889918684969,
      "fill_p95_ex_post_usdt": 465.936256,
      "fill_p99_ex_post_usdt": 1046.65464,
      "notional_fraction_ge_p99": 0.29528332108926914,
      "exit_code": 0
    }
  ],
  "qc": {
    "all_five_exec_rc0": true,
    "fill_count_side_partition_5_of_5": true,
    "independent_decimal_qc": "NOT_RUN_THIS_WINDOW",
    "source_sha256": "NOT_RUN",
    "timestamp_precision": "float(timestamp), sub-ms membership uncertain"
  },
  "limitations": [
    "These are first-hour ex-post distribution summaries only, not 24h full replay, no temporal as-of predictive feature.",
    "No per-fill independent verification or source SHA256 for May21; mirror loaded commit not independently attested.",
    "Do not map these summary values to profit or trading signal; old OL90 job was not altered."
  ],
  "next": "If host capacity remains permitted, run separate Decimal independent QC 60 minutes and confirm exact installed source identity; no fresh overlapping unknown jobs."
}

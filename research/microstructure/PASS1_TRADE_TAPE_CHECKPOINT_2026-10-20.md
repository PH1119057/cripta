# CRIPTA — первый из пяти рабочих проходов: trade/tape №1–8, №27–28

Дата контрольной точки: 2026-10-20. Статус: **RESEARCH EVIDENCE / PASS 1 STARTED / NOT RESEARCH-READY**. GitHub `main` проверен до этой ревизии: `3ef144ce58a058ba43d8d1cee23d89e33d8e6a15`. Этот документ не является CANON, не изменяет торговый контур и не заменяет `RESEARCH_COMPUTE_RULES_RU.md §21.6`.

## Цель и идентичность прохода

Первая группа = 10 indicator IDs × 5 Bybit instruments = **50 distinct instrument×indicator pairs**: №1,2,3,4,5,6,7,8,27,28; APTUSDT, INJUSDT, DOTUSDT, LTCUSDT, ARBUSDT. Контрольный основной день для имеющихся pair gate = 2026-05-20 UTC. №27/28 имеют дополнительные May21 first60/first120 и APT May21 full-day timestamp-membership evidence, но это отдельные scopes; запрещено объединять их с May20 или переносить проверку APT на другие symbols.

## CHECKED HERE — актуальный GitHub source/evidence review

- `passports/trade_minute_six_factors_2026-05-20_gate_v2.json` содержит 30/30 pair records со статусом `NO_READY`. Самостоятельный candidate-v2 all-minute comparator ранее выполнил 5/5 × 1440 rows × 8 tested fields в явной float tolerance. Оригинальный v1 **FAILED на APT**: три fills у границы 23:26:59.9995 распределялись в следующую минуту. V2 не подменяет исторический V1.
- `passports/trade_tape_4_8_27_28_2026-05-20_gate_v2.json` содержит 20/20 records `ready=NO`; независимая Decimal **суточная агрегатная** QC 5/5 для №4/#8/#27/#28 не подтверждает полную per-row/per-fill parity.
- Current `trade_minute_factors_v2_exact_timestamp_floor.py`: bucket membership `int(Decimal(timestamp)*1000)`; агрегируются строки Bybit public_trades с `foreignNotional`, `side`, day-start reset для CVD; RVOL использует 60 **предыдущих** завершённых минут. `available_after_utc` устанавливается на закрытие минуты **алгоритмически**, но actual receiver receipt/publication time в source reader отсутствует: `RECEIVER_AVAILABLE_AFTER_NOT_PROVED`.
- Current `trade_minute_full_row_crosscheck_v2.py`: независимая агрегация Decimal по epoch/window, сравнение по 8 полям через tolerance `rel_tol=1e-10, abs_tol=1e-6`; primary source закреплён на GitHub commit `48fd2e2aedae9e9d49df4f8be346ae01879b9bfe`. Это не доказательство byte-level hashes всех полей и не проверка received time.
- Current `large_trade_streaks_v1.py`: №4 использует prior60 historical p99 при минимум 100 fills; №8 использует adjacent same timestamp+side groups (не доказанный parent order). В текущем source `float(timestamp)` не гарантирует exact fine-grained membership.
- Current `trade_distribution_v1.py`: №27 положительные интервалы между adjacent recorded fills; №28 ex-post p95/p99 fill notional — описательная статистика всего выбранного окна, не причинный online threshold. У current reader также `float(timestamp)`.
- Дополнительное May21 evidence: `TRADE_DISTRIBUTION_2026-05-21_FIRST60_DECIMAL_QC.md` — 5/5; `TRADE_DISTRIBUTION_2026-05-21_FIRST120_INDEPENDENT_QC.md` — 5/5; `TRADE_DISTRIBUTION_MAY21_EXACT_BOUNDARY_QC.md` — **только APT** May21 93665 records, Decimal/float full-day membership zero mismatches. Попытка следующего INJ check была upstream OpenAI-blocked до подтверждённого запуска. Не повторять или обходить тот route без owner/tool-policy решения.

## Невыполненные обязательные §21.6 gates — по каждой из 50 пар

1. Raw file immutable SHA256 с exact source file metadata и linkage к source/run manifest: **OPEN**.
2. Отдельный `event_time / observed_at / publication semantics / available_after` contract; source не подтверждает фактическое exchange-to-receiver delivery time: **OPEN**.
3. Полный owner-selected requested research period для каждой монеты, не только один контрольный день/час: **OPEN**.
4. Для №4/#8/#27/#28 — independent per-minute/per-fill value+event QC и exact timestamp-bucket semantics (не только aggregate distribution): **OPEN**.
5. Для №1/#2/#3/#5/#6/#7 — manual source sign/denominator checks, exact source/run identity, deterministic scope beyond historical May20: **OPEN**.
6. Формальные machine-readable per-pair QC verdicts `RESEARCH-READY`: **0/50**.

## Operational stop/go

- Работа в этом checkpoint только GitHub read/source audit; **ни одного нового серверного job**, никакого изменения Strategy/Entry/Exit/Execution/DB/services/deploy/LIVE.
- Hard Stop для ранее заблокированного May21 cross-instrument exact-boundary route: **BLOCKED_UPSTREAM_OPENAI / OWNER_DECISION_REQUIRED for alternative**. Это локальная блокировка ветви, не основание останавливать весь проект.
- Перед любым новым server-side research process: current actor/host capacity, source reproducibility, permitted tool route; перед filesystem write: `DEVELOPMENT_RELEASE §5.1, §19.1–19.4` и `CURRENT_PROJECT_MAP §1.4`; тяжёлый job не запускать без соответствующих gates. При неизвестном результате job не дублировать его.
- Ни один из 50 pair не получает READY за счёт переноса QA с другой монеты или периода. `NO_DATA/UNKNOWN` не равно нулю.

## Итог текущей операции / следующий исполнимый шаг

**50/50 записей перепроверены на уровне существующего реестра и GitHub source definitions, 0/50 formally READY. Первый проход открыт, а не завершён.** Следующий шаг — разрешённый provenance/clock-data audit конкретных May20 raw files; только затем bounded независимый QC недостающих trade/tape rows. Если receiver publication/observed timing вообще не записывался, честно оформить недоказуемость retrospective causal availability и не присваивать READY. Этот checkpoint фиксирует новый исходниковый forensic, а не новый успешный server replay.


## 2026-10-21 — continuation: host provenance access preflight

- GitHub main HEAD at check: `6d931baf1a8eeb323704df36cb765f0c67d477f9`; `RESEARCH_COMPUTE_RULES_RU.md §21`, `DEVELOPMENT_RELEASE_RULES_RU.md §5.1/§19`, `CURRENT_PROJECT_MAP_RU.md §1.4` reviewed for this operation.
- SentinelX host `robot`, id `host_b7fb1dededf247f6`, connected/operational, agent 0.11.14; allowlist verified. Read-only `uptime`: exit 0, load averages 3.48/2.70/2.11 at check. This is **not** an exhaustive resource/other-job status; notifications showed other ongoing work (one live job plus orphaned previous outcomes). Do not launch heavy research merely from `uptime`.
- Exact attempted read-only source provenance metadata command: `stat /data/cripta/datasets/raw/20260518_20260816/APTUSDT/public_trades/APTUSDT2026-05-20.csv.gz`. SentinelX `sentinel_exec` returned `ok=true`, `returncode=1`, host stderr `stat: cannot statx '...': Permission denied`. **CLASS=UNIX ACCESS DENIAL FOR INVOKING EFFECTIVE ACTOR/PATH**, not an OpenAI upstream or SentinelX allowlist denial. Actual UID/GID for this invocation was **NOT CHECKED HERE**; path components/mode/ACL were **NOT CHECKED HERE**. This failure does NOT establish file absence, and does not establish that designated research actor `cripta` cannot read. No actor switching, permission mutation, command retry or bypass attempted.
- `SOURCE_FILE_SHA256=NOT_CHECKED_HERE`; raw row schema and true source capture/receipt clock = `NOT_CHECKED_HERE`. No pass is promoted. Existing GitHub source-reader audit remains valid, but source provenance gate remains OPEN.
- Next action requires non-mutating effective actor/path/ACL preflight under approved research rail and a server source access contract compliant with §19.5–19.6. Do not use root as stand-in for intended research actor, do not widen permissions. If exact source visibility cannot be established under approved tool/actor gate, classify `BLOCKED`, keep 50/50 pairs `NO_READY` and continue only independent GitHub-side tasks.
- **Pass 1 still OPEN; 0/50 RESEARCH-READY**. No server filesystem writes, no jobs launched/stopped, no DB/Strategy/LIVE changes. This operation's only write is this GitHub research checkpoint.


## 2026-10-21 — identity preflight continuation, upstream gate

- Exact read-only `sentinel_exec(command="whoami")` returned `ok=true`, `returncode=0`, `output="sentinelx"`. Therefore the previous unqualified `stat` was executed on the management `sentinelx` route, not proven as `cripta`. The `stat` permission denial may reflect management actor traversal; **it cannot be attributed to research actor `cripta`**.
- The *next* independent diagnostic tool invocation in a staged sequence (`id` command) was **BLOCKED_UPSTREAM_OPENAI** before a host result. The batch aborted; `namei` and the planned allowlisted `sudo -u cripta python3 -c` file-read smoke were **NOT EXECUTED / NOT CHECKED HERE**. No guesses about the exact OpenAI block rule. No retry, alternate formatting, actor substitution, privilege/permission change, or tool-policy mutation.
- Current actor conclusion: `MANAGEMENT_ACTOR=sentinelx CHECKED HERE`; `RESEARCH_ACTOR_SOURCE_READ=UNKNOWN`; source SHA256/receipt clock/full-period checks stay OPEN, 0/50 READY.
- Route remains **BLOCKED_UPSTREAM_OPENAI** for further host actor/path QC until an independently approved policy route exists. GitHub code review is unaffected. The blocker is not a failure of `cripta`'s proven Unix rights; no server work launched.


## 2026-10-21 — owner-authorized correct research actor read-only smoke + source SHA256

Owner explicitly authorized actual archive access verification. Current GitHub source reviewed: `DEVELOPMENT_RELEASE_RULES_RU.md §19.5–19.6`. SentinelX capabilities summary returned operational agent 0.11.14; no tool policy changes.

- `sudo -u cripta python3 -c 'import os; print(os.geteuid())'` → rc0 UID **107**.
- `sudo -u cripta python3 -c '...os.stat(Apt May20 archive)...os.access(..., os.R_OK)...'` → rc0 UID107, **4549721** bytes, readable True.
- Five separate read-only `sudo -u cripta python3 -c '...hashlib.sha256(open(p,"rb").read())...'` runs all rc0; no files changed. **These hash values are evidence from the current bytes, not independent verification against an earlier immutable source manifest.**

| Bybit public_trades, UTC date 2026-05-20 | Compressed bytes | SHA256 |
|---|---:|---|
| APTUSDT | 4549721 | `6a589dc5afbc064a577f9d08bb921008e1518da42540fb5c15e4ea05a1144b02` |
| INJUSDT | 9464084 | `7e2d2716b34167d65b43ca64fcd767aae902267f3d5f20ca3d2e90a3066a5a6e` |
| DOTUSDT | 5656864 | `39831bb5b07974f5de407efa8a2aa8ef2b32f257d67796aa7dc532b680fd477f` |
| LTCUSDT | 3213522 | `b6e784e82918cf0e2c7b9f7d9464dc49478c7077eff0317f7a50be27764ee84e` |
| ARBUSDT | 8019377 | `7d8de412077e6f51eb46d1b3e1055af43dd228ec3d24da41a36c9e5f16cb254a` |

**Resolved:** previous `sentinelx` management-actor `stat` denial is not an archive outage; proper `cripta` read path PASS on APT and SHA256 reads PASS all five. SOURCE SHA256 now independently registered at this checkpoint for the five May20 compressed files. 50 pair scope shares those five source files, but per-pair source manifest linkage/complete period remains OPEN. `observed_at`/receiver publication and causal available-after timing not proven; #4/#8/#27/#28 independent row QC not proven. **0/50 strict §21.6 RESEARCH-READY**, first pass still open.

No server file write, process launch, research job, privilege change, SentinelX policy change, DB mutation, LIVE action. The only change is this GitHub research report.


## 2026-10-21 — raw CSV schema and full-day integrity audit, all five instruments

Read-only `sudo -u cripta python3 -c` opened each already SHA256-registered May20 compressed Bybit public trade file and read all records with `gzip/csv/Decimal`. All **10 command invocations** completed `returncode=0` (five initial full-file scans, five independent bounded numeric/day-window scans), no filesystem writes. Source day `2026-05-20T00:00:00Z..2026-05-21T00:00:00Z` [start inclusive, end exclusive]; numeric comparisons use `Decimal` and exact epoch seconds.

Raw CSV column names identical for five symbols: `timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional,RPI`. **No receiver receipt/observed_at/source publication timestamp field** in these raw CSV headers (does not prove no metadata exists in a separate collector). The `timestamp` is source event timestamp, not proven arrival time. Historical as-of semantics still require a specific conservative availability contract.

| Symbol | Fill records | Nonempty UTC minutes / 1440 | Nonmonotone event timestamps | Invalid symbol/side | Outside exact UTC day | Nonpositive price or size / negative foreignNotional |
|---|---:|---:|---:|---:|---:|---:|
| APTUSDT | 117069 | 1440 | 0 | 0 | 0 | 0 |
| INJUSDT | 260994 | 1440 | 0 | 0 | 0 | 0 |
| DOTUSDT | 151067 | 1436 | 0 | 0 | 0 | 0 |
| LTCUSDT | 93645 | 1423 | 0 | 0 | 0 | 0 |
| ARBUSDT | 209152 | 1440 | 0 | 0 | 0 | 0 |
| **TOTAL** | **831927** | — | **0** | **0** | **0** | **0** |

**Nuance:** four minutes without any recorded trades for DOT, 17 for LTC. Valid empty trade minutes or missing source segments cannot be distinguished by this file-only scan. No automatic `NO_DATA=0` promotion. No proof of exchange-side 100% completeness, uniqueness of `trdMatchID`, quantity×price accounting reconciliation, separate receiver timestamps, full requested multi-day period, per-fill indicator numeric parity, or historical immutable manifest. Those gates remain open.

**Status:** `CHECKED HERE` file identity/raw schema/monotonicity/day-window/numeric basic integrity; `RESEARCH-READY=0/50`; `PASS1=OPEN`. Nothing launched/stopped, no dataset/server mutation, no LIVE. GitHub journal/checkpoint update only.

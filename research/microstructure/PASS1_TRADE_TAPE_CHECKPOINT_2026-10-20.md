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

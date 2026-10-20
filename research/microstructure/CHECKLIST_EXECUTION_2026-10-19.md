# CRIPTA — рабочий чек-лист завершения исследования 30 индикаторов

Дата: 2026-10-19. Статус: **RESEARCH / EVIDENCE, NOT CANON**. Единственный authority: GitHub `PH1119057/cripta:main`. Основание: `research/microstructure/INDICATOR_PROGRESS_2026-10-19.md`, `research/CRIPTA_RESEARCH_JOURNAL_2026-10-18.md`, `docs/RESEARCH_COMPUTE_RULES_RU.md §21`. Контрольные периоды Bybit 2026-05-19 и 2026-05-20 UTC; инструментные результаты не объединять без определения нормализации.

## Общий gate каждого из 30 × 5 инструментов

- [ ] Измерительный контракт: formula_version, источник, UTC event/observed/available-after, units, знаменатели, window/reset/missing/censoring.
- [ ] Исходник в GitHub и source SHA256, период, точный instrument identity и run manifest.
- [ ] Физический расчёт полного требуемого периода по каждому инструменту, evidence QC.
- [ ] Независимая проверка каждого instrument×indicator, включая доступность во времени и отсутствие lookahead.
- [ ] Версионированный per-pair паспорт §21.6; READY только после всех gate.
- [ ] Отдельный OOS/holdout и экономические результаты **после комиссий**, если изучается торговая ценность.
- [ ] Не менять Strategy/Entry/Exit/Execution/REAL/LIVE без owner decision и канонического процесса.

## Очередь текущего этапа

- [x] №19, №22: первичный и независимый 1440-row SHA256 QC на 2026-05-20, по пяти инструментам, raw source SHA256 5/5. `RESEARCH-READY` не присвоен — формальный §21 gate остаётся.
- [x] №9, №11, №29: полный 2026-05-19 день и независимые суточные **округлённые сводки** 5/5. Полный независимый post-row QA и паспорт открыты.
- [x] №1/2/3/4/5/6/7/8/27/28: физические вычисления второго дня 5/5; требуется независимый QC и паспорта.
- [ ] №18, №20: второй день 2026-05-20 — только APT/INJ/DOT 3/5 подтверждены. LTC/ARB не проверены после **OpenAI upstream tool-security block**. Блокированный маршрут не повторять и не обходить. `HARD_STOP=YES` для маршрута; `OWNER_DECISION_REQUIRED=YES` для его замены.
- [ ] №10: TOP1 OFI и top-five stock decomposition есть, но strict multi-level price-aware OFI открыт.
- [ ] №12: visible depletion / book↔trade timestamp windows и первые seq/cts samples выполнены; настоящие отмены от исполнений не отделены.
- [ ] №13/14/15/16/17/21/30: полнодневные физические proxy-расчёты 5/5; строгая физическая идентичность, независимый QC и паспорта ещё открыты.
- [ ] №23 OI: нет проверенного source coverage на выбранный день, `NO_DATA` не ноль.
- [ ] №24 Liquidations: частичная часовáя source inventory, полное покрытие/содержание не доказано; отдельный SentinelX `command_not_allowed` на альтернативном interpreter, без обхода.
- [ ] №25 Spot/Perp: Spot источник на выбранный день не подтверждён, `NO_DATA`.
- [ ] №26 Cross-Venue Lead–Lag — **последним**, как решил owner.

## Правило продвижения

Каждый индикатор должен иметь отдельный коммит результатов в `research/CRIPTA_RESEARCH_JOURNAL_2026-10-18.md` и обновление `research/microstructure/INDICATOR_PROGRESS_2026-10-19.md`. Незаполненные и отрицательные проверки писать явно как OPEN / NO_DATA / NOT CHECKED, не считать их PASS. При запрете команды/доступа фиксировать фактический уровень запрета; не повторять заблокированное действие, не менять actor и не обходить policy. При `HARD_STOP` остановить затронутый маршрут и потребовать решение владельца.

**Текущий маршрут исполнения №18/№20: HARD_STOP=YES, OWNER_DECISION_REQUIRED=YES**. Разрешена аналитика ранее сохранённых evidence и документация без запрещённых серверных операций; новых расчётов через заблокированный маршрут не выполнять.


## Следующая разрешённая работа после hard stop — документарный gate §21.6

Проверено в GitHub `main` на контрольной точке `8b7b77a8b2556d5e48aa4c8d538e2893adce3312`: паспорт `research/microstructure/passports/range_vwap_2026-05-20_independent_v1.json` имеет source SHA256 каждой из пяти монет и точные независимые minute-row SHA256; однако формула, timeline, источники и ограничения описаны на **общем уровне features**, а per-pair `readiness=PHYSICAL_QC_PASS_NOT_RESEARCH_READY`. Нет основания объявить `RESEARCH-READY` автоматически. В доступном evidence отсутствуют отдельное подтверждение event/observed/available-after для каждого источника, сохраняемый проверенный аналитический cube/dataset artifact и формальная проверка всего §21.6. Второй день 2026-05-20 — evidence, не OOS доказательство доходности.

- [x] **Источник каждого из пяти сырьевых trade-файлов идентифицирован по SHA256** для #19/#22, в версии паспорта 2026-05-20.
- [x] **Независимые полные minute-row checksum 5/5** для #19/#22 документированы.
- [ ] **Per-pair formal §21.6 gate для #19/#22**: источник, физические поля, as-of availability, version+scope, no-leak и покрытие каждого instrument×indicator отдельно.
- [ ] **Persistent dataset/cube manifest**: физическое хранилище, его writer actor/path контракт и QC требуют отдельной подготовки; не создавать серверный writer без обязательного DEVELOPMENT/MAP pre-read и ACL evidence.
- [ ] **OOS и доходность после комиссий**: не выполнены, доходность не заявлять.

Этот этап — документарная сверка уже сохранённого GitHub evidence, а **не новый серверный расчёт**. Блокированная операция №18/20 не повторялась. В работе следующими допустимыми действиями остаются проверка существующих документов и source-code definitions через GitHub и подготовка явных QC gaps; server-side execution заблокированного маршрута ждёт owner/tool-policy разрешения.


## §21.6 machine-readable per-instrument gate audit — 2026-10-19

CHECKED HERE: a separate **derived, non-canonical** evidence audit for #19 Range Compression and #22 running VWAP/POC, on five instruments for May 20, was committed to `research/microstructure/passports/range_vwap_2026-05-20_gate_audit_v1.json` in GitHub `42183051b9aad0d31b9597454cb5b2ab3087a05c`.

- [x] 5/5 per-instrument compressed raw source SHA256 and source QC already present in prior passport
- [x] 5/5 1440-row independent checksum parity already attested
- [x] Formula and completed-minute availability **declared by versioned source**, with missingness rules
- [ ] Independent measurement of actual exchange publication / receiver observation time — **NOT CHECKED**; declaration alone cannot prove causal visibility
- [ ] Persisted full-minute analytical dataset/cube with independent file-manifest QC — **NOT VERIFIED**
- [ ] Formal READY proof for all `(instrument,indicator,version,scope)` pairs — **OPEN**; no automatic promotion
- [ ] Out-of-sample trading result after fees — **NOT CHECKED**, distinct from physical measurement readiness

Machine-readable audit is an inventory of verified source evidence and exact missing gates, not a new research experiment. Blocked upstream server route (#18/#20) was not invoked or retried.


## Проверка источников для №10/12–17/21/30 — 2026-10-19

CHECKED HERE: `research/microstructure/passports/proxy_identity_gate_audit_2026-10-19_v1.json` saved in GitHub commit `082df27f903d57b6b51758652af2a7a0eb7fcec2`. Audit covers nine unfinished indicator IDs and maps current main source files, physical proxy identities and strict §21 gates without rerunning blocked server operations.

- [x] Найдены в текущем main существующие исходники proxy/physical измерений всех девяти ID.
- [ ] №10: `top1_price_transition_ofi_v1.py`, закреплённый в историческом журнале, **отсутствует** в текущем GitHub main tree. Есть отдельный `rank_ofi_v1.py`; они **не объявляются эквивалентными** без проверки алгоритма и historical-source SHA/commit. Strict multi-level OFI остаётся OPEN.
- [ ] №12: снятие ликвидности vs исполнение не разделены; verified trade-window proximity не делает cancellation observable.
- [ ] №13/15/16/21/30: измеряются только заявленные физические proxy, независимый QC и строгая identity открыты.
- [ ] №14/17: future labels не могут использоваться как as-of features.
- [ ] Все девять: instrument-scoped full §21.6 passports, independent replay, source/availability gates OPEN.

**Этот этап — аудит GitHub source identity, не новая серверная проверка и не RESEARCH-READY.** Ранее блокированный OpenAI route #18/20 не использовался. Выявленный source mismatch №10 требует source/commit reconciliation; не исправлять его догадкой.


## №10 OFI — следующий проверенный этап GitHub source semantics

CHECKED HERE: исторический `top1_price_transition_ofi_v1.py` и актуальный `rank_ofi_v1.py` сопоставлены по точной формуле bid/ask price transitions. `rank0` совпадает **алгебраически только при одинаковых обработанных событиях и достаточной глубине стакана**; числовое равенство не доказано (Decimal vs float; gating по `levels`). Для более глубоких rank1–rank4 условие same-price identity не соблюдается во всех переходах из-за пересортировки ценовых рангов. `book_top5_membership_v1.py` измеряет decomposition quote-notional, а не строгий OFI. Отчёт: `research/microstructure/passports/ofi_source_semantics_comparison_2026-10-19_v1.json`, commit `76701f94c63474cde6aeec8fb96d24baab68c61f`. Журнал `753ba1e2407056502fb8e4885a7997b2406b49fb`.

- [x] Historical top1 physical formula восстановлена и сравнена с rank0 условно по алгебре
- [ ] Проверить числовую идентичность на одном exact source/event cohort и выявить влияние float/Decimal
- [ ] Создать отдельный price-identity multi-level reader и independent QC только по разрешённому маршруту
- [ ] Зарегистрировать per-instrument §21.6 passport; не объявлять новые ранговые значения multi-level OFI без evidence

Только GitHub/code review. Нет серверного запуска или обхода заблокированного маршрута №18/20.


## №10 OFI — exact-price identity design checkpoint

- [x] Подготовлен отдельный `RESEARCH DESIGN v0` `research/microstructure/passports/ofi_exact_price_identity_design_v0.md` (GitHub `04fd75b350146816b092e41bdda53e783e96e0be`) с Decimal price keys, отделением изменений размера уровня от смены top-K membership, snapshot resets, units and timestamp QC.
- [ ] Строгая итоговая multi-level OFI scalar формула **не выбрана**; пока неоднозначны сравниваемая price cohort, weight/signed aggregation и handling новых/исчезнувших цен. `TERM_AMBIGUOUS=YES; HARD_STOP=YES; OWNER_DECISION_REQUIRED=YES` **для канонического определения/продвижения strict multi-level OFI**.
- [ ] Execution-level проверка на сервере не выполнялась: upstream-blocked route #18/#20 не повторять. Research design ≠ implemented/QC/READY.

Пока решение по strict multi-level formula не принято, разрешено продолжать только документарный source/QC gap review других индикаторов, не придумывая canonical scalar по собственной инициативе.


## Большой проход 2026-10-19: следующие четыре независимые задачи #15/#16/#17/#21

CHECKED HERE: date-parametrized read-only primary implementations committed separately to GitHub: `#15 effort_result_minute_v2.py` `10fe631`; `#16 absorption_prior60_candidate_v2.py` `4ab1848`; `#17 flow_decay_5m_v2.py` `277458d`; `#21 sweep_reclaim_ordered_trades_v2.py` `efd5d50`. Five instruments each, May20 source, **20/20 server runs rc=0**. Commits with observed results: #15 journal `7b3be4c`, #16/#17 journal `15e4d54`, #21 journal `ef20ee3`; indicator index updated. The #16 original output string incorrectly labeled May19 despite May20 path/start; corrected string-only GitHub commit `42cfa21`, no re-execution of revised file, not a change to calculated evidence.

- [x] #15 second physical day 5/5 (minute turnover-denominator proxy, not causal impact).
- [x] #16 second physical day 5/5 (prior60 signed-flow p90 against-price proxy, not proved passive absorption).
- [x] #17 second physical day 5/5 (future reversal as outcome label only, no lookahead as feature).
- [x] #21 second physical day 5/5 (prior60 print-extrema sweeps/reclaims, not orderbook sweep).
- [ ] All four: independent per-minute QC, §21.6 formal measurement passports, as-of source-capture gate and OOS after-fee tests.
- [ ] #16: run repaired output label reader for QA on an approved later visit, **not claimed tested yet**.

Route blocked upstream on #18/#20 was not used; no execution through alternate interpreter or privilege change. No Strategy/DB/deploy/LIVE change. This checkpoint is historical physical coverage and versioned source provenance, not RESEARCH-READY or trading edge.


## Addendum — #14 second day partial and new OpenAI upstream stop

Date-selectable `signed_flow_next_minute_v2.py` was committed `33b71227`. Bybit May20 APT/INJ/DOT/LTC physical 1439 minute pairs each tested, valid counts 1439/1439/1431/1407, outcome partition and missing checks PASS. **ARB NOT EXECUTED**: OpenAI upstream tool-security blocked the fifth identical-shape invocation before a server result. No retry/encoded variation/actor substitution, route `HARD_STOP=YES` and explicit owner/tool-policy decision required; journal `b17a58288389dc5a4b67c2272e8d5183b548e700`. #14 remains future outcome study, NOT actual single-trade impact/after-fees predictive proof. This blocked route is separate from prior #18/#20 restriction.


## 2026-10-19 — #13 and #30 day-two book-depth batch

- [x] #13 versioned second-day reader `exact_price_recovery_v2.py` at commit `6359ada71a2495638bff3aeedf74e9e55174250e`; full May20 5/5 rc=0 and bid/ask episode partition QC PASS. Journal `46afd5053d40e80574f815a2c1fd2985ed06b870`.
- [x] #30 versioned day-selectable `top5_depth_resilience_v2.py` at commit `b88acafc4cfcd4296b2c2299e38274f3a1b737a3`; full May20 APT/DOT/LTC confirmed 3/5 and partition QC. Journal `d9f0671b6450b5e23704728a0048d210bc441e32`.
- [ ] #30 May20 INJ: SentinelX `job_e2201594f884` returned timeout exit_code -1, outcome UNKNOWN (not algorithm FAIL, no retry).
- [ ] #30 May20 ARB: SentinelX `job_22c8f6bba901` returned agent no-response exit_code null, outcome UNKNOWN (not success, no retry). Journal `5089f506fe24fec85ad8032800468a28f3be7e2d`.
- [ ] Both require host/job evidence reconciliation before an authorized re-run. Same-order order resiliency is not proved from top-five aggregate stock. Independent per-episode QC/§21 research readiness still OPEN.

No changes to Strategy, DB, services, LIVE or execution. The prior OpenAI blocked routes #14/#18/#20 were not invoked.


## 2026-10-19 — #12 second-day five-coin chronology bound

- [x] Created date-selectable `book_trade_clock_sample_v2.py` in GitHub `5236c5aaf103286daa66d767a72aa42c54794a6c`. All 5 read-only server samples of **first 500 public trades and first 500 ob200 messages** for May20 completed with rc=0.
- [x] `seq` and `u` nondecreasing **inside sampled 500-message windows** on each coin. Observed sampled `ts−cts`: APT 1–99ms; INJ 1–98ms; DOT 1–123ms; LTC 1–100ms; ARB 1–99ms. Journal `6cc3322aa2b7aed44e9692cca882ad1fc5c8ebde`; indicator tracker `d491603398251f33e6761711dd90dc9c30d614c7`.
- [ ] Cross-feed actual arrival/availability common-clock reconstruction remains absent; sampled `ts−cts` is not publication latency. Cannot promote observed same-price book loss to verified order cancellations.
- [ ] Formal instrument×indicator §21 QC and strict Pulling Rate remains OPEN. Historical timed-out LTC long scan from previous pilot not rerun.

No blocked #14/#18/#20 route used; no unexpected server writes, Strategy/DB/deploy/LIVE mutations.


## 2026-10-19 — next batch #12 / #23 / #25

- [x] #12: May20 independently bounded first 500 trade + book records each symbol 5/5; within-sample seq/u monotonic and ts−cts 1–123 ms. Reader `book_trade_clock_sample_v2.py` `5236c5a`, journal `6cc3322`, tracker `d491603`. NOT physical pulling/cancel differentiation or full-day chronology.
- [x] #23: May20 5/5 bounded shallow OI **path candidate inventories**, all empty; reader `oi_coverage_probe_v2.py` `9952dfd`, journal `31f79a8`. Exact-day OI source still NOT VERIFIED. Absence from known shallow paths is NOT proof OI does not exist in other archives; `NO_DATA` is not 0.
- [ ] #25: May20 date-selectable probe script `spot_perp_source_probe_v2.py` was committed `d6f2cd9`, but its first invocation blocked by OpenAI upstream security; **0/5 executed**, route HARD_STOP; journal `31f79a8`. No bypass/retry.
- [ ] All three: §21 physical source/causal availability/independent QC, strict indicator identity and full passport OPEN. #26 cross-venue last.

No Strategy/DB/runtime/LIVE changes.


## 2026-10-19 — independent QC six trade-minute factors

- [x] #1 RVOL, #2 Trade Rate, #3 Trade Rate Acceleration, #5 Buy/Sell Delta, #6 CVD, #7 Delta Acceleration: on May20 independently reconstructed 1440-minute aggregates from raw gzip per coin using Decimal reader `trade_minute_decimal_independent_qc_v1.py` GitHub `16043cbeaff5df865bf6c4df646863622e0ee1f5`.
- [x] Five coins, 10 independent + primary reads rc=0, agreement for total fills, nonempty minute coverage, baseline-eligible RVOL, minute0/minute60/last-minute values with float/Decimal numeric tolerance. Journal `c0ae6bad09d4aea99e16f0a7af7de684701be4e5`, progress tracker `89af05491210a114c66065cd381dedf900469241`.
- [ ] Full 1440-row per-feature independent QC hash / statistical tolerances and immutable per-instrument §21.6 READY gate still OPEN. Decimal vs binary float cannot claim bitwise equality.
- [ ] Evaluate predictive usefulness only after separate as-of and OOS study, with fee-adjusted outcomes; this QC does not establish alpha.

Prior blocked upstream routes #14/#18/#20/#25, SentinelX alternate interpreter and unknown #30 long jobs were not retried. No trading, DB, service, deploy or LIVE changes.


## 2026-10-19 — second independent QC block, #27/#28

- [x] #27 Inter-Trade Duration and #28 Trade-Size Distribution: five independent full-day Decimal source readers from `trade_distribution_decimal_independent_qc_v1.py` (`da4bed3f4cdcb013786ffb74a5a2481ce95f780b`) and five primary `trade_distribution_v1.py` on May20, all ten rc=0.
- [x] Fill inventory and buy/sell split, positive inter-fill gap median, adjacent same-timestamp proportion, ex-post notional p95/p99 and upper-p99 notional volume share independently agree 5/5. Minor floating-point sub-ms difference in interval medians documented. Journal `4fd8777af938d70097aa73c61073b2afe04408fc`, progress register `cfbba3ab5fc885b31346900ec28e4881e47b4fc2`.
- [ ] Full independent per-record hashes, same-timestamp physical maker/taker order identity, independent captured publication timestamps, §21.6 per-pair READY gates still OPEN.
- [ ] Ex-post p95/p99 cannot serve as minute-by-minute causal thresholds; OOS after-fee edge has not been tested.

The current large batch now contains independent source QC for #1/#2/#3/#5/#6/#7/#27/#28 on 5/5 each (aggregate/sampled-minute or ex-post full-distribution summary, not comprehensive per-minute row QC). No blocked execution paths retried. No Strategy/deploy/runtime/LIVE modifications.


## 2026-10-19 — Full 1440-minute independent QC #1/#2/#3/#5/#6/#7: 4/5 PASS, APT FAIL

- [x] Crosscheck reader `trade_minute_full_row_crosscheck_v1.py` committed `4fee6da163a3f43bb2c9c518b12b84a49b0a7a96`, primary pinned to GitHub `3a71473acb7b50e8821ee218af318521e2b3d9af`. Five separate read-only server runs completed rc=0.
- [x] INJ/DOT/LTC/ARB: all 1440 minute rows × 8 checked data columns per coin agree at precise numeric tolerances (integer counts exact).
- [ ] APT: **FAIL**, 2 mismatched minute counts (minute 1406/1407). Root cause independently verified: primary rounds `timestamp=1779319619.9995` into subsequent minute; three APT Buy fills 23:26:59.9995 UTC totaling 6.175744 USDT, moved from true minute 1406 into minute 1407. Secondary rate/delta/CVD/RVOL effects. Independent diagnostic readers committed `0ca3e999` and `8eae1820`. Journal `1a68bf4098691174708282d535f45da1633ae77d`.
- [ ] Explicit measurement-contract owner/canonical timestamp rule, versioned corrected **research reader**, independent rerun and updated passports for #1/#2/#3/#5/#6/#7. Do not silently patch current algorithm or relabel 4/5 as complete 5/5.
- [ ] §21 as-of/publication-clock, per-instrument passport and OOS after fees still OPEN.

This is a **confirmed data-boundary bug in existing research calculation**, not a new Strategy rule or trading runtime change. No existing blocked route retried. Earlier 5/5 summary QC was too coarse to detect this defect and must not be represented as all-row QC.


## 2026-10-19 — candidate v2 exact timestamp floor replay closes six-factor physical row QC

- [x] New **research-only candidate version**, old v1 preserved: `trade_minute_factors_v2_exact_timestamp_floor.py` GitHub `48fd2e2aedae9e9d49df4f8be346ae01879b9bfe`.
- [x] Independent complete-row comparator `trade_minute_full_row_crosscheck_v2.py` GitHub `0864c79b53a74d8bc916bfc83441f456e4881954` gave **5/5 exact instrument passes**, 1440 rows × 8 tested fields each, zero differences outside explicit float tolerance. Journal `b81280e64c2a7c549f563a9aa1cc49af875c0bad`; tracker `67480e1fb8d344d3c7edb9b66fe19111b5da1769`.
- [x] APT defect cause proven raw source: three fills at 23:26:59.9995 UTC belong minute 1406, previously rounded by v1 to 1407; candidate floor v2 fixes. V1 remains **FAILED** on APT to preserve audit trail.
- [ ] §21.6 per instrument/formula/source-scope READY still OPEN: actual observed/received publication time, formal version selection, per-pair artifact+manifest. 5/5 numeric QC is not owner approval for Strategy or canonical threshold.
- [ ] OOS/favorable-adverse after-fee trading assessments not performed.

No runtime, DB, service, exchange, production Strategy or LIVE mutation. No earlier tool-security route retries.


## 2026-10-19 — #4/#8 independent full-day cohort summary cross-check

- [x] `large_trade_streaks_independent_decimal_qc_v1.py` new independent Decimal reader, immutable GitHub `c437056b60278e0d78c3b01157164ed52cc05269`, ten read-only `cripta` executions of primary+independent full UTC 2026-05-20 source, all rc=0.
- [x] #4: Large Trade Share rolling previous 60-minute *historical* p99 threshold; resulting median/p90 large share and eligible-minute inventory agree 5/5.
- [x] #8: Same-timestamp+side **heuristic** fill grouping; group count, multiple-fill groups, streak episodes, median/p90/max agree 5/5.
- [ ] Full minute-level large-share row equivalence, as-of publication timing and independent source/row hashes; same-timestamp+side grouping does NOT identify parent orders; formal per-pair §21.6 passports and OOS performance after fees remain OPEN.

GitHub journal `01b5e8798596efa37725ef879a7e371c4d0f6ff6`, progress registry `40eeeeb1067c3b7e60a6c162bc2caa84cc59e9fd`. No blocked command retry; trading runtime and LIVE unchanged.


## 2026-10-19 — #9/#11/#29 full-minute independent book QC attempt

- [x] New independent Decimal book reconstruction `book_minute_full_row_independent_qc_v1.py` committed `8dfa76d5a5a78666c382b702a90a897a15443984`, covering bid/ask near-mid depth, queue imbalance and spread across 1440 minute slots, primary pinned `b1862ab`.
- [ ] APT first full-day independent row run `job_cb166e3a8aa1` returned **SentinelX agent non-response, exit_code null** on event notification; on-host outcome UNKNOWN, **0/5 newly verified full-minute row checks**, NO RETRY. Journal checkpoint `9be5812a6ce549a6f8aa5946544c3390226f9ce1`.
- [x] Earlier independent **rounded daily summary QC** 5/5 remains valid and unchanged; cannot promote to all-row equality.
- [ ] Obtain authorized host/job evidence; only then decide on bounded next execution if permitted. Formal §21.6 passports and actual exchange observed/available-after validation still OPEN.

This is an operational access/outcome problem, not a confirmed algorithm mismatch or approval to change service, privileges or runtime. No LIVE change.


## 2026-10-19 — independent #15 summary QC and #16 label-fix proof

- [x] #15 Effort/Result: independent Decimal reader `effort_result_decimal_independent_qc_v1.py` GitHub `af16f88d25b5b6bdd03cd53da0cf3760adb045b1`, source May20 five coins, valid/missing count and independently reconstructed median and p90 match earlier primary 5/5. Journal `633b84e4caad70348b1cfd4b296dbd252e61059f`, registry `860a5fb0a333efa55171bf5c2f7b51898da0134b`. Full 1440-minute *per-row* QC still OPEN.
- [x] #16 revised `absorption_prior60_candidate_v2.py` corrected output date label re-executed on APT with rc=0, window `2026-05-20 full UTC day` and same partition counts 1380/152/15/128/9 as pre-fix primary. Journal `186929a06f5fb74d1ae4ccc1357643d7c0842d9b`.
- [ ] #16 other four label-fix re-executions are NOT claimed; no independent implementation yet.
- [ ] #15 and #16 still require per-pair §21 source and availability contracts, full row/threshold QC, OOS after-fee validation.

Independent book 1440-row job `job_cb166e3a8aa1` remains UNKNOWN after agent nonresponse, with no fresh result notifications observed. No retry. Prior OpenAI upstream-blocked routes also not invoked. No production/runtime mutation.


## 2026-10-19 — #17 independent second-day QC

- [x] Independent `flow_decay_5m_independent_qc_v1.py` GitHub `806f8a9386f515ce51c86b246d1ca4924f5ea73e` independently reconstructs 288 five-minute blocks, current flow ratios and **separate future-label** counts; all five coins rc=0, exact primary parity at summary/group level May20.
- [x] All five count partitions valid (eligible anchors, flow-decay and nondecay, future reversal/continuation/flat), journal `8378ed63698ccf5c1e963f244d60036b35e31d42`, progress register `cadddabe4c2871292d728e5c241336092171fcc0`.
- [ ] Full event/row hashes, nonoverlapping OOS, independent capture/available-after proof and per-pair §21.6 gate remain OPEN. Next 5m price direction is a future outcome label only, not an as-of signal.

Prior #9/#11/#29 APT agent no-response outcome still unknown and not retried. No blocked #14/#18/#20/#25 execution route invoked. No production trading runtime, DB or LIVE mutation.


## 2026-10-19 — next independent QC block #16 + #21 on May20

- [x] #16 Absorption proxy: independent `absorption_prior60_independent_decimal_qc_v1.py`, commit `407a848c576bc8ce3a90ef3a4d457d850fe1faf0`, May20 `5/5` read-only rc=0 and exact unusual/against/aligned/flat/unavailable partition parity. Research journal `fbb306810257fe0585aa2ebec4e5be7969c7a889`; indicator register `720b68f9ae46ef7cdbe644e02d16d3ac34fe08d1`.
- [x] #21 Sweep/Reclaim print candidate: independent `sweep_reclaim_print_independent_qc_v1.py`, commit `30199a0f3058be61e51a14ff781c65166da21641`, May20 `5/5` read-only rc=0, exact prior60 print-extrema event and missing-window totals, research journal `8ee1c7a768ea710b84c62afedd7e875ec251fc90`, indicator register `587587091802a1d3e4c6e9befd67b99e864a74aa`.
- [ ] #16 still flow/price simultaneous minute-close proxy, not demonstrated actual passive order absorption; full per-minute independent hash and as-of capture provenance pending.
- [ ] #21 still print-tape extrema candidate, not proven orderbook sweep; same-timestamp ordering and full per-event independent row QC pending.
- [ ] Both require §21.6 per-pair passport, source coverage/latency and OOS predictive testing after fees. Neither has authority to alter trading rules.

No retry of blocked OpenAI command routes (#14/#18/#20/#25), no retry of unknown SentinelX outcome (#9/#11/#29/#30), no changes to live runtime, services, DB or Strategy.


## 2026-10-19 — #15 second-day all-minute algebraic QC

- [x] `effort_result_minute_allrow_qc_v1.py` committed `3b6d9ea8da2fc0daead3967f7b291ea01d40af31`, five separate read-only May20 runs rc=0, zero mismatches between two independently aggregated and algebraically rearranged Decimal minute formulas on all valid rows: 1440/1440/1436/1423/1440.
- [x] Explicit missing counts APT 0, INJ 0, DOT 4, LTC 17, ARB 0; no-data not zero; journal `c23cd552cc125f8f46cb1e2d0b217b52941a0da1`, indicator register `073d9078ed2653185ee5691517371e6b67e43889`.
- [ ] **Not** direct primary-per-row equivalence: diagnostic script loads but does not invoke primary reader, though independent primary summary agreement was separately checked. Source/observed availability gate, independent raw hash manifest and per-instrument §21.6 READY still OPEN.
- [ ] OOS/performance after fees not tested; no runtime/Strategy/LIVE modification.


## 2026-10-20 — #13 endpoint-expiration independent synthetic QC

- [x] Immutable synthetic no-write audit `depth_recovery_synthetic_endpoint_audit_v1.py`, GitHub `d7c661ffb7b00c12c89803133fbab621c5422241`, ran actor `cripta` rc=0. Cases recovery on time, expiry after another delta, and no-delta-after-reduction all classified by prototype; no primary-raw reread.
- [x] Primary `exact_price_recovery_v2.py` currently ages pending only on a later delta, and carries all remaining to `open` at end even when pending age exceeds 60 seconds. Synthetic old-at-end case classified `censored=1, pending_older_than_window_at_end=1`; code-level finding registered in journal `2b0218456ba8a9d2b0abf81f7989f9899bf545d6`.
- [ ] Determine formal expiry-vs-right-censor measurement contract and source uptime coverage, implement separate version and independently remeasure May20 5-coin raw episode age distribution. Current all-episode integer partitions PASS do NOT establish correct 60s endpoint classification or research-ready gate.
- [ ] Strict parent-order replenishment, independently timestamped availability, §21.6 per-pair passports and OOS after fees remain OPEN.

Earlier blocked action on creating a variant of #15 was not retried. No restart, privileged action, Strategy, services, DB or LIVE mutation.


## 2026-10-20 — #13 real May20 pending-age endpoint source audit

- [x] New read-only independent full-day `depth_recovery_pending_age_audit_v1.py` pinned GitHub `4ff2f9f43e12d672c75a0e8b64dce7411bd2064a` ran 5/5 coins with rc=0; 10 side-level episode partitions and original v2 event inventories agree.
- [x] At May21 00:00 UTC, 705 pending total across five coins. **Two aged >60 seconds**: LTCUSDT Bid 1, ARBUSDT Bid 1. Confirmed source-endpoint behavior defect/censor policy finding, not merely synthetic; journal `cbb0cb9892039776ab202aae9be0b227dcb0fff0`; tracker `bc1b53b388498fdbcf572251e996482afc015cf2`.
- [ ] Formal time expiry versus censor policy and verified feed coverage; study between-message stale ages inside day, not just endpoint; independently verify amended research version before any §21.6 promotion.
- [ ] This is not a net-after-fee trading performance result, and no Strategy/LIVE activation follows.

No blocked invocation repeated, no service/DB/runtime/trading code mutated.


## 2026-10-20 — #13 intra-day pending-age QC attempt

- [x] Independently versioned, nonmutating read-only audit source `depth_recovery_intraday_age_audit_v1.py` pinned GitHub `22d1eb98fd599a6a515015219ba4bd0b25e07b05`. Checks source message time gaps, monotonicity, pending ages immediately before cleanup, end-of-day age and episode partitions.
- [ ] First APT May20 full-day job `job_395c5d11489b` moved to async then notification returned **agent did not respond to exec in 60.0s**, `exit_code=null`, status failed at access layer. Computation **OUTCOME UNKNOWN**, 0/5 new full-day audit results verified; no retry.
- [ ] Before attempting equivalent heavy tasks, reconcile host access and this job status with approved authority and bounded execution, not actor/privilege substitutions. Existing 2 aged-at-end results on LTC and ARB remain valid, but *intra-day additional episode age classification not checked*.
- [ ] Formal §21 expiry-vs-right-censor policy, independent raw coverage and OOS still OPEN. Journal `7af2604164da628ec4d79f69af46bc85146b5665`.


## 2026-10-20 — #13 concrete execution outcome reconciliation

- [x] Read SentinelX full job detail `job_395c5d11489b`: failed at agent response/transport, exit_code=null, started_at=null, no stdout, finished 2026-10-20 07:05:18 UTC. On-host Python execution **NOT VERIFIED**, result **UNKNOWN**, no retry.
- [x] Identified concurrent distinct `job_d031ae9c1823`: registered RUNNING with started_at=null, no confirmed process/ownership; not altered. Do not conflate it with #13 or infer server CPU use.
- [ ] Recover on-host outcome/host health using approved read-only diagnostic path before further heavy replay; no restart or actor substitution without evidence/approval. Journal `c5d9ed105a136087c7d2413312c89ecb0fd4652d`.
- [ ] #13 intraday time-gap, pending-age and timer-contract study remains incomplete; existing boundary evidence (two old Bid pending at end) intact. No Strategy/DB/deployment/LIVE changes.


## 2026-10-20 — third pass: six trade-minute factors, 30 pair-level passport drafts

- [x] `research/microstructure/passports/trade_minute_six_factors_2026-05-20_draft_v1.json` GitHub commit `1967e7260a5d6b9f2226345df315c2b7bf5b8155` prepared for #1/#2/#3/#5/#6/#7 × APT/INJ/DOT/LTC/ARB: **30 instrument-level DRAFT measurement worksheets**, neither full formal passports nor `RESEARCH-READY`.
- [x] Each row identifies exact instrument/source/day/reader version, formula, units, aggregation operator, UTC boundary, reset/missing semantics, independent whole-day 1440-minute numeric QC reference and explicit unverified fields. V1 APT timestamp boundary bug remains distinct from candidate v2 5/5 numerical validation.
- [ ] Per-pair exact raw source SHA256, source/receipt/publication clock and timeliness, full requested period, verified source+run manifests and manual sign/denominator proof remain OPEN; until then `PHYSICAL_NUMERIC_QC_PASS_NOT_RESEARCH_READY`.
- [ ] 30 draft rows are NOT an increment in the number of `RESEARCH-READY` indicators or valid after-fee trading evidence; continue #4/#8/#27/#28 related trade tape families next, then price/book groups.
- [x] No server-side compute or file write, production services/Strategy/LIVE unchanged.


## 2026-10-20 — third pass tape distributions: #4/#8/#27/#28 × 5 draft passport records

- [x] `research/microstructure/passports/trade_tape_4_8_27_28_2026-05-20_draft_v1.json`, GitHub commit `34db5d41ff558105a9086323f584ad11e71b4094`: **20 per-pair measurement drafts** for five Bybit instruments and four tape indicators, source/formula/unit/aggregation/window/lack of observed-time recorded.
- [x] Prior independent Decimal summaries 5/5: #4 prior60 large-fill, #8 timestamp+side heuristic group streaks, #27 positive inter-fill gaps, #28 ex-post fill-size p95/p99. No new server calculation and no READY grant.
- [ ] Need immutable source SHA256 and per-minute/per-fill explicit parity; source arrival/available-after, requested complete window; distinguish ex-post thresholds from causal streaming thresholds; §21.6 gate remains OPEN.
- [x] #1/#2/#3/#5/#6/#7 30 prior draft rows separately retained. Tape group has 50 draft rows total, 0 READY.


## 2026-10-20 — third-pass price/flow inventory #14–22

- [x] `research/microstructure/passports/price_flow_14_22_2026-05-20_draft_v1.json` commit `9ddc2164629f70ea83444aa98b0a7ecf370b5cc6`: 9 indicators × 5 instruments = **45 draft rows**, explicitly **5 not executed** (#14 ARB, #18/#20 LTC/ARB). May20 numeric evidence DOES NOT fill these missing pairs.
- [x] #19/#22 raw SHA256 and independent 1440-minute row hashes recorded per instrument from existing gate audit; source arrival/publication clock and §21 gate remain open.
- [x] #14/#17 next-window return classified FUTURE LABEL; #16 absorption and #21 sweep are mechanism-unverified proxies, #15 numeric proxy is not marginal price impact.
- [ ] This draft is a **scope/status inventory**, not a proof that 45 fully specified passport formulas are verified. Full source/formula/timing/causality QC and owner decisions remain for deeper phase.
- [x] Draft rows across tape and price-flow now 95 (=50 +45), formal per-pair RESEARCH-READY 0. No new server jobs or runtime/LIVE actions.

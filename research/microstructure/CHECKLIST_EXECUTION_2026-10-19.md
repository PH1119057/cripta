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

# Project execution infrastructure

- Read `docs/CURRENT_PROJECT_MAP_RU.md` as the concise current map of the
  repository, production services, PostgreSQL schemas, and component ownership.
  When a change makes that map stale, update its version and commit, and explicitly
  tell the owner to replace the copy used as a ChatGPT project source.

## Server operations and patch installation

- The mandatory operational contract for diagnostics, gates, temporary overlays,
  patch installation, rollback, soak jobs, and field-proof jobs is
  `docs/CODEX_AUTOMATION_AND_PATCH_INSTALL_RU.md`.
- A patch must pass manifest, SHA-256, baseline, dirty-tree, temporary-overlay,
  and project-gate checks before any real checkout file is changed. Keep full
  logs and machine JSON on the server; return only the compact summary by default.
- Operational tooling is not permission to change Entry, Exit, Risk, Execution,
  Mayak feature logic, Dispatcher profiles, Supervisor strategy, or live trading.
  Any such patch remains trading-sensitive and requires the task's explicit scope.

## Авторитетность документации

- Перед использованием документа как задания определить его статус по
  `docs/DOCUMENT_AUTHORITY_RU.md`. Исторический research/runbook/handoff не
  является текущим поручением. При конфликте текущий `AGENTS.md` и канонические
  архитектурные контракты имеют приоритет.

## Глобальная архитектура проекта

- Канонический порядок управления изменениями находится в
  `docs/PROJECT_GOVERNANCE_RU.md`. До реализации исполнитель сверяет GitHub HEAD,
  читает карту и контракты затрагиваемых слоёв, объявляет архитектурное влияние и
  следует порядку: решение владельца -> документ/версия -> архитектурный тест ->
  реализация -> проверки -> GitHub -> deploy -> runtime evidence.

- Канонический глобальный контракт находится в
  `docs/PROJECT_ARCHITECTURE_RU.md`. Перед изменением связей между Маяком,
  Диспетчером стратегий, торговыми стратегиями, Entry, Risk, Execution, Exit,
  Position Supervisor, статистикой или research исполнитель обязан прочитать его
  полностью вместе с архитектурными контрактами затрагиваемых слоёв.
- Канонический контракт непрерывной жизни сигнала находится в
  `docs/SIGNAL_LIFECYCLE_CONTRACT_RU.md`. Любое изменение журналирования сигнала,
  решений, заявок, исполнений, позиции, ручного вмешательства, выхода,
  post-exit-наблюдения, аналитического read-model или будущей карточки обязано
  сохранять `signal_id` как постоянный корневой идентификатор и следовать этому
  документу.
- Разрешённое основное направление данных:
  `внешний рынок -> Маяк -> Диспетчер -> стратегии -> Entry/Risk/Execution/Exit`.
  Position Supervisor отдельно наблюдает фактическую позицию после fill, а
  статистика и research читают причинную историю всех слоёв.
- Аналитический слой не получает торговых прав автоматически. Любое новое влияние
  проходит отдельные стадии `RESEARCH -> SHADOW -> LIVE EQUIVALENCE -> MICRO_LIVE -> LIVE`
  и отдельное решение владельца. Отсутствующие данные не превращаются в ноль,
  нейтральное или безопасное состояние.
- Один общий снимок внешнего рынка (`SharedMarketContext`) является неизменяемым
  наблюдением без торговой команды. Несколько стратегий и экземпляров ботов могут
  причинно прочитать один `market_context_id` и принять разные решения. Запрещено
  превращать общий контекст Маяка/Диспетчера в общий стратегический `CLOSE ALL`;
  глобальным остаётся только отдельный технический аварийный останов биржи.
- Геометрия Entry фиксируется в момент сигнала отдельным неизменяемым handoff и
  передаётся по идентификаторам `signal -> command -> fill -> position`. После fill
  нельзя восстанавливать зоны по текущему рынку или владение по symbol/time.
- Владение каждой реальной позицией задаётся точными `bot_instance_id`,
  `strategy_id/version`, `trade_id`, `position_id`, `signal_id`,
  `entry_command_id` и exchange/client execution IDs. Аналитик и Exit обязаны
  связывать жизненный цикл по этим IDs, а не выбирать ближайшую сделку по монете.
- Если фактический funding не получен, полный `actual_net_pnl` остаётся NULL.
  Допускается отдельно показывать `actual_net_without_funding` с полнотой
  `PARTIAL_NO_FUNDING`; неизвестный funding нельзя молча подставлять как ноль.

## Жёсткая остановка при архитектурном конфликте

- `ARCHITECTURE_CONFLICT_HARD_STOP=YES`. Маяк и Диспетчер являются только
  наблюдательным и рекомендательным контекстом: `MAYAK_TRADING_EFFECT=NONE`,
  `DISPATCHER_TRADING_EFFECT=NONE`. Они не могут создавать Entry, блокировать
  Entry, принудительно закрывать позицию или напрямую менять ордера, стопы,
  позиции и торговые команды.
- Решение о входе принадлежит версии торговой стратегии
  (`STRATEGY_OWNS_ENTRY_DECISION=YES`), а решение об удержании/выходе после fill —
  стратегии сопровождения и утверждённому Exit/Risk-контуру
  (`STRATEGY_OWNS_HOLD_EXIT_DECISION=YES`). После fill владение Entry заканчивается;
  геометрия Entry неизменяема.
- Техническая безопасность отделена от оценки рынка
  (`OPERATIONAL_SAFETY_IS_SEPARATE=YES`). Неизвестное состояние биржи, потеря
  reconciliation, устаревшее обязательное private state, неизвестные qty/fill/
  protection и owner kill остаются fail-closed, но статус Маяка/Диспетчера сам по
  себе таким safety gate не является.
- `EXCHANGE_STATE_IS_LIVE_TRUTH=YES`,
  `POSTGRESQL_IS_CANONICAL_DATA_TRUTH=YES`. Общерыночная оценка не является
  универсальной командой закрыть позиции (`GLOBAL_MARKET_FORCED_EXIT=NO`).
- Изменение концепции допускается только в порядке: решение владельца -> новая
  версия архитектурного документа -> архитектурные тесты -> реализация.
  Необъявленная связь данных или конфликт ТЗ с каноническим контрактом означает
  hard stop без изменения файлов: явно вывести требование, запрет и запросить
  решение владельца.
- Каждый завершённый логический этап проходит максимально сильные применимые
  проверки, получает отдельный commit и push, после чего сверяются local/remote
  HEAD и dirty state. Production deployment обязан соответствовать уже
  опубликованному GitHub commit.


## Обязательный статистический, аудиторский и архивный контракт

- Статистика является частью production-архитектуры, а не необязательным отчётом.
  Любое новое наблюдение, фильтр, профиль среды, торговая политика, исполнение,
  сопровождение позиции или исследовательское сравнение должно оставлять
  причинный и версионированный след, достаточный для последующего независимого
  воспроизведения решения.
- Записывать нужно не только итог сделки, но и то, что система реально знала к
  моменту решения. Future outcome, будущая свеча, будущий snapshot или
  future-derived признак не могут задним числом становиться частью состояния в T.
- Для каждого потенциального торгового сигнала, включая отклонённый, ожидающий
  или не исполненный, статистический след должен позволять восстановить минимум:
  `signal_id`, время, symbol, side, `strategy_id/version`, fingerprint Entry,
  causal feature snapshot, ссылку на последний доступный Mayak snapshot,
  ссылку на применимую оценку Dispatcher с `profile_id/version`, решение,
  причину, `policy_version` и `settings_version`. Статистическая привязка может
  выполняться отдельным причинным коррелятором после события по правилу
  `context_time <= decision_time`; торговый runtime не должен зависеть от
  доступности пассивных слоёв только ради журналирования. Если слой ещё не
  подключён, отсутствие ссылки фиксируется явно; нельзя подставлять фиктивное
  значение.
- Различать два типа контекста: (1) `OBSERVED_CONTEXT` — какой Mayak/Dispatcher
  контекст объективно существовал к моменту события и был связан аналитическим
  коррелятором; (2) `CONSUMED_CONTEXT` — какой конкретный snapshot/assessment
  торговая стратегия действительно прочитала и использовала в live-решении.
  Наличие `OBSERVED_CONTEXT` не является доказательством торгового влияния.
  Для обеих связей хранить link quality/provenance и времена.
- Для каждого реального исполнения хранить фактические exchange truth:
  request/response time, exchange/client order IDs, actual avg fill, actual qty,
  fees, slippage where measurable, funding where applicable, protection IDs и
  причины отказов/ошибок. Теоретическая цена сигнала не заменяет actual fill.
- Для каждой реальной позиции сохранять durable handoff и причинную историю:
  owning Entry/fill, initial stop, protection changes, Supervisor transitions,
  MFE, MAE, time underwater/time in profit, связанные Mayak snapshots и
  Dispatcher assessments. После fill Entry не владеет сопровождением позиции.
- Для каждого выхода сохранять actual exit, exit reason, gross/net PnL, fees,
  funding, slippage, price move %, R, holding time, MFE/MAE и версию Exit/Risk
  policy. Production break-even должен быть economic/fee-aware, когда биржа
  предоставляет необходимые данные.
- Shadow и advisory-слои обязаны журналировать «что бы они рекомендовали» даже
  при `trading_effect=NONE`. Это позволяет после факта считать saved losses,
  lost good trades, destroyed recoveries и дополнительные execution costs до
  выдачи слою каких-либо live-прав.
- Любой профиль Dispatcher является версионированным статистическим объектом.
  Изменение профиля создаёт новую версию. Исторические assessment нельзя
  пересчитывать молча новой версией и выдавать за старые live-решения.
- Для стратегий, где пригодность среды до Entry и после fill различается,
  использовать отдельные профили/контракты `ENTRY_ENVIRONMENT` и
  `HOLD_ENVIRONMENT`; не смешивать критерии нового входа с критериями удержания
  уже открытой позиции.
- Кластерные потери и одновременно открытые коррелированные позиции анализировать
  отдельно от одиночных сделок. Signal replay не считается portfolio backtest:
  портфельная статистика обязана учитывать хронологию, одновременные позиции,
  capital/margin, конфликты, one-position-per-symbol contract (если принят),
  fees/slippage/funding и portfolio crowding.
- Каждое серьёзное исследование и сравнение обязано фиксировать provenance:
  project commit, source-tree fingerprint, DB/schema version, Mayak version,
  Dispatcher version, strategy/profile version, Entry fingerprint, Exit/Risk
  version, symbols, период, dataset fingerprint, development/OOS/holdout label,
  signal/trade count и completeness. Markdown — представление; DB/JSONL/CSV —
  машинная истина.
- Настройки являются частью статистики. Текущий singleton config недостаточен:
  изменения параметров должны иметь append-only history, а каждое решение должно
  ссылаться на действовавшие `settings_version` и `policy_version`.
- При добавлении новой таблицы/журнала production-разработчик обязан одновременно
  обновить compact-export list, `DATABASE_MANIFEST.json`, архивный smoke-test и
  правила восстановления. Нельзя считать статистический слой завершённым, если
  данные есть только в PostgreSQL dump и не отражены в manifest/export contract.
- Контрольный диагностический архив обязан быть воспроизводимым снимком
  установленного commit и содержать без секретов минимум:
  production source, весь актуальный active test tree, `docs/`, `config/`,
  operations/service definitions, PostgreSQL full dump, compact JSONL ключевых
  таблиц, `DATABASE_MANIFEST.json`, git HEAD, branch, dirty/clean,
  source-tree fingerprint, service states и необходимые журналы.
- Полный активный набор тестов данного commit должен попадать в контрольный архив.
  Если на сервере заявлено `N passed`, архив должен содержать достаточный test
  tree и зависимости проекта, чтобы этот authoritative gate можно было
  воспроизвести или должно быть явно написано, почему часть gate не
  воспроизводима из архива. Нельзя подменять полный server gate только
  20–30 targeted smoke tests из ZIP.
- После упаковки обязательна проверка именно распакованного архива:
  manifest/count consistency, JSON/schema validation, imports/py_compile,
  targeted smoke и максимально полный доступный test gate из содержимого ZIP.
  `ARCHIVE_SMOKE=PASS` не означает, что server full gate воспроизведён, если
  полный active test tree не включён.
- Исходные коды, которыми реально пользуются systemd/runtime, должны попадать в
  архив в фактических production paths. Не считать историческую копию,
  research snapshot или одноимённый старый файл доказательством production
  состояния. Архив должен позволять определить единственный активный entrypoint.
- Не включать в архив API keys, passwords, private keys, access tokens,
  `.env`/credential files или иные секреты. Наличие секретов не оправдывается
  требованиями воспроизводимости.
- Если файл установлен на диск, но соответствующий live process не был безопасно
  перезапущен/reloaded, отчёт обязан разделять:
  `INSTALLED ON DISK` и `LOADED/RUNNING`. Нельзя говорить, что новый runtime-код
  действует в live только потому, что файл уже скопирован.
- Нельзя объявлять изменение экономически полезным по числу спасённых стопов.
  Минимальная оценка:
  `saved losses - lost good trades - destroyed recoveries - extra fees/slippage`
  с совместимым sample и явным provenance.
- Результаты live торговли не могут автоматически переписывать Mayak, Dispatcher,
  Entry, Exit или Risk. Контур изменений:
  `STATISTICS -> RESEARCH -> NEW VERSION -> SHADOW -> LIVE EQUIVALENCE ->
  MICRO_LIVE -> LIVE`, с отдельным решением владельца.


## Диспетчер стратегий — независимый интерпретатор рыночной среды

- Канонический архитектурный контракт Диспетчера находится в
  `docs/STRATEGY_DISPATCHER_ARCHITECTURE_RU.md`, словарь — в
  `docs/STRATEGY_DISPATCHER_MARKET_VOCABULARY_RU.md`, этапы внедрения — в
  `docs/STRATEGY_DISPATCHER_RUNBOOK_RU.md`, реализационный контракт D0–D6 — в
  `docs/STRATEGY_DISPATCHER_IMPLEMENTATION_D0_D6_RU.md`, правила создания новых
  профилей среды — в `docs/STRATEGY_DISPATCHER_PROFILE_GUIDE_RU.md`. Перед изменением ядра Диспетчера,
  его профилей, адаптера Маяка или способа использования стратегиями исполнитель
  обязан прочитать эти документы вместе с контрактом Маяка.
- Направление данных одностороннее: `Маяк -> Диспетчер -> потребитель`. Диспетчер
  не меняет Маяк, не читает торговый PnL как рыночный признак и не имеет прямого
  пути к Entry/Exit/Risk/Execution или приватным ордерам.
- Профиль стратегии описывает требуемую внешнюю рыночную среду и версионируется
  отдельно от Entry. Добавление новой стратегии не должно требовать патча Маяка
  или ядра Диспетчера.
- До отдельного подтверждённого этапа D6 Диспетчер работает только пассивно/SHADOW.
  Сам Диспетчер никогда не получает права торговать даже после D6.


## Маяк — независимый слой наблюдения

- Канонический архитектурный контракт Маяка находится в
  `docs/MAYAK_ARCHITECTURE_PRINCIPLES_RU.md`. Перед любым изменением ядра Маяка,
  его источников, снимков, исторического воспроизведения, интерфейса или способа
  использования стратегиями главный исполнитель обязан прочитать этот документ
  полностью. Краткое изложение на портале не заменяет первоисточник.
- Маяк описывает внешний рынок и качество данных независимо от наличия торговых
  стратегий, сигналов и открытых позиций. Торговые сигналы, позиции и их результаты
  не являются входными данными Маяка; сопоставление с ними выполняется только во
  внешнем аналитическом корреляторе.
- У Маяка не должно быть прямого технического пути к торговым мутациям. Он не
  открывает и не закрывает позиции, не размещает и не отменяет ордера, не двигает
  стопы, не меняет размер, плечо или риск и не разрешает либо запрещает вход.
  Стратегии могут читать неизменённый снимок Маяка и принимать собственные решения
  только в своём контуре.
- Снимки Маяка причинны, версионируемы и объяснимы: используются только сведения,
  реально доступные к моменту снимка; время события и получения различаются;
  отсутствующие, устаревшие и непрогретые данные не превращаются в ноль или
  нейтральное подтверждение. Синхронность направления и статистическая корреляция
  хранятся и называются раздельно.
- Фундаментальное изменение этих границ требует новой явно согласованной версии
  архитектурного контракта. Его нельзя вносить скрытым патчем под видом улучшения
  Маяка или торговой стратегии.

- All user-facing research reports, explanations, table headings, state names,
  outcome names, and conclusions must be written in Russian. Do not require the
  user to infer the meaning of English trading/research terminology or literal
  translations with ambiguous established meanings. Define every specialized
  concept in plain Russian by the actual price behavior it represents. When an
  internal English identifier is needed to locate a CSV field, report, or code
  branch, put it only as a secondary note in parentheses after the Russian term;
  never use it as the primary user-facing label.
- Every new research or execution-analysis script must be coin-independent by
  construction. A symbol, input dataset, report root, period, and worker settings
  must be explicit inputs rather than hard-coded coin assumptions. Do not create
  one-off scripts tied to a particular coin. Once a universal script has passed
  its ETH 10% smoke test and shown useful results, reuse it unchanged on new coins
  to strengthen or refute the evidence instead of rewriting it.

- The SSH host alias `robot-admin` points to the project server whose hostname is `robot`.
- Before compute-heavy or long-running work, check whether `robot` is reachable and inspect its currently available CPU, memory, disk space, and load.
- When `robot` is reachable and has sufficient free capacity, prefer running project operations and large data processing jobs there instead of on the local workstation.
- Use the server only for work within the user's requested scope. Keep read-only diagnostics non-mutating and preserve existing server services, data, and running jobs.
- If a planned run needs more resources than are currently free, tell the user the estimated CPU, RAM, disk, and expected duration, and ask them to temporarily scale the server. The user is comfortable scaling it up for large runs and reducing it afterward.
- After a scaled compute run, report completion so the user knows the server can be reduced again. Do not resize or reduce the server yourself unless the user explicitly requests it.
- In its normal low-resource state, treat `robot` as the project's information server: it hosts the portal that exposes research history, executed scripts, statistics, and related project status. Avoid disrupting that portal while scheduling compute work.
- Server storage is split: `/` and `/srv/cripta` are on the small system disk, while the large research datasets are on the separate volume mounted at `/data/cripta`. Always inspect both filesystems before concluding that data or disk capacity is missing.
- The historical raw universe is stored at `/data/cripta/datasets/raw/20260518_20260816`; it contains 24 symbols. Reuse the research source and virtual environments under `/srv/cripta/research_runs` and existing reports under `/srv/cripta/reports` before creating or transferring replacements.
- Heavy symbols depend on the workload. BTCUSDT and ETHUSDT are allowed to remain in research even when rebuilding bars/features from their dense public-trade tapes is expensive: they are core market assets and must not be dropped merely to shorten a run. Schedule them from workload-specific timing history, preferably in a separate heavyweight lane while smaller symbols use the remaining workers.
- The current live observation and trading universe is always the intersection of the requested symbol set and the instruments actually permitted for the active account on the selected exchange. For the current Bybit KZ account, public catalog presence is not sufficient: account-level API permission is authoritative. `1000PEPEUSDT` and `DOGEUSDT` are currently excluded because real order attempts were rejected as unsupported for this account. Do not show them in the live scanner, enable them for automatic entry, or include them in new trading-oriented analysis unless a later account-level audit confirms support. Preserve their historical datasets and completed reports.
- For new compute-heavy historical research, exclude `NEARUSDT` and `XLMUSDT` by default unless the user explicitly requests them. Their completed historical results remain valid and broadly matched the other symbols, but they were the anomalous long-tail pair in `universal_entry_path_replay`: the prior isolated service consumed 6h48m of CPU and NEAR finished last. Preserve their datasets and completed reports. This is a compute-cost rule, not a claim that their market data or trading behavior is defective.
- Exchange eligibility is exchange-specific. If another exchange is added later, maintain a separate verified eligibility list for that exchange and account; never transfer Bybit KZ restrictions or permissions by assumption.
- Choose worker concurrency from the effective CPU capacity observed inside the server, not from the advertised flavor alone. A previous run with eight workers on an effective one-CPU quota made every process slower; two workers completed the queue faster.
- For repeated hypotheses over raw trades, prefer building and reusing a verified intermediate cache (for example normalized trade-day/path metrics or aggregated bars) instead of decompressing and scanning the full BTC/ETH tick archives again for every study.
- For multi-symbol runs, use duration-aware dynamic scheduling rather than a fixed alphabetical batch. Preserve per-symbol checkpoints/results so completed light symbols are not recomputed when a heavyweight task is retried.
- If the final one or two heavyweight symbols would leave most boosted cores idle,
  split each heavy symbol into explicit, non-overlapping signal-index shards and
  run those shards across the freed cores. Record every shard boundary, verify
  that their union covers the source exactly once, then merge them back into one
  ordinary per-symbol result. The script must expose generic slice/shard inputs;
  never hard-code BTC/ETH branches into its calculation logic.

# Servercore API access and astronomical-hour billing discipline

- The service user `robot` has the `member` role on the Servercore project
  `robot`. Its OpenStack RC file is `C:\cripta\rc.sh`; its password is stored as a
  Windows-DPAPI encrypted value at
  `C:\Users\alex\.config\servercore\robot-password.dpapi`. Never print, copy,
  commit, upload, or include the password, decrypted value, or authentication token
  in logs or chat. Authentication has been verified read-only in region `kz-1`.
- The cloud server is named `robot`, is in availability zone `kz-1a`, and can be
  inspected and resized through the Servercore/OpenStack API. A resize, stop,
  start, reboot, or flavor-confirm operation is an external state change: perform
  it only when explicitly requested by the user or by the idle-downscale guard
  defined below.
- The verified normal/base flavor is the private custom configuration
  `0daaa6b9-b6f4-4137-8bca-9dd560dd1061` (1 vCPU, 2048 MB RAM). A private
  1-vCPU/1842-MB flavor also exists, but use 1-vCPU/2048-MB as the automatic base
  until the user explicitly changes the base. Existing private custom flavors are
  visible through the API and may be reused; do not assume that an arbitrary new
  CPU/RAM combination can be created through member-level OpenStack access.
- Treat Servercore compute billing as strict astronomical clock-hour scheduling.
  A job that requires a boosted flavor must be scaled up and started at the
  beginning of an hour, preferably during minutes `00` through `05`. Do not begin
  a newly boosted compute run late in an hour merely because capacity is available.
- Before scaling up, estimate whether the approved queue can productively occupy
  the paid hour. If the primary calculation finishes early, use the remaining paid
  interval for already approved, useful project calculations or verified queued
  symbols. Do not invent unrelated research merely to consume CPU, and do not
  recompute completed outputs.
- At minutes `50` and `55` of every hour, the idle-downscale guard must inspect the
  server when it is above the base flavor. Check active research processes,
  systemd research/compute services, queued or resumable per-symbol jobs, load and
  CPU activity, and the health of the portal. Low CPU alone is not proof of idle:
  a job may be I/O-bound or waiting on a child process.
- If the server is boosted and no approved compute job is active or waiting, the
  guard is authorized to return it to the verified base flavor (1 vCPU, 2048 MB)
  using the documented safe stop/resize/confirm/start sequence, then verify that
  the server and portal return healthy. This is the only standing authorization
  for an automatic infrastructure mutation.
- If activity is ambiguous, a job is still running, the portal cannot be checked,
  authentication fails, or a resize step fails, fail safe: do not stop or resize
  the server. Report the condition instead. Never terminate work merely to meet
  the hour boundary.

# Reusable universal research scripts

## Experimental per-position Exit/Risk card

- Status: research hypothesis only; Entry V1 and live trading behavior are not
  changed by this section.
- Statistics are used to design and validate causal rules, never as the direct
  reason to hold or close an individual live position. Each position must carry
  its own point-in-time card from entry onward.
- The card must update at least: position direction, entry/current price, time in
  trade, MFE/MAE; broad BTC/ETH market direction and breadth when available;
  symbol momentum across available causal timeframes; protective-zone and next
  obstacle-zone identity, distance, freshness, tests and confirmed state; signed
  taker-money flow and whether price progresses with that flow; volume impulse or
  exhaustion; open-interest/basis/crowding context when available; volatility/noise
  allowance; and one current mutually exclusive position state.
- The mutually exclusive states are: `developing`, `movement_confirmed`,
  `exhausting`, `structure_broken`, and `runner`. User-facing reports must render
  these in Russian. State priority is broken > exhausting > confirmed > developing;
  runner is entered only after its causal continuation conditions are satisfied.
- A favorable broad market, intact protective structure, favorable price progress,
  and continuing flow are reasons not to choke a position with a noise-level stop.
  Overbought/oversold alone is a warning, never an exit. Exhaustion requires price
  non-progress plus causal flow/volume or structural confirmation.
- A confirmed protective-zone break against the position is the primary candidate
  for an early causal exit before the mechanical -1% stop. A confirmed obstacle
  break with the position is the primary candidate for preserving a runner beyond
  +1.10%. Obstacle rejection is an intermediate warning, not automatically a full
  exit until the frozen experiment says otherwise.
- Every simulated decision must store the card values, state transition, one
  primary Russian reason code, decision timestamp, first actually available fill
  after the decision, and the counterfactual baseline. No future bars, backfilled
  stops, overlapping labels presented as independent cohorts, or hindsight choice
  of the best exit is permitted.
- Economic evaluation uses a default $100 margin and 10x leverage ($1,000 initial
  notional), with entry/exit fees and explicit slippage. Report dollars per trade,
  total net PnL, profit factor, drawdown, saved loss, forfeited recovery, runner
  contribution, and results by symbol/direction/time segment. Keep underlying
  price percentages, leveraged equity percentages, notional, and margin separate.

### Point-in-time card schema and live decision boundary

- The live card answers only what is happening to this position in the current
  market. Historical hit rates, full-sample averages, and old market regimes may
  validate sensors offline but must never be direct reasons to hold or close a
  live trade.
- Use the exchange-provided break-even price for the actual position, including
  its execution and fee context. Do not replace it in live logic with a fixed
  percentage approximation. Store its source timestamp and recalculate when the
  exchange changes it.
- Для реальной торговли стоп и биржевая цель устанавливаются только после
  подтверждённого исполнения Bybit и рассчитываются от фактической средней цены
  позиции, а не от сигнала или цены неисполненной заявки. Первоначальный стоп —
  -1,00%, биржевой Take Profit — +3,00%. Уровень +1,10% остаётся отдельным
  ожидаемым рубежом стратегии и точкой принятия решения, а не Take Profit.
- Первоначальные механические границы каждой реально исполненной позиции считаются
  только от фактической средней цены исполнения Bybit: стоп `−1,00%`, цель
  `+1,10%` по направлению позиции с округлением лишь до допустимого шага цены.
  Предварительная цена лимитной заявки не является ценой входа. После каждого
  частичного исполнения и изменения средней цены сервер обязан повторно привести
  обе границы к фактическому входу; запрещено самовольно делать риск меньше или
  больше под предлогом безопасности.
- Защита чистой прибыли не имеет фиксированного порога `+0,13%` или `+0,20%`.
  Для каждой фактически открытой позиции сервер обязан отдельно рассчитать цену
  защиты из реально уплаченной комиссии входа, ожидаемой комиссии рыночного
  выхода, шага цены инструмента, запаса на проскальзывание и не менее `0,01 USDT`
  ожидаемой чистой прибыли. Стоп разрешено ставить только после достижения цены,
  на которой рассчитанная защита исполнима. В интерфейсе показываются фактически
  подтверждённые биржей цена стопа и процент от входа. Рыночный разрыв всё равно
  может исполнить стоп хуже расчёта; такой случай должен быть отмечен как
  проскальзывание, а не назван успешной защитой прибыли.
- Плавающий стоп настраивается отдельно возле каждой открытой позиции. По
  умолчанию он выключен, базовый отступ — `0,20%` от текущей цены. Включение
  означает немедленное сопровождение текущей позиции и не переносится на другие
  позиции или будущие входы. Выбор отступа сам по себе не отправляет команду:
  включение и выключение выполняются отдельной подтверждаемой кнопкой. Сервер
  обязан запретить включение, если стартовая граница плавающего стопа окажется
  хуже индивидуально рассчитанной цены защиты чистой прибыли; простого нахождения
  текущей цены выше входа недостаточно.
- Keep raw measurements and three independent conclusions; do not collapse them
  into an unexplained weighted score:
  1. `position_now`: current directional return; distance to exchange break-even,
     +1.10% objective and -1.00% risk boundary; elapsed time; current MFE/MAE;
     giveback from MFE; directional price speed over 30s/1m/3m/5m; new favorable
     high/low progress; time since last favorable progress; current noise/ATR.
  2. `symbol_now`: signed taker-money flow over 30s/1m/3m/5m; trade count and USD
     volume impulse; price progress per unit of directed flow; adverse flow that
     fails to move price (absorption); favorable flow without price progress
     (exhaustion); completed 5m/15m direction; protective and obstacle zone IDs,
     distances, freshness, retests, break/reclaim state; open-interest change over
     5m/15m; price-plus-OI quadrant; basis/mark/index divergence; account crowding
     when point-in-time data exists.
  3. `market_now`: BTC and ETH directional return and taker flow over 1m/5m/15m;
     breadth of the currently enabled trading universe; share of symbols moving
     with the position; market volatility shock; the symbol's move relative to
     BTC/ETH rather than merely its absolute move.
- Add `execution_now`: bid/ask spread, available depth for the intended close,
  estimated slippage, data age, exchange connection health, and whether the
  break-even order is acknowledged. A theoretically correct decision is not
  executable when these fields fail.
- Every field carries `observed_at`, source, and quality (`fresh`, `stale`,
  `missing`, or `partial`). Missing/stale is not neutral and must never be silently
  converted to zero or favorable confirmation.
- Render three plain-Russian conclusions independently:
  `сделка продолжает движение / остановилась / разворачивается`,
  `рынок помогает / нейтрален / мешает`, and
  `структура цела / под угрозой / сломана`. Preserve the measurements that caused
  each conclusion.
- At the exchange break-even decision point, keep the position exposed toward
  +1.10% only when current favorable price progress is real, structure is not
  broken, and the current market is not opposing it. If continuation is absent or
  data needed to prove it is stale/missing, protecting at the exchange break-even
  is the conservative action. This is a point-in-time decision, not a historical
  probability threshold.
- Overbought/oversold, crowding, one adverse candle, or one flow pulse is never a
  standalone exit. Record it as evidence. A reversal conclusion requires
  agreement between price non-progress/reversal and at least one independent
  current mechanism such as directed flow, structure, or broad market direction.

### Experimental continuation rule after +1.10%

- Status: frozen research rule only. Evaluate it causally at the first touch of
  +1.10%; do not change live exits until an untouched-period test is profitable
  after fees and slippage. Mirror the Long rules for Short.
- Continue only when all three mandatory groups hold: (1) the favorable extreme
  was renewed within 5 minutes, giveback from entry-to-MFE is at most 35%, and the
  last completed 5-minute candle is favorable or closes in its favorable outer
  third; (2) the last protective zone is not broken by two completed 1-minute
  closes and the next obstacle leaves at least 0.50% room; (3) 3-5 minute signed
  taker-money flow is favorable, price progresses with it, and current volume is
  at least 60% of the preceding comparable 15-minute average.
- BTC and ETH price direction must never be used as standalone market indicators
  or automatic confirmations. Their meaning depends on the current capital-flow
  regime: money may rotate from BTC into altcoins, from altcoins into BTC, or out
  of both. Use point-in-time BTC dominance (BTC.D), enabled-universe breadth, and
  the traded symbol's relative move versus BTC and the altcoin basket to identify
  the regime. If causal dominance/breadth data is missing, mark the market-regime
  conclusion missing; do not approximate it from BTC or ETH price direction.
- Also require at least two confirmations: the current dominance/breadth regime
  supports the position; the symbol outperforms its appropriate BTC/altcoin
  benchmark in the position direction; over half of the enabled universe moves
  with the position; 3-minute directional speed is at least half of the preceding
  3-minute directional speed.
- Exit at +1.10% when continuation is not proven. One critical failure also exits:
  confirmed protective break; two failed obstacle tests plus no favorable
  progress; favorable flow without price progress; the measured capital-flow
  regime is sharply adverse; or stale/missing mandatory data. Otherwise two warnings exit: no new
  favorable extreme for 10 minutes; giveback above 50%; directional volume down
  over 50%; two completed 5-minute candles with worsening favorable extremes;
  adverse signed flow; adverse majority breadth; next obstacle within 0.20%.
- For an admitted runner, initially protect no worse than the exchange break-even.
  After +2.00%, trail behind the latest causally confirmed protective structure,
  never by an invented fixed percentage. Record measurements, missing-data state,
  decision, first executable fill, and the counterfactual +1.10% close.
- The thresholds above are frozen initial experimental boundaries, not established
  trading facts.

The six tools listed in this section are the ready-to-run, coin-independent
inventory. Before writing any new research script or copying old experiment code,
check this inventory and the existing reports. If one of these tools already
performs the requested operation, use it unchanged on `robot`; changing only the
symbol list, input/output roots, worker count, fraction, policy, or other exposed
CLI arguments is not a reason to write new code. Extend an existing universal tool
only when the requested calculation is genuinely absent, and preserve its generic
per-symbol interface and tests.

Do not recreate coin-specific copies of these tools. They accept either one symbol
or a comma-separated symbol list, as documented below, and are intended to run
independently for every coin. Each coin keeps its own signals and statistics; a
multi-symbol run is scheduling, not an assumption that the coins behave as one
group. Apply the default red/excluded list before launching them.

The canonical server Python and source tree are:

- Python: `/srv/cripta/research_runs/minute_entry_book_v1/.venv/bin/python`
- `PYTHONPATH`: `/srv/cripta/research_runs/universal_entry_v1/source_stage/src`
- Raw trades: `/data/cripta/datasets/raw/20260518_20260816`

Run them from the canonical source tree rather than copying a script elsewhere:

```bash
cd /srv/cripta/research_runs/universal_entry_v1/source_stage
PYTHONPATH=src /srv/cripta/research_runs/minute_entry_book_v1/.venv/bin/python \
  -m bybit_workbench.research.<module> <module arguments>
```

Use `--help` first to confirm the deployed interface. Start with a small supported
`--fraction` or single non-excluded symbol when available, then launch the full
run. Write outputs under a new, clearly named directory in `/srv/cripta/reports`;
never overwrite an existing completed report.

## Required workflow for a new research script

Use this workflow only after confirming that none of the ready universal tools
already performs the requested calculation:

1. Implement the missing operation as a reusable per-symbol script with explicit
   CLI inputs and resumable/per-symbol outputs; do not hard-code a full-universe
   launch into the script.
2. Always use `ETHUSDT` as the test symbol and run the new algorithm on exactly
   10% of ETH's available period or records. Do not choose or guess another test
   symbol. This is the mandatory smoke test; do not start a full run yet.
3. Verify both conditions before proceeding: the process completes without an
   error, and its output contains the information and semantics requested by the
   current task. Merely completing is not sufficient if the result is incomplete,
   incorrectly defined, or unusable.
4. During the 10% ETH smoke test, measure wall time, peak/effective CPU use, peak
   RAM, output size, and processed data volume. Extrapolate these measurements to
   the requested period and symbol count, allowing for different symbol sizes.
   After the smoke test succeeds, stop and report both its result and a practical
   recommendation for `robot` CPU, RAM, disk headroom, worker count, and expected
   duration. Do not launch the full compute-heavy run until the user confirms that
   the server has been scaled.
5. Decide the production symbol universe from the current task together with the
   user. It may be only the actively traded symbols, a selected research subset,
   or all available symbols when broad coverage is needed to detect statistical
   distortion. The 24 downloaded symbols are available data, not the automatic
   default universe.
6. Apply the compute-heavy research exclusion for NEAR/XLM unless the user explicitly overrides it for the
   named task. After capacity is confirmed, use the scheduling policy below and
   per-symbol checkpoints, then report completion so the user can scale the server
   down again.

## Multi-symbol production scheduling

- Treat one worker process per actively running symbol as the normal CPU model,
  but do not automatically request one core for every symbol in the universe.
  Choose a bounded worker pool from the measured 10% ETH test and the known timing
  history. For example, a 20-symbol run may deliberately use about seven workers
  when that gives a better cost/completion balance.
- Estimate symbol weight from existing archive sizes and prior timings. Put the
  known or predicted longest symbols at the front of a single dynamic queue. As
  each worker finishes, immediately assign it the next waiting lighter symbol;
  do not use fixed batches that leave cores idle while one slow batch member runs.
- Keep one symbol per worker unless measurements show that a symbol cannot use a
  core effectively. Never oversubscribe effective server CPU merely because more
  symbols are waiting.
- The objective is to finish the long tail close to the rest of the queue. Server
  capacity is billed in wall-clock hours and cannot be reduced safely while the
  last symbol is still running, so minimize the period in which an expensive
  scaled server has mostly idle cores.
- Preserve completed per-symbol results immediately. A slow or failed symbol must
  not cause already completed symbols to be recomputed, and a remaining outlier
  should be resumable or movable to a smaller follow-up run when safe.

## Dataset materialization and the old 15m/5m pipeline

1. `src/bybit_workbench/research/materialize_entry_dataset.py`
   - Universal per-symbol builder from local `public_trades` archives.
   - Produces `trade_5m.csv`, `trade_15m.csv`, `trade_60m.csv`, `flow_1m.csv`,
     `dataset_manifest.json`, and `DATASET_COMPLETE.json`.
   - Supports `--cache-root`; reuse it instead of decompressing the same trade days.
   - Main arguments: `--raw-root`, `--output-root`, `--symbol`, optional
     `--max-days` and `--cache-root`.

2. `src/bybit_workbench/research/universal_entry_pool.py`
   - Runs the legacy 15m+5m Entry generator for any materialized symbols.
   - Main arguments: `--dataset-root`, `--output-root`, `--symbols`, `--workers`,
     and optional `--fraction` for a smoke test.
   - Produces one `signals.csv` and `summary.json` per symbol plus
     `pool_status.json`.
   - This is a reproducibility tool for the old hypothesis. Do not treat 15m+5m
     as a proven universal entry rule.

3. `src/bybit_workbench/research/universal_entry_pipeline.py`
   - End-to-end wrapper that materializes datasets and runs the legacy Entry pool.
   - Main arguments: `--raw-root`, `--work-root`, `--symbols`, `--workers`, and
     optional `--max-days`.
   - Use for reproducing the old pipeline, not for discovering independent entries.

4. `src/bybit_workbench/research/universal_entry_path_replay.py`
   - Replays E0/E10/E20 entry paths from an existing Entry signal root against raw
     trades for any symbol list.
   - Main arguments: `--raw-root`, `--entry-root`, `--output-root`, `--symbols`,
     `--workers`, `--fraction`, and `--policy` (`eo1_floor` or `no_floor`).
   - Produces `events.csv`, `summary.json`, and `POOL_STATUS.json`.
   - This tool inherits the old Entry signals and its policies. Do not use its
     output as a clean test of a new entry hypothesis.

5. `src/bybit_workbench/research/zone_episode_entry.py`
   - Clean per-symbol audit of exact 15m/5m overlap episodes.
   - One continuous overlap per direction is one episode; Long and Short are
     independent. Tests depths 0/25/50/75/100%, cancels an unfilled order when the
     episode ends, and records the first +1.10% or -1.00% outcome.
   - Main arguments: `--dataset-root`, `--raw-root`, `--output-root`, `--symbol`.
   - Produces `events.csv` and `summary.json`, including discovery/validation splits.
   - Existing first-ten report:
     `/srv/cripta/reports/zone_episode_entry_v1/FIRST10_20260825`.

## Entry discovery independent of 15m/5m zones

6. `src/bybit_workbench/research/independent_entry_search.py`
   - Current universal per-symbol Entry search directly from public trades. It does
     not use the 15m+5m hypothesis.
   - Builds causal 5-minute price, USD volume, and signed taker-flow bars and tests:
     price impulse, money-flow impulse, volume-confirmed range breakout, and VWAP
     mean reversion. Entry is the next 5-minute open. Outcome is first +1.10% or
     -1.00%; a same-bar target/stop collision is conservatively a stop.
   - Uses chronological 60% train / 20% validation / 20% untouched test inside each
     coin. Never select a candidate using its test segment.
   - Main arguments: `--raw-root`, `--output-root`, `--symbol`.
   - Produces per-symbol `events.csv` and `summary.json`.
   - Complete 24-symbol report:
     `/srv/cripta/reports/independent_entry_search_v1/ALL24_20260825`.
   - This version scans raw gzip tapes. Reuse its completed reports. Before testing
   more hypotheses, add/reuse a verified aggregated-bar cache rather than scanning
   BTCUSDT and ETHUSDT again.

## Exact-touch outcome and pre-stop depth

7. `src/bybit_workbench/research/exact_touch_pre_stop_mfe.py`
   - Universal per-symbol replay of exact-touch signals against public trades.
   - Classifies the first `+1.10%` target or `-1.00%` stop within a configurable
     horizon and records the maximum favorable excursion strictly before that
     first event. This directly separates stops that never traded above entry from
     stops that first moved into profit and measures how deeply they did so.
   - Main arguments: `--signals`, `--raw-symbol-dir`, `--output-dir`, `--symbol`,
     `--fraction`, `--horizon-hours`, `--target-pct`, and `--stop-pct`.
   - Produces per-symbol `events.csv` and `summary.json`. It skips historical P31
     candidate rows with an empty `touch_at`; those are not `exact_touch` events.
   - Verified with the required 10% ETH smoke test and the complete old nine-asset
     panel at a 24-hour horizon. Existing complete report:
     `/srv/cripta/reports/exact_touch_pre_stop_mfe_v1/ALL9_24H_20260825`.

8. `src/bybit_workbench/research/exact_touch_zone_structure.py`
   - Universal per-symbol join of exact-touch paths to the frozen P45.1 causal
     zone-event catalog. It replays the +0.10% activation and +1.10%/-1.00% outcome
     over 24 hours, then classifies protective hold/break, obstacle rejection/break,
     early 60-minute structure, and cumulative favorable/adverse balance.
   - Reuse the frozen `independent_zone_touch_outcomes.csv`; do not rebuild or tune
     zones merely to expand from the old 1,063 core signals to exact-touch signals.
   - Main arguments: `--signals`, `--zone-events`, `--raw-symbol-dir`,
     `--output-dir`, `--symbol`, `--fraction`, `--horizon-hours`,
     `--activation-pct`, `--target-pct`, and `--stop-pct`.
   - Verified with the required 10% ETH smoke test and the complete 8,652-event
     old nine-asset panel. Existing complete report:
     `/srv/cripta/reports/exact_touch_zone_structure_v1/ALL9_24H_20260825`.

9. `src/bybit_workbench/research/position_card_economics_v1.py`
   - Universal per-symbol causal experiment for the Exit/Risk position card. It
     compares the mechanical +1.10%/-1.00% baseline with early exit after a
     confirmed protective-zone break while the position is below entry, and with
     runner continuation only when obstacle structure, the symbol's completed
     15-minute bars, and completed BTC/ETH market bars agree at the target.
   - This first version intentionally does not claim historical order-book, open
     interest, or continuous taker-flow coverage. Add those inputs only when their
     point-in-time coverage for the tested signals is verified; never substitute a
     hindsight label or silently treat a missing feature as confirmation.
   - Main arguments are generic paths and parameters: `--signals`,
     `--zone-events`, `--raw-symbol-dir`, `--symbol-bars`, `--btc-bars`,
     `--eth-bars`, `--output-dir`, `--symbol`, `--fraction`, horizon/target/stop,
     margin, leverage, fees, and slippage. It is not tied to any coin.
   - Economic defaults are $100 margin, 10x leverage, $1,000 notional, 0.02% entry
     fee, 0.055% exit fee, and 0.02% slippage. Reports use Russian decision and
     state names and preserve the baseline counterfactual for every deal.
   - The required 10% ETH smoke test passed technically on 2026-08-25. Its first
     result was economically worse than the baseline, so the rule remains an
   experiment and must not be promoted to live behavior without panel evidence.

10. `src/bybit_workbench/research/break_confirmation_matrix_v1.py`
   - Universal per-symbol experiment for confirmed protective-zone breaks. For
     each candidate it tests decision delays of 5/15/30 minutes, loss depths of
     0.10/0.20/0.30/0.50/0.70%, decision-time return into the broken zone, and
     completed 30-minute symbol/BTC/ETH direction filters.
   - It reads the already produced position-card deals and scans raw trades only
     around the disputed break, avoiding a full repeat of all 8,652 paths. Heavy
     candidate sets support non-overlapping `--start-index`/`--end-index` shards.
   - Reports distinguish saved loss, forfeited recovery/profit, and net dollar
     difference from the unchanged baseline. Select candidate rules on an earlier
     time segment and verify unchanged on a later segment; never promote the best
     full-sample row directly.
   - The 2026-08-25 all-nine experiment found no transferable edge: the rule
     selected on the first 70% made no decisions and added $0 on the final 30%.

## Verification and source-of-truth notes

- Tests for the tools above are in:
  - `tests/test_materialize_entry_dataset.py`
  - `tests/test_universal_entry_pool.py`
  - `tests/test_universal_entry_pipeline.py`
  - `tests/test_universal_entry_path_replay.py`
  - `tests/test_zone_episode_entry.py`
  - `tests/test_independent_entry_search.py`
- Run focused checks with `python -m pytest <test-file> -q` and run Ruff/Mypy on a
  changed module before deploying it.
- The old nine-coin full-panel source of truth is
  `reports/cross_asset_validation/ENTRY_V1_FULL_PANEL_20260518_20260816`.
  Its `panel_summary.json` and `panel_pipeline_asset_layers.csv` show 8,660 P30
  candidates and 8,652 `exact_touch` signals. Do not describe 8,652 as the raw
  candidate count.
- Existing reports and successful per-symbol checkpoints must be inspected before
  launching a replacement run. Never recompute completed symbols merely to produce
  a combined table.

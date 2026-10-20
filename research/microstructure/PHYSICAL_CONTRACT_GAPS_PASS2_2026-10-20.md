# CRIPTA — проход 2: физические измерительные контракты, спорные индикаторы

**Статус: RESEARCH INVENTORY / NOT CANON / NOT READY.** Current authority: `PH1119057/cripta:main`; проверен HEAD `5d7ea12f18e4710d8a52bcc7672cc6c9178ce55a`. Основание: `docs/RESEARCH_COMPUTE_RULES_RU.md §21.5–21.7`, `docs/CRIPTA_GLOSSARY_RU.md`, `research/microstructure/FIRST_PASS_MAP_30_INDICATORS_2026-10-20.md`, соответствующие current research readers. **Не меняет каноническое определение ни одного термина, не выбирает за владельца новую формулу.** Для неидентичных proxy сохранять собственные estimator/formula_variant IDs.

## Матрица контрактов / решений

| № | Что реально измерено в pilot | Чего это НЕ доказывает | Следующий уровень проверки / decision gate |
|---|---|---|---|
| 10 Multi-Level OFI | Top-of-book volume-flow и top-five **rank-indexed** stock/decomposition | Price-aware OFI на неизменных ценовых уровнях: rank shifts смешивают ликвидность с перемещением уровней | **OWNER_DECISION_REQUIRED** для выбора strict price-identity multi-level estimand либо сохранения rank-stock как отдельно названного proxy; затем source sequence/price-key replay |
| 12 Pulling Rate | Уменьшение видимого объёма на exact price, чувствительность к trade/book join clock | Реальные отмены, отличённые от executions/cross-feed latency | **CONTRACT_OPEN**: событие source book quantity decrease; trade association отдельно, но не выводить cancel без sequence-equivalence |
| 13 Depth Recovery | Падение видимого размера на exact price и последующее достижение прежнего уровня (один pending на price), event-triggered 60s cleanup | Возврат того же participant/order и непрерывная 60s time-coverage; при day-end найдено 2 stale OPEN (>60s) | **OWNER_DECISION_REQUIRED** для expiration timer относительно observation horizon, snapshot censor, elapsed clock и запрета stale carry; physical intraday gap audit unknown после agent non-response, RAW end evidence 5/5 |
| 14 Price Impact | Signed flow в текущей минуте против следующей минуты ценовой доходности | Marginal execution price impact отдельного ордера; next-minute outcome — FUTURE LABEL | **CONTRACT_OPEN**: label и измеряемую marginal impact разделить; не использовать next-return как as-of feature |
| 16 Absorption | Signed quote-notional flow выше prior60 p90 и противоположное first-to-last price move в минуте | Поглощение пассивными лимитными ордерами / identity participant | **CONTRACT_OPEN**: назвать `against_signed_flow_price_move_proxy`; при фактической absorption требуется доказанная book/tape causal tie |
| 17 Exhaustion | 5m снижение signed aggressor-flow magnitude против предыдущего блока и отдельный следующий 5m outcome | Истощение покупателей/продавцов; будущее направление как торговый feature | **CONTRACT_OPEN**: feature только на закрытии 5m; next5m return — строго OOS label, не исходное измерение |
| 21 Sweep / Reclaim | Crossing high/low предыдущих полных 60 минут по ordered trade prints и возврат в пределах текущей минуты | Sweep resting liquidity / гарантированный порядок fills с одинаковым timestamp | **CONTRACT_OPEN**: trade-print excursion назвать отдельно, доказать tie ordering и book depletion перед именем liquidity sweep |
| 30 Replenishment / Resiliency | Снижение и восстановление совокупного Top-5 quote-USDT stock стакана | Возвращение того же уровня/участника или фактическая resiliency рынка; rank migration | **OWNER_DECISION_REQUIRED** если переименовать proxy в strict same-level/same-order resiliency; May20 INJ/ARB timeout/agent UNKNOWN, no retry |

## Универсальные поля будущего паспорта — сейчас НЕ заполнены

Для **каждой** `instrument_id × indicator_id × formula_version × source/window`: physical event / parser fields / exact identity; sign/direction; numerator+denominator and units; event/observed/available-after UTC clocks; complete lookback and inclusion boundaries; treatment of snapshot/reset/late/missing/timeout/censor; aggregation grain and operator (§21.5); immutable source hashes; raw provenance + source coverage; independent per-event/per-minute QC; full requested period on instrument. Не путать пилот по двум дням с полной верификацией OOS. Пока готовой доказанной пары с полным gate нет.

## Приоритет дальнейшей работы

1. **Завершить обзор без нового deep research:** из source inventory №23 OI/№24 Liq/№25 Spot явный `SOURCE_GAP`, №26 по решению владельца — последним; №14/18/20/30 сохраняют `PARTIAL/BLOCKED/UNKNOWN`. Все позиции имеют truthfully classified status. 
2. Глубина: сперва trade-side прямая физическая определённость (#1–8/#27–28), затем price/flow (#14–22), далее book (#9–13/#29–30), источники OI/Liq/Spot отдельно. Измерительный contract фиксируется до source-specific independent replay, затем §21.6 per-pair passport.
3. Если физическая сущность не доказана — сохранять отдельно названный proxy и его паспорт при наличии достаточного QC, но **не** записывать proxy как доказанную strict mechanism.
4. Отдельные `OWNER_DECISION_REQUIRED` (#10, #13, #30 when strict replacement proposed) блокируют лишь их неразрешённые ветви, а не весь остальной исследовательский порядок. Ни один из них не даёт автоматического изменения канона.
5. Никакого future-target корреляционного ранжирования, торговых thresholds, OOS profitability, Strategy/Execution/LIVE изменений до готовых измерений и перехода к соответствующему согласованному этапу.

## Текущее состояние

Проход 2 — **методическая предварительная инвентаризация DONE**, а не проверенные физические контракты по всем восьми индикаторам. Точные формулы и owners — только по current canonical docs; этот файл не вносит канонические definition edits. Следующая работа — пройти паспортизацию бесспорных показателей и получить необходимые решения по спорным отдельно. Никакого server job в этом проходе не запущено.

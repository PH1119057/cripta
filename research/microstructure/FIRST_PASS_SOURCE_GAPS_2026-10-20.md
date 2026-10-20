# CRIPTA — этап 1: инвентаризация пробелов OI, Liquidations, Spot и заблокированных расчётов

**RESEARCH/EVIDENCE ONLY; НЕ КАНОН.** Основание: проверен `PH1119057/cripta:main` HEAD `1132b074ddecab144b46fb7799c979eb83b8972d` перед публикацией, `research/microstructure/FIRST_PASS_MAP_30_INDICATORS_2026-10-20.md`, `coverage_2026-05-19.md`, и source readers из `research/microstructure/experiments`. Этот документ классифицирует уже накопленные evidence **без нового серверного чтения**.

## №23 OI — источник не установлен

`oi_coverage_probe_v2.py` инспектирует **только** верхний уровень известных корней `/data/cripta/datasets/raw/20260518_20260816/{symbol}` и `/data/cripta/datasets/external`, по именам с `oi`/`open_interest`. Отрицательный shallow result не доказывает отсутствие исторического OI в других источниках/деревьях и не доказывает корректность временной доступности. Статус `SOURCE_GAP: NO_VALIDATED_OI_TIMESERIES`. Отсутствие наблюдения ≠ нулевое изменение OI.

## №24 Liquidations — файл ≠ полный поток

Документ `research/microstructure/coverage_2026-05-19.md` подтверждает непустые файлы **29 из 120** пар `symbol×hour`, по пяти монетам: APT 7/24, INJ 15/24, DOT 1/24, LTC 2/24, ARB 4/24. Отсутствующие **91/120** остаются `UNVERIFIED`, а не нулевыми событиями. Даже 29 присутствующих часов **не подтверждают полноту потока** и внутреннюю timestamp/schema/actor provenance. `liquidation_hour_coverage_v1.py` проверяет наличие/размер файлов, не пропущенные записи. Статус `SOURCE_GAP: FILES_PARTIAL_AND_EVENT_COMPLETENESS_UNVERIFIED`. Ранее полученный SentinelX `command_not_allowed` на альтернативном interpreter не обходить.

## №25 Spot/Perp — source identity open

`spot_perp_source_probe_v2.py` ищет лишь ограниченные семейства названий Spot в двух известных корнях, **не** подтверждает непрерывные значения Spot price на May20. `coverage_2026-05-19.md` отмечает `mayak_dispatcher_market_v1/FEATURE_COVERAGE.json` с первым отмеченным наблюдением Spot component `2026-05-19T02:24:32Z`, однако без доказанного полного ряда для каждой из пяти монет или совпадения часов Perp. Это слабое подтверждение наличия Spot-related компонента, **не готовый источник Spot/Perp basis**. Статус `SOURCE_GAP: EXACT_SPOT_SERIES_UNVERIFIED`. Прежний OpenAI upstream security block на source-probe route не повторять.

## Частичные расчёты и физическая неоднозначность

- #14 May20 4/5: настоящее price-impact-of-order measurement не доказано, next-minute flow-return — только future label; ARB execution route blocked.
- #18/#20 May20 3/5: LTC/ARB execution routes blocked. May19 pilot свидетельства отдельно сохраняются.
- #30 May20 3/5: INJ timeout и ARB agent no-response, исходы UNKNOWN; не повторять без reconciliation.
- #10/#12/#13/#16/#17/#21 остаются `CONTRACT_OPEN`: имеющиеся proxy не автоматически соответствуют строгой физической идентичности названного явления.
- #26 Cross-Venue — строго последним по explicit owner decision, не входит в текущий Bybit compute batch.

## Следующая стадия и stop/go

Первичный обзор **всех 30 позиций классифицирован**, но физический охват не закрыт по #23/#24/#25. После проверки непротиворечивого доступного источника каждой семьи — только ограниченный source-specific read-only smoke, затем полная coverage QC и отдельная формула. Пока источника нет, статус `NO_DATA/UNVERIFIED`, не имитировать нулевые значения. Для связанных с upstream блокировкой маршрутов `HARD_STOP=YES`, `OWNER_DECISION_REQUIRED=YES` для изменения маршрута. Нельзя превращать неудобный выбор формулы в скрытое изменение канона.

## Серверный gate этой сессии

Сначала SentinelX `notifications_check` показал `running=[]` и успешное завершение отдельного ранее запущенного исследования `job_f10dd85958e0` с кодом 0. Следующая **отдельная** команда просмотра `uptime; free -m; ps ...` была **заблокирована upstream OpenAI tool-security** до получения снимка ресурса: `BLOCKED_UPSTREAM_OPENAI`. Это не SentinelX `command_not_allowed`, не Unix `EACCES` и не доказательство занятости/незанятости сервера. Команда не повторялась и не маскировалась другим actor/интерпретатором; тяжёлые расчёты в этом проходе **не запускались**. Следующие новые server-side job требуют нового подтверждённого resource snapshot через одобренный доступ; до этого возможна документарная работа по GitHub.

**Нет изменений** Strategy/Execution/DB/services, runtime/deploy, LIVE. Эта source inventory не является per-pair §21 passport или OOS after-fee исследованием.

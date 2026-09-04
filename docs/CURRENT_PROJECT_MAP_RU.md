# Текущее устройство и архитектурные границы проекта CRIPTA

**Документ:** `CURRENT_PROJECT_MAP_RU.md`
**Версия документа:** 3.0
**Дата:** 2026-09-05
**Статус:** краткая текущая карта; не отдельный архитектурный контракт

## 1. Проверенные checkpoints

```text
CURRENT_VERIFIED_SOURCE_CHECKPOINT=22f1ed07ec34a4713f23d4d196765ded545ec610
SOURCE_CHECKPOINT_VERIFIED_AT=2026-09-05
LAST_VERIFIED_RUNTIME_CHECKPOINT=22f1ed07ec34a4713f23d4d196765ded545ec610
RUNTIME_CHECKPOINT_EVIDENCE_DATE=2026-09-04
PRODUCTION_VERSION=V36.1.11
```

На 2026-09-05 GitHub `PH1119057/cripta:main` и
`/srv/cripta/source_checkout` совпали; tracked server worktree был чист. Runtime
checkpoint выше является последней зафиксированной проверкой, а не утверждением
о непрерывно наблюдаемом состоянии «сейчас».

## 2. Где находится истина

| Объект | Роль |
|---|---|
| GitHub `PH1119057/cripta:main` | общий канонический source checkpoint |
| `/srv/cripta/source_checkout` | канонический server checkout, синхронный GitHub main |
| `/srv/cripta/production`, `/srv/cripta/monitoring`, `/srv/cripta/connectivity`, `/srv/cripta/dashboard` | установленный runtime, не source repository |
| PostgreSQL | каноническая сохранённая operational/analytical truth |
| Bybit | живая истина по позициям, ордерам и исполнениям |
| `/data/cripta` | большие datasets |
| `/srv/cripta-share/incoming` | доставка пакетов, не source of truth |

`C:\cripta`, старые ZIP, чаты и локальные заметки Codex не являются source of
truth.

## 3. Что читать сначала

1. `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`.
2. `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`.
3. `AGENTS.md`, `docs/DOCUMENT_AUTHORITY_RU.md` и
   `docs/PROJECT_GOVERNANCE_RU.md`.
4. Контракты затрагиваемых слоёв.

## 4. Основное направление данных

```text
внешний рынок
    ↓
Mayak — наблюдение, торговый эффект NONE
    ↓
Dispatcher — профильная рекомендация, торговый эффект NONE
    ↓
версионированная стратегия — причинное решение о входе
    ↓
Entry → Execution → Bybit
```

Один `SharedMarketContext` может быть прочитан несколькими стратегиями с разными
решениями. `OBSERVED_CONTEXT != CONSUMED_CONTEXT`: существовавший рядом контекст
не доказывает, что стратегия его прочитала и использовала.

## 5. Ownership до и после fill

До confirmed fill Entry владеет причинным решением и замороженной геометрией
входа. Execution владеет order request, actual fill, exchange/client IDs,
reconciliation и initial server-side protection.

```text
confirmed fill
      ↓
Execution durable handoff
      ↓
Exit / Risk
      ↑
Position Supervisor = observation / context / advisory
```

- Entry ownership заканчивается на confirmed fill.
- Exit владеет утверждёнными protection transitions, economic break-even,
  trailing, close и restart recovery.
- Risk владеет допустимым денежным риском и risk limits.
- Execution исполняет фактические биржевые мутации.
- Position Supervisor не владеет close, stop, trailing, Risk или Entry.
- Стратегия может поставлять утверждённую Exit policy, но не создаёт скрытый
  параллельный runtime ownership.
- Dispatcher HOLD остаётся context/advisory, пока конкретная утверждённая версия
  стратегии явно не объявит его причинным входом.

Связь сохраняется точными ID: `signal_id -> strategy_decision_id ->
entry_command_id -> exchange execution IDs -> position_id -> exit_decision_id`.
Связывать сделку только по symbol и ближайшему времени запрещено.

## 6. Safety

Operational safety отделена от оценки рынка и остаётся fail-closed при:

- неизвестном или stale обязательном private exchange state;
- clock/reconnect/reconciliation failure;
- неизвестных qty, fill или protection;
- невозможности безопасной биржевой мутации;
- emergency owner kill.

Mayak и Dispatcher не могут создавать или блокировать Entry, закрывать позицию,
двигать stop либо менять ордер.

```text
GLOBAL_SAFETY_ARCHITECTURE_STATUS=OWNER_DECISION_REQUIRED
```

Имеет ли отдельный рыночный/fleet-слой право выполнять `BLOCK_NEW_ENTRIES` или
`EMERGENCY_CLOSE` по рыночному контексту, этой ревизией не решено и не
активировано.

## 7. Основные компоненты

| Путь | Назначение |
|---|---|
| `src/bybit_workbench/` | основная библиотека и legacy Workbench-компоненты |
| `production/src/bybit_workbench/strategy_dispatcher/` | канонический Dispatcher |
| `operations/monitoring/` | Mayak, Entry scanner, Supervisor, causal correlator |
| `operations/connectivity/` | private Bybit runtime, reconciliation, execution |
| `operations/dashboard/` | dashboard, API, Archive V2 |
| `operations/devtools/` | state/gate/diff/patch/rollback/soak/field-proof |
| `operations/systemd/` | systemd units |
| `config/strategy_dispatcher/` | Dispatcher profiles и schema |
| `tests/` | unit/contract/architecture/runtime tests |
| `research/`, `research_tools/`, `scripts/` | исследования и operational scripts |
| `docs/` | текущие контракты и исторический provenance |

## 8. Данные, статистика и исследования

PostgreSQL хранит причинный след решений и фактическую историю. Неизвестный
funding не подставляется как ноль: полный `actual_net_pnl` остаётся NULL, а
доступный результат маркируется `PARTIAL_NO_FUNDING`.

Новая логика проходит `RESEARCH -> SHADOW -> LIVE EQUIVALENCE -> MICRO_LIVE ->
LIVE` с отдельными решениями владельца. Исторический research не разрешает live
автоматически. Любая оценка полезности учитывает saved losses, lost good trades,
destroyed recoveries, fees и slippage на совместимом sample.

## 9. Operations

Единственный канонический server ZIP rail:

```text
sudo /usr/local/sbin/cripta-apply-incoming <zip>
```

В корне ZIP обязательны `MANIFEST.json`, `install.sh`, `SHA256SUMS.txt`, без
wrapper directory. Sidecar: `<sha256><two spaces><basename.zip>`.

## 10. Контроль карты

Карту обновляют при изменении ownership, source/runtime paths, production
entrypoints, trading policy, PostgreSQL ownership, safety contract,
Mayak/Dispatcher interfaces, restart/reconciliation или обязательных платформенных
сервисов. После изменения версии владелец должен заменить статическую копию,
используемую как ChatGPT project source: читать текущую карту из GitHub, не из
памяти.

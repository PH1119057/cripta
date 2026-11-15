# P48.2 — обязательный live-аудит Entry Bot и цветовая дистанция

## Базовый контракт

- Entry V1 logic не ретюнится.
- P46, Exit, Risk и реальное Mainnet execution не меняются.
- Auto Entry остаётся `SHADOW · AUTO ENTRY LOCKED`.
- Рабочая десятка и BTC/ETH reference universe не меняются.

## Что добавлено

1. SQLite schema v9: append-only таблица `entry_bot_candidate_events`.
2. История кандидатов пишется автоматически, а не только при CORE SIGNAL.
3. Сохраняются изменения distance-band, shadow pre-limit intent, touch, veto/signal и диагностические post-touch milestones.
4. Для early-failure anatomy фиксируется гонка `+0.10% before -1.00%`, а также `+0.50%`, `+1.00%`, `-1.00%`, `-3.00%` и возврат к Entry / +0.10% после -1% в пределах текущего diagnostic horizon (по умолчанию 360 минут).
5. Таблица Bot Mode визуально подсвечивает существующий armed candidate:
   - красный: дальше watch-band (`>0.60%`);
   - жёлтый: `0.25–0.60%`;
   - зелёный: `<=0.25%`.
   Если armed candidate исчез, Distance становится `—` и цвет снимается.
6. В runtime header показывается накопленное число `Audit N`.
7. Добавлен экспорт истории:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\export_entry_bot_history_windows.ps1
```

CSV по умолчанию: `reports\entry_bot_live_audit\entry_bot_history.csv`.

## Shadow pre-limit

При входе цены в зелёную зону Entry Bot пишет `PRELIMIT_ARM_SHADOW`, но **никакой биржевой ордер не отправляет**.
При выходе из зелёной зоны/смене кандидата пишется `PRELIMIT_CANCEL_SHADOW`; при касании уровня — `PRELIMIT_TOUCH_SHADOW`.

Это сделано специально: настоящий заранее выставленный limit может исполниться в момент touch **до того, как exact-touch flow/OI gate подтвердит CORE SIGNAL**, то есть изменит исследованную Entry V1. Сначала shadow-аудит должен показать, сколько таких hypothetical fills затем получают CORE SIGNAL, а сколько были бы veto.

## Что патч не делает

- не создаёт, не изменяет и не отменяет реальные заявки;
- не меняет stop/TP/position sizing/leverage;
- не меняет Exit/Risk;
- не меняет frozen research/P46;
- не скачивает market data.

# EO1 — Adverse Entry Offset Replay V1

## Вопрос

На тех же 1063 frozen ALL9 Entry проверить не изменение сигнала, а **перенос фактической цены входа против направления сделки**:

- Long: вход по исходному Entry `-0.10%` и `-0.20%`;
- Short: вход по исходному Entry `+0.10%` и `+0.20%`;
- контроль: исходный Entry `0.00%`.

Не все сигналы превращаются в сделки. Скрипт отдельно считает fill/no-fill.

## Primary fill contract

Pending Entry существует максимум 72 часа от исходного сигнала.

Для смещённых входов сделка считается состоявшейся, если новая цена Entry достигнута **раньше**, чем исходный сигнал достиг `+1.10%` в свою сторону. Если исходный `+1.10%` произошёл раньше, это `original_target_before_fill`: движение прошло без нашей сделки.

Дополнительно сохраняется диагностический признак, была ли смещённая цена всё-таки затронута позднее внутри 72h pending-window. Это позволяет отдельно видеть буквально «сколько вообще дошли до этой цены», не смешивая поздний возврат с валидным входом исходной сделки.

## Exit после фактического fill

Все уровни переякориваются к **новой фактической цене входа**, а не к старому сигналу:

- initial stop: `-1.00%` по сделке;
- activation: `+0.10%` по сделке;
- после activation theoretical floor: `+0.10%` по сделке;
- target: `+1.10%` по сделке.

Long/Short считаются зеркально через directional move.

После fill исследовательский горизонт = 72 часа от фактического fill. Для поздних входов это может потребовать данных до 144 часов от исходного сигнала. Если данных не хватает, событие маркируется `data_end`, а не дорисовывается.

## Важно про +0.10 floor

`+0.10%` — теоретический price-floor. Это **не заявление о гарантированном экономическом безубытке** после fees/slippage. Для иллюстративной экономики отдельно применяется резерв полного круга `0.10% notional`; в production должен использоваться фактический fee/slippage/funding/Bybit breakEvenPrice contract.

## Что сохраняется

- число исходных сигналов;
- fills для 0.00 / 0.10 / 0.20;
- fill rate;
- original +1.10 before fill;
- сколько delayed-price вообще было затронуто внутри 72h;
- время до fill;
- сколько после fill достигло +0.10 activation;
- сколько выбито initial -1.00;
- сколько выбито +0.10 floor;
- сколько дошло до +1.10;
- target rate на filled и на все исходные сигналы;
- MFE/MAE;
- ALL9 / DEV2 / HOLDOUT7 / каждая монета;
- три временных fold;
- иллюстративная fixed-stake экономика $100 margin x10.

Machine truth: CSV/JSON. Markdown — только presentation.

## Границы

- Research only.
- Downloads: DISABLED / fail-closed.
- ALL9 only; NEW5 не используется.
- Frozen Entry не меняется.
- Existing Exit/Risk/Execution/live/UI не меняются.
- Это signal replay, не portfolio backtest.

## EO1.2 memory-bounded engine revision

EO1.2 does not change the EO1 V1 research contract, offsets, fill/cancel rules,
stop, activation, floor, target, costs, horizons, frozen signals, or source data.
It replaces only the execution engine used to replay the local public-trade tape.

The previous EO1.1 engine materialized up to 144 hours of raw trades into ordinary
Python lists and then copied those lists into tuples. On high-volume BTC days this
could exhaust process memory even though the underlying TradeDay cache already used
packed doubles.

EO1.2 scans the same packed daily TradeDay objects in chronological order and keeps
only bounded scenario state for 0.00%, 0.10%, and 0.20%. It still observes the full
pending and post-fill horizons needed by the EO1 V1 contract, including MFE/MAE.

Resume compatibility is intentional: EO1.2 keeps the same research run contract so a
partial EO1.1 result may be resumed only when run_contract.json matches exactly and
the partial file contains complete three-row signal blocks. The final provenance
records engine_revision and resumed_event_rows.

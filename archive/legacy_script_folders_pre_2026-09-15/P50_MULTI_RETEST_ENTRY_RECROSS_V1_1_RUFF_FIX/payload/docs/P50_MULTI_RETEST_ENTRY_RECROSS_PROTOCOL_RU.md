# P50 — Multi-Retest / Entry Recross Lifecycle

**Статус:** discovery research only  
**Downloads:** DISABLED  
**Entry V1 / frozen P46 / live Execution / Exit / Risk:** не меняются.

## Цель

P49.3 показал, что первый полный ретест у подтверждённых Entry чаще всего успешно восстанавливается, но существенная часть будущих +1/+2/+3 runners позже снова возвращается глубоко к Entry. Поэтому P50 исследует не один первый ретест, а последовательность повторных возвратов.

Фиксированная выборка: **995 Entry**, которые достигли `+0.10%` раньше исходного structural stop `-1.00%`. 66 early failures в этот цикл не входят. Пять новых активов BNB/AVAX/SUI/AAVE/LTC не читаются и остаются OOS.

## Два параллельных определения

### Peak-retest cycle

После `+0.10%` формируется текущий peak. Ретест начинается при откате не менее `0.05` процентного пункта от peak и заканчивается только:

- causal reclaim этого peak;
- исходным `-1.00%`;
- right censoring.

После reclaim начинается следующий цикл. Так получаем retest #1, #2, #3 и далее.

### Entry-zone visit / recross episode

Отдельный возврат к Entry начинается, когда после уже достигнутого `+0.10%` цена снова становится `<= Entry`. Мелкие пересечения вокруг нуля не дробят эпизод. Он заканчивается только:

- восстановлением `+0.10%`;
- исходным `-1.00%`;
- right censoring.

Новый Entry-zone visit может начаться только после предыдущего восстановления `+0.10%`.

## Что сохраняется для каждого события

Для каждого retest и Entry-zone visit: номер, peak перед возвратом, low относительно Entry, время, длительность, число пересечений Entry внутри эпизода, higher/lower low относительно предыдущего события, causal recovery/reclaim или исходный stop.

## Главные таблицы

- `retest_cycles.csv` — все peak-retest cycles;
- `entry_zone_visits.csv` — все отдельные возвраты к Entry;
- `signal_lifecycle.csv` — сводка по 995 сигналам;
- `retest_by_number.csv` — качество retest #1..#6;
- `entry_recross_by_number.csv` — качество Entry-return #1..#6;
- `entry_recross_low_trend.csv` — higher-low против lower/equal-low;
- `stop_tradeoff_by_action_number.csv` — saved losers vs lost future runners после causal recovery/reclaim #1..#6;
- `runner_required_room_by_action_number.csv` — сколько adverse room реально требовалось будущим +0.5/+1/+2/+3 после каждого causal checkpoint.

## Stop candidates

Заранее фиксированы: `-0.75/-0.60/-0.50/-0.35/-0.25/+0.10%`.

P50 не выбирает production stop. Он только показывает цену каждого варианта: сколько будущих runners он убивает и сколько будущих initial-stop losers закрывает раньше.

## Anti-overfit

P50 не использует flow/OI/orderbook/Mayak и не строит персональное правило. Это чистая price-path anatomy. После discovery допускается выбрать максимум 1–2 простых кандидата и один раз проверить их на пяти зарезервированных OOS-активах.

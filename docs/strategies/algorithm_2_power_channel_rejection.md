# Алгоритм 2 — Power Channel Rejection

Статус: согласованная спецификация v0.1 для реализации и исторической проверки.  
Strategy ID: `user_algorithm_2`.  
Текущая версия реализации: `0.2.0`.  

Алгоритм основан на визуальной идее Support and Resistance Power Channel, которой
пользовался владелец проекта, но намеренно переписан как причинная стратегия без
перерисовки. Исходные исторические ромбики Pine Script не копируются.

## 1. Назначение

Алгоритм ищет не пробой, а подтверждённый выход цены из крайней зоны диапазона:

- Long после касания зоны поддержки и следующей свечи полностью выше зоны;
- Short после касания зоны сопротивления и следующей свечи полностью ниже зоны.

После подтверждения выставляется ограниченная по времени limit-заявка на ретест
границы зоны. V0.1 работает на одном символе и одном таймфрейме за запуск и никогда
не запускается одновременно с Алгоритмом 1 на том же символе.

## 2. Причинный снимок канала

Перед оценкой touch-свечи `t` используются только предыдущие закрытые свечи.

```text
range_high[t] = max(high[t-range_lookback : t])
range_low[t]  = min(low[t-range_lookback : t])
width[t]      = zone_half_width_atr * ATR[t-1]

resistance_top    = range_high + width
resistance_bottom = range_high - width
support_top       = range_low + width
support_bottom    = range_low - width
midline           = (range_high + range_low) / 2
```

Правая граница срезов не включается. ATR рассчитывается по Wilder RMA точно так же,
как в спецификации Алгоритма 1, и на момент открытия touch-свечи уже известен.

Снимок включает все шесть уровней, ATR, границы исходных баров, timestamp, параметры
и версию стратегии. После появления кандидата снимок неизменяем: новые экстремумы и
ATR не имеют права передвигать старый сигнал.

Канал считается пригодным только если:

```text
support_top < resistance_bottom
(range_high - range_low) / ATR[t-1] >= min_center_range_atr
```

## 3. Параметры v0.1

| Параметр | Тип | По умолчанию | Допустимо | Смысл |
| --- | --- | ---: | ---: | --- |
| `range_lookback` | int | 130 | 20..300 | Окно экстремумов |
| `atr_period` | int | 200 | 20..300 | Период Wilder ATR |
| `zone_half_width_atr` | Decimal | 0.5 | 0.1..3 | Полуширина каждой зоны |
| `min_center_range_atr` | Decimal | 3.0 | 1..50 | Запрет слишком узкого/перекрытого диапазона |
| `confirmation_bars` | int | 1 | только 1 в v0.1 | Следующая закрытая свеча подтверждает выход |
| `entry_valid_bars` | int | 2 | 1..10 | Срок жизни limit entry |
| `stop_buffer_atr` | Decimal | 0.1 | 0..3 | Буфер за внешним краем зоны |
| `minimum_reward_risk` | Decimal | 1.0 | 0..20 | Минимальное отношение до frozen midline |
| `trailing_activation_r` | Decimal | 1.0 | 0..20 | Когда разрешить структурный trailing |
| `cooldown_bars` | int | 3 | 0..100 | Пауза после закрытия |
| `requested_leverage` | Decimal | 1 | 1..10 | Запрос, подчинённый Risk Gate |
| `direction_mode` | text | `both` | `long`, `short`, `both` | Разрешённые направления |
| `take_profit_mode` | text | `midline` | `midline`, `none` | Frozen TP либо только trailing/stop |
| `use_candle_power_filter` | bool | false | true/false | Опциональный исследовательский фильтр |
| `minimum_power_share` | Decimal | 0.55 | 0.5..1 | Доля свечей нужного направления |

Неизвестные значения, неверные типы и выход за диапазон блокируют запуск.

Минимальный прогрев:

```text
max(range_lookback + 2, atr_period + 3)
```

До прогрева выдаётся только диагностический `NoOpIntent`.

## 4. Машина состояний

```text
WARMUP -> FLAT -> TOUCH_CANDIDATE -> ENTRY_PENDING -> LONG/SHORT -> COOLDOWN -> FLAT
```

- Одновременно существует не более одного touch-кандидата.
- Одновременное касание обеих зон одной свечой считается неоднозначностью: оба
  кандидата отбрасываются, вход запрещён.
- Кандидат существует ровно до следующей закрытой свечи.
- Состояние и frozen snapshot сериализуются и восстанавливаются.
- Биржевое состояние заявки и позиции имеет приоритет над локальным.

## 5. Touch-кандидат

Стратегия рассматривает кандидата только при flat-позиции, отсутствии pending entry
и завершённом cooldown.

### Long touch

```text
low[t] <= support_top
close[t] >= support_bottom
```

Закрытие ниже `support_bottom` делает движение пробоем/инвалидацией, а не отбоем.
Кандидат Long не создаётся.

### Short touch

```text
high[t] >= resistance_bottom
close[t] <= resistance_top
```

Закрытие выше `resistance_top` инвалидирует Short-кандидат.

## 6. Подтверждение

Проверяется только следующая закрытая свеча относительно frozen snapshot.

### Long confirmation

```text
low[t+1] > frozen.support_top
```

То есть вся подтверждающая свеча находится выше зоны поддержки.

### Short confirmation

```text
high[t+1] < frozen.resistance_bottom
```

Если строгое условие не выполнено, кандидат удаляется. Равенство границе не является
подтверждением. Стратегия не просматривает старые 130 свечей заново и не ставит на
них новые исторические метки.

## 7. Опциональный Candle Power

Фильтр по умолчанию выключен. Если он включён, считаются свечи frozen-окна:

```text
bull = count(close > open)
bear = count(close < open)
```

Doji не входят в знаменатель. Для Long требуется:

```text
bull / (bull + bear) >= minimum_power_share
```

Для Short используется доля `bear`. Это именно количество направленных свечей, а
не объём, order flow или давление покупателей/продавцов. В UI нельзя называть его
«объёмом покупок/продаж».

## 8. Вход и исходная защита

После подтверждения и прохождения фильтров:

### Long

```text
entry_price = frozen.support_top
stop_price  = frozen.support_bottom - stop_buffer_atr * frozen.ATR
take_profit = frozen.midline  # если take_profit_mode=midline
```

### Short

```text
entry_price = frozen.resistance_bottom
stop_price  = frozen.resistance_top + stop_buffer_atr * frozen.ATR
take_profit = frozen.midline  # если take_profit_mode=midline
```

Вход — только `OrderType.LIMIT`. Заявка появляется после закрытия подтверждающей
свечи и не может исполниться раньше следующей. Она отменяется через
`entry_valid_bars`, если ретеста не было.

Если включён midline TP, до отправки проверяется:

```text
reward / initial_risk >= minimum_reward_risk
```

Неправильная сторона TP, перекрытые зоны, нулевой ATR или недостаточное reward/risk
дают `NoOpIntent` с точной причиной.

## 9. Инвалидация pending entry

Помимо истечения срока, создаётся один `CancelEntryIntent`, если до подтверждённого
исполнения закрытая свеча:

- для Long закрылась ниже frozen `support_bottom`;
- для Short закрылась выше frozen `resistance_top`.

При partial fill остаток отменяется, позиция считается открытой и немедленно должна
иметь подтверждённый hard stop на фактическое количество.

## 10. Сопровождение и структурная стоп-полоса

Начальный stop всегда остаётся серверной защитой. После движения минимум на
`trailing_activation_r * initial_R` разрешается структурный trailing.

На каждой закрытой свече рассчитывается новый причинный канал из данных, известных
до этой свечи.

```text
Long candidate  = new_support_bottom - stop_buffer_atr * new_ATR
Long new_stop   = max(current_confirmed_stop, candidate)

Short candidate = new_resistance_top + stop_buffer_atr * new_ATR
Short new_stop  = min(current_confirmed_stop, candidate)
```

Обновление отправляется только после tick-нормализации, если оно улучшает защиту и
остаётся на защитной стороне текущей Mark Price. Stop никогда не отдаляется.

При `take_profit_mode=midline` действует frozen midline исходного сигнала. При
`none` позицию сопровождают hard stop и структурный trailing. Усреднение и добавление
к позиции запрещены.

## 11. Golden examples

Пусть причинный снимок имеет:

```text
range_high = 120
range_low  = 100
ATR        = 4
width      = 2

resistance = [118, 122]
support    = [98, 102]
midline    = 110
```

Touch-свеча имеет low `101` и close `102.5`: создаётся frozen Long-кандидат. Следующая
свеча имеет low `102.2`, то есть строго выше `102`: Long подтверждён.

При `stop_buffer_atr=0.1` создаётся план:

```text
limit entry = 102
stop        = 97.6
target      = 110
risk        = 4.4
reward      = 8
R/R         = 1.818...
```

Если low подтверждающей свечи равен `101.9` или ровно `102`, сигнала нет. Если после
touch новый рыночный экстремум передвинул текущий канал, frozen уровни примера всё
равно остаются неизменными.

## 12. Обязательные counterexamples

- Пересчёт старого кандидата относительно сегодняшнего max/min или ATR.
- Signal/entry по открытой свече.
- Включение touch-свечи в её собственный frozen канал.
- Подтверждение по `close`, когда low Long-свечи всё ещё внутри зоны.
- Обе стороны после одной сверхширокой свечи.
- Вход после закрытия за внешним краем зоны без нового сетапа.
- Limit fill без пересечения цены.
- Повторный intent после рестарта.
- Long stop вниз или Short stop вверх.
- Название Candle Power как объёма или order flow.

## 13. Лицензия и происхождение

Исходный Pine Script ChartPrime опубликован под MPL 2.0. Python-реализация должна
быть написана независимо по этой спецификации: не копировать структуру, комментарии,
имена и графический код Pine. Если в репозиторий всё же переносится покрытый MPL код,
его нужно изолировать и сохранить соответствующее уведомление и условия лицензии.

## 14. Первые BackTest-эксперименты

Сначала проверяется конфигурация по умолчанию отдельно на таймфреймах `60` и `240`.
`use_candle_power_filter=false` остаётся контрольной версией; включённый фильтр —
отдельный эксперимент и отдельный parameters fingerprint.

Алгоритм 1 и Алгоритм 2 получают независимые отчёты и не объединяются в один equity
curve до прохождения собственных golden, stress, walk-forward и out-of-sample тестов.

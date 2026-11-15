# P53 1M Entry Displacement V1.4

Чистая исправляющая версия поверх принятого P53 V1.3.

## Что произошло

V1.3 успешно прошёл exact 1m→5m OHLCV equivalence на UNI, LINK, BTC и ETH. На XRP он остановился на первой 5m свече всего 91-дневного периода: `H/L/C/V` совпали, но `open` отличался на один tick (`1.4128` против frozen `1.4129`). Это boundary-condition, а не основание ослаблять gate.

## Исправление

Первая минута всего evaluation period получает causal seed из `open` первой frozen 5m свечи. Это opening point на начале того же интервала; future `high/low/close/volume` не используются. После этого каждая 1m свеча по-прежнему открывается только по предыдущему causal close.

Exact OHLCV gate остаётся строгим, tolerance нет.

## Cache / resume

Cache version не повышен специально. Уже тяжело рассчитанные V1.3 дни переиспользуются, если первый cached open совпадает с ожидаемым seed. Несовместимый день пересобирается из frozen raw archive. Поэтому BTC/ETH не должны без необходимости пересчитываться заново.

## Установка

Из `C:\cripta`:

```powershell
Expand-Archive `
    -LiteralPath .\P53_1M_ENTRY_DISPLACEMENT_V1_4.zip `
    -DestinationPath . `
    -Force

powershell -ExecutionPolicy Bypass -File `
    .\P53_1M_ENTRY_DISPLACEMENT_V1_4\APPLY_P53_1M_ENTRY_DISPLACEMENT_V1_4.ps1
```

После `Authoritative Windows gate: GREEN`:

```powershell
powershell -ExecutionPolicy Bypass -File `
    .\scripts\research_entry_1m_displacement_p53_windows.ps1
```

## Не меняется

1063 frozen Entry, frozen 15m+5m baseline, 1m параметры, shift sign, 3h availability, P46/NEW5, Entry, Exit, Risk, Execution, live runtime и UI.

# P47N — untouched -1.00% vs +1.10% exact first-touch baseline

## Назначение

Получить ровно одну цифру без ретюнинга Exit: сколько из 1063 frozen Entry ALL9 достигают `+1.10%` раньше исходного structural `-1.00%`, если стоп вообще не двигать до достижения `+1.10%`.

## Контракт

- Entry V1 frozen / unchanged.
- ALL9 = 1063 frozen Entry.
- Anchor = frozen `touch_at`.
- Horizon = 72h.
- First favorable touch `>= +1.10%` => win.
- First adverse touch `<= -1.00%` => loss.
- Никакого `+0.10` activation.
- Никакого `+0.50` activation.
- Никакого `-0.50` floor.
- Никакого retest rule.
- Никакого trailing/runner rule.
- Downloads: **DISABLED / fail-closed**.
- Внутренний пропуск trade-day => ошибка, а не silent repair/download.

## Иллюстративная экономика

По запросу пользователя отчёт также показывает простую арифметику для:

- margin = `$100` на одну сделку;
- leverage = `10x`;
- notional = `$1000`;
- условный резерв на полный круг издержек = `0.10%` notional = `$1`.

Тогда:

- `+1.10%` gross = `+$11`, illustrative net = `+$10`;
- `-1.00%` gross = `-$10`, illustrative net = `-$11`.

Это **не утверждение о фактической комиссии аккаунта Bybit** и не production economic break-even accounting. Точные fees/slippage/funding здесь не моделируются.

## Outputs

- `event_results.csv`
- `scope_summary.csv`
- `sources.csv`
- `summary.json`
- `summary.md`
- компактный ZIP отчёта

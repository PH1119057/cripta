# Exit / Risk Research — следующий цикл после P45

Статус: research-only. Entry V1 frozen.

## P46 — обязательный quality gate

Перед любым Exit V2 повторить P45 на исправленном path engine.

Decision gate:
- проверить 72h archive coverage;
- сравнить P45 vs P46 baseline MFE/MAE;
- отдельно проверить, изменились ли runner-preservation +5/+10;
- если меняются только 72h complete-N и terminal metrics, а runner-preservation стабилен,
  сохранить вывод P45 о вреде слишком раннего BE;
- если runner-preservation заметно меняется, старые P45 проценты считать superseded.

## P47 — Protection V2

На frozen UNI/LINK Entry сравнить три семейства без выбора одного параметра заранее:

1. SIMPLE BE
   - activation threshold -> economic BE.

2. STAGED RISK REDUCTION
   - initial -1R;
   - затем несколько ступеней остаточного риска;
   - economic BE только после статистически подтвержденной стадии.

3. STRUCTURAL / HYBRID
   - stop за сформированным swing / локальной структурой;
   - structural stop может уменьшать риск, но не расширять первоначальный max loss;
   - после runner confirmation разрешить volatility/structure trailing.

Все семейства считать на одном и том же path engine и одном frozen Entry наборе.

## P48 — Runner management

После выбора 2–4 protection candidates:
- structural trailing;
- ATR / realized-volatility trailing;
- MFE giveback;
- hybrid trailing;
- partial TP: 0%, 20%, 25%.

Главная метрика — не только win rate, а сохранение правого хвоста.

## Обязательные метрики

Trade-level:
- expectancy in R;
- median trade;
- profit factor;
- loss tail;
- duration;
- MAE/MFE;
- realized MFE capture.

Strategy-level:
- max drawdown;
- top-10 / top-20 contribution;
- +3/+5/+10/+20 runner preservation;
- share of total P&L coming from rare runners;
- fraction of runner P&L destroyed by protection/partial TP.

## Market regime

P44 regime features пока не превращать в Entry veto и не использовать как жесткий universal
runner filter. Предварительное объединение P44 с P45 показывает нестабильность между UNI и LINK:
некоторые BTC/ETH directional features связаны с runners у UNI, но дают противоположное или
слабое направление у LINK.

Regime лучше исследовать позднее как optional modifier runner-management после того, как базовая
Exit policy работает без него.

## После development sample

Когда останутся 2–4 Exit-кандидата:
1. freeze parameters;
2. без ретюнинга прогнать остальные assets;
3. только затем position sizing in R;
4. portfolio/cluster replay;
5. leverage.

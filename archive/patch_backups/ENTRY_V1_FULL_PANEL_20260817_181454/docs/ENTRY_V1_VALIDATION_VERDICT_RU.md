# ENTRY V1 — POST-LINK VALIDATION VERDICT

Статус: **Entry Research V1 закрыт; переход к Exit/Risk разрешён.**

## Короткий вывод

LINK подтвердил не UNI-level 82.3% headline, а более фундаментальные части концепции:
локальную 15M/5M геометрию, exact touch, устойчивость ~1% structural invalidation и особенно
60m pause после плохой зоны. При этом flow/OI/orderbook показали asset-specific поведение и поэтому
не должны становиться универсальными hard gates.

Самый важный LINK факт для следующей стадии: decisive `+1 vs -1` ratio почти идентичен UNI
(**63.64% vs 63.55%**), но LINK имеет намного больше `neither` в фиксированном 6h горизонте.
Следовательно, Exit/Risk нужно строить не как одинаковый fixed +1% TP для всех монет, а через
MFE/MAE, скорость движения, break-even и volatility-normalized управление.

## Frozen cross-asset table

| Metric | UNI | LINK |
|---|---:|---:|
| P30 candidates | 973 | 988 |
| signals/day | 10.811 | 10.978 |
| baseline +0.5/-1 | 62.80% | 60.02% |
| after 60m pause +0.5/-1 | 69.10% | 67.78% |
| pressure→reversal +0.5/-1 | 66.97% | 58.53% |
| core signals | 113 | 114 |
| core +0.5/-1 headline | 82.30% | 64.91% |
| core +1/-1 headline | 60.18% | 42.98% |
| core +1/-1 decisive | 63.55% | 63.64% |
| OB support-net-positive +0.5/-1 | 100% (N=19) | 50% (N=10) |

## Что считается переносимым baseline V1

- 15M + 5M local zones;
- 1H context only, no veto;
- exact public-trade touch;
- shock/reset causal logic;
- normal adverse breathing roughly 0.3–0.5%;
- research structural invalidation around -1% price;
- 60m post-invalidation pause.

## Что считается asset-sensitive

- pressure→reversal;
- OI-tail interaction;
- dynamic orderbook / absorption states;
- crowding;
- basis.

Эти признаки могут использоваться в Entry Quality score/context, но не как универсальный veto
без собственной проверки на конкретном активе.

## Практическое решение перед live

1. Не дорабатывать Entry до бесконечности.
2. Исследовать Exit/Risk на frozen UNI + LINK datasets.
3. Первую micro-live end-to-end проверку проводить на UNI с малым риском.
4. LINK использовать как второй live validation asset, не ожидая автоматически UNI-level 82.3%.
5. Если денежный результат расходится с replay, возвращаться к Entry только после анализа execution,
   fees/slippage, exit logic и regime drift.

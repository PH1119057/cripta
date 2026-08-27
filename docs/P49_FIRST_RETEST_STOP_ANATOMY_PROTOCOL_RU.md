# P49 — First Retest / Stop Tightening Anatomy

## Назначение

P49 отвечает на один вопрос Exit/Risk, не меняя Entry V1: **после какого causal-события исходный hard SL −1.00% можно подтянуть ближе к Entry, не уничтожив нормальный первый ретест и будущих runner'ов.**

## Зафиксированная терминология

**Первый ретест не начинается в момент Entry.** Сначала цена обязана достичь положительного activation milestone. P49 параллельно считает +0.10%, +0.20%, +0.25% и +0.50%.

Любое adverse-движение до первого достижения выбранного milestone — это начальный шум Entry, а не ретест.

После activation строится direction-normalized Peak #1. Первый ретест начинается, когда цена откатывает от текущего Peak #1 не менее чем на 0.05 процентного пункта. После начала ретеста фиксируется его фактический минимум относительно Entry. Ретест считается **causally confirmed** только после rebound не менее 0.05 процентного пункта от уже наблюдавшегося low. Это разделяет hindsight low и момент, когда live-код уже вправе действовать.

Если до causal rebound цена достигает −1.00%, событие классифицируется как `initial_stop_during_retest`.

## Что измеряется

Для каждого из 1063 frozen Entry V1 сигналов и каждого activation milestone P49 сохраняет Peak #1, retest low относительно Entry, drawdown от Peak #1, пересечение Entry, достижения −0.25/−0.50/−0.75/−1.00 на первом ретесте, causal rebound confirmation, reclaim Peak #1 и дальнейшие +0.50/+1/+2/+3 относительно исходного −1%.

Дополнительно моделируются candidate stops −0.75/−0.50/−0.25/+0.10, но только **после causal confirmation первого ретеста**. Это исследовательская симуляция, не live Exit.

## Границы исследования

- Entry V1: не изменяется.
- P46 confirmatory holdout: не изменяется и не читается.
- Live Execution / Exit / Risk: не изменяются.
- Downloads: **DISABLED**.
- Источник path: только локальные raw public trades из уже существующих P40 dataset directories.
- Старые 9 активов — discovery sample. Новые BNB/AVAX/SUI/AAVE/LTC не анализируются P49 V1 и остаются для последующей OOS-проверки выбранной гипотезы.

## Выходы

- `first_retest_events.csv` — machine truth, Entry × activation.
- `depth_buckets.csv` — исходы по глубине первого ретеста.
- `runner_retest_depth.csv` — распределение глубины первого ретеста будущих +0.5/+1/+2/+3 runner'ов.
- `post_retest_stop_policy.csv` — runner preservation при causal stop tightening после retest confirmation.
- `summary.json` — provenance/config/source hashes.
- `summary.md` — краткое описание методики.

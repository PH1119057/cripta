# Issue #158 — фактический чек-лист завершения исследования 30 индикаторов

**Дата аудита:** 2026-10-30  
**Authority checked:** `PH1119057/cripta:main@db50682d372c382ed77da9635a21d5e3a39da549`  
**Task continuity:** Issue #158 / Draft PR #223 / branch `research/r158-executed-vwap-reader-20261026`.  
**Статус:** RESEARCH EVIDENCE / NOT CANON / NOT LIVE. Canon gate: `docs/RESEARCH_COMPUTE_RULES_RU.md §21.6, §21.8`.

## 1. Новая точка отсчёта

- [x] Все 30 indicator IDs учтены.
- [x] Физически найдены **23/30 indicator slots** с полным first-month `5 instruments × 30 UTC days` materialization и последующим independent QC.
- [x] Эти 23 slots дают **115/150 exact instrument×indicator pairs** с проверенным существующим output/QC evidence.
- [x] Создан derived registry: `R158_FIRSTMONTH_23_INDICATOR_115_PAIR_AUDITED_PASSPORTS_2026-10-30.json`.
- [ ] Formal `RESEARCH-READY`: **0/150**. Наличие расчёта не подменяет causal available-after/source-completeness/physical-identity gate.
- [x] Повторный расчёт 115 существующих пар **ЗАПРЕЩЁН без доказанного defect**.

Полные найденные слоты: **1,2,3,4,5,6,7,8,9,11,12,14,15,17,18,19,20,21,22,27,28,29,30**.

Открытый физический/source хвост: **10,13,16,23,24,25,26**.

## 2. Что считать завершённым физически, но не RESEARCH-READY

### Trade-tape
- [x] №1/2/3/5/6/7 — `trade_minute_cube_20261021_30d_v1`, 5×43200 minutes, prior independent 8-field QC.
- [x] №4/8/27/28 — `microstructure_joint_4f_20261023`, full 30d 5/5 + independent Decimal QC.
- [x] №14 — full 30d future-response **LABEL**, не causal feature.
- [x] №15 — full 30d physical proxy, не marginal order impact.
- [x] №17 — full 30d signed-flow decay proxy, не доказательство exhaustion.
- [x] №18/20 — full 30d observed price-series variants.
- [x] №19/22 — full 30d range/VWAP/POC variant.
- [x] №21 — full 30d print-extrema/reentry proxy, не order-book sweep.

### Order book
- [x] №9/11/29 — full 30d 5/5 last-observed OB200 minute states + independent Decimal QC.
- [x] №12 — full 30d visible exact-price decrease proxy; cancel/fill identity не доказана.
- [x] №30 — full 30d Top5 stock-recovery proxy; same-price/same-order resiliency не доказана.

**Общий blocker исторических архивов:** local receiver/publication `available_after` не записан или не доказан. Это фиксируется как limitation; повторный historical replay его не создаёт.

## 3. Реальный хвост

### №10 Multi-Level OFI
- [x] Canon: exact-price `oldTop5 ∪ newTop5`, bid/ask отдельно, UNKNOWN при недоказанной видимости.
- [x] Reader реализован в PR #223: `R158_OFI_EXACT_PRICE_TOP5_UNION_V1`.
- [x] Synthetic tests покрывают exact price union, source-gap reset, snapshot reset, invisible level → UNKNOWN.
- [ ] Full `5 × 30d` materialization.
- [ ] Independent source/event QC по полному периоду.
- [ ] 5 evidence passports / DESCRIPTIVE_ONLY classification.
- [ ] Formal RESEARCH-READY остаётся false без causal availability.

### №13 Depth Recovery
- [x] Canon требует all visible OB200, Top5 и 6–200 strata, horizons 15/60/300 sec, outcomes R/N/C.
- [x] Day-end unresolved → C; later observation required; snapshot-gap pilot evidence существует.
- [ ] **HARD_STOP для финального reader:** exact equality на horizon boundary, tied-event ordering, доказательство N при source gap/snapshot reset и visibility outside OB200 остаются OPEN в §21.8.
- [ ] OWNER_DECISION_REQUIRED до финальной формулы и 30d materialization.
- [ ] Не наследовать semantics из старого `exact_price_recovery_v2.py`.

### №16 Absorption
- [x] Canon: только observed executed aggressor flow + independently visible book changes + observed price movement.
- [x] Reader реализован в PR #223: `R158_ABSORPTION_COMPONENTS_NATIVE_V1`.
- [x] Output explicitly `ABSORPTION_EVIDENCE_PROXY_COMPONENTS_ONLY`; maker/passive identity не заявляется.
- [ ] Full `5 × 30d` materialization.
- [ ] Independent source/event QC.
- [ ] Common-clock/available-after остаётся UNKNOWN; без него causal as-of join не утверждать.

### №23 OI Change
- [x] Canon forbids substitution of 5m historical snapshots for original observed/published change stream.
- [ ] Locate/prove original first-month change stream.
- [ ] Если такого source нет: per-pair `NO_DATA`, не synthetic delta и не failed algorithm.

### №24 Liquidation Spikes
- [x] PR #223 reader preserves side and missing-hour `UNKNOWN_NOT_ZERO`.
- [x] 1,157/3,600 vendor object-hour receipts; normalized observed rows существуют.
- [ ] Original provider Parquet row/unit parity.
- [ ] Object revision/correction semantics.
- [ ] Price-drop-matched control after original-row parity; old 47/60 vs 15/60 is DESCRIPTIVE_ONLY.

### №25 Spot/Perp Divergence
- [x] PR #223 uses executed base-quantity VWAP and corrected bounded native float-representation QC.
- [x] Perp original first-month archive family exists.
- [ ] Immutable original Spot 5×30d corpus + hashes/manifest under approved acquisition contract.
- [ ] 5×30d synchronized Spot/Perp calculation + independent QC.

### №26 Cross-Venue
- [x] OWNER_DEFERRED_LAST.
- [ ] Не запускать до закрытия Bybit block и отдельного owner decision по Venue/source/timing.

## 4. Server mutation gate для №10/16

**Сейчас PREPARED=NO.** Причина не в Unix data root: task continuity PASS, jobs none, source mirror clean. Текущий SentinelX allowlist не содержит exact fetch/worktree-add rail для PR #223 branch; security policy самостоятельно не расширяется.

Планируемый contract до любого writer:

```text
TASK = Issue #158 first-month #10/#16
EFFECTIVE_ACTOR = cripta
SOURCE_BRANCH = research/r158-executed-vwap-reader-20261026
SOURCE_HEAD = exact remote PR head at run preparation
WORKTREE = /data/cripta/research/worktrees/r158_firstmonth_20261030
READ_PATHS =
  /data/cripta/datasets/raw/20260518_20260816
  exact isolated worktree
WRITE_ROOT =
  /data/cripta/research/runs/r158_firstmonth_10_16_20261030
CREATE_CHILD_PARENTS =
  /data/cripta/research/worktrees
  /data/cripta/research/runs
RENAME =
  only atomic .part -> final inside new run root
DELETE = NONE
DB_ROLE = NONE
SYSTEMD_SANDBOX = none for bounded direct research job
EXPECTED_OWNER/GROUP = cripta:cripta
EXPECTED_DIR_MODE = 02770
EXPECTED_FILE_MODE = 0660
LIVE / DB / SERVICE MUTATION = NONE
```

До `PREPARED=YES`: exact tool rail, actual-actor create/write/rename preflight, target nonexistence, source/raw readability and source commit snapshot must PASS.

## 5. Следующая последовательность без кругов

1. [x] **AUDIT / REUSE:** 115 existing pairs inventoried; no recalculation.
2. [x] **PR READERS:** №10/16 implementation inspected; semantics agree with current §21.8.
3. [ ] **№13 OWNER DECISION:** resolve four remaining physical semantics; then only one final reader version.
4. [ ] **SERVER PREP №10/16:** prepare exact PR worktree/writer rail; no broad privilege changes.
5. [ ] **RUN №10/16:** one bounded first-month job family, 5 instruments, immutable outputs, independent QC.
6. [ ] **SOURCE №24:** original Parquet parity before any new predictive inference.
7. [ ] **SOURCE №25:** immutable Spot corpus before divergence run.
8. [ ] **SOURCE №23:** prove source or close as NO_DATA.
9. [ ] **PASSPORT PROMOTION:** update per-pair gates from evidence; never promote by aggregate count.
10. [ ] **OOS:** only after first-month physical/source set is frozen; freeze first-month thresholds before reading independent period.
11. [ ] **ECONOMICS:** only after OOS, after commissions/slippage/funding assumptions; no automatic Strategy.
12. [ ] **№26 LAST.**

## 6. Stop conditions

- Source absent → `NO_DATA`, not zero and not invented reconstruction.
- Physical term ambiguous → `HARD_STOP=YES / OWNER_DECISION_REQUIRED=YES`.
- Tool-security block → record exact class; no actor/tool encoding bypass.
- Existing run/result found → inspect/reuse; no blind rerun.
- Timeout/job unknown → check job/result first; no duplicate job.
- Research output passing QC → still not Strategy, deploy, LIVE or positive P&L claim.

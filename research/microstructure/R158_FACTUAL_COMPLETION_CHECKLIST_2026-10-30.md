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

Фактически разрешено: **23 computed + №23 terminal NO_DATA**. Открытый физический/source хвост теперь: **10,13,16,24,25,26**.

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
- [x] В PR #223 добавлены bounded materializer `r158_firstmonth_10_16_materialize_v1.py` и отдельный independent full-row checker `r158_firstmonth_10_16_independent_qc_v1.py`; server run ещё НЕ запускался.
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
- [x] №16 входит в тот же bounded materializer; independent checker пересчитывает book/trade components заново и не импортирует primary readers.
- [ ] Full `5 × 30d` materialization.
- [ ] Independent source/event QC.
- [ ] Common-clock/available-after остаётся UNKNOWN; без него causal as-of join не утверждать.

### №23 OI Change
- [x] Canon forbids substitution of 5m historical snapshots for original observed/published change stream.
- [x] Найден historical Bybit V5 5m snapshot corpus с per-file SHA, но он начинается только `2026-05-25T06:00Z` и по физике является snapshot series, а не требуемым native per-change source.
- [x] Найденный recent OI run за `2026-09-18..2026-10-01` сам классифицирован `RECENT_ONLY_EVIDENCE` и не относится к first month.
- [x] Bounded source-identity audit завершён: current code умеет наблюдать OI prospectively через `tickers.*`/current REST с receiver clocks, но immutable first-month capture с этими clocks не найден; future capture не реконструирует historical availability.
- [x] Пять strict first-month пар закрыты как `NO_DATA_FOR_APPROVED_FIRSTMONTH_STRICT_CONTRACT` в `R158_FIRSTMONTH_OI23_STRICT_SOURCE_AUDIT_2026-10-30.json`.
- [x] №23 больше не rerun-ить. Re-open только если физически появится first-month source, удовлетворяющий §21.8, либо владелец отдельно изменит measurement contract/new formula version.

### №24 Liquidation Spikes
- [x] PR #223 reader preserves side and missing-hour `UNKNOWN_NOT_ZERO`.
- [x] 1,157/3,600 vendor object-hour receipts; normalized observed rows существуют.
- [x] Current physical vendor root подтверждён: `/data/cripta/datasets/external/cryptohftdata/bybit_liquidations_v1/{OBJECTS.jsonl,SUMMARY.json,raw/}`.
- [x] Current system Python 3.12 не имеет `pyarrow/zstandard/polars/duckdb/pandas`; это decoder/tooling blocker, НЕ отсутствие исходных объектов.
- [ ] Подготовить отдельный bounded decoder environment/package contract без изменения trading runtime.
- [ ] Original provider Parquet row/unit parity.
- [ ] Object revision/correction semantics.
- [ ] Price-drop-matched control after original-row parity; old 47/60 vs 15/60 is DESCRIPTIVE_ONLY.

### №25 Spot/Perp Divergence
- [x] PR #223 uses executed base-quantity VWAP and corrected bounded native float-representation QC.
- [x] Perp original first-month archive family exists.
- [x] Найден reusable historical Bybit Spot original cache с native schema `id,timestamp,price,volume,side,rpi` и historical `SOURCE_MANIFEST.json`, где для каждого файла сохранены URL/bytes/SHA256/source_symbol.
- [x] First-month physical mask: APT/INJ/DOT/ARB по 23/30, LTC 22/30 = **114/150 файлов физически присутствуют**. У всех пяти отсутствуют May18–May24; дополнительно у LTC отсутствует 2026-06-11. Итого **36 missing**.
- [x] В PR #223 добавлен read-only verifier `r158_spot_legacy_original_audit_v1.py`: current bytes ↔ historical manifest size/SHA, full native CSV schema/timestamp/order/value QC; он не скачивает и не копирует данные.
- [x] Физический объём 114 reusable originals: **27,958,018 bytes (~28 MB)**.
- [x] В PR #223 добавлен bounded `r158_spot_firstmonth_source_materialize_v1.py`: legacy cache read-only; после current QC byte-for-byte копирует 114 originals в новый staging corpus, скачивает только missing, затем atomic whole-root rename; DELETE=NONE.
- [ ] Запустить verifier на всех 114 существующих originals после доступности exact PR source; historical manifest сам по себе не заменяет текущую независимую проверку.
- [ ] После PASS собрать новый immutable first-month corpus; acquisition нужен только для 36 missing originals.
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
6. [ ] **SOURCE №25 REUSE:** independently verify 114 cached originals, then acquire only 36 missing files under approved source-write contract.
7. [ ] **SOURCE №24:** prepare isolated decoder tooling, then original Parquet parity before any new predictive inference.
8. [x] **SOURCE №23:** strict first-month source audit closed as five `NO_DATA` pairs; no snapshot substitution.
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

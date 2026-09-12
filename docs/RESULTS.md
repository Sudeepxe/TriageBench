# Results

Status: **in progress.** Arm 0 and Arm 1 complete; Arms 2-5 pending. Every
number below is read directly from `reports/results/*.json` (via
`scripts/aggregate_results.py` for the data-efficiency table) -- nothing
here is typed in ahead of the corresponding experiment. See
`docs/EXPERIMENT_LOG.md` for the full narrative including failures and
corrections, and `docs/DATASET_CARD.md` for dataset measurements.

## Data-efficiency results (Arm 0 vs. Arm 1)

Source: `reports/results/data_efficiency/summary.json`, generated from
`reports/results/arm0/*.json` and `reports/results/arm1/*.json`.

**Arm 0**: TF-IDF + Logistic Regression, deterministic (1 run per regime).
**Arm 1**: DistilBERT-base-uncased fine-tuned, 3 epochs, MPS, 3 seeds per
regime (mean ± population stdev shown).

### Primary macro-F1 (20 primary classes; Incubator excluded per the frozen long-tail policy)

| Regime | n_train | Arm 0 (test-ID) | Arm 1 (test-ID) | Arm 0 (temporal-shift) | Arm 1 (temporal-shift) |
|---|---:|---:|---:|---:|---:|
| 50/class | 985 | 0.3955 | 0.1668 ± 0.0079 | 0.2954 | 0.0993 ± 0.0086 |
| 200/class | 3,412 | 0.4990 | 0.4179 ± 0.0109 | 0.3649 | 0.2906 ± 0.0012 |
| 1000/class | 15,614 | 0.5747 | 0.5341 ± 0.0040 | 0.3980 | 0.3740 ± 0.0023 |
| full | 72,736 | 0.6295 | 0.6123 ± 0.0203 | 0.4261 | 0.4449 ± 0.0031 |

Full-regime bootstrap 95% CIs for primary macro-F1 (test-ID): Arm 0
[0.593, 0.657], Arm 1 seed 0 [0.600, 0.647] -- **substantially
overlapping, statistically indistinguishable** at full data on this
metric.

### Full micro-F1 (all 21 classes; test-ID)

| Regime | Arm 0 | Arm 1 (mean) |
|---|---:|---:|
| 50/class | 0.4531 | 0.1815 |
| 200/class | 0.5505 | 0.4898 |
| 1000/class | 0.6404 | 0.6880 |
| full | 0.7399 | 0.8097 |

![Data-efficiency curves](../reports/results/plots/data_efficiency_curves.png)
![Micro-F1 vs. data regime](../reports/results/plots/micro_f1_vs_regime.png)

### Findings (no model forced to win)

- **TF-IDF+LogReg leads on in-distribution primary macro-F1 at every
  tested regime**, though the gap narrows steadily as training data
  grows (0.229 at 50/class -> 0.017 at full, and the full-regime gap is
  within bootstrap noise).
- **A genuine crossover exists**, but only on two of the four metrics:
  DistilBERT overtakes TF-IDF on full micro-F1 somewhere between the
  1000/class and full regimes, and on temporal-shift primary macro-F1
  at the full regime (0.445 vs. 0.426) -- the encoder generalizes
  slightly better to the held-out recent era once given enough data,
  despite trailing on the in-distribution headline metric.
- **Every arm degrades substantially under temporal shift**: Arm 0 drops
  0.204 (0.630 -> 0.426); Arm 1 drops 0.167 (0.612 -> 0.445) at full
  data. Robustness to temporal drift is a real, shared limitation, not
  specific to either approach.
- **Seed variance for Arm 1 is regime-dependent**, not uniformly small:
  stdev 0.004-0.011 at 50/200/1000/class, but 0.020 at full data (see
  `docs/EXPERIMENT_LOG.md` EXP-005) -- a single-seed full-regime
  estimate would have been noticeably less reliable than at smaller
  regimes.
- **Latency**: Arm 0 (scikit-learn, Apple M5 CPU) p50 0.186ms / p95
  0.288ms, ~22,000 req/s batched. Arm 1 (DistilBERT, Apple M5 MPS) p50
  ~7ms / p95 ~10-64ms (varies by regime's tokenizer warm state),
  ~11,000 req/s batched. See individual result files for exact
  per-regime figures; M5 numbers are not compared against any NVIDIA
  measurement (none exists -- Arm 1 ran entirely on local $0 compute).

## Arm 2/3: small LLM base vs. prompted (paired 500-example subset)

Source: `reports/results/arm2/*.json`, `reports/results/arm3/*.json`.
Model: `mlx-community/Qwen2.5-1.5B-Instruct-4bit` (Apache-2.0 base
license), via `mlx-lm` on Apple M5, $0 cost. Evaluated on a fixed,
uniform-random 500-example subset of each frozen test split (not the
full set -- generation is far slower per example than the
classifier-head arms; see `configs/experiments.yaml` `llm_arms` for the
pre-registered subset methodology). **Lower statistical power than the
full-test-set Arm 0/1 results above.**

| Arm | Split | primary macro-F1 | full micro-F1 | unparseable rate |
|---|---|---:|---:|---:|
| Arm 2 (naive prompt, no class list) | test-ID | 0.1284 | 0.1660 | 62.6% |
| Arm 2 (naive prompt, no class list) | test-shift | 0.2247 | 0.1880 | 68.0% |
| Arm 3 (engineered prompt, full class list) | test-ID | 0.1664 | 0.2040 | 7.4% |
| Arm 3 (engineered prompt, full class list) | test-shift | 0.1996 | 0.2680 | 6.4% |

**Findings**: giving the model the 21-class vocabulary (Arm 3) collapses
the unparseable/hallucinated-output rate from ~62-68% down to ~6-7%, but
only modestly moves primary macro-F1 -- fixing output *format* is not
the same as fixing classification *accuracy*. **Both LLM arms
substantially underperform Arm 0 even at its smallest (50/class) data
regime** (0.396 test-ID) and Arm 1's smallest regime (0.167 test-ID):
on this task, zero-shot prompting with a small (1.5B) model does not
outperform classical ML given even minimal supervision. This is a real,
evidence-based answer to one of the project's central questions, not
assumed in either direction ahead of time.

## Pending

- **Arm 4** (LoRA/QLoRA fine-tune): not yet run.
- **Arm 5** (frontier API reference): marked unavailable -- no API
  credentials configured, by explicit user decision (see
  `docs/EXPERIMENT_LOG.md`).
- **Phase 4** (system/economics) and **Phase 5** (reliability/error
  analysis): not yet run beyond the latency figures already captured
  per-arm above.

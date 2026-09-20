# Limitations

Final for v1.0.0. Dataset and design limitations first, then limitations of the experiments.

## Dataset and design

- **`sample_data.csv` is not representative of Platform.** It contains only
  CDT-product issues. All Platform-specific claims require the full
  Platform extraction (`reports/phase1_platform.json`).
- **Target incorporates post-filing information.** The Component label is
  the current/settled value, not the filing-time value, even though the
  model never sees post-filing text. See `docs/METHODOLOGY.md`.
- **Label instability is not a human-error ceiling.** Measured at 17.81%
  (see `docs/METHODOLOGY.md` and `docs/DATASET_CARD.md`).
- **No cross-product transfer in V1.0.** Findings are scoped to Eclipse
  Platform only and should not be assumed to generalize to other Eclipse
  projects or other issue trackers without further validation.
- **Overwhelmingly terminal-status data (measured, not assumed).** 94.9%
  of Platform issues are RESOLVED/CLOSED/VERIFIED; only 5.1% are still
  open. This dataset does not represent the status mix a live triage
  queue would see, and any deployment-scenario claim should account for
  training on mostly-already-resolved historical issues rather than
  freshly-filed, still-open ones. See `docs/DATASET_CARD.md`.
- **Data cutoff predates the archive's publication date.** Creation times
  span 2001-10-11 to 2022-04-12, but the Zenodo record was published
  2024-11-27 — a ~2.5 year gap. This is not a snapshot current as of
  publication.
- **Class imbalance.** 21 measured Platform components; the top 10 account
  for 88.0% of issues. Only one class (`Incubator`, 15 issues) has fewer
  than 50 examples. A minimum-support policy (threshold 50, 20 primary classes,
  Incubator secondary) was frozen before any model result existed and is
  enforced by `src/triagebench/evaluation/long_tail_policy.py`. The full
  test splits contain only 1 (ID) and 4 (temporal) Incubator examples, so rare-class
  claims for any arm rest on almost nothing.
- **2.05% of issues have a missing/blank Description** (1 row also missing
  Summary). Rows with a blank Description but a valid Summary
  are kept (2,512 rows in the model-ready set); no rows were dropped for missing text or
  Component (`reports/headroom_gate.json`: 0 and 0 of 122,496).

## Experiments

- **Evaluation sets are not identical across arms.** Arms 2, 3 and 4 are scored
  on a fixed random 500-example subset of each test split (generation is slow);
  Arms 0 and 1 are scored on the full splits (15,585 ID and 18,590 temporal
  examples). Subset estimates carry wide intervals (Arm 4 ID 95% CI
  [0.569, 0.705]). No paired significance test was possible because Arms 0/1
  predictions were not stored.
- **Arm 4 full regime has one seed** (a compute-driven, pre-committed limit);
  the 50/200/1000-per-class regimes have three.
- **Arm 4 Incubator behaviour is not measured**: Incubator is absent from both
  subsets. Three primary classes are absent from the ID subset and two from the
  temporal subset.
- **Arm 5 (frontier API) was not evaluated**: no credentials and a $0
  external-compute/API budget. There is no large-model upper bound, and the
  frontier-contamination question the original design raised is untested.
- **Encoder substitution.** The originally planned ModernBERT-base was too slow
  on the local Apple M5 under the $0 constraint and was superseded by
  DistilBERT-base-uncased (`docs/DESIGN_DECISIONS.md`). No ModernBERT result
  exists. The first MPS pilot was recorded as apparently hung, retracted when
  it turned out to have finished, and kept as evidence in
  `reports/results/arm1_mps_pilot_failure/`.
- **Only one small LLM (Qwen2.5-1.5B, 4-bit) and two prompts** were tested for
  Arms 2-4; no prompt search or hyperparameter sweep was run for any arm beyond
  the frozen configurations.
- **Error analysis is Arm 4 only** and covers one seed on a 500-example subset;
  cross-arm disagreement analysis and Arm 0/1 per-class analysis are not
  possible from stored artifacts (`docs/ERROR_ANALYSIS.md`).
- **Timing is from one laptop.** Latency and training time were measured on a
  single Apple M5 MacBook Pro; no server, CUDA or cloud measurement exists.
  Inference memory, energy and load behaviour were not measured. Several
  training wall-clock values are inflated by macOS sleep (`caffeinate -i` does
  not prevent lid-closed sleep; diagnosed with `pmset -g log` in EXP-005 and
  EXP-007), and one Arm 4 full-regime attempt was lost to an unexplained reboot
  and rerun unchanged. Raw values are kept unedited with corrected estimates
  alongside.
- **Arm 4 collapse risk.** One of three seeds at 50 per class collapsed to
  predicting one class; the cause (an unlucky seed at very small data) is
  inferred from the training-loss trajectory and prediction distribution, not
  isolated experimentally.
- **Generalisation.** Eclipse Platform only, one dataset, one time period; the
  conclusions are not claimed to transfer to other trackers or products.

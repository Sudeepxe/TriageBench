# Limitations

Updated as they're discovered. Known so far:

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
  than 50 examples. A long-tail/minimum-support policy will be
  pre-registered before any model result exists, per `docs/DESIGN_DECISIONS.md`.
- **2.05% of issues have a missing/blank Description** (1 row also missing
  Summary). These rows still have a valid Summary-only input; how they're
  handled in training will be documented when the baseline is built.
- Further limitations (frontier-model contamination risk, statistical
  power of the frontier paired subset, stack-trace ablation findings) are
  added here as each later phase measures them.

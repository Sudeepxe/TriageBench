# Limitations

Updated as they're discovered. Known so far:

- **`sample_data.csv` is not representative of Platform.** It contains only
  CDT-product issues. All Platform-specific claims require the full
  Platform extraction (`reports/phase1_platform.json`).
- **Target incorporates post-filing information.** The Component label is
  the current/settled value, not the filing-time value, even though the
  model never sees post-filing text. See `docs/METHODOLOGY.md`.
- **Label instability is not a human-error ceiling.** See
  `docs/METHODOLOGY.md` and `docs/DATASET_CARD.md`.
- **No cross-product transfer in V1.0.** Findings are scoped to Eclipse
  Platform only and should not be assumed to generalize to other Eclipse
  projects or other issue trackers without further validation.
- Further limitations (completeness gaps, extraction-tool status/resolution
  bias, frontier-model contamination risk, statistical power of the
  frontier paired subset) are added here as each phase measures them.

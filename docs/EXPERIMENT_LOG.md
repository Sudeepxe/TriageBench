# Experiment Log

Every major run is recorded here, including failures. Chronological order.

## 2026-09-10 — EXP-000: Dataset pin resolution and sample inspection

- **Purpose**: Resolve the Zenodo DOI ambiguity flagged in prior design
  review; validate CSV parsing assumptions before any large download.
- **Config**: `configs/dataset.yaml`
- **Data**: `sample_data.csv` (2.9MB, upstream Zenodo record 15348468)
- **Hardware**: MacBook Pro, Apple M5 (local only, no GPU needed)
- **Result**: Sample is 100 rows, single-product (CDT only), 0 malformed
  rows, 0 missing Summary/Description/Component, 29% of rows show at least
  one Component change in history. Confirms `escapechar="\\"` parsing is
  required and correct; confirms `History/Activity Log` is valid JSON once
  CSV-unescaped and must not go through the Description-style
  newline-unescape pass (see failure below).
- **Interpretation**: Sample validates parsing logic but cannot answer any
  Platform-specific question (wrong product). Full Platform extraction is
  required next.
- **Failure encountered**: Initial `parse_history()` ran the generic
  `unescape_text_field()` on the JSON history string before `json.loads`,
  corrupting in-JSON escaped quotes (`\"` → `"`) and causing 4/100 rows to
  fail history parsing. Root-caused, fixed, and covered by a regression
  test (`test_parse_history_does_not_corrupt_json_internal_escaped_quotes`).
  After the fix: 0 history parse failures on the sample.
- **Next action**: Extract the Platform-only ZIP member and run
  `inspect_phase1.py --product-filter Platform` for the real measurements.

## 2026-09-10 — EXP-001: Platform archive extraction — Deflate64 discovery

- **Purpose**: Extract only the Platform project CSV from the 17.2GB
  archive without downloading the other 8 projects.
- **Result (structural)**: The archive's central directory (read via HTTP
  range requests, ~17.16GB apparent size, never downloaded in full) shows
  18 entries — one directory + one CSV per project. Platform:
  `Eclipse/P_Platform/Platform_dataset_issues.csv`, 8.06GB compressed,
  15.34GB uncompressed, **compression method 9 (Deflate64)**.
- **Failure**: Python's stdlib `zipfile` raised `NotImplementedError` on
  method 9 — Deflate64 isn't supported. `pip install zipfile-deflate64`
  failed to build from source (zlib compile errors on macOS 26 arm64 with
  system clang).
- **Resolution**: `brew install p7zip` (prebuilt bottle, no compile) gives
  a Deflate64-capable `7z` CLI. Built a sparse local ZIP container (real
  header+data for the Platform entry at its true offset, real central
  directory/EOCD, everything else an unwritten filesystem hole) and handed
  it to `7z x`. See `docs/DESIGN_DECISIONS.md`.
- **Failure (2)**: A dry run on the smaller TPTP entry (~0.39GB compressed)
  hit a transient `504 Gateway Time-out` from Zenodo's file server partway
  through a sustained sequence of 64MB range GETs.
- **Fix**: Added retry-with-backoff to `HTTPRangeFile` (6 attempts,
  exponential backoff) and reduced the per-request chunk size from 64MB to
  12MB to lower the odds of hitting the server's apparent per-request
  timeout.
- **Status**: BLOCKED (external, not a code issue). While re-validating on
  TPTP, request reliability degraded from "occasional transient 504,
  succeeds on retry" to 100% failure on every request regardless of size
  (a 1KB range, a 16MB range, and a plain unranged GET on `README.md` all
  timed out identically at ~30s). Direct test of `https://zenodo.org`
  itself (no range, no auth) also timed out completely, while
  `https://github.com` responded in 0.26s from the same machine at the
  same moment -- ruling out a local network issue. This is a live Zenodo
  outage, not a bug in `HTTPRangeFile` or the sparse-container approach
  (both had already been validated working end-to-end on the smaller
  TPTP entry before the outage began). Full Platform inspection numbers
  land in a follow-up log entry once Zenodo recovers and
  `reports/phase1_platform.json` exists. **NOT MEASURED** until then.

## 2026-09-10 — EXP-002: Zenodo recovery, real Platform extraction and Phase 1 completion

- **Purpose**: Complete the actual Phase 1 gate: extract Platform data,
  verify integrity, run the full inspection, measure real statistics,
  freeze the temporal split boundary.
- **Result (Zenodo)**: Recovered (confirmed independently: HTTP 200 in
  0.25s). Re-ran the Platform extraction at healthy throughput
  (~6-8 MB/s, no retries needed) and it downloaded successfully to 100%
  (8.06GB in 1373s) -- but then **7z failed** with "Can't open as
  archive."
- **Failure (root-caused, not worked around blindly)**: The sparse-file
  reconstruction approach (preserve the target entry at its *original*
  multi-GB absolute offset inside a sparse local file mirroring the
  remote archive's layout) makes p7zip 17.05 fail outright when that
  offset is large. Reproduced independently and precisely: a synthetic
  sparse file with the same valid ZIP64 structure works fine with a
  ~1KB leading gap (warning only) and completely fails with a ~9GB
  leading gap ("Can't open as archive"), isolated from compression
  method, ZIP64 usage, and trailing gaps (both tested separately and
  found fine). This is very likely a 32-bit-scale overflow in p7zip
  17.05's legacy SFX-offset-detection heuristic, never exercised at
  multi-GB scale by its authors.
- **Fix**: Replaced the sparse-preservation design with a *minimal*
  single-entry container: the target entry's local header + data placed
  at local offset 0, followed by a freshly-built (not reused/patched)
  central directory record and ZIP64 EOCD/locator/classic-EOCD. No
  leading gap exists at all, so the buggy code path is never triggered.
  Validated against a real `7z` subprocess (not just Python zipfile) in
  `tests/test_prepare_data_resume.py::test_minimal_container_is_actually_openable_by_real_7z`
  before touching the real data again.
- **Avoided a second ~23-minute re-download**: the already-downloaded
  8.06GB of compressed data from the failed run was still on local disk
  (in the old sparse container). Wrote a one-off local repackaging step
  that read the local header + compressed data from that file (local
  disk I/O only) and a fresh, small `info` object from 4 lightweight
  remote metadata requests, and reassembled it directly into the new
  minimal-container format -- zero bulk re-download.
- **Second failure (found immediately after, same run)**: `inspect_phase1.py`
  crashed on the real Platform CSV with `_csv.Error: field larger than
  field limit (50000000)`. Root-caused (not just raised the limit
  blindly): measured every column's max field size across all 122,496
  rows and found `Attachments` at 406,417,641 bytes in one row (a large
  binary attachment, almost certainly base64-embedded by Issuex).
  `Attachments` is already excluded from model input
  (`POST_HOC_FIELDS`); raised `DEFAULT_CSV_FIELD_SIZE_LIMIT` to 1GB (real
  headroom above the measured max, not an arbitrary round number) and
  added a regression test asserting that headroom explicitly.
- **Integrity verification**: extracted CSV size (15,340,818,646 bytes)
  matches the ZIP's own recorded uncompressed size exactly. `7z` also
  reported no CRC errors.
- **Result (Phase 1 measurements)**: See `docs/DATASET_CARD.md` for full
  numbers. Headline findings: 122,496 rows (99.999% of the stated
  122,497 -- effectively complete), 21 components (88.0% head-10 share),
  17.81% label instability, 94.9% of issues in a terminal status
  (RESOLVED/CLOSED/VERIFIED), data spans 2001-10-11 to 2022-04-12 (a
  ~2.5-year gap before the 2024-11-27 publication date), 458
  exact-duplicate text groups, one 406MB Attachments outlier.
- **Decision**: Temporal split boundary frozen at `2015-01-01T00:00:00Z`
  (84.81% / 15.19%, chosen from the year-level histogram alone, no
  sub-year search performed to chase a closer match to the 85/15
  target). Written to `configs/experiments.yaml` with `frozen: true` and
  guarded by a test. Per instruction, **no advance to Phase 2** --
  stopping here to report findings.
- **Next action**: Await review of Phase 1 findings before any headroom
  gate / Arm 0 work begins.

## 2026-09-10 — DECISION-001: Long-tail policy frozen (pre-registered, no model results used)

- **Purpose**: Phase 1 review approved; before any model training, freeze
  a deterministic minimum-support policy for evaluation, decided purely
  from the measured Phase 1 taxonomy.
- **Input**: `reports/phase1_platform.json` component_taxonomy.counts (21
  classes, 122,496 rows) -- no model has been trained yet, so this
  decision could not have been informed by any model's performance.
- **Decision**: Minimum support threshold = 50 (matches the boundary
  Phase 1's own inspection already reports as `classes_below_50_support`,
  so the threshold wasn't invented fresh for this policy). Exactly one
  class falls below it: Incubator (n=15). The other 20 classes are
  "primary." Treatment is "secondary_analysis_only": the full 21-class
  taxonomy is preserved everywhere (dataset, splits, training); rare
  classes are excluded only from the primary macro-F1 average and always
  reported separately (per-class metrics + full micro-F1 + confusion
  matrix, all of which still include Incubator).
- **Implementation**: `src/triagebench/evaluation/long_tail_policy.py`
  hardcodes the frozen class lists (`PRIMARY_CLASSES`/`RARE_CLASSES`,
  both `frozenset`) and exposes `primary_macro_f1_report`,
  `full_micro_f1_report`, `rare_class_report`, `missing_primary_classes`,
  and `evaluate_with_policy` as the single canonical evaluation path
  every model arm must use -- no arm may recompute or locally redefine
  the threshold. `configs/experiments.yaml` and
  `reports/class_filtering.json` mirror the same policy for
  documentation/discoverability, guarded by a test asserting they never
  disagree with the code.
- **Tests**: `tests/test_long_tail_policy.py` (15 tests) verifies: the
  primary/rare partition is exact and matches each class's own measured
  support; the frozen support snapshot matches the live
  `reports/phase1_platform.json` (catches drift if the dataset is ever
  re-extracted); primary macro-F1 genuinely excludes rare-class true
  labels from its average while full micro-F1 genuinely includes them;
  the rare-class report contains only rare classes; and the policy
  produces byte-identical results across independent imports (simulating
  two different arms' evaluation scripts). Plus a config-vs-code
  cross-check in `tests/test_config_validation.py`. Full suite: 85
  passed, lint clean.
- **Result**: Policy frozen. Per instruction, **Arm 0 has not been
  started**. Stopping here for review.

## 2026-09-10 — EXP-003: Phase 2 begins — splits, headroom gate, Arm 0

- **Purpose**: Phase 1 review approved a second time; user authorized
  full autonomous execution through v1.0.0. Extended `temporal_split()`
  to carve a real test_in_distribution set (previously stubbed empty),
  froze older-era fractions (70/15/15 train/val/test-ID) in
  `configs/experiments.yaml` before any training. Built the model-ready
  pipeline and ran the headroom gate.
- **Headroom gate result**: `scripts/prepare_model_data.py` streamed the
  full 15.34GB CSV row-by-row (never loaded at once) in 56.1s, peak RSS
  3.1GB (well within the 16GB M5 budget) -- PASS. Output: a 166MB
  compact dataset (122,496 rows: id, filing-time text, Component,
  Creation time, label_stable). Per-row label_stable computation
  reproduced Phase 1's aggregate finding exactly (82.19% stable),
  cross-validating both implementations.
- **Frozen splits** (`scripts/build_splits.py` ->
  `reports/results/splits/platform_splits.json`): train=72,736,
  val=15,585, test_in_distribution=15,585, test_temporal_shift=18,590.
  10 duplicate groups spanned the temporal boundary (unified onto the
  earlier member's side, per the existing group-first algorithm).
  Real finding: `Update  (deprecated - use Eclipse>Equinox>p2)` has
  zero examples in test_temporal_shift (makes sense -- a deprecated
  component accumulates no new issues in the recent era). Flagged
  automatically by `missing_primary_classes_in_eval_set`, not a bug.
- **Bug found and fixed immediately after Arm 0's first run**: the
  bootstrap CI for primary macro-F1 used the generic (unfiltered,
  21-class) `macro_f1_metric` instead of a policy-filtered (20-class)
  one, so its `point_estimate` silently disagreed with the actual
  reported `primary_macro_f1` (0.5994 vs 0.6295 on the full regime).
  Root cause: two different metric definitions sharing a similar name.
  Fixed by adding `primary_macro_f1_metric()` to
  `long_tail_policy.py` and using it for the bootstrap CI; two
  regression tests added. Arm 0 was re-run in full after the fix (not
  patched after the fact) -- point estimates were identical (LogReg is
  deterministic), only the CI changed to match its own point estimate.
- **Arm 0 result** (TF-IDF + Logistic Regression, C=10.0 selected via a
  3-value validation-only pilot [0.1, 1.0, 10.0], single seed since
  lbfgs is a deterministic convex solver):

  | regime | n_train | val primary-F1 | test_ID primary-F1 | test_ID micro-F1 | shift primary-F1 | shift micro-F1 | stable-slice primary-F1 |
  |---|---:|---:|---:|---:|---:|---:|---:|
  | 50/class | 985 | 0.4009 | 0.3955 | 0.4531 | 0.2954 | 0.3921 | 0.4011 |
  | 200/class | 3,412 | 0.5028 | 0.4990 | 0.5505 | 0.3649 | 0.4850 | 0.5030 |
  | 1000/class | 15,614 | 0.5674 | 0.5747 | 0.6404 | 0.3980 | 0.5379 | 0.5967 |
  | full | 72,736 | 0.6155 | 0.6295 | 0.7399 | 0.4261 | 0.6126 | 0.6522 |

  Full-regime bootstrap 95% CI: primary macro-F1 [0.593, 0.657],
  full micro-F1 [0.732, 0.748] (test_in_distribution).
- **Interpretation**: Monotonic improvement with more labeled data (no
  crossover to report yet -- single arm so far). **Substantial temporal
  degradation**: full-regime primary macro-F1 drops from 0.630
  (in-distribution) to 0.426 (temporal-shift) -- a ~32% relative drop.
  This is a real, measured limitation of a bag-of-words classifier under
  vocabulary/taxonomy drift over the ~7-year gap between the older and
  recent eras, not an artifact. Stable-label slice consistently
  outperforms the full test set (0.652 vs 0.630 at full regime),
  consistent with stable-label issues being less ambiguous to route.
  Incubator (rare class) scores 0 on the tiny test set it appears in (1
  example) -- expected given its size, reported transparently rather
  than hidden.
- **Latency** (Apple M5, local CPU, scikit-learn, full regime): p50
  0.186ms, p95 0.288ms, p99 0.411ms single-request; batch-64 throughput
  ~22,006 req/s. Effectively free at any realistic production volume.
- **Tests**: 92 passed, lint clean.
- **Next action**: Arm 1 (encoder fine-tune, ModernBERT-base) -- pilot
  the smallest regime first to measure real MPS throughput before
  committing to the full-data regime.

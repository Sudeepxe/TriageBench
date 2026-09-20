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

## 2026-09-10 — EXP-004: Arm 1 MPS pilot -- apparent hang, corrected diagnosis, move to CUDA

- **Purpose**: Pilot Arm 1 (`answerdotai/ModernBERT-base`) on the
  smallest regime (50/class, 985 examples) on Apple M5/MPS before
  committing to the full 4-regime x 3-seed sweep.
- **Two real bugs found and fixed before the pilot could even start**:
  (1) `TrainingArguments(use_mps_device=...)` -- removed in the
  installed transformers version (5.17.0); MPS is now auto-detected.
  (2) `RuntimeError: stack expects each tensor to be equal size` on the
  first batch -- the custom `TextClassificationDataset` tokenizes
  without padding, and `Trainer`'s default collator doesn't pad. Fixed
  by passing `DataCollatorWithPadding(tokenizer)` to `Trainer`. Neither
  bug is specific to the real dataset; both would have been caught
  instantly by a synthetic-data smoke test, which didn't exist yet --
  added retroactively (see Correction below).
- **Apparent hang**: after the fixes, the pilot ran for ~58 minutes with
  no visible output. Non-destructive diagnosis (process inspection,
  `lsof`, a 2-second `sample` stack trace) showed: PID alive, only ~69s
  of CPU time accumulated over 58 minutes wall-clock (~2% utilization),
  9.7GB physical footprint plateaued at its peak, main thread blocked in
  `torch::autograd::THPVariable_cpu` -> `MPSStream::synchronize` ->
  `-[_MTLCommandBuffer waitUntilCompleted]`, and a separate Metal
  command-queue thread actively submitting GPU commands. This was
  reported as "pathologically slow, working hypothesis: MPS/ModernBERT
  incompatibility -- not confirmed" and the process was terminated via
  SIGTERM on instruction (PID 11467; its parent `uv` process 11465 also
  exited; no other process touched).
- **Correction to the diagnosis, made immediately upon new evidence**:
  killing the process via SIGTERM allowed its `tail`-piped, previously
  block-buffered stdout to flush before exit -- and that output revealed
  the run had **not** actually hung. It had completed training (3
  epochs, 186 steps, `train_runtime=1401s`, `train_loss=2.462`) and the
  `test_in_distribution` evaluation (`primary_macro_f1=0.1805`), and was
  midway through the `test_temporal_shift` evaluation when killed. The
  2-second stack sample had simply caught it mid-step during a real,
  if very slow, synchronous MPS wait -- not a deadlock. **The MPS-hang
  hypothesis from the initial diagnosis is retracted**; it does not
  match the completed-run evidence and should not be treated as
  established. Root cause for *why* it's this slow remains an open,
  unconfirmed hypothesis (possibly a CPU-fallback path for some
  ModernBERT-specific op, possibly generic MPS overhead for this
  architecture) -- not investigated further, since the practical
  conclusion (too slow to be worth debugging in place) is the same
  either way.
  - Measured throughput: training 2.11 samples/sec (7.53s/it at
    batch=16); eval ~42.6 samples/sec. Extrapolated training time alone
    (ignoring eval overhead) at this rate: 200/class ~27min,
    1000/class ~2.1hr, full (72,736 examples) **~28.7 hours per seed**
    -- impractical for a 4-regime x 3-seed sweep regardless of whether
    the process was "hung" or merely this slow.
  - The printed 0.1805 test_in_distribution figure is **not** an
    official result: the run was interrupted before writing its result
    JSON (no `hyperparameters`/`config` metadata block was ever
    assembled), so it is recorded here only as diagnostic context, not
    as a comparable Arm 1 data point. `reports/results/arm1/` remains
    empty.
  - Evidence preserved: `reports/results/arm1_mps_pilot_failure/`
    (stack sample, final `ps` snapshot, full interrupted-run stdout).
- **Correction applied**:
  1. Removed the per-epoch validation eval (`eval_strategy="no"`) --
     unnecessary recurring cost with no early-stopping/checkpoint
     logic depending on it.
  2. Added `get_device(override=...)`, `configure_determinism()`
     (`cudnn.deterministic=True`, `cudnn.benchmark=False` on CUDA), and
     `describe_hardware()` (records exact GPU name/VRAM/CUDA version so
     M5 and NVIDIA numbers are never treated as directly comparable) to
     `scripts/train_encoder.py`.
  3. Added `tests/test_encoder_training_smoke.py`: a fast (~3s),
     network-free forward+backward pass test using a tiny randomly
     initialized BERT (not real ModernBERT weights) that exercises the
     exact padding/collation/device-placement path -- this specific
     test would have caught bug (2) above in milliseconds. Includes a
     CUDA variant (skipped here, no CUDA on this machine) that must
     pass before any cloud GPU run is launched.
  4. Decision: move Arm 1 fine-tuning to a rented CUDA GPU. M5 remains
     the local development/inference-benchmarking environment; its
     numbers are never compared directly against NVIDIA timings.
- **Tests**: 98 passed, 1 skipped (the CUDA smoke test, no CUDA here),
  lint clean.
- **Next action**: awaiting review before provisioning cloud GPU (see
  chat for the recommended provider/GPU/cost estimate). Plan: run the
  CUDA smoke test on the rented instance first, then a short timed pilot
  (single seed, 50/class) to measure real throughput empirically before
  committing to the full 4-regime x 3-seed sweep -- not assuming a
  throughput number.

## 2026-09-11 — EXP-005: Arm 1 full data-efficiency sweep on DistilBERT (\$0, local MPS)

- **Purpose**: User withdrew approval for all paid cloud compute.
  Investigated a local, \$0-cost alternative before marking Arm 1
  unevaluated (see `docs/DESIGN_DECISIONS.md` for the full substitution
  rationale: DistilBERT-base-uncased in place of ModernBERT-base,
  measured ~3x faster in an isolated probe on the same M5/MPS
  hardware). MPS smoke test
  (`tests/test_encoder_training_smoke.py::test_forward_backward_pass_on_mps`)
  passed before any real run.
- **Pilot** (50/class, seed 0): completed twice for cross-check --
  105.9s and 105.8s, 27.6-27.9 samples/sec, primary macro-F1 0.177-0.178
  (test_ID). Confirmed practical; proceeded automatically per the
  pre-agreed five-condition gate (smoke test passed, pilot succeeded,
  throughput practical, memory footprint is batch/seq-size-dependent
  not dataset-size-dependent so it generalizes safely, cost is \$0).
- **Gap found and fixed before the sweep**: neither Arm 0's nor Arm 1's
  result JSON recorded `git_commit` or which frozen split file/boundary
  produced it -- both required fields per the reproducibility
  checklist. Added `triagebench.utils.reproducibility`
  (`get_git_commit`, `get_split_metadata`), wired into both training
  scripts, and re-ran Arm 0 (fast, deterministic -- point estimates
  unchanged) and the Arm 1 pilot (fast -- point estimates shifted by
  ~0.0006, consistent with MPS not being bit-exact deterministic despite
  `torch.manual_seed`) to backfill.
- **Data-efficiency results** (DistilBERT-base-uncased, MPS, 3 epochs,
  batch=16, lr=2e-5, no per-epoch eval):

  | regime | seed | n_train | test_ID primary-F1 | test_shift primary-F1 |
  |---|---:|---:|---:|---:|
  | 50 | 0/1/2 | 985 | 0.178 / 0.163 / 0.160 | 0.100 / 0.089 / 0.110 |
  | 200 | 0/1/2 | 3,412 | 0.431 / 0.405 / 0.418 | 0.292 / 0.290 / 0.290 |
  | 1000 | 0/1/2 | 15,614 | 0.540 / 0.530 / 0.533 | 0.377 / 0.372 / 0.373 |
  | full | 0 | 72,736 | 0.623 | 0.447 |

  Seed variance is small and consistent across regimes (stdev
  0.004-0.011 in primary macro-F1) -- pre-registered evidence that
  additional seeds would likely narrow CIs only marginally, informing
  the seed-count decision for the full regime below.
- **Full-regime seed 0 vs. Arm 0 (TF-IDF) at full data**: DistilBERT
  0.623 (test_ID) / 0.447 (test_shift) / 0.809 (full micro-F1, test_ID)
  vs. TF-IDF 0.630 / 0.426 / 0.740. Bootstrap 95% CIs for primary
  macro-F1 overlap substantially (DistilBERT [0.600, 0.647] vs. TF-IDF
  [0.593, 0.657]) -- **statistically indistinguishable** on the primary
  headline metric at full data. DistilBERT shows a real, if modest,
  edge on temporal-shift primary macro-F1 and a larger edge on full
  micro-F1. Neither model is forced to "win"; both are reported as-is.
- **Failure/anomaly found and root-caused (not hidden)**: the full-regime
  seed-0 run's *wall-clock* elapsed time was 57,327s (~15.9 hours),
  wildly inconsistent with its own steady-state step rate (~1.86 it/s,
  which implies ~7,330s / ~2.04 hours of actual compute for 13,638
  steps). Non-destructively diagnosed while the job was still running
  (process alive, step counter advancing, loss genuinely decreasing --
  not the EXP-004 hang pattern) by checking macOS's power log
  (`pmset -g log`), which showed **67 distinct "Entering Sleep state"
  events** during the run's window, roughly every 10-20 minutes
  overnight on this unattended laptop. The process was suspended (no
  progress, no CPU time) through most of those intervals; wall-clock
  timers kept advancing regardless. This is an infrastructure/
  environmental finding, not a code defect: **local unattended training
  on a laptop is vulnerable to system idle-sleep inflating wall-clock
  duration far beyond actual compute time.** The raw `train_seconds`
  field in `reports/results/arm1/regime_full_seed0.json` (57327.2) is
  left unmodified as an honest record of what literally elapsed for
  that invocation, but must **not** be read as real compute cost --
  ~7,330s (~2.04hr) is the corrected active-compute estimate, consistent
  with the measured ~27-30 samples/sec seen at every other regime.
- **Correction applied**: subsequent long local runs are launched under
  `caffeinate -i` to prevent idle sleep from recurring, so wall-clock
  and active-compute time can be trusted to match going forward.
- **Tests**: 101 passed, 1 skipped, lint clean throughout.
- **Next action**: full-regime seeds 1 and 2, under `caffeinate`, to
  complete the 3-seed protocol.

## 2026-09-12 — EXP-005 continued: full-regime seeds 1-2, a second sleep-related finding

- **Seed 1** (`caffeinate -i env ... train_encoder.py --regime full
  --seeds 1,2`): ran cleanly. `train_runtime=7742s` (~2.15hr) at
  28.18 samples/sec -- matches the EXP-005 corrected active-compute
  estimate almost exactly. `caffeinate -i` worked as intended here.
- **Seed 2**: `train_runtime=24170s` (~6.71hr), naive throughput
  9.03 samples/sec -- inflated again, same pattern as seed 0.
  Diagnosed via `pmset -g log` for the exact window
  (2026-09-11 22:02 to 2026-09-12 04:54): 34 sleep events, including
  **2 "Clamshell Sleep" and 1 "Thermal Emergency Sleep"** (plus 24
  "Maintenance Sleep" and 7 "Sleep Service Back to Sleep").
  **`caffeinate -i` only prevents *idle* sleep -- it does not prevent
  lid-closed (clamshell) sleep**, which is a separate, stronger macOS
  trigger. The thermal emergency sleep is itself notable: sustained
  MPS compute with the lid closed (restricting airflow) pushed the
  machine into a thermal-safety shutdown of activity. This is a real
  hardware/environmental constraint for this project, not a code
  defect -- **reliable long unattended local training on this MacBook
  requires the lid to stay open** (or an external display, or
  `sudo pmset -b disablesleep 1`), not just `caffeinate -i`.
  Corrected active-compute estimate (from the Trainer's own
  steady-state per-step rate, same method as seed 0): ~7,743s
  (~2.15hr), consistent with seeds 0 and 1. The raw
  `train_seconds=24167.6` field in
  `reports/results/arm1/regime_full_seed2.json` is left unmodified as
  an honest record of what literally elapsed; this correction is
  documented here, not substituted into the artifact. Result quality
  itself was not affected by the interruption (training resumed
  correctly after each wake, exactly as with seed 0).
- **Full-regime 3-seed aggregate** (test_in_distribution primary
  macro-F1): mean 0.6123, **stdev 0.0203** (values: 0.623, 0.630,
  0.584) -- notably higher than the 0.004-0.011 stdev seen at the
  50/200/1000 regimes. This is why running the complete 3-seed
  protocol for the full regime (rather than stopping at 1 seed, which
  was considered and explicitly deferred pending seed 0's actual
  runtime) was the right call: seed variance at full data turned out
  to be *larger*, not smaller, than at smaller regimes, and a
  single-seed estimate could have been off by up to ~0.03-0.04 in
  either direction. test_temporal_shift primary macro-F1 was much more
  stable across seeds: mean 0.4449, stdev 0.0031.
- **Corrected active-compute totals for the full regime, all 3 seeds**:
  ~7,330s + 7,742s + ~7,743s = **~22,815s (~6.34 hours)**, all \$0 local
  MPS compute (M5 unified memory, no VRAM/cost concerns at any point).
- **Tests**: 101 passed, 1 skipped, lint clean.
- **Arm 1 status: COMPLETE.** All 4 regimes x 3 seeds (50/200/1000
  fully clean, full regime with two documented sleep-related wall-clock
  anomalies but unaffected result quality) are in
  `reports/results/arm1/`. Next: aggregate Arm 0 + Arm 1 into the
  data-efficiency analysis, generate plots, then move to the LLM arms
  (2/3/4) -- subject to the same local-feasibility-first investigation
  pattern established here before assuming any cloud spend is needed.

## 2026-09-12 — EXP-006: Arms 2/3 -- naive vs. engineered prompting (Qwen2.5-1.5B, $0 local)

- **Purpose**: Evaluate a genuinely small (1.5B) open-source LLM as a
  frozen base (Arm 2, naive prompt) and with a frozen engineered prompt
  (Arm 3), on the same fixed evaluation subset, entirely at $0 local
  cost.
- **Model**: `mlx-community/Qwen2.5-1.5B-Instruct-4bit` (Qwen2.5 base
  license Apache-2.0; MLX 4-bit quantization by mlx-community), via
  `mlx-lm` on Apple M5. Verified loading (~21s incl. download) and
  generation (~1.4s/example in an isolated single-call test) before
  committing to any real run.
- **Pre-registered methodology** (frozen in `configs/experiments.yaml`
  `llm_arms`, decided before any prompt was written or any result
  existed): generation is far slower per example than the
  classifier-head arms, so Arms 2/3/4 evaluate on a fixed
  **uniform-random** (not stratified -- preserves real class
  distribution) **500-example subset** of each frozen test split,
  seed 0, reused identically across arms for paired comparison. This
  mirrors the frontier arm's paired-subset design, applied locally for
  wall-clock rather than API-dollar cost.
- **Real run**: both arms x both splits (2,000 generations total)
  completed in ~304s (~0.15s/example -- faster than the isolated probe,
  plausibly due to shorter realized outputs), under `caffeinate -i`
  (no sleep interruption this time -- short enough to not need the lid
  to stay open for hours).
- **Bug found and fixed before any run touched the real data**: a
  first implementation of `match_component_label()` used plain
  substring search, which would have false-matched short class names
  (`Doc`, `Ant`, `IDE`, `CVS`, `PMC`) inside unrelated English words
  ("documentation", "provide") -- caught by a dedicated test written
  alongside the function, fixed with `\bLABEL\b` word-boundary regex
  matching before any real generation was scored.
- **Results** (primary macro-F1, 20 primary classes; unparseable
  predictions counted as always-wrong, never coerced to a default
  class):

  | Arm | Split | primary macro-F1 | full micro-F1 | unparseable rate |
  |---|---|---:|---:|---:|
  | Arm 2 (naive prompt) | test-ID | 0.1284 | 0.1660 | 62.6% |
  | Arm 2 (naive prompt) | test-shift | 0.2247 | 0.1880 | 68.0% |
  | Arm 3 (engineered prompt) | test-ID | 0.1664 | 0.2040 | 7.4% |
  | Arm 3 (engineered prompt) | test-shift | 0.1996 | 0.2680 | 6.4% |

- **Findings**:
  - Providing the 21-class vocabulary in the prompt (Arm 3) collapses
    the unparseable/hallucination rate from ~62-68% to ~6-7% -- a large,
    unambiguous effect of minimal prompt engineering.
  - That same change improves primary macro-F1 only modestly (+0.038
    test-ID, -0.025 test-shift) -- **fixing output format is not the
    same as fixing classification accuracy**. Even when constrained to
    valid labels, this 1.5B model frequently picks the wrong one.
  - **Both LLM arms substantially underperform Arm 0 even at Arm 0's
    smallest (50/class) data regime** (0.396 test-ID) and Arm 1's
    smallest regime (0.167 test-ID) -- zero-shot prompting with a small
    (1.5B) model does not "win in cold start" against classical ML with
    even minimal supervision, on this task. A real, evidence-based
    answer to one of the central research questions, not assumed either
    way in advance.
  - Arm 2's *higher* temporal-shift score vs. its in-distribution score
    (0.225 vs. 0.128) is not interpreted as a robustness advantage --
    with a ~65% unparseable rate, both numbers are computed over a
    small effective sample of parseable predictions and carry limited
    statistical weight; the bootstrap CI in each result file should be
    consulted before drawing conclusions from this reversal.
- **Tests**: 121 passed, 1 skipped, lint clean.
- **Next action**: Arm 4 (LoRA/QLoRA fine-tune of the same base model)
  -- subject to the same local-feasibility-first check (MLX has native
  LoRA support via `mlx_lm.lora`, avoiding the MPS/transformers
  backend issues found in EXP-004).

## 2026-09-16 — EXP-007: Arm 4 -- QLoRA fine-tune of Qwen2.5-1.5B via MLX ($0 local)

- **Purpose**: Fine-tune the same base model used by Arms 2/3
  (`mlx-community/Qwen2.5-1.5B-Instruct-4bit`) with LoRA adapters over
  the frozen 4-bit quantized weights (QLoRA), entirely at $0 local cost
  via `mlx-lm`'s native LoRA support (`mlx_lm.lora`), to measure whether
  fine-tuning closes the gap Arm 3 left between prompting and Arm 0/1.
- **Frozen config** (`src/triagebench/models/lora_arm.py`, decided
  before any Arm 4 result existed): rank 8, dropout 0.05, scale 20.0
  (peft-equivalent alpha 160), all 28 transformer layers, learning rate
  1e-4, 3 epochs (converted to `iters` via train-set size), effective
  batch size 16 (batch 4 x grad-accumulation 4), max sequence length
  512, engineered prompt template (same as Arm 3) so any quality
  difference vs. Arm 3 is attributable to the adapter, not a different
  prompt.
- **Smoke test caught a real LoRA-mechanics misunderstanding before any
  training ran**: `tests/test_lora_smoke.py`'s first version asserted
  both `lora_a` and `lora_b` receive a nonzero gradient on the very
  first backward pass. `lora_a`'s gradient was exactly zero. Root
  cause: `mlx-lm` zero-initializes `lora_b` (standard LoRA init, so the
  adapter contributes nothing before training); since the output
  depends on `lora_a` only through `lora_b`, the gradient w.r.t.
  `lora_a` is mathematically exactly zero at step 0 -- correct LoRA
  behavior, not a bug. Fixed the test to check both phases explicitly
  (only `lora_b` nonzero at step 0; `lora_a` nonzero only after one
  optimizer step moves `lora_b` away from zero).
- **Genuine failure, diagnosed and fixed -- Metal out-of-memory on the
  very first training step**: the pilot's first attempt (50
  examples/class, seed 0) completed its initial validation pass (Val
  loss 8.693) then crashed with
  `RuntimeError: [METAL] Command buffer execution failed: Insufficient
  Memory (00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)` inside
  `mlx_lm/tuner/trainer.py`'s `mx.eval(state, losses, n_tokens,
  grad_accum)`. Diagnosis: `grad_checkpoint` was hardcoded `False`,
  meaning activations across all 28 transformer layers of a
  1.5B-parameter model were held simultaneously for backprop, on a
  16GB M5 with several GB already committed to background apps
  (confirmed via `vm_stat`/`sysctl hw.memsize`/`top`, not assumed).
  Fix: set `GRAD_CHECKPOINT = True` -- a pure memory/compute tradeoff
  (recompute activations during backward instead of storing them all)
  that changes no experimental variable (batch size, learning rate,
  LoRA rank, epochs) and so does not alter the frozen methodology.
  Retried: succeeded, peak memory stable at 4.399 GB throughout (well
  under the 16GB budget).
- **Pilot training** (regime 50/class, seed 0): 985 train examples, 183
  iterations, 800.5s wall-clock (13.3 min). Validation-loss trajectory:
  iter 1 = 8.693 -> iter 45 = 1.134 -> **iter 90 = 4.664 (a real,
  unhidden mid-training spike)** -> iter 135 = 0.964 (recovered) ->
  iter 180 = 0.745 (best) -> iter 183 = 0.774 (final). The spike is
  reported as-is, not smoothed over or reinterpreted after the fact;
  possible causes (a difficult mini-batch, transient LR/adapter
  instability at this very small data regime) are plausible but
  unconfirmed -- what matters for the go/no-go decision is the
  downstream classification metric, not the LM-loss curve alone (see
  below).
- **Pilot evaluation** (`scripts/evaluate_lora.py`, same fixed
  500-example paired subset per split as Arms 2/3, same engineered
  prompt, `mlx_lm.load(..., adapter_path=...)`):

  | Split | primary macro-F1 | 95% CI | full micro-F1 | unparseable |
  |---|---:|---|---:|---:|
  | test-ID | 0.2968 | [0.250, 0.345] | 0.2720 | 71/500 (14.2%) |
  | test-shift | 0.2599 | [0.187, 0.316] | -- | 50/500 (10.0%) |

  Compared at the same 50/class regime: Arm 0 (TF-IDF+LogReg) 0.3955 /
  0.2954, Arm 1 (DistilBERT) 0.1668 / 0.0993, Arm 3 (same base model,
  prompted only, no fine-tuning) 0.1664 / 0.1996. **QLoRA fine-tuning
  substantially outperforms both prompting the same base model (Arm 3)
  and fine-tuning DistilBERT (Arm 1) at this regime, on both splits --
  but still trails classical TF-IDF+LogReg (Arm 0).** This is a real,
  evidence-based result: fine-tuning clearly helps the small LLM far
  more than prompt engineering did, without yet overtaking the
  cheapest baseline at minimal data. The unparseable rate (14.2%/10.0%)
  is higher than Arm 3's engineered-prompt rate (7.4%/6.4%) despite
  training directly on the label-completion format -- plausibly because
  the pilot's 985-example training set is small relative to the
  21-class output space; this should be watched as data scales up.
  Verdict: **the pilot is healthy and practical** -- no crash, stable
  memory, a non-degenerate and internally consistent metric -- so Arm 4
  continues automatically to the remaining regimes per the pre-agreed
  protocol.
- **Scaling projection and a genuine practicality finding (decided
  BEFORE running any further regime, from the measured 4.374s/iteration
  rate at regime 50, not chosen after seeing which number would look
  better)**: `compute_iters` scales linearly with train-set size, and
  QLoRA backprop through all 28 layers of a 1.5B model is far more
  expensive per step than Arm 1's DistilBERT classifier-head training
  (which measured ~28 samples/sec on the same machine). Projected
  single-seed training time: 200/class ~0.78h, 1000/class ~3.55h,
  full ~16.57h. Three seeds at the full regime would be ~50 hours of
  continuous unattended compute on a personal laptop already documented
  (EXP-004, EXP-005) to be vulnerable to sleep/thermal interruption over
  multi-hour runs. Per the project's $0/local-compute constraint and
  "no large hyperparameter sweep" instruction, the seed plan is fixed
  **before running any of these regimes**: 50/class and 200/class get 3
  seeds (modest cost, ~2.8h combined for the remaining seeds); 1000/class
  gets 3 seeds if the measured seed-0 time confirms the projection stays
  under a $0-project's reasonable overnight-run budget; **the full
  regime is trained at seed 0 only** -- not 3 seeds -- documented here
  as a resource-practicality limitation, not a fabricated or
  cherry-picked result. This mirrors the same honesty standard applied
  to Arm 1's MPS limitation: state the constraint plainly rather than
  either silently cutting corners or burning days of laptop time to
  force parity with Arm 0/1's seed count.
- **Tests**: 126 passed, 1 skipped, lint clean.

## 2026-09-16 — EXP-007 continued: regime 50 seeds 1-2 -- a genuine, severe seed-collapse failure

- **Seed 1** trained cleanly (final val loss 0.480, 849s) and evaluated
  *better* than seed 0: primary macro-F1 0.3161 test-ID (25/500
  unparseable, 5.0%) / 0.2768 test-shift (7/500, 1.4%).
- **Seed 2 produced a genuinely collapsed adapter**, not a measurement
  artifact: primary macro-F1 **0.0241** test-ID / **0.0106** test-shift
  -- near-random and far below every other Arm 4 result, Arm 1's worst
  regime, and even Arm 3's frozen-base prompting. Crucially, the
  unparseable rate stayed *low* (3/500, 4/500) -- the model was not
  failing to produce valid labels, it was confidently producing the
  *wrong* one. Diagnosed before drawing any conclusion (per the
  project's no-blind-workaround rule): a one-off diagnostic script
  (written, run, and deleted -- not part of the pipeline) generated 40
  fresh completions from the seed-2 adapter and compared them against
  ground truth. Result: **37/40 predictions were "Doc" and the
  remaining 3 were "SWT"**, regardless of the actual issue content or
  true label (true labels in the same sample were UI, SWT, Debug, Team,
  etc. -- a normal, varied distribution). This is textbook LoRA mode
  collapse: at this data regime (985 training examples across 21
  classes, batch size 4, only 183 steps), an unlucky seed can walk the
  tiny adapter into a degenerate minimum that emits a near-constant
  label -- consistent with seed 2's own validation-loss trajectory,
  which never recovered from its iter-90 spike and finished at 2.618
  (vs. 0.774 and 0.480 for seeds 0 and 1) -- the LM loss curve *was* a
  real leading indicator here, unlike the transient iter-90 spikes seed
  0 and seed 1 both recovered from.
- **Aggregate regime-50 result across all 3 seeds**
  (`scripts/aggregate_results.py`): primary macro-F1 test-ID
  **0.2123 ± 0.1333**, test-shift **0.1824 ± 0.1217** -- stdev over 15x
  larger than Arm 1's stdev at the same regime (0.0079 test-ID). **This
  is itself a central, evidence-based finding for the project's
  research question**: QLoRA fine-tuning of a 1.5B model at minimal
  data is dramatically less seed-stable than fine-tuning a much smaller
  encoder classifier head (Arm 1) at the same regime -- a real risk a
  team choosing this strategy in production would need to hedge
  against (e.g. by training multiple candidates and validating before
  deploying), not something a single-seed pilot would have revealed.
  This is reported as measured, not smoothed into a single "it works"
  number by only running seed 0.
- **Tests**: 126 passed, 1 skipped, lint clean (no code changes this
  entry -- diagnostic script was written, used, and deleted outside the
  test suite).
## 2026-09-16/17 — EXP-007 continued: regime 200 -- no collapse, near-parity with Arm 0

- **All 3 seeds trained cleanly** (639 iters each, ~640-660s/seed,
  peak memory comparable to regime 50). No seed showed the regime-50
  seed-2 collapse pattern; validation loss for all three seeds tracked
  a normal decreasing curve (final losses 0.406/0.384/0.364).
- **Evaluation** (same fixed 500-example paired subset, engineered
  prompt):

  | Seed | test-ID primary macro-F1 | unparseable | test-shift primary macro-F1 | unparseable |
  |---|---:|---:|---:|---:|
  | 0 | 0.4326 | 0/500 | 0.3447 | 1/500 |
  | 1 | 0.4913 | 1/500 | 0.3578 | 0/500 |
  | 2 | 0.4869 | 0/500 | 0.3756 | 0/500 |

  Aggregate: **0.4703 ± 0.0267** test-ID, **0.3593 ± 0.0127**
  test-shift -- stdev back in a normal range (comparable to Arm 1's
  0.0109 at this regime), consistent with regime-50 seed 2 being a
  genuine tail-risk event at minimal data rather than a systematic
  QLoRA instability that recurs at every regime.
- **Comparison at 200/class**: Arm 0 0.4990 (single seed), Arm 1
  0.4179 ± 0.0109, Arm 3 (prompted, no fine-tune) 0.1664 (fixed,
  no regime). **Arm 4 now nearly matches Arm 0** (0.4703 vs. 0.4990,
  within ~0.03) and clearly overtakes Arm 1 (+0.052) at the same
  regime -- unparseable rate is essentially zero (0-1/500) at this
  larger training size, unlike the 5-14% seen at 50/class. Fine-tuning
  is closing the gap to classical ML rapidly as data increases, a real
  trend worth checking against regime 1000/full.
- **Tests**: 126 passed, 1 skipped, lint clean.

## 2026-09-17 — EXP-007 continued: regime 1000 seed 0 -- another sleep-inflation finding, corrected

- **Reported wall-clock time was 57,854.1s (16.07h)** -- roughly 4.5x
  the ~3.55h projected from the regime-50/200 per-iteration rate. Not
  accepted at face value: `It/sec` reported throughout training
  (0.228-0.321, no downward trend) shows genuine per-step compute cost
  never changed, so the inflation had to be external, not a real
  slowdown. Diagnosed via `pmset -g log` (per the project's
  never-assume rule) rather than guessed.
- **Diagnosis, fully evidenced**: the run started ~00:03. At 00:50:47
  the machine entered `Clamshell Sleep` (lid closed). This is the
  exact limitation already documented in EXP-004: `caffeinate -i` does
  not prevent lid-closed sleep. `pmset -g log` shows a continuous
  sequence of `Maintenance Sleep` / `Sleep Service Back to Sleep`
  cycles (brief ~1-60s wake windows every ~900-1050s for OS
  housekeeping, negligible compute) lasting until **14:12:39**, when a
  `Wake ... due to ... lid` event shows the lid was physically
  reopened -- **13 hours 22 minutes of near-total suspension**.
  Training then resumed and finished at 16:07:16 (1h55m of further
  active compute). Reconstructing active-compute time from iteration
  counts either side of the sleep window (~655 iters pre-sleep + the
  remaining iters post-wake, both at the same ~4.3s/iter rate measured
  in EXP-007's regime 50/200 pilots) gives an estimated **~3.5h of
  genuine active compute** -- matching the original projection almost
  exactly. The reported 57,854.1s in
  `train_regime_1000_seed0.json` is left unedited (it is what the
  wall clock actually measured), but is not a genuine QLoRA compute
  cost and must not be read as one -- `scripts/aggregate_results.py`'s
  `load_arm4` now attaches a `train_seconds_note` making this explicit,
  matching the same correction pattern already applied to Arm 1's
  `load_arm1` for the same class of issue.
- **This does not affect training correctness**: mlx-lm's training
  loop is not aware of or disrupted by system sleep -- it simply stops
  executing while the OS is suspended and resumes exactly where it
  left off, with no state loss (final val loss 0.205, a clean
  monotonic-ish trajectory throughout, no collapse). Only the
  wall-clock/resource-cost measurement is affected, not the resulting
  adapter or its evaluation.
- **Practical implication for the remaining plan**: the corrected
  ~3.5h active-compute estimate for 1000/class confirms the original
  per-seed projection, so seeds 1-2 proceed as planned. For the
  full regime (already committed to seed-0-only on cost grounds), the
  projected ~16.6h of active compute could again be stretched across
  a much longer unattended wall-clock window if the lid closes
  overnight -- this is a known, now twice-documented environmental
  constraint of this $0/local-laptop setup, not a new limitation
  discovered here, and does not change the pre-committed regime/seed
  plan.
- **Tests**: 126 passed, 1 skipped, lint clean.

## 2026-09-17 — EXP-007 continued: regime 1000 seed 0 -- Arm 4 overtakes Arm 0 and Arm 1

- **Evaluation** (same fixed 500-example paired subset, 0% unparseable
  on both splits): **primary macro-F1 0.6568 test-ID / 0.4281
  test-shift.**
- **Comparison at 1000/class**: Arm 0 (TF-IDF+LogReg) 0.5747 / 0.3980,
  Arm 1 (DistilBERT) 0.5341 ± 0.0040 / 0.3740 ± 0.0023. **Arm 4 now
  clearly overtakes both** classical ML (+0.082 test-ID, +0.030
  test-shift) and the fine-tuned encoder (+0.123 test-ID, +0.054
  test-shift) on a single seed -- the first regime where QLoRA
  fine-tuning of the small LLM is the outright best-performing arm
  measured so far in this project on both splits simultaneously.
  Consistent with the trend already visible at 200/class (closing the
  gap to Arm 0), this crossover happening by 1000/class, well before
  the full-data regime, is itself a central, evidence-based answer to
  the project's research question -- not assumed in advance in either
  direction.
- **Tests**: 126 passed, 1 skipped, lint clean.
- **Next action**: train and evaluate seeds 1-2 at regime 1000 (active
  compute confirmed ~3.5h/seed, sleep-inflation risk noted but doesn't
  block proceeding), to confirm this crossover isn't a single-seed
  artifact; then the full regime at seed 0 only.

## 2026-09-17 — EXP-007 continued: regime 1000 seeds 1-2 -- crossover confirmed, no collapse recurrence

- **Both seeds trained cleanly** with no sleep interruption this time
  (seed 1: 9,273.6s = 2.58h wall-clock, matching the corrected
  active-compute estimate almost exactly; seed 2: 11,588.6s = 3.22h,
  slightly higher peak memory at 5.271GB vs. 4.399GB for seeds 0/1,
  consistent with the same minor per-seed memory variation already
  seen at regime 50). Final validation losses 0.234 (seed 1) and 0.228
  (seed 2) -- both well-converged, no collapse signature at any point
  in either trajectory.
- **Evaluation** (same fixed 500-example paired subset):

  | Seed | test-ID primary macro-F1 | test-shift primary macro-F1 |
  |---|---:|---:|
  | 0 | 0.6568 | 0.4281 |
  | 1 | 0.6375 | 0.5182 |
  | 2 | 0.5875 | 0.4579 |

  Aggregate: **0.6273 ± 0.0292** test-ID, **0.4680 ± 0.0375**
  test-shift -- seed variance in a normal range (comparable to regime
  200's 0.0267), confirming regime 50's seed-2 collapse was a genuine
  tail-risk event specific to that minimal-data regime, not a
  recurring property of this fine-tuning setup.
- **The Arm 0/Arm 1 crossover holds across all 3 seeds, not just
  seed 0**: every single Arm 4 seed at 1000/class beats Arm 0
  (0.5747/0.3980) and Arm 1 (0.5341 ± 0.0040/0.3740 ± 0.0023) on both
  splits -- the worst Arm 4 seed (2: 0.5875/0.4579) still exceeds both
  baselines. This is now a robust, multi-seed finding, not a
  single-run artifact: by 1000 examples/class, QLoRA fine-tuning of
  the small LLM is the best-performing arm measured in this project.
- **Tests**: 126 passed, 1 skipped, lint clean.
- **Next action**: the full regime, seed 0 only, per the pre-committed
  scaling plan (projected ~16.6h active compute; wall-clock may again
  be inflated by sleep, per the documented environmental constraint).

## 2026-09-19 — EXP-007 continued: full-regime seed 0 run lost to an unexplained reboot

- **What happened**: the full-regime (72,736 examples, 13,638 iters)
  seed-0 run started 2026-09-17 22:13 and reached at least iter 10,896
  (val loss 0.173 at iter 6,818, 0.169 at iter 10,227 -- healthy, no
  collapse) before the machine rebooted at 2026-09-19 04:36. No result
  JSON and no adapter exist: `train_lora.py` saves only at the final
  iteration (`save_every = iters`), so all progress was lost. This is
  reported as a genuine failed run, not a result.
- **Evidence, not assumption**: `pmset -g log` shows Clamshell Sleep
  (lid closed) at 2026-09-18 23:29:20 followed by hours of
  Maintenance/Sleep-Service cycling (the same pattern as the regime-1000
  seed-0 run), then `powerd` restarting at 04:37 and `last reboot`
  showing 04:36. No kernel panic report was found in
  `/Library/Logs/DiagnosticReports`, so the *cause of the reboot itself
  is unknown* (not diagnosed); only the preceding sleep pattern and the
  loss of the process are established.
- **Consequence**: full-regime Arm 4 is currently NOT MEASURED. Nothing
  else is affected -- regimes 50/200/1000 (all 3 seeds each) are
  committed and complete.
- **Next action**: relaunch full-regime seed 0 unchanged (same frozen
  config; no methodology change), on the same $0 local hardware. The
  known risk is that a ~14-16h uncheckpointed run on a laptop that
  sleeps when the lid closes can fail again; if it does, full regime
  will be reported as BLOCKED by local-compute reliability rather than
  worked around.

## 2026-09-20 — EXP-007 concluded: Arm 4 full-regime seed 0 evaluated

- **Run**: the relaunched full-regime seed-0 run completed 13,638 of
  13,638 iterations (final val loss 0.163, peak memory 4.399 GB,
  wall-clock 87,683.9s = 24.4h, inflated by sleep; not a compute-cost
  figure). Adapter and `train_regime_full_seed0.json` written 2026-09-20
  05:01. I did not diagnose the sleep periods within this specific run.
- **Evaluation** (unchanged `scripts/evaluate_lora.py`, same fixed
  500-example paired subset, same frozen protocol; no retraining, no
  methodology change): primary macro-F1 **0.6262** test-ID
  (95% CI [0.569, 0.705]) / **0.4723** test-shift ([0.401, 0.588]);
  full micro-F1 0.772 [0.738, 0.810] / 0.690 [0.648, 0.730]; 0/500
  unparseable on both; generation p50 218ms / p95 305ms (test-ID).
  `rare_class_metrics` is empty because Incubator is absent from both
  500-example subsets, so Arm 4 rare-class behavior is NOT MEASURED.
- **Finding**: Arm 4 plateaus -- 0.6262 at full data vs. 0.6273 at
  1000/class (test-ID). At full data Arms 0 (0.6295), 1 (0.6123) and 4
  (0.6262) are statistically indistinguishable on test-ID primary
  macro-F1; Arm 4's test-shift point estimate (0.4723) is the highest
  but its CI overlaps Arms 0/1. No winner is claimed. Caveats recorded
  in RESULTS.md: subset (n=500) vs. full-test-set comparison, and a
  single full-regime seed with no variance estimate (seed-0-only by the
  pre-committed compute plan, not extended).
- **Arm 4 is complete**: 3 seeds at 50/200/1000 per class, 1 seed at
  full. Plots regenerated with Arm 4 added; 126 tests passed, 1
  skipped, lint clean.

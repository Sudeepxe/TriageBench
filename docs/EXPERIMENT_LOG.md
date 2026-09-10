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

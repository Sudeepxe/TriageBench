# Dataset Card — Eclipse Issue Report Dataset (Platform subset)

## Provenance

| Field | Value |
|---|---|
| Title | Eclipse issue report dataset |
| Author | López Durán, Noelia (Universidad de Sevilla) |
| Zenodo DOI | `10.5281/zenodo.15348468` |
| Version | v1.0.1 |
| Published | 2024-11-27 |
| License | CC-BY-SA-4.0 |
| Extractor | [Issuex](https://github.com/diverso-lab/Issuex) |
| Record | <https://zenodo.org/doi/10.5281/zenodo.15348468> |

Verified directly against the live Zenodo record (not assumed): file list,
sizes, and MD5 checksums are pinned in `configs/dataset.yaml` and were
matched exactly against locally downloaded files (`sample_data.csv`,
`README.md`).

This repository does **not** redistribute the dataset. See "Reproducing"
below.

## License note

Code in this repository is MIT-licensed. The dataset itself is
CC-BY-SA-4.0, and any derived dataset artifacts (splits, preprocessed
files) this project's scripts produce inherit CC-BY-SA-4.0's share-alike
obligations, independent of this repo's code license. See `LICENSE`.

CC-BY-SA-4.0 permits commercial use and adaptation; it requires (a)
attribution to the original author, (b) a link to the license, (c)
indication of whether changes were made, and (d) that adaptations be
distributed under the same license. This project satisfies (a)-(c) via the
attribution block below and does not currently distribute any adaptation
of the dataset itself (only code that operates on a locally-downloaded
copy), so (d) does not yet apply; if derived data artifacts are ever
published, they will be licensed CC-BY-SA-4.0, not MIT.

### Required attribution

> This project uses the "Eclipse issue report dataset" by López Durán,
> Noelia (Universidad de Sevilla), obtained via Zenodo
> (DOI: [10.5281/zenodo.15348468](https://doi.org/10.5281/zenodo.15348468),
> v1.0.1), licensed under
> [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
> No modifications have been made to the redistributed data (the dataset
> is not redistributed by this project at all; see "Reproducing" below).

## Scope used by TriageBench

- **Product**: Eclipse Platform only (`Product == "Platform"`). Cross-product
  transfer is out of scope for V1.0.
- **Upstream-stated Platform row count**: 122,497 (from the upstream
  README/Zenodo description — a claim, independently verified against the
  actual extracted data in `reports/phase1_platform.json`, not assumed).
- **Input fields used**: `Summary`, `Description` only (filing-time
  information). See `docs/METHODOLOGY.md` for the full leakage-prevention
  rule and `tests/test_feature_allowlist.py` for its enforcement.
- **Target**: current/settled `Component` — see `docs/METHODOLOGY.md` for
  why this, not the filing-time component, is the V1 target.

## Structural findings (measured)

- The full archive (`Eclipse_dataset.zip`) stores each of the 9 Eclipse
  projects as an independent ZIP entry, compressed with method 9
  (Deflate64). Platform's entry: ~8.06GB compressed, ~15.34GB uncompressed.
  See `docs/DESIGN_DECISIONS.md` for how this is extracted without
  downloading the other 8 projects or the full 17.2GB archive.
- `sample_data.csv` (the small upstream sample, 100 rows) is **single-product
  (CDT only)**, not a cross-project sample — measured via
  `reports/phase1_sample.json`. It validates CSV parsing/schema but cannot
  be used to validate anything Platform-specific.
- CSV parsing requires `escapechar="\\"`; free-text fields carry a second
  escaping layer for literal newlines (see `docs/METHODOLOGY.md`).

## Platform-specific measurements

Measured 2026-09-10 from the real, fully-extracted `Platform_dataset_issues.csv`
(source: `reports/phase1_platform.json`; run it yourself with `make inspect-full`).

### Completeness

122,496 rows measured vs. 122,497 stated upstream (gap of exactly 1 row,
0.001%) — effectively a complete census of the upstream-stated Platform
count. 0 malformed rows, 0 CSV header/parse failures, 0 duplicate IDs.
All 122,496 rows have `Product == "Platform"` (the per-project archive
entry is already cleanly scoped; no cross-product contamination).

**Temporal coverage**: `Creation time` spans 2001-10-11 to 2022-04-12.
The archive was published 2024-11-27 but contains no issues newer than
April 2022 — a ~2.5 year gap between the last recorded issue and the
publication date. Production-relevance claims should account for this:
this is not a live-to-publication-date snapshot.

### Status / resolution composition (extraction-bias check)

| Status | Count | % |
|---|---|---|
| RESOLVED | 74,374 | 60.7% |
| CLOSED | 22,592 | 18.4% |
| VERIFIED | 19,223 | 15.7% |
| NEW | 4,788 | 3.9% |
| ASSIGNED | 1,153 | 0.9% |
| REOPENED | 345 | 0.3% |
| UNCONFIRMED | 21 | 0.02% |

**94.9% of Platform issues are in a terminal status** (RESOLVED/CLOSED/
VERIFIED); only 5.1% are still open (NEW/ASSIGNED/REOPENED/UNCONFIRMED).
This is very likely because the archive captures an entire 20+ year
project history rather than a snapshot biased toward open issues (a
mature, long-lived project accumulates mostly-resolved issues over time)
— but it does mean this dataset is **not representative of "currently
open, actively-being-triaged" issues** in the proportions a live triage
queue would see. See `docs/LIMITATIONS.md`.

Resolution breakdown: FIXED 53,880 (44.0%), WONTFIX 22,584 (18.4%),
DUPLICATE 19,301 (15.8%), WORKSFORME 10,940 (8.9%), INVALID 8,040 (6.6%),
blank/none 6,307 (5.1%, mostly still-open issues), NOT_ECLIPSE 1,350
(1.1%), MOVED 94 (0.08%).

### Component taxonomy

**21 distinct components** measured (matches the commonly-cited historical
"~21-22 components" figure for Eclipse Platform, independently confirmed
rather than assumed). Top-10 components account for **88.0%** of all
issues — substantial class imbalance. Only one class falls below 50
examples (`Incubator`, 15 issues); none fall below 10. Top components:
UI (37,307), SWT (25,495), Team (7,205), Debug (6,920), Releng (6,731),
Resources (5,495), Text (5,126), User Assistance (5,023), IDE (4,415),
Ant (4,129). One label shows a visible historical rename:
`Update  (deprecated - use Eclipse>Equinox>p2)` (3,278 issues).

### Label instability (NOT a human-accuracy ceiling — see `docs/METHODOLOGY.md`)

**`post_filing_routing_label_instability_rate` = 17.81%** — 21,815 of
122,496 issues have at least one recorded Component-change event.
100,681 issues (82.2%) have zero recorded Component changes and form the
**stable-label slice** for secondary evaluation. History was present and
parseable for all 122,496 rows (`history_empty: 0`, `history_parse_failures: 0`)
so `filing_time_component_reconstruction_rate` = 100%. Of the
reconstructed filing-time values, 103,440 match the current Component and
19,056 (15.6% of all issues) genuinely differ from it.

### Duplicates

458 exact-duplicate `(Summary, Description)` text groups, comprising 594
"extra" rows beyond each group's first occurrence (0.48% of all rows).
Handled at split-construction time via `triagebench.data.splitting`
(duplicate groups are never split across train/val/temporal-shift
boundaries).

### Missing fields

Summary missing in 1 row (0.001%). Description missing/blank in 2,512
rows (2.05%) — these issues were filed with only a summary.

### Text length

Summary: mean 56.5 chars, median 55, max 255 (Bugzilla's summary field
cap), p99 117. Description: mean 1,158 chars, median 441, p90 2,234, p99
12,998, max 131,234 (long stack-trace-heavy reports exist in the tail).

### Field-size outlier (parsing robustness finding)

One row's `Attachments` field is 406,417,641 bytes (~406MB) — almost
certainly a large binary attachment base64-embedded by the Issuex
extractor. `Attachments` is already excluded from model input (see
`POST_HOC_FIELDS`); the parser's CSV field-size limit was raised (with
real headroom) after this was discovered mid-inspection. See
`docs/EXPERIMENT_LOG.md`.

### Temporal split boundary (frozen)

Chosen from this exact histogram, then frozen in `configs/experiments.yaml`
(`split.frozen: true`) before any model was trained:

**Boundary: `2015-01-01T00:00:00Z`** → 84.81% older-era / 15.19%
recent-era, closely matching the pre-registered 85/15 target with a clean
calendar-year cut (no sub-year search was performed to chase a closer
match).

## Reproducing

```
make inspect-sample   # small schema-validation pass (no large download)
make inspect-full      # streams only the Platform entry (~8GB), never the
                        # other 8 projects or the full 17.2GB archive
```

See `configs/dataset.yaml` for the exact pin and `scripts/prepare_data.py`
for the extraction method.

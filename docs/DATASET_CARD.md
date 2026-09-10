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

Populated from `reports/phase1_platform.json` once Phase 1 full-data
inspection completes. Until then: **NOT MEASURED**.

- Row count and completeness vs. the stated 122,497: NOT MEASURED
- Component taxonomy (distinct classes, head share, long tail): NOT MEASURED
- Status/resolution composition: NOT MEASURED
- Creation-year distribution (drives the temporal split boundary): NOT MEASURED
- `post_filing_routing_label_instability_rate`: NOT MEASURED
- Duplicate rate: NOT MEASURED
- Summary/Description length statistics: NOT MEASURED

## Reproducing

```
make inspect-sample   # small schema-validation pass (no large download)
make inspect-full      # streams only the Platform entry (~8GB), never the
                        # other 8 projects or the full 17.2GB archive
```

See `configs/dataset.yaml` for the exact pin and `scripts/prepare_data.py`
for the extraction method.

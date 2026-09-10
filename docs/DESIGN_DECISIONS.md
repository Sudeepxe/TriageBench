# Design Decisions

Decisions recorded as they're made, with the reasoning and what would
change them. This is a log, not a plan — entries are added, not rewritten.

## 2026-09-10 — Dataset source pinning

Resolved the Zenodo DOI ambiguity by fetching the live record
(`10.5281/zenodo.15348468`, v1.0.1, published 2024-11-27, CC-BY-SA-4.0) and
recording its exact file list and MD5 checksums directly in
`configs/dataset.yaml`. This is the single authoritative upstream source
for V1.0; no other Zenodo record or extractor run is mixed in. Verified by
downloading `sample_data.csv` and `README.md` and matching MD5s exactly.

## 2026-09-10 — sample_data.csv is single-product, not representative

Measured (not assumed): `sample_data.csv` (2.9MB, 100 rows) contains **only
CDT** issues, not a cross-product sample. This means the sample is useful
for validating CSV parsing/schema correctness (which it did — see the
Deflate64 and escaping findings below) but cannot be used to validate
anything about Eclipse Platform specifically (taxonomy, class balance,
label instability rate, etc.). Those require the full Platform data.
Recorded here so this limitation isn't silently forgotten later.

## 2026-09-10 — Extracting only the Platform project, not the full 17.2GB archive

The full archive stores each of the 9 Eclipse projects as an independent
ZIP entry (verified by reading the remote central directory over HTTP range
requests without downloading the archive). Platform's entry alone is
~8.06GB compressed / ~15.34GB uncompressed. `scripts/prepare_data.py
extract-platform` streams only that entry, saving ~9GB of transfer for
projects (BIRT, CDT, Equinox, JDT, Mylyn, Papyrus, PDE, TPTP) this project
doesn't use. `make download-full` remains available as a fallback if the
full archive is ever needed.

## 2026-09-10 — Deflate64 compression requires 7z, not Python's zipfile

The archive's entries use ZIP compression method 9 (Deflate64 / "Enhanced
Deflate"), which Python's stdlib `zipfile` cannot decode
(`NotImplementedError`). The third-party `zipfile-deflate64` package failed
to build from source on this machine (macOS 26 arm64, missing/incompatible
system zlib headers for the vendored build). Resolution: reconstruct a
sparse local ZIP container (real local-header+compressed-data for the
target entry, real central directory/EOCD, everything else an unwritten
filesystem hole) and decompress it with `7z` (installed via
`brew install p7zip`, prebuilt bottle, no compilation needed), which
supports Deflate64 natively. This preserves the "don't download unrelated
data" goal — the sparse container's on-disk footprint is dominated by the
~8GB of actually-written compressed bytes, not its ~17GB apparent size.

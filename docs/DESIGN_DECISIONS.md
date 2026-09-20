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

## 2026-09-10 — Correction: sparse-container layout doesn't work with p7zip 17.05 at real scale

The sparse-container design above (preserving the target entry at its
*original* multi-GB absolute offset) was implemented and unit-tested
successfully, but **failed against the real archive**: p7zip 17.05
refused to open the reconstructed ~17GB file at all ("Can't open as
archive"), despite every structural region being independently verified
byte-correct (local header, central directory, ZIP64 EOCD chain all
checked by hand with `xxd`/`struct`).

Root-caused rather than worked around blindly: reproduced the exact
failure with a synthetic sparse file isolated to one variable at a time
(compression method, ZIP64 usage, trailing gap size, leading gap size).
The failure is specifically triggered by a large *leading* gap before the
archive's true start — works fine at ~1KB, fails completely at ~9GB.
This is almost certainly a scale limitation in p7zip 17.05's legacy
SFX-offset-detection heuristic (designed for small prepended stubs, a few
hundred KB to a few MB, never tested at multi-GB scale).

**Corrected design**: build a *minimal* single-entry container instead --
the target entry's local header + compressed data at local offset 0,
followed by a freshly-constructed (not reused/patched) central directory
record and ZIP64 EOCD/locator/classic-EOCD referencing that offset. No
leading gap exists, so the buggy heuristic is never triggered. Validated
against a real `7z` subprocess call in
`tests/test_prepare_data_resume.py` before re-running against real data.
See `docs/EXPERIMENT_LOG.md` (EXP-002) for the full repro/fix narrative.

This is left as a *correction* entry rather than an edit to the original
decision above, per this file's own stated policy (a log, not a plan) --
the original reasoning for using 7z at all (Deflate64 support) still
holds; only the container-layout strategy changed.

## 2026-09-10 — Arm 1 model substitution: DistilBERT-base instead of ModernBERT-base, zero cloud spend

**Decision reversed**: after approving a cloud CUDA plan for Arm 1
(RTX A5000, ~$0.27/hr, projected ~$2-8 total), the user withdrew
approval for any paid cloud compute or API spend entirely. TriageBench
must remain a $0-external-compute project going forward. This overrides
the earlier cloud-GPU decision; the CUDA smoke test and provisioning
code added for it are kept (harmless, zero-cost, and validate the
device-selection code path) but will not be used against a paid
instance.

**Why the original encoder (ModernBERT-base) was impractical locally**:
measured at ~2.11 samples/sec in the full Trainer pipeline on Apple
M5/MPS (EXP-004) -- extrapolated to ~28.7 hours for the full-data
regime alone, per seed. Not itself proof of an "MPS incompatibility";
see the refinement below.

**Refinement to the root-cause understanding** (new evidence, not yet in
EXP-004): a controlled, isolated forward+backward timing probe (15
real training steps, batch=16, seq_len=256, fixed-length padding, no
Trainer/DataCollator/eval scaffolding) measured:

| model | params | samples/sec (isolated) |
|---|---:|---:|
| distilbert-base-uncased | 67M | 30.36 |
| answerdotai/ModernBERT-base | 149.6M | 10.62 |

ModernBERT alone is ~2.9x slower than DistilBERT in this isolated test
(plausibly explained by 2.2x more parameters plus architecture
overhead) -- but that is far short of explaining the ~5x additional gap
between this isolated 10.62/s and the ~2.11/s observed in the real
EXP-004 pilot. Most of that additional gap is now attributed to
`Trainer` pipeline overhead already removed since EXP-004 (specifically
the per-epoch validation pass over the full 15,585-row val set,
`eval_strategy="epoch"` -> changed to `"no"`), not to a ModernBERT- or
MPS-specific defect. This means the original "MPS/ModernBERT
incompatibility" framing was probably too strong; the more accurate
statement is "ModernBERT-base's absolute compute cost, plus now-removed
pipeline overhead, combined to make the full experiment matrix
impractical on this hardware within a reasonable time budget" --
recorded here as a further refinement of EXP-004, not a contradiction of
its retraction (which stands: the process was not hung).

**Why DistilBERT-base-uncased is a scientifically comparable
substitute for Arm 1's role**:
- The task's own methodology (`docs/METHODOLOGY.md`) defines Arm 1 as
  "a small pretrained encoder, fine-tuned for Component
  classification" -- it does not name a specific architecture as part
  of the frozen scientific protocol. The original project brief itself
  explicitly allows "ModernBERT/DeBERTa-class encoder **or equivalent
  small encoder appropriate for the available hardware**."
  Substituting within that already-anticipated flexibility, backed by
  the diagnostic evidence above, is not a change to the task
  definition, the leakage policy, the long-tail policy, the temporal
  boundary, or the evaluation methodology -- all of which are
  unchanged and still enforced by the same code paths
  (`long_tail_policy.py`, `dataset_io.py`, the frozen splits file).
- DistilBERT-base-uncased is a standard, extremely well-established
  (2019, Sanh et al.) small pretrained transformer encoder, Apache-2.0
  licensed, 67M parameters, routinely used in production text
  classification including issue-triage-style systems -- arguably a
  *more* realistic "small production encoder" choice than a
  bleeding-edge 2024 architecture with immature backend support, not a
  downgrade in relevance to the central research question.
- Same fine-tuning protocol applies unchanged: full 21-class taxonomy
  as the output space, same frozen train/val/test splits, same
  long-tail policy (primary macro-F1 excludes only Incubator, full
  micro-F1 includes everything), same bootstrap CI methodology, same
  data-efficiency regimes (50/200/1000/full per class), same
  determinism/hardware-recording infrastructure.

**Expected resource requirements** (from the isolated probe, to be
confirmed against the real pilot before committing to the full sweep):
at 30.36 samples/sec, full-regime training (72,736 examples x 3
epochs) is O(2 hours) per seed rather than O(29 hours) -- a ~14x
improvement, on top of removing the periodic-eval overhead. All local
on Apple M5, $0 cost.

**Whether the comparison remains valid**: yes, with one explicit
caveat recorded for the final report: Arm 1's result should be labeled
by its actual model (DistilBERT-base-uncased), not described generically
as "the encoder arm ran ModernBERT," and any interpretation of Arm
1-vs-Arm-2/3/4 (small LLM arms) should note this was the practical
encoder choice available under a strict $0 compute budget, not
necessarily the most capable encoder that exists.

## 2026-09-16 — Arm 4 design: QLoRA on the Arm 2/3 base, frozen before any Arm 4 result

- **Model and method.** Same `mlx-community/Qwen2.5-1.5B-Instruct-4bit` base as
  Arms 2/3, with LoRA adapters trained through `mlx-lm` (QLoRA: adapters over a
  frozen 4-bit base), so any difference from Arm 3 is attributable to the
  adapter rather than the model or prompt. The engineered Arm 3 prompt is the
  training and evaluation template.
- **Frozen configuration** (`src/triagebench/models/lora_arm.py`): rank 8,
  dropout 0.05, scale 20, all 28 layers, learning rate 1e-4, 3 epochs
  (converted to iterations by training-set size), effective batch size 16,
  max sequence length 512, completion-only loss. Values are standard, not
  tuned; no sweep was run.
- **One change after the first attempt, methodology-neutral.** The first pilot
  hit a Metal out-of-memory error; gradient checkpointing was enabled (a
  memory/compute trade-off that changes no experimental variable).
- **Evaluation on the fixed 500-example subset**, identical to Arms 2/3, because
  generation is far slower than classifier-head inference. Sampling is uniform
  random (preserving the real class mix) rather than stratified. The cost is
  lower statistical power than the Arm 0/1 full-split evaluation, which is
  carried through every comparison.
- **Seed plan fixed before running larger regimes**: 3 seeds at 50, 200 and 1000
  per class; **seed 0 only at the full regime**, because a full-data QLoRA run is
  many hours of local compute and a $0 project cannot buy more. Documented as a
  limitation rather than hidden.
- **Arm 5 not run**: no credentials; the project has a $0 API budget.

## 2026-09-20 — Error analysis by inference-only re-scoring, not retraining

The stored artifacts lack predictions, and Arm 0/1 models were not persisted.
Retraining them was out of scope for the release, so cross-arm error analysis
was declared not measurable. For Arm 4 alone, the saved adapter was re-run on the
identical frozen subset (`scripts/error_analysis_arm4.py`), with a hard check
that recomputed metrics equal the recorded ones (they did, exactly). No training,
no methodology change, and the resulting predictions are stored with their split
metadata.


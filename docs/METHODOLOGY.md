# Methodology

Status: living document, updated as each phase completes. This file
records design decisions and their rationale; measured results live in
[RESULTS.md](RESULTS.md).

## Central engineering question

Given a newly filed technical issue, what model strategy provides the best
production trade-off between routing quality, labeled-data requirements,
latency, robustness, and cost? See the repository root README for the
full framing. This project does not assume an LLM wins; the conclusion is
whatever the measurements show.

## Task definition

- **Primary product**: Eclipse Platform (`Product == "Platform"` in the
  Issuex export — not to be confused with the unrelated `Platform` *column*,
  which is the Bugzilla hardware/OS field with values like `PC`/`Mac`/`All`;
  see `src/triagebench/data/csv_parser.py`).
- **Task**: single-label Component classification.
- **Input (filing-time only)**: `Summary` + `Description` (the initial
  report text). See the feature allowlist enforced by
  `tests/test_feature_allowlist.py`.
- **Target**: the **current/settled** Component value recorded in the
  export, not the filing-time Component.

## Why "current Component" and not "filer's original routing choice"

The dataset's `Component` field reflects the component as of extraction
time, which may differ from what was recorded when the issue was filed —
components get reassigned as investigation proceeds, ownership changes, or
taxonomy is cleaned up. Training on `Component` therefore asks a
specific, intentionally-chosen question:

> Given only the text available at filing time, can a model predict where
> this issue will eventually settle?

This is a genuinely useful production question (it's what a routing system
actually needs to get right), but it is **not** the same as predicting the
human triager's initial decision, because the target incorporates
information that accumulated after filing — even though the model itself
never sees that information as input. Conflating the two would overstate
what the model has learned. This distinction is treated as a modeling
choice with real, measured consequences (see `post_filing_routing_label
_instability_rate` in `docs/DATASET_CARD.md`), not swept under the rug.

## Label instability (not a human-error ceiling)

`post_filing_routing_label_instability_rate` = fraction of issues with at
least one `component` field-change event recorded in `History/Activity
Log`. This measures how often the target itself moved after filing. It is
explicitly **not** interpreted as `100% - instability = human accuracy`,
because component reassignment can reflect legitimate scope changes,
ownership transfers, or taxonomy cleanup — not filer error. A **stable-label
slice** (zero recorded Component changes) is evaluated separately as a
secondary, cleaner signal.

## Splits

Primary split is **temporal**, within Eclipse Platform only (no
cross-product transfer in V1.0), based on `Creation time`. The boundary
was chosen only after inspecting the real year histogram from the full
Platform data (`reports/phase1_platform.json`) and is now **frozen** in
`configs/experiments.yaml`: **`2015-01-01T00:00:00Z`**, giving 84.81%
older-era / 15.19% recent-era — a clean calendar-year cut chosen because
it already landed close to the pre-registered 85/15 target, with no
sub-year search performed to chase a closer match. This value must not be
adjusted based on any model result. See `docs/DESIGN_DECISIONS.md` and
`docs/EXPERIMENT_LOG.md` (EXP-002).

## Long-tail policy (frozen, pre-registered)

Decided 2026-09-10 from the measured Phase 1 taxonomy alone (21
components, `reports/phase1_platform.json`), **before any model was
trained or any model result existed**, and enforced in code by
`src/triagebench/evaluation/long_tail_policy.py` — every model arm's
evaluation must import its class lists/functions from that single module
rather than recomputing a threshold locally (guarded by
`tests/test_long_tail_policy.py`, which also cross-checks the module's
frozen support snapshot against the live `reports/phase1_platform.json`
to catch any drift). Full rationale in `reports/class_filtering.json`.

**Minimum support threshold: 50** (total dataset support). Chosen as a
round, common floor for a per-class F1 estimate to be minimally
statistically meaningful, and because it exactly matches the boundary
Phase 1's own inspection script already reports
(`classes_below_50_support`) — not a threshold invented after the fact to
produce a particular split.

**Classification** (of the 21 measured components):
- **Primary classes (20)**: support ≥ 50. UI, SWT, Team, Debug, Releng,
  Resources, Text, User Assistance, IDE, Ant, CVS, `Update  (deprecated -
  use Eclipse>Equinox>p2)`, Runtime, Compare, Search, Doc, Website,
  Scripting, PMC, WebDAV.
- **Rare classes (1)**: support < 50. Incubator (n=15).

**Treatment — "secondary analysis only," never exclusion from the
dataset**:
- The full 21-component taxonomy is preserved everywhere — in the
  dataset, in every split, and in training. Rare-class rows are never
  dropped, never relabeled, never merged into an "Other" bucket.
- **Primary metric** (used for all headline arm-vs-arm comparisons,
  including the headroom gate): macro-F1 computed only over examples
  whose *true* label is a primary class (`primary_macro_f1_report`).
  Excluding rare-class *true* labels from this particular unweighted
  per-class average is what a support threshold is *for* — a 15-example
  class would otherwise contribute a wildly noisy F1 estimate that
  swings the entire macro-F1 figure disproportionately to its size.
- **Micro-F1 stays fully interpretable without any threshold**: it is
  computed over *all* examples, rare classes included
  (`full_micro_f1_report`). Because micro-F1 is a single pooled
  correct/total count, each class's natural frequency weights its own
  contribution automatically — the instability problem is specific to
  macro-F1's unweighted per-class averaging, not to micro-F1.
- **Rare classes are always reported, never hidden**: a dedicated
  secondary report (`rare_class_report`) gives per-class precision/
  recall/F1/support for every rare class, computed from the exact same
  predictions used everywhere else. The confusion matrix also always
  includes every class regardless of this policy.
- **This is orthogonal to the stable-label slice** (issues with zero
  recorded Component changes, 82.2% of Platform issues). The two axes —
  primary-vs-rare (class support) and stable-vs-unstable (label history)
  — are applied independently and never conflated: a model is evaluated
  on (a) the full frozen test set with the primary/rare split, and (b)
  separately, the stable-label subset with the same primary/rare split
  applied again within it.

## Duplicates and leakage

Exact-duplicate `(Summary, Description)` text is identified before
splitting; duplicate rows are prevented from crossing the train/test
boundary. `Dupe of` is used only for this diagnostic cleaning step, never
as a model feature (see `POST_HOC_FIELDS` in `csv_parser.py`).

## CSV parsing quirk

The Issuex extractor writes CSV with escape character `\` rather than
standard doubled-quote escaping, and free-text fields carry a *second*
escaping layer: after correct `csv.reader(..., escapechar="\\")` parsing, a
real embedded quote round-trips cleanly to a bare `"` (verified against the
live upstream sample), but real newlines survive as a literal two-character
`\n`/`\r` token and need one further unescape pass
(`unescape_text_field()`). `History/Activity Log` is different again: once
CSV-unescaped it is directly valid JSON, and must **not** be run through
the Description-style unescape (JSON has its own `\"` convention for
in-string quotes — doing so corrupts valid history records; see the
regression test in `tests/test_csv_parser.py`).

## Model arms and evaluation protocol (as run in v1.0.0)

- **Arm 0** TF-IDF + logistic regression; **Arm 1** DistilBERT-base-uncased
  fine-tune (planned encoder ModernBERT-base superseded for local feasibility,
  see `docs/DESIGN_DECISIONS.md`); **Arm 2/3** frozen Qwen2.5-1.5B 4-bit with a
  naive / engineered prompt; **Arm 4** QLoRA of the same base; **Arm 5**
  (frontier API) not evaluated.
- **Data-efficiency regimes:** 50, 200, 1000 examples per class and full, with a
  stratified per-class sample of the training split; Arms 1 and 4 use 3 seeds
  (Arm 4 full: 1 seed).
- **Leakage controls:** models see only `Summary` + `Description`
  (allowlist enforced by `tests/test_feature_allowlist.py`); exact-duplicate text
  groups never straddle splits; post-filing fields (status, resolution,
  history, comments) are excluded from inputs and from error-analysis examples.
- **Temporal evaluation:** the older era (before 2015-01-01) is split 70/15/15 into
  train/validation/in-distribution test; the whole recent era is the
  temporal-shift test. The boundary, ratios and seed are frozen in
  `configs/experiments.yaml`, each introduced in a single commit and never
  changed afterwards; all 50 result files carry identical split metadata.
- **Long-tail policy:** primary macro-F1 averages the 20 classes with at least
  50 training examples; Incubator is reported separately and counted in
  micro-F1 and confusion matrices (`long_tail_policy.py`).
- **Uncertainty:** 1000-resample percentile bootstrap, 95% intervals.
- **LLM arms:** greedy decoding, at most 20 new tokens; an output that does not
  match a known class is scored as an always-wrong `__UNPARSEABLE__`
  prediction, never coerced to a default class.


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
cross-product transfer in V1.0), based on `Creation time`. The exact
calendar boundary is chosen only after inspecting the real year histogram
from the full Platform data (`reports/phase1_platform.json`) and then
frozen in `configs/experiments.yaml` — never adjusted after seeing model
results. See `docs/DESIGN_DECISIONS.md` for the boundary once chosen.

## Long-tail policy

Pre-registered (before any model results exist) in
`reports/class_filtering.json` once the full Platform taxonomy is
measured. Minimum-support threshold and treatment of rare classes are
decided from the class-distribution measurement alone.

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

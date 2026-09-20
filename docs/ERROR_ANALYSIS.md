# Error analysis

## Scope and what could and could not be done

The frozen result artifacts (`reports/results/arm*/*.json`) store aggregate
metrics only: no per-example predictions, per-class metrics or confusion
matrices were persisted for any arm. Trained Arm 0 and Arm 1 models were also
not persisted (Arm 1 used `save_strategy="no"`), and retraining is outside the
release scope. Consequently:

| Requested analysis | Status |
|---|---|
| Per-class P/R/F1, confusion patterns, temporal-shift error profile for **Arm 4** | **Done**, via inference-only re-scoring of the saved full-regime seed-0 adapter (below) |
| Per-class / confusion analysis for **Arm 0, Arm 1** | **NOT MEASURABLE from existing artifacts** (predictions not stored, models not stored) |
| Examples where **Arm 0, Arm 1 and Arm 4 disagree** | **NOT MEASURABLE** (same reason) |
| Arm 4 rare-class / Incubator behaviour | **NOT MEASURED**: Incubator is absent from both 500-example subsets (see below) |

**How the Arm 4 predictions were obtained.** `scripts/error_analysis_arm4.py`
reloads the saved adapter and re-runs greedy generation on the same fixed
500-example subset, prompt and decoding settings as `scripts/evaluate_lora.py`.
It first verifies the re-run against the recorded evaluation: recomputed
primary macro-F1 equals the recorded value exactly on both splits
(0.626239 and 0.472318; `matches_recorded_metric: true`). No training was run
and no methodology was changed. Output:
`reports/results/arm4/predictions_regime_full_seed0.json`. Every statement
below is about **Arm 4, full regime, seed 0 only** (one seed, no variance
estimate), on a 500-example subset per split. It must not be read as a
statement about Arms 0/1 or as a cross-arm leaderboard.

**Leakage.** Analysis uses only ids, gold labels and model outputs. The example
issue titles quoted below are filing-time `Summary` text, which is model input
and therefore permitted; no post-filing field (status, resolution, history,
comments) is used.

## Statistical-power caveat

Per-class figures come from 500 examples. Classes with n < 10 (ID: Compare 8,
Search 8, Doc 5, WebDAV 1; shift: Team 3, Compare 3, Search 2, PMC 2, CVS 1,
Scripting 1, Website 1) have F1 values that swing by tens of points on one
example and are shown only for completeness. Three primary classes are absent
from the ID subset (PMC, Scripting, Website) and two from the shift subset
(Update, WebDAV), so primary macro-F1 averages over the remaining primary
classes only.

## Overall error rates (Arm 4 full, seed 0)

| Split | Errors | Accuracy | Unparseable |
|---|---:|---:|---:|
| In-distribution | 114 / 500 | 0.772 | 0 |
| Temporal-shift | 155 / 500 | 0.690 | 0 |

## Per-class results (largest classes, n >= 15 in the ID subset)

| Class | ID n | ID P / R / F1 | Shift n | Shift P / R / F1 |
|---|---:|---|---:|---|
| UI | 144 | 0.80 / 0.94 / 0.87 | 155 | 0.70 / 0.76 / 0.73 |
| SWT | 102 | 0.87 / 0.75 / 0.81 | 118 | 0.85 / 0.79 / 0.82 |
| Team | 40 | 0.63 / 0.80 / 0.70 | 3 | 0.20 / 0.33 / 0.25 |
| Debug | 35 | 0.93 / 0.74 / 0.83 | 18 | 0.56 / 0.78 / 0.65 |
| Resources | 23 | 0.68 / 0.65 / 0.67 | 11 | 0.60 / 0.82 / 0.69 |
| Releng | 20 | 0.63 / 0.85 / 0.72 | 68 | 0.72 / 0.94 / 0.82 |
| Update (deprecated) | 19 | 1.00 / 0.58 / 0.73 | 0 | -- |
| User Assistance | 19 | 0.61 / 0.89 / 0.72 | 14 | 0.60 / 0.43 / 0.50 |
| CVS | 17 | 0.54 / 0.41 / 0.47 | 1 | 0.00 / 0.00 / 0.00 |
| Runtime | 17 | 0.82 / 0.53 / 0.64 | 15 | 0.64 / 0.60 / 0.62 |
| IDE | 11 | 0.50 / 0.09 / 0.15 | 55 | 0.43 / 0.05 / 0.10 |

Full per-class values are in the predictions artifact and plotted in
`reports/results/plots/arm4_per_class_f1.png`; row-normalised confusion is in
`reports/results/plots/arm4_confusion.png`.

## Confusion patterns

Most frequent confusions (true -> predicted, counts on the 500-example subset):

- **In-distribution:** SWT -> UI (12), CVS -> Team (9), IDE -> UI (6),
  SWT -> User Assistance (5), Debug -> UI (4), Team -> CVS (4).
- **Temporal-shift:** IDE -> UI (23), SWT -> UI (14), UI -> SWT (10),
  SWT -> Releng (7), UI -> Text (6), IDE -> Text/Debug/Releng (6 each).

Observations:

1. **UI acts as a sink class.** The model predicts UI 170 times against 144 true
   UI examples (ID) and 169 against 155 (shift). The most frequent confusion
   pair in both splits has UI as the predicted class (ID: SWT -> UI, 12; shift:
   IDE -> UI, 23).
2. **Some, but not most, errors are between semantically adjacent components.**
   Using three descriptive groupings chosen by component name after seeing the
   results (so exploratory, not pre-registered): errors within
   {UI, SWT, Text} are 15.8% of ID errors and 21.9% of shift errors; within
   {Team, CVS, Compare} 13.2% (ID) and 0.6% (shift); within
   {Doc, User Assistance} 2.6% (ID) and 0% (shift). The bulk of errors
   (well over half in each split) fall outside these groupings, so
   "failures are concentrated in semantically similar components" is only
   partially supported: it holds for the UI/SWT and Team/CVS pairs but does not
   describe most errors.
3. **CVS/Team confusion is specific to the ID split**, where CVS is 3.4% of the
   subset; CVS recall is 0.41 with 9 of 17 CVS examples predicted as Team.
   CVS is an implementation of the Team API, so this confusion is plausible,
   but with n = 17 it is a single-seed observation.

## Long-tail failures

Class frequency in the training split (72,736 examples) runs from UI 30.4% down
to Incubator 8, PMC 27, Website 57, WebDAV 60, Scripting 60 examples. Within the
subsets, WebDAV (ID n = 1) and the shift-only classes PMC, CVS, Scripting and
Website (n = 1-2) all score F1 = 0, but each rests on 1-2 examples and
cannot support a conclusion about long-tail behaviour. Doc (n = 5, recall 0.20,
3 of 5 predicted User Assistance) and Search (n = 8, recall 0.38) are the
low-support classes with enough examples to be suggestive, not conclusive.
**Incubator:** the full test splits contain only 1 (ID) and 4 (shift)
Incubator examples, and none fell in either 500-example subset, so Arm 4's
Incubator behaviour is not measured.

## Temporal shift changes the error profile

Class priors move substantially between the training era and the held-out
recent era. On the **full** splits (not just the subsets): IDE is 2.5% of train
and 10.3% of the temporal-shift split, Releng 4.1% -> 13.4%, while Team falls
from 6.7% to 1.0% and CVS from 3.1% to 0.3%. Consequences for Arm 4:

- **IDE becomes the dominant failure.** IDE recall is 0.09 (ID, n = 11) and
  0.05 (shift, n = 55); 52 of the 155 shift errors (34%) are true-IDE examples,
  23 of them predicted UI. IDE is the largest single source of shift errors and of the
  accuracy loss. (Primary macro-F1 weights every class equally, so IDE counts
  once, like the four 1-2-example classes that score F1 = 0; its 11% share
  matters for accuracy/micro-F1, not for its weight in macro-F1.)
- **The CVS/Team confusion disappears** simply because those classes are nearly
  absent from the shift split; this is a change in class mix, not evidence that
  the model handles them better.
- **Releng improves** (F1 0.72 -> 0.82) while being 3x more frequent.
- Overall accuracy falls from 0.772 to 0.690 and primary macro-F1 from 0.626 to
  0.472.

Whether Arms 0 and 1 fail on the same classes cannot be determined from stored
artifacts. Their aggregate temporal-shift drops (Arm 0 0.630 -> 0.426, Arm 1
0.612 -> 0.445 on the full test splits) are similar in size to Arm 4's
(0.626 -> 0.472 on the subset), but those are not comparable measurements.

## Examples (filing-time Summary only; test subsets)

| Split | Id | True | Predicted | Summary |
|---|---|---|---|---|
| shift | 551463 | IDE | UI | "UI freeze on right click over a project on Project explorer" |
| shift | 577195 | IDE | UI | "JavaLeakTest.testTextEditorCloseOneOfTwo fails" |
| ID | 86081 | CVS | Team | "cvs progress report wildly inaccurate" |
| ID | 26748 | SWT | UI | "Java editor did not honour navigator resize" |
| ID | 89759 | Doc | User Assistance | "Bidirectional Support topic is missing" |

Several of these are arguably ambiguous from the title alone (a "UI freeze"
filed under IDE; an editor-resize bug labelled SWT). This is consistent with the
project's measured 17.81% label instability (`docs/DATASET_CARD.md`): the target
is the settled Component, which is not always inferable from filing-time text,
so part of the error mass here is label noise rather than model failure. That
proportion was not measured for Arm 4.

## What this analysis does not show

- Nothing about which of Arms 0, 1 or 4 is better on any class.
- Nothing about seed variance of these patterns (one seed).
- Nothing about Incubator for Arm 4.
- The cluster groupings are exploratory and post hoc.

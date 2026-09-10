"""Frozen, pre-registered long-tail policy for Eclipse Platform Component
classification.

This policy was decided on 2026-09-10 from the measured Phase 1 taxonomy
alone (`reports/phase1_platform.json`), BEFORE any model was trained and
BEFORE any model result existed. It must never be adjusted based on how
any arm performs. See docs/METHODOLOGY.md ("Long-Tail Policy") and
docs/EXPERIMENT_LOG.md for the full rationale.

Every model arm's evaluation code must import PRIMARY_CLASSES /
RARE_CLASSES / MINIMUM_SUPPORT_THRESHOLD from here (or call the functions
below) rather than recomputing a threshold or class list locally -- that
is what guarantees the policy is identical and cannot drift across Arm 0,
the encoder, the LLM arms, or LoRA.
"""

from __future__ import annotations

from triagebench.evaluation.metrics import ClassificationReport, classification_report

# Minimum total-dataset support (see reports/phase1_platform.json,
# component_taxonomy.counts) required for a class to be included in the
# PRIMARY macro-F1 figure used for headline model comparison. Chosen as a
# round, common threshold for per-class F1 to be minimally statistically
# meaningful, and because it exactly matches the boundary Phase 1's own
# inspection script already reports (`classes_below_50_support`) -- it is
# not tuned to produce a particular split of classes.
MINIMUM_SUPPORT_THRESHOLD = 50

# Frozen snapshot of reports/phase1_platform.json's
# component_taxonomy.counts (measured 2026-09-10, 122,496 Platform rows).
# Hardcoded rather than read live from the report file so the policy
# cannot silently shift if that file is ever regenerated; a test
# (test_long_tail_policy.py) cross-checks this snapshot against the live
# report and fails loudly on any drift instead of re-deriving the policy.
MEASURED_COMPONENT_SUPPORT: dict[str, int] = {
    "UI": 37307,
    "SWT": 25495,
    "Team": 7205,
    "Debug": 6920,
    "Releng": 6731,
    "Resources": 5495,
    "Text": 5126,
    "User Assistance": 5023,
    "IDE": 4415,
    "Ant": 4129,
    "CVS": 3301,
    "Update  (deprecated - use Eclipse>Equinox>p2)": 3278,
    "Runtime": 3122,
    "Compare": 2075,
    "Search": 1244,
    "Doc": 1123,
    "Website": 186,
    "Scripting": 108,
    "PMC": 106,
    "WebDAV": 92,
    "Incubator": 15,
}

ALL_CLASSES: frozenset[str] = frozenset(MEASURED_COMPONENT_SUPPORT)

PRIMARY_CLASSES: frozenset[str] = frozenset(
    c for c, n in MEASURED_COMPONENT_SUPPORT.items() if n >= MINIMUM_SUPPORT_THRESHOLD
)

RARE_CLASSES: frozenset[str] = frozenset(
    c for c, n in MEASURED_COMPONENT_SUPPORT.items() if n < MINIMUM_SUPPORT_THRESHOLD
)

assert PRIMARY_CLASSES | RARE_CLASSES == ALL_CLASSES
assert PRIMARY_CLASSES.isdisjoint(RARE_CLASSES)


def primary_macro_f1_report(y_true: list[str], y_pred: list[str]) -> ClassificationReport:
    """The PRIMARY headline metric for arm-vs-arm comparison: macro-F1
    computed over PRIMARY_CLASSES only. Examples whose *true* label is a
    rare class are excluded from this computation entirely (not
    relabeled, not merged into an "Other" bucket -- simply not part of
    this particular aggregate figure); a model's prediction being a rare
    class for some other (primary-labeled) example still counts as wrong
    for that example, same as any other incorrect prediction.
    """
    filtered_true, filtered_pred = [], []
    for t, p in zip(y_true, y_pred, strict=True):
        if t in PRIMARY_CLASSES:
            filtered_true.append(t)
            filtered_pred.append(p)
    if not filtered_true:
        raise ValueError("no examples with a primary-class true label -- cannot compute primary macro-F1")
    return classification_report(filtered_true, filtered_pred)


def full_micro_f1_report(y_true: list[str], y_pred: list[str]) -> ClassificationReport:
    """Micro-F1 (== accuracy for single-label multiclass) computed over
    ALL examples, rare classes included. Micro-F1 stays interpretable
    without a support threshold because it is a single pooled
    correct/total count -- each class's natural (small) frequency
    weights its contribution automatically, unlike macro-F1's unweighted
    per-class average, which is exactly why macro-F1 (not micro-F1)
    needs this policy at all.
    """
    return classification_report(y_true, y_pred)


def rare_class_report(y_true: list[str], y_pred: list[str]) -> dict[str, dict[str, float]]:
    """Per-class precision/recall/F1/support for rare classes only,
    computed from the SAME full (unfiltered) predictions used for
    full_micro_f1_report -- i.e. rare classes are always scored on
    exactly the same predictions as everything else, just reported
    separately due to their small-sample statistical unreliability.
    Never omitted, never silently folded into the primary figure.
    """
    full_report = classification_report(y_true, y_pred)
    return {c: full_report.per_class[c] for c in RARE_CLASSES if c in full_report.per_class}


def missing_primary_classes(y_true: list[str]) -> list[str]:
    """Primary classes with zero examples in this particular evaluation
    set -- a diagnostic, not an error: expected occasionally on small
    slices (e.g. the frontier paired subset), should be investigated if
    it happens on the full frozen test set."""
    present = set(y_true)
    return sorted(c for c in PRIMARY_CLASSES if c not in present)


def evaluate_with_policy(y_true: list[str], y_pred: list[str]) -> dict:
    """Convenience wrapper bundling the full policy-compliant evaluation:
    primary macro-F1, full micro-F1, rare-class secondary breakdown, and
    the missing-primary-class diagnostic, in one structured result every
    arm's evaluation script can produce identically."""
    primary = primary_macro_f1_report(y_true, y_pred)
    full = full_micro_f1_report(y_true, y_pred)
    return {
        "policy": {
            "minimum_support_threshold": MINIMUM_SUPPORT_THRESHOLD,
            "primary_classes": sorted(PRIMARY_CLASSES),
            "rare_classes": sorted(RARE_CLASSES),
        },
        "primary_macro_f1": primary.macro_f1,
        "primary_n_examples": primary.n,
        "full_micro_f1": full.micro_f1,
        "full_n_examples": full.n,
        "rare_class_metrics": rare_class_report(y_true, y_pred),
        "missing_primary_classes_in_eval_set": missing_primary_classes(y_true),
    }

"""Guards the pre-registered long-tail policy (docs/METHODOLOGY.md,
docs/EXPERIMENT_LOG.md): it must stay deterministic, must match the real
Phase 1 measurement it was derived from, and must never be recomputed or
adjusted based on any model's results.
"""

import json
from pathlib import Path

import pytest

from triagebench.evaluation.long_tail_policy import (
    ALL_CLASSES,
    MEASURED_COMPONENT_SUPPORT,
    MINIMUM_SUPPORT_THRESHOLD,
    PRIMARY_CLASSES,
    RARE_CLASSES,
    evaluate_with_policy,
    full_micro_f1_report,
    missing_primary_classes,
    primary_macro_f1_metric,
    primary_macro_f1_report,
    rare_class_report,
)
from triagebench.evaluation.metrics import bootstrap_ci, macro_f1_metric

REPO_ROOT = Path(__file__).resolve().parents[1]
PHASE1_PLATFORM_REPORT = REPO_ROOT / "reports" / "phase1_platform.json"


def test_threshold_is_fifty():
    assert MINIMUM_SUPPORT_THRESHOLD == 50


def test_primary_and_rare_partition_all_classes_exactly():
    assert PRIMARY_CLASSES | RARE_CLASSES == ALL_CLASSES
    assert PRIMARY_CLASSES.isdisjoint(RARE_CLASSES)
    assert len(ALL_CLASSES) == 21


def test_rare_classes_is_exactly_incubator():
    assert RARE_CLASSES == frozenset({"Incubator"})
    assert len(PRIMARY_CLASSES) == 20


def test_every_class_correctly_classified_by_its_own_measured_support():
    for c in PRIMARY_CLASSES:
        assert MEASURED_COMPONENT_SUPPORT[c] >= MINIMUM_SUPPORT_THRESHOLD
    for c in RARE_CLASSES:
        assert MEASURED_COMPONENT_SUPPORT[c] < MINIMUM_SUPPORT_THRESHOLD


def test_frozen_support_snapshot_matches_the_real_measured_report():
    # Guards against silent drift between the hardcoded policy snapshot
    # and reports/phase1_platform.json -- if the dataset were
    # re-extracted and counts changed, this must fail loudly rather than
    # let the policy silently disagree with reality.
    if not PHASE1_PLATFORM_REPORT.exists():
        pytest.skip("reports/phase1_platform.json not present in this checkout")
    live = json.loads(PHASE1_PLATFORM_REPORT.read_text())
    live_counts = live["component_taxonomy"]["counts"]
    assert live_counts == MEASURED_COMPONENT_SUPPORT


def test_policy_constants_are_immutable_frozensets():
    # frozenset (not set/list) is itself the guarantee that nothing
    # (including a future evaluation script for some later arm) can
    # silently mutate the class lists at runtime.
    assert isinstance(PRIMARY_CLASSES, frozenset)
    assert isinstance(RARE_CLASSES, frozenset)
    assert isinstance(ALL_CLASSES, frozenset)


def test_primary_macro_f1_excludes_rare_class_true_labels():
    # 100 correct "UI" predictions + 5 badly wrong "Incubator" predictions.
    # If Incubator leaked into the primary macro-F1's class average, this
    # would not be a perfect score; since it's excluded, it must be.
    y_true = ["UI"] * 100 + ["Incubator"] * 5
    y_pred = ["UI"] * 100 + ["SWT"] * 5  # every Incubator example wrong
    report = primary_macro_f1_report(y_true, y_pred)
    assert report.macro_f1 == 1.0
    assert "Incubator" not in report.per_class
    assert report.n == 100  # only the primary-labeled examples counted


def test_full_micro_f1_includes_rare_classes():
    y_true = ["UI"] * 100 + ["Incubator"] * 5
    y_pred = ["UI"] * 100 + ["SWT"] * 5
    report = full_micro_f1_report(y_true, y_pred)
    assert report.n == 105
    # 100 correct out of 105 total -- rare-class errors DO count here
    assert report.micro_f1 == pytest.approx(100 / 105)


def test_rare_class_report_contains_only_rare_classes():
    y_true = ["UI", "Incubator", "Incubator"]
    y_pred = ["UI", "Incubator", "UI"]
    report = rare_class_report(y_true, y_pred)
    assert set(report.keys()) == {"Incubator"}
    assert report["Incubator"]["support"] == 2
    assert report["Incubator"]["recall"] == pytest.approx(0.5)


def test_rare_class_report_empty_if_no_rare_examples_present():
    y_true = ["UI", "SWT"]
    y_pred = ["UI", "UI"]
    assert rare_class_report(y_true, y_pred) == {}


def test_missing_primary_classes_flags_absent_ones():
    y_true = ["UI"] * 10
    missing = missing_primary_classes(y_true)
    assert "SWT" in missing
    assert "UI" not in missing
    assert "Incubator" not in missing  # not a primary class, shouldn't be flagged as a "missing primary" one


def test_missing_primary_classes_empty_when_all_present():
    y_true = list(PRIMARY_CLASSES)
    assert missing_primary_classes(y_true) == []


def test_primary_macro_f1_raises_if_no_primary_examples():
    with pytest.raises(ValueError):
        primary_macro_f1_report(["Incubator"], ["Incubator"])


def test_evaluate_with_policy_structure_and_determinism():
    y_true = ["UI"] * 50 + ["SWT"] * 30 + ["Incubator"] * 3
    y_pred = ["UI"] * 45 + ["SWT"] * 5 + ["SWT"] * 30 + ["Incubator"] * 3
    result1 = evaluate_with_policy(y_true, y_pred)
    result2 = evaluate_with_policy(y_true, y_pred)
    assert result1 == result2  # fully deterministic given the same inputs

    assert result1["policy"]["minimum_support_threshold"] == 50
    assert "Incubator" in result1["policy"]["rare_classes"]
    assert "Incubator" not in result1["policy"]["primary_classes"]
    assert result1["full_n_examples"] == 83
    assert result1["primary_n_examples"] == 80
    assert "Incubator" in result1["rare_class_metrics"]


def test_primary_macro_f1_metric_matches_the_report_exactly():
    # Regression test for a real bug caught during Arm 0's first run: a
    # bootstrap CI computed with the generic (unfiltered) macro_f1_metric
    # silently disagreed with the policy-filtered point estimate from
    # primary_macro_f1_report, because they measure different things
    # (21-class vs. 20-class macro average). primary_macro_f1_metric must
    # always agree with primary_macro_f1_report's .macro_f1 exactly, and
    # must differ from the generic (unfiltered) metric whenever a rare
    # class is present with errors -- proving it's actually filtering.
    y_true = ["UI"] * 60 + ["SWT"] * 40 + ["Incubator"] * 10
    y_pred = ["UI"] * 60 + ["SWT"] * 30 + ["UI"] * 10 + ["SWT"] * 10  # SWT hurt, Incubator all wrong

    report = primary_macro_f1_report(y_true, y_pred)
    assert primary_macro_f1_metric(y_true, y_pred) == report.macro_f1

    unfiltered = macro_f1_metric(y_true, y_pred)
    assert primary_macro_f1_metric(y_true, y_pred) != unfiltered, (
        "primary_macro_f1_metric must differ from the unfiltered metric when a rare class has errors "
        "-- if they're equal, the filtering isn't actually happening"
    )


def test_bootstrap_ci_of_primary_macro_f1_uses_the_filtered_metric():
    # The bootstrap CI's own point_estimate field must match the actual
    # reported primary macro-F1 -- this is the literal shape of the bug
    # that shipped in Arm 0's first run before being caught.
    y_true = ["UI"] * 60 + ["SWT"] * 40 + ["Incubator"] * 10
    y_pred = ["UI"] * 55 + ["SWT"] * 5 + ["SWT"] * 40 + ["UI"] * 10
    report = primary_macro_f1_report(y_true, y_pred)
    ci = bootstrap_ci(y_true, y_pred, primary_macro_f1_metric, n_resamples=50, seed=0)
    assert ci["point_estimate"] == report.macro_f1


def test_policy_is_identical_across_repeated_imports():
    # Simulates two different arms' evaluation scripts each importing the
    # policy module independently -- they must see byte-identical class
    # lists, not independently-recomputed ones that could drift.
    import importlib

    from triagebench.evaluation import long_tail_policy as mod1
    mod2 = importlib.import_module("triagebench.evaluation.long_tail_policy")
    assert mod1.PRIMARY_CLASSES == mod2.PRIMARY_CLASSES
    assert mod1.RARE_CLASSES == mod2.RARE_CLASSES
    assert mod1.MINIMUM_SUPPORT_THRESHOLD == mod2.MINIMUM_SUPPORT_THRESHOLD

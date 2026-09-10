import pytest

from triagebench.evaluation.metrics import (
    bootstrap_ci,
    classification_report,
    macro_f1_metric,
    micro_f1_metric,
)


def test_perfect_predictions_give_f1_of_one():
    y_true = ["UI", "Core", "UI", "Debug", "Core"]
    y_pred = list(y_true)
    report = classification_report(y_true, y_pred)
    assert report.micro_f1 == 1.0
    assert report.macro_f1 == 1.0
    for c in report.per_class.values():
        assert c["precision"] == 1.0
        assert c["recall"] == 1.0
        assert c["f1"] == 1.0


def test_all_wrong_predictions_give_f1_of_zero():
    y_true = ["UI", "Core"]
    y_pred = ["Core", "UI"]
    report = classification_report(y_true, y_pred)
    assert report.micro_f1 == 0.0
    assert report.macro_f1 == 0.0


def test_macro_f1_weights_classes_equally_regardless_of_support():
    # 90 easy "Core" examples (all correct) + 10 "Rare" examples (all
    # misclassified as Core). Core: precision=90/100=0.9, recall=1.0,
    # f1=0.947368...; Rare: precision=0 (no predictions), recall=0, f1=0.
    y_true = ["Core"] * 90 + ["Rare"] * 10
    y_pred = ["Core"] * 90 + ["Core"] * 10
    report = classification_report(y_true, y_pred)
    # micro (=accuracy here) is dominated by the head class
    assert report.micro_f1 == pytest.approx(0.90)
    # macro treats Core and Rare equally regardless of their support
    assert report.macro_f1 == pytest.approx((0.9473684210526316 + 0.0) / 2)
    assert report.macro_f1 < report.micro_f1


def test_confusion_matrix_counts_are_correct():
    y_true = ["UI", "UI", "Core"]
    y_pred = ["UI", "Core", "Core"]
    report = classification_report(y_true, y_pred)
    assert report.confusion_matrix["UI"]["UI"] == 1
    assert report.confusion_matrix["UI"]["Core"] == 1
    assert report.confusion_matrix["Core"]["Core"] == 1


def test_hallucinated_class_not_in_true_labels_is_tracked_and_counted_wrong():
    y_true = ["UI", "Core"]
    y_pred = ["UI", "NotARealComponent"]
    report = classification_report(y_true, y_pred)
    assert report.unknown_predicted_labels == ["NotARealComponent"]
    assert report.per_class["Core"]["recall"] == 0.0
    # the hallucinated label must not silently inflate any real class's FP-derived precision
    assert "NotARealComponent" not in report.per_class


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        classification_report(["UI"], ["UI", "Core"])


def test_empty_input_raises():
    with pytest.raises(ValueError):
        classification_report([], [])


def test_bootstrap_ci_contains_point_estimate_and_is_ordered():
    y_true = ["UI", "Core", "UI", "Debug", "Core", "UI", "Core"] * 5
    y_pred = list(y_true)
    y_pred[0] = "Core"  # one wrong prediction
    result = bootstrap_ci(y_true, y_pred, micro_f1_metric, n_resamples=200, seed=42)
    assert result["ci_low"] <= result["point_estimate"] <= result["ci_high"]
    assert 0.0 <= result["ci_low"] <= 1.0
    assert 0.0 <= result["ci_high"] <= 1.0


def test_bootstrap_ci_is_deterministic_given_seed():
    y_true = ["UI", "Core", "Debug"] * 10
    y_pred = ["UI", "Core", "UI"] * 10
    r1 = bootstrap_ci(y_true, y_pred, macro_f1_metric, n_resamples=100, seed=7)
    r2 = bootstrap_ci(y_true, y_pred, macro_f1_metric, n_resamples=100, seed=7)
    assert r1 == r2


def test_bootstrap_ci_narrows_on_perfect_agreement():
    y_true = ["UI", "Core", "Debug"] * 20
    y_pred = list(y_true)
    result = bootstrap_ci(y_true, y_pred, micro_f1_metric, n_resamples=200, seed=0)
    assert result["ci_low"] == result["ci_high"] == 1.0

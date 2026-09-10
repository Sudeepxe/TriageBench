"""Evaluation metrics shared by every model arm, so scoring logic is
implemented once and results are comparable across arms.

Deliberately dependency-light (no sklearn requirement for the core
computation) so it can run in the classical-ML environment as well as any
minimal inference environment.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class ClassificationReport:
    micro_f1: float
    macro_f1: float
    per_class: dict[str, dict[str, float]]
    confusion_matrix: dict[str, dict[str, int]]
    support: dict[str, int]
    n: int
    unknown_predicted_labels: list[str] = field(default_factory=list)


def _prf1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def classification_report(y_true: list[str], y_pred: list[str]) -> ClassificationReport:
    """Compute micro/macro F1, per-class precision/recall/F1, and a
    confusion matrix, for single-label multi-class predictions.

    Classes are derived from y_true (the label space the model is actually
    being evaluated against). A prediction naming a class absent from
    y_true (a "hallucinated class", relevant for LLM arms) is tracked in
    `unknown_predicted_labels` and always counted as wrong for its true
    class's recall, rather than silently ignored or crashing.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(f"y_true and y_pred must have equal length: {len(y_true)} != {len(y_pred)}")
    if len(y_true) == 0:
        raise ValueError("cannot compute a classification report on zero examples")

    classes = sorted(set(y_true))
    class_set = set(classes)
    support = Counter(y_true)

    confusion: dict[str, dict[str, int]] = {c: Counter() for c in classes}
    unknown_predicted: set[str] = set()

    tp_by_class = Counter()
    fp_by_class = Counter()
    fn_by_class = Counter()

    for true_label, pred_label in zip(y_true, y_pred, strict=True):
        confusion[true_label][pred_label] += 1
        if pred_label not in class_set:
            unknown_predicted.add(pred_label)
        if pred_label == true_label:
            tp_by_class[true_label] += 1
        else:
            fn_by_class[true_label] += 1
            if pred_label in class_set:
                fp_by_class[pred_label] += 1

    per_class: dict[str, dict[str, float]] = {}
    for c in classes:
        precision, recall, f1 = _prf1(tp_by_class[c], fp_by_class[c], fn_by_class[c])
        per_class[c] = {"precision": precision, "recall": recall, "f1": f1, "support": support[c]}

    total_correct = sum(tp_by_class.values())
    micro_f1 = total_correct / len(y_true)  # for single-label multiclass, micro-F1 == accuracy
    macro_f1 = sum(per_class[c]["f1"] for c in classes) / len(classes) if classes else 0.0

    return ClassificationReport(
        micro_f1=micro_f1,
        macro_f1=macro_f1,
        per_class=per_class,
        confusion_matrix={c: dict(confusion[c]) for c in classes},
        support=dict(support),
        n=len(y_true),
        unknown_predicted_labels=sorted(unknown_predicted),
    )


def bootstrap_ci(
    y_true: list[str],
    y_pred: list[str],
    metric_fn,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> dict[str, float]:
    """Percentile bootstrap CI for a scalar metric computed by
    `metric_fn(y_true_sample, y_pred_sample) -> float`.

    Resamples (with replacement) paired (y_true, y_pred) at the example
    level, preserving the true/pred pairing for each resampled example.
    """
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have equal length")
    n = len(y_true)
    if n == 0:
        raise ValueError("cannot bootstrap a metric on zero examples")

    rng = random.Random(seed)
    point_estimate = metric_fn(y_true, y_pred)

    resample_scores = []
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        sample_true = [y_true[i] for i in idx]
        sample_pred = [y_pred[i] for i in idx]
        resample_scores.append(metric_fn(sample_true, sample_pred))

    resample_scores.sort()
    alpha = 1 - confidence
    lo_idx = int((alpha / 2) * n_resamples)
    hi_idx = int((1 - alpha / 2) * n_resamples) - 1
    hi_idx = min(hi_idx, n_resamples - 1)

    return {
        "point_estimate": point_estimate,
        "ci_low": resample_scores[lo_idx],
        "ci_high": resample_scores[hi_idx],
        "n_resamples": n_resamples,
        "confidence": confidence,
    }


def macro_f1_metric(y_true: list[str], y_pred: list[str]) -> float:
    return classification_report(y_true, y_pred).macro_f1


def micro_f1_metric(y_true: list[str], y_pred: list[str]) -> float:
    return classification_report(y_true, y_pred).micro_f1

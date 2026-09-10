#!/usr/bin/env python3
"""Arm 0: TF-IDF + Logistic Regression, across the mandatory data-efficiency
regimes (50/200/1000/full examples per class), evaluated on the frozen
test_in_distribution and test_temporal_shift splits with the frozen
long-tail policy.

Config selection (TF-IDF/LogReg hyperparameters) is a small, cheap,
validation-only pilot performed BEFORE looking at any test result -- see
`select_config()`. The test sets are never touched until final evaluation.

LogisticRegression with the (default) lbfgs solver is a deterministic
convex optimization given a fixed random_state -- repeating it across
multiple seeds would reproduce the identical model and waste compute, so
Arm 0 uses a single fixed seed (documented here and in the result JSON)
rather than the 3-seed protocol used for stochastically-trained arms.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from triagebench.data.splitting import stratified_sample_per_class  # noqa: E402
from triagebench.evaluation.long_tail_policy import (  # noqa: E402
    MINIMUM_SUPPORT_THRESHOLD,
    evaluate_with_policy,
    primary_macro_f1_metric,
)
from triagebench.evaluation.metrics import bootstrap_ci, micro_f1_metric  # noqa: E402
from triagebench.models.baseline import BaselineConfig, build_pipeline, combine_text  # noqa: E402

ARM_SEED = 0  # fixed; see module docstring for why Arm 0 doesn't use 3 seeds
CANDIDATE_C = [0.1, 1.0, 10.0]  # small, cheap pilot grid -- decided before any run


def load_model_ready(path: Path) -> dict[str, dict]:
    rows = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            rows[r["id"]] = r
    return rows


def texts_and_labels(ids: list[str], rows: dict[str, dict]) -> tuple[list[str], list[str]]:
    texts = [combine_text(rows[i]["summary"], rows[i]["description"]) for i in ids]
    labels = [rows[i]["component"] for i in ids]
    return texts, labels


def select_config(rows: dict[str, dict], train_ids: list[str], val_ids: list[str]) -> tuple[BaselineConfig, dict]:
    """Small validated pilot: try each candidate C on the FULL training
    set, score by PRIMARY macro-F1 on validation (never test), pick the
    best. This is the only place any hyperparameter decision is made,
    and it happens strictly before any test-set evaluation.
    """
    train_texts, train_labels = texts_and_labels(train_ids, rows)
    val_texts, val_labels = texts_and_labels(val_ids, rows)

    pilot_results = []
    for c in CANDIDATE_C:
        config = BaselineConfig(C=c, random_state=ARM_SEED)
        pipeline = build_pipeline(config)
        t0 = time.time()
        pipeline.fit(train_texts, train_labels)
        fit_seconds = time.time() - t0
        val_pred = list(pipeline.predict(val_texts))
        val_primary_macro_f1 = evaluate_with_policy(val_labels, val_pred)["primary_macro_f1"]
        pilot_results.append(
            {"C": c, "val_primary_macro_f1": val_primary_macro_f1, "fit_seconds": round(fit_seconds, 1)}
        )
        print(f"  pilot C={c}: val primary macro-F1={val_primary_macro_f1:.4f} (fit {fit_seconds:.1f}s)")

    best = max(pilot_results, key=lambda r: r["val_primary_macro_f1"])
    print(f"  selected C={best['C']} (val primary macro-F1={best['val_primary_macro_f1']:.4f})")
    return BaselineConfig(C=best["C"], random_state=ARM_SEED), {"pilot_results": pilot_results, "selected_C": best["C"]}


def measure_latency(pipeline, sample_texts: list[str], n_single: int = 200, batch_size: int = 64) -> dict:
    # Single-example latency (warm-up excluded)
    warmup = min(10, len(sample_texts))
    for t in sample_texts[:warmup]:
        pipeline.predict([t])
    single_texts = (sample_texts * (n_single // max(1, len(sample_texts)) + 1))[:n_single]
    singles_ms = []
    for t in single_texts:
        t0 = time.perf_counter()
        pipeline.predict([t])
        singles_ms.append((time.perf_counter() - t0) * 1000)
    singles_ms.sort()

    def pct(p: float) -> float:
        idx = min(len(singles_ms) - 1, int(round(p * (len(singles_ms) - 1))))
        return singles_ms[idx]

    # Batched throughput
    batch = (sample_texts * (batch_size // max(1, len(sample_texts)) + 1))[:batch_size]
    t0 = time.perf_counter()
    pipeline.predict(batch)
    batch_elapsed = time.perf_counter() - t0
    throughput = batch_size / batch_elapsed if batch_elapsed > 0 else float("inf")

    return {
        "environment": "Apple M5 MacBook Pro (local CPU, scikit-learn)",
        "n_single_measurements": len(singles_ms),
        "p50_ms": round(pct(0.50), 3),
        "p95_ms": round(pct(0.95), 3),
        "p99_ms": round(pct(0.99), 3),
        "batch_size": batch_size,
        "batch_throughput_per_sec": round(throughput, 1),
    }


def evaluate_split(pipeline, ids: list[str], rows: dict[str, dict], bootstrap_resamples: int) -> dict:
    texts, labels = texts_and_labels(ids, rows)
    preds = list(pipeline.predict(texts))
    policy_report = evaluate_with_policy(labels, preds)

    primary_ci = bootstrap_ci(labels, preds, primary_macro_f1_metric, n_resamples=bootstrap_resamples, seed=ARM_SEED)
    micro_ci = bootstrap_ci(labels, preds, micro_f1_metric, n_resamples=bootstrap_resamples, seed=ARM_SEED)

    stable_ids = [i for i in ids if rows[i]["label_stable"]]
    unstable_ids = [i for i in ids if not rows[i]["label_stable"]]
    stable_report = None
    if stable_ids:
        s_texts, s_labels = texts_and_labels(stable_ids, rows)
        s_preds = list(pipeline.predict(s_texts))
        stable_report = evaluate_with_policy(s_labels, s_preds)

    return {
        "n": len(ids),
        "policy_evaluation": policy_report,
        "primary_macro_f1_bootstrap_ci": primary_ci,
        "full_micro_f1_bootstrap_ci": micro_ci,
        "n_stable_label": len(stable_ids),
        "n_unstable_label": len(unstable_ids),
        "stable_label_slice_evaluation": stable_report,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-ready", default="data/processed/platform_model_ready.jsonl", type=Path)
    ap.add_argument("--splits", default="reports/results/splits/platform_splits.json", type=Path)
    ap.add_argument("--experiments-config", default="configs/experiments.yaml", type=Path)
    ap.add_argument("--output-dir", default="reports/results/arm0", type=Path)
    ap.add_argument("--bootstrap-resamples", type=int, default=None, help="override configs/experiments.yaml value")
    args = ap.parse_args()

    exp_cfg = yaml.safe_load(args.experiments_config.read_text())
    bootstrap_resamples = args.bootstrap_resamples or exp_cfg["statistics"]["bootstrap_resamples"]

    print("Loading model-ready data and frozen splits...")
    rows = load_model_ready(args.model_ready)
    splits = json.loads(args.splits.read_text())
    train_ids_all = splits["train_ids"]
    val_ids = splits["val_ids"]
    test_id_ids = splits["test_in_distribution_ids"]
    test_shift_ids = splits["test_temporal_shift_ids"]

    print(f"train={len(train_ids_all)} val={len(val_ids)} test_id={len(test_id_ids)} test_shift={len(test_shift_ids)}")

    print("\n=== Config selection pilot (validation only, before any test evaluation) ===")
    config, pilot_meta = select_config(rows, train_ids_all, val_ids)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    regimes = [r["examples_per_class"] for r in exp_cfg["data_efficiency_regimes"]]
    for regime in regimes:
        n_per_class = None if regime == "full" else int(regime)
        regime_label = "full" if n_per_class is None else str(n_per_class)
        print(f"\n=== Regime: {regime_label} examples/class ===")

        train_ids = stratified_sample_per_class(
            [{"id": i, "component": rows[i]["component"]} for i in train_ids_all],
            "component",
            n_per_class,
            seed=exp_cfg["split"]["split_seed"],
        )
        train_texts, train_labels = texts_and_labels(train_ids, rows)
        print(f"  training on {len(train_ids)} examples ({len(set(train_labels))} classes present)")

        pipeline = build_pipeline(config)
        t0 = time.time()
        pipeline.fit(train_texts, train_labels)
        train_seconds = time.time() - t0
        print(f"  fit in {train_seconds:.1f}s")

        print("  evaluating on val (sanity check)...")
        val_eval = evaluate_split(pipeline, val_ids, rows, bootstrap_resamples)
        print(f"    val primary macro-F1={val_eval['policy_evaluation']['primary_macro_f1']:.4f}")

        print("  evaluating on test_in_distribution (FROZEN)...")
        test_id_eval = evaluate_split(pipeline, test_id_ids, rows, bootstrap_resamples)
        print(f"    test_id primary macro-F1={test_id_eval['policy_evaluation']['primary_macro_f1']:.4f}")

        print("  evaluating on test_temporal_shift (FROZEN)...")
        test_shift_eval = evaluate_split(pipeline, test_shift_ids, rows, bootstrap_resamples)
        print(f"    test_shift primary macro-F1={test_shift_eval['policy_evaluation']['primary_macro_f1']:.4f}")

        print("  measuring latency...")
        latency = measure_latency(pipeline, train_texts[:50] or ["placeholder text"])

        result = {
            "status": "MEASURED",
            "arm": "arm0_tfidf_logreg",
            "regime": regime_label,
            "n_per_class": n_per_class,
            "seed": ARM_SEED,
            "seed_note": (
                "LogisticRegression(lbfgs) is a deterministic convex solver given a fixed "
                "random_state -- a single seed is used rather than 3, since repeating would "
                "reproduce an identical model."
            ),
            "hyperparameter_selection": pilot_meta,
            "config": {
                "max_features": config.max_features,
                "ngram_range": list(config.ngram_range),
                "min_df": config.min_df,
                "sublinear_tf": config.sublinear_tf,
                "C": config.C,
                "class_weight": config.class_weight,
            },
            "long_tail_policy": {"minimum_support_threshold": MINIMUM_SUPPORT_THRESHOLD},
            "n_train_examples": len(train_ids),
            "n_train_classes_present": len(set(train_labels)),
            "train_seconds": round(train_seconds, 1),
            "val": val_eval,
            "test_in_distribution": test_id_eval,
            "test_temporal_shift": test_shift_eval,
            "latency": latency,
            "hardware": platform.platform(),
            "bootstrap_resamples": bootstrap_resamples,
        }

        out_path = args.output_dir / f"regime_{regime_label}.json"
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"  wrote {out_path}")


if __name__ == "__main__":
    main()

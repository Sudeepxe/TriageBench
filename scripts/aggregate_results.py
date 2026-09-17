#!/usr/bin/env python3
"""Aggregate every model arm's per-regime/per-seed result files into one
machine-readable data-efficiency summary. Never hand-types a number --
every value here is read directly from an existing reports/results/*.json
artifact.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

REGIME_ORDER = ["50", "200", "1000", "full"]


def mean_stdev(values: list[float]) -> dict:
    return {
        "mean": statistics.mean(values),
        "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "values": values,
    }


def load_arm0(path: Path) -> dict:
    """Arm 0 has one result per regime (deterministic solver -> 1 seed)."""
    by_regime = {}
    for regime in REGIME_ORDER:
        f = path / f"regime_{regime}.json"
        if not f.exists():
            continue
        r = json.loads(f.read_text())
        id_eval = r["test_in_distribution"]["policy_evaluation"]
        shift_eval = r["test_temporal_shift"]["policy_evaluation"]
        stable_eval = r["test_in_distribution"]["stable_label_slice_evaluation"]
        by_regime[regime] = {
            "n_seeds": 1,
            "n_train_examples": r["n_train_examples"],
            "test_in_distribution": {
                "primary_macro_f1": mean_stdev([id_eval["primary_macro_f1"]]),
                "full_micro_f1": mean_stdev([id_eval["full_micro_f1"]]),
                "bootstrap_ci": r["test_in_distribution"]["primary_macro_f1_bootstrap_ci"],
                "stable_label_slice_primary_macro_f1": stable_eval["primary_macro_f1"] if stable_eval else None,
            },
            "test_temporal_shift": {
                "primary_macro_f1": mean_stdev([shift_eval["primary_macro_f1"]]),
                "full_micro_f1": mean_stdev([shift_eval["full_micro_f1"]]),
            },
            "rare_class_metrics": id_eval["rare_class_metrics"],
            "latency_p50_ms": r["latency"]["p50_ms"],
            "latency_p95_ms": r["latency"]["p95_ms"],
            "train_seconds": r["train_seconds"],
        }
    return by_regime


def load_arm1(path: Path) -> dict:
    """Arm 1 has up to 3 seeds per regime; aggregate mean/stdev."""
    by_regime: dict[str, list[dict]] = {r: [] for r in REGIME_ORDER}
    for f in sorted(path.glob("regime_*_seed*.json")):
        r = json.loads(f.read_text())
        regime = str(r["regime"])
        by_regime.setdefault(regime, []).append(r)

    result = {}
    for regime, runs in by_regime.items():
        if not runs:
            continue
        id_vals = [r["test_in_distribution"]["policy_evaluation"]["primary_macro_f1"] for r in runs]
        id_micro = [r["test_in_distribution"]["policy_evaluation"]["full_micro_f1"] for r in runs]
        shift_vals = [r["test_temporal_shift"]["policy_evaluation"]["primary_macro_f1"] for r in runs]
        shift_micro = [r["test_temporal_shift"]["policy_evaluation"]["full_micro_f1"] for r in runs]
        stable_vals = [
            r["test_in_distribution"]["stable_label_slice_evaluation"]["primary_macro_f1"]
            for r in runs
            if r["test_in_distribution"]["stable_label_slice_evaluation"]
        ]
        result[regime] = {
            "n_seeds": len(runs),
            "n_train_examples": runs[0]["n_train_examples"],
            "test_in_distribution": {
                "primary_macro_f1": mean_stdev(id_vals),
                "full_micro_f1": mean_stdev(id_micro),
                "stable_label_slice_primary_macro_f1": statistics.mean(stable_vals) if stable_vals else None,
            },
            "test_temporal_shift": {
                "primary_macro_f1": mean_stdev(shift_vals),
                "full_micro_f1": mean_stdev(shift_micro),
            },
            "rare_class_metrics": runs[0]["test_in_distribution"]["policy_evaluation"]["rare_class_metrics"],
            "latency_p50_ms": runs[0]["latency"]["p50_ms"],
            "latency_p95_ms": runs[0]["latency"]["p95_ms"],
            "train_seconds_raw": [r["train_seconds"] for r in runs],
            "train_seconds_note": (
                "Some full-regime values include macOS sleep-interruption wall-clock inflation; "
                "see docs/EXPERIMENT_LOG.md EXP-005 for corrected active-compute estimates."
            ),
        }
    return result


def load_llm_arm(path: Path) -> dict:
    """Arms 2/3 have no data-efficiency regime (zero-shot/frozen-base) --
    just one result per frozen test split, on the fixed paired subset."""
    result = {}
    for split in ["test_in_distribution", "test_temporal_shift"]:
        f = path / f"{split}.json"
        if not f.exists():
            continue
        r = json.loads(f.read_text())
        result[split] = {
            "eval_subset_size": r["eval_subset_size"],
            "primary_macro_f1": r["policy_evaluation"]["primary_macro_f1"],
            "full_micro_f1": r["policy_evaluation"]["full_micro_f1"],
            "primary_macro_f1_bootstrap_ci": r["primary_macro_f1_bootstrap_ci"],
            "n_unparseable_predictions": r["n_unparseable_predictions"],
            "unparseable_rate_pct": r["unparseable_rate_pct"],
            "latency_p50_ms": r["latency"]["p50_ms"],
        }
    return result


def load_arm4(path: Path) -> dict:
    """Arm 4 (QLoRA fine-tune) has training metadata in
    train_regime_{regime}_seed{seed}.json (loss/runtime/memory -- an LM
    loss proxy, not a classification metric) and classification results
    in eval_regime_{regime}_seed{seed}_{split}.json (generated on the
    SAME fixed 500-example paired subset as Arms 2/3), written by
    separate scripts (train_lora.py, evaluate_lora.py) since training
    and generation-based evaluation are independent, resumable steps."""
    by_regime: dict[str, list[dict]] = {r: [] for r in REGIME_ORDER}
    for train_f in sorted(path.glob("train_regime_*_seed*.json")):
        train_r = json.loads(train_f.read_text())
        regime = str(train_r["regime"])
        seed = train_r["seed"]
        id_f = path / f"eval_regime_{regime}_seed{seed}_test_in_distribution.json"
        shift_f = path / f"eval_regime_{regime}_seed{seed}_test_temporal_shift.json"
        if not (id_f.exists() and shift_f.exists()):
            continue  # trained but not yet evaluated
        id_r = json.loads(id_f.read_text())
        shift_r = json.loads(shift_f.read_text())
        by_regime.setdefault(regime, []).append(
            {"train": train_r, "id_eval": id_r, "shift_eval": shift_r}
        )

    result = {}
    for regime, runs in by_regime.items():
        if not runs:
            continue
        id_vals = [r["id_eval"]["policy_evaluation"]["primary_macro_f1"] for r in runs]
        id_micro = [r["id_eval"]["policy_evaluation"]["full_micro_f1"] for r in runs]
        shift_vals = [r["shift_eval"]["policy_evaluation"]["primary_macro_f1"] for r in runs]
        shift_micro = [r["shift_eval"]["policy_evaluation"]["full_micro_f1"] for r in runs]
        result[regime] = {
            "n_seeds": len(runs),
            "n_train_examples": runs[0]["train"]["n_train_examples"],
            "test_in_distribution": {
                "primary_macro_f1": mean_stdev(id_vals),
                "full_micro_f1": mean_stdev(id_micro),
                "eval_subset_size": runs[0]["id_eval"]["eval_subset_size"],
                "unparseable_rate_pct": [r["id_eval"]["unparseable_rate_pct"] for r in runs],
            },
            "test_temporal_shift": {
                "primary_macro_f1": mean_stdev(shift_vals),
                "full_micro_f1": mean_stdev(shift_micro),
                "eval_subset_size": runs[0]["shift_eval"]["eval_subset_size"],
                "unparseable_rate_pct": [r["shift_eval"]["unparseable_rate_pct"] for r in runs],
            },
            "rare_class_metrics": runs[0]["id_eval"]["policy_evaluation"]["rare_class_metrics"],
            "latency_p50_ms": runs[0]["id_eval"]["latency"]["p50_ms"],
            "latency_p95_ms": runs[0]["id_eval"]["latency"]["p95_ms"],
            "train_seconds_raw": [r["train"]["train_seconds"] for r in runs],
            "train_seconds_note": (
                "Some values include macOS Clamshell/Maintenance-Sleep wall-clock "
                "inflation from long unattended runs (caffeinate -i does not prevent "
                "lid-closed sleep) -- see docs/EXPERIMENT_LOG.md EXP-007 for the "
                "pmset -g log diagnosis and corrected active-compute estimates."
            ),
            "peak_memory_gb_raw": [r["train"]["peak_memory_gb"] for r in runs],
            "note_statistical_power": (
                "Evaluated on the same fixed 500-example paired subset as Arms 2/3, "
                "not the full test split -- lower statistical power than Arm 0/1."
            ),
        }
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", default="reports/results", type=Path)
    ap.add_argument("--output", default="reports/results/data_efficiency/summary.json", type=Path)
    args = ap.parse_args()

    summary = {
        "status": "MEASURED",
        "regime_order": REGIME_ORDER,
        "arms": {
            "arm0_tfidf_logreg": load_arm0(args.results_dir / "arm0"),
            "arm1_distilbert_finetune": load_arm1(args.results_dir / "arm1"),
            "arm4_qlora_finetune": load_arm4(args.results_dir / "arm4"),
        },
        "llm_arms_paired_subset": {
            "note": (
                "Arms 2/3 have no training data regime (frozen base model, zero-shot "
                "prompting only) and are evaluated on a fixed 500-example random subset "
                "of each frozen test split, not the full set -- see configs/experiments.yaml "
                "llm_arms and docs/EXPERIMENT_LOG.md for the pre-registered methodology."
            ),
            "arm2_llm_base_naive_prompt": load_llm_arm(args.results_dir / "arm2"),
            "arm3_llm_prompted_engineered": load_llm_arm(args.results_dir / "arm3"),
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")

    for arm_name, by_regime in summary["arms"].items():
        print(f"\n{arm_name}:")
        for regime in REGIME_ORDER:
            if regime not in by_regime:
                continue
            d = by_regime[regime]
            idf = d["test_in_distribution"]["primary_macro_f1"]
            shf = d["test_temporal_shift"]["primary_macro_f1"]
            print(
                f"  {regime:>5}/class (n_seeds={d['n_seeds']}): "
                f"test_ID={idf['mean']:.4f}+/-{idf['stdev']:.4f}  "
                f"test_shift={shf['mean']:.4f}+/-{shf['stdev']:.4f}"
            )

    print("\nllm_arms_paired_subset (500-example random subset, no training regime):")
    for arm_name in ["arm2_llm_base_naive_prompt", "arm3_llm_prompted_engineered"]:
        by_split = summary["llm_arms_paired_subset"][arm_name]
        print(f"  {arm_name}:")
        for split, d in by_split.items():
            print(
                f"    {split}: primary_macro_f1={d['primary_macro_f1']:.4f} "
                f"unparseable={d['n_unparseable_predictions']}/{d['eval_subset_size']} "
                f"({d['unparseable_rate_pct']}%)"
            )


if __name__ == "__main__":
    main()

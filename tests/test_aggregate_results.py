"""Verifies the aggregation logic on synthetic result files -- never
touches the real (large, gitignored) reports/results/arm* directories,
so this stays fast and independent of whatever experiments have run."""

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

_spec = importlib.util.spec_from_file_location("aggregate_results", REPO_ROOT / "scripts" / "aggregate_results.py")
aggregate_results = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aggregate_results)


def _fake_arm1_result(regime, seed, id_f1, shift_f1, micro_f1=0.5, shift_micro=0.4):
    return {
        "regime": regime,
        "n_train_examples": 100,
        "test_in_distribution": {
            "policy_evaluation": {
                "primary_macro_f1": id_f1, "full_micro_f1": micro_f1, "rare_class_metrics": {},
            },
            "stable_label_slice_evaluation": {"primary_macro_f1": id_f1 + 0.01},
        },
        "test_temporal_shift": {
            "policy_evaluation": {"primary_macro_f1": shift_f1, "full_micro_f1": shift_micro},
        },
        "latency": {"p50_ms": 1.0, "p95_ms": 2.0},
        "train_seconds": 10.0,
    }


def test_load_arm1_aggregates_multiple_seeds(tmp_path):
    arm1_dir = tmp_path / "arm1"
    arm1_dir.mkdir()
    for seed, val in enumerate([0.40, 0.42, 0.44]):
        (arm1_dir / f"regime_50_seed{seed}.json").write_text(
            json.dumps(_fake_arm1_result("50", seed, val, val - 0.1))
        )

    result = aggregate_results.load_arm1(arm1_dir)
    assert result["50"]["n_seeds"] == 3
    assert abs(result["50"]["test_in_distribution"]["primary_macro_f1"]["mean"] - 0.42) < 1e-9
    assert result["50"]["test_in_distribution"]["primary_macro_f1"]["stdev"] > 0
    assert len(result["50"]["test_in_distribution"]["primary_macro_f1"]["values"]) == 3


def test_mean_stdev_single_value_has_zero_stdev():
    result = aggregate_results.mean_stdev([0.5])
    assert result["mean"] == 0.5
    assert result["stdev"] == 0.0


def test_load_llm_arm_reads_per_split_files(tmp_path):
    arm2_dir = tmp_path / "arm2"
    arm2_dir.mkdir()
    fake = {
        "eval_subset_size": 500,
        "policy_evaluation": {"primary_macro_f1": 0.15, "full_micro_f1": 0.2},
        "primary_macro_f1_bootstrap_ci": {"ci_low": 0.1, "ci_high": 0.2},
        "n_unparseable_predictions": 100,
        "unparseable_rate_pct": 20.0,
        "latency": {"p50_ms": 150.0},
    }
    (arm2_dir / "test_in_distribution.json").write_text(json.dumps(fake))
    result = aggregate_results.load_llm_arm(arm2_dir)
    assert "test_in_distribution" in result
    assert "test_temporal_shift" not in result  # file doesn't exist -- not silently faked
    assert result["test_in_distribution"]["primary_macro_f1"] == 0.15
    assert result["test_in_distribution"]["unparseable_rate_pct"] == 20.0


def test_load_arm0_reads_single_seed_per_regime(tmp_path):
    arm0_dir = tmp_path / "arm0"
    arm0_dir.mkdir()
    fake = {
        "n_train_examples": 100,
        "test_in_distribution": {
            "policy_evaluation": {
                "primary_macro_f1": 0.5, "full_micro_f1": 0.6,
                "rare_class_metrics": {},
            },
            "primary_macro_f1_bootstrap_ci": {"ci_low": 0.4, "ci_high": 0.6},
            "stable_label_slice_evaluation": {"primary_macro_f1": 0.55},
        },
        "test_temporal_shift": {
            "policy_evaluation": {"primary_macro_f1": 0.3, "full_micro_f1": 0.4},
        },
        "latency": {"p50_ms": 0.1, "p95_ms": 0.2},
        "train_seconds": 5.0,
    }
    (arm0_dir / "regime_50.json").write_text(json.dumps(fake))
    result = aggregate_results.load_arm0(arm0_dir)
    assert result["50"]["n_seeds"] == 1
    assert result["50"]["test_in_distribution"]["primary_macro_f1"]["mean"] == 0.5
    assert result["50"]["test_in_distribution"]["bootstrap_ci"]["ci_low"] == 0.4

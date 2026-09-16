#!/usr/bin/env python3
"""Arm 4: QLoRA fine-tuning of Qwen2.5-1.5B-Instruct-4bit (same base
model as Arms 2/3) via mlx-lm's native LoRA support, across the mandatory
data-efficiency regimes, on the frozen splits.

Reuses mlx_lm.lora.run() directly (the same orchestration the mlx-lm CLI
uses) rather than reimplementing the training loop, per the project's
"reuse existing infrastructure" preference -- every hyperparameter is
frozen in triagebench.models.lora_arm before this script runs anything.

Run with --regime/--seeds to pilot a single combination first (see
docs/EXPERIMENT_LOG.md for the pilot-first protocol every arm in this
project has followed).
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import mlx.core as mx  # noqa: E402
import yaml  # noqa: E402
from mlx_lm.lora import CONFIG_DEFAULTS, run  # noqa: E402

from triagebench.data.dataset_io import load_model_ready, load_splits  # noqa: E402
from triagebench.data.splitting import stratified_sample_per_class, uniform_random_subset  # noqa: E402
from triagebench.models.lora_arm import (  # noqa: E402
    BATCH_SIZE,
    EPOCHS,
    GRAD_ACCUMULATION_STEPS,
    GRAD_CHECKPOINT,
    LEARNING_RATE,
    LORA_CONFIG,
    MAX_SEQ_LENGTH,
    MODEL_NAME,
    PRECISION,
    TARGET_MODULES,
    build_completion_examples,
    compute_iters,
    write_jsonl,
)
from triagebench.utils.reproducibility import get_git_commit, get_split_metadata  # noqa: E402

VAL_SUBSET_SIZE = 200  # for training-time monitoring only, not used for any tuning decision


def build_args_namespace(data_dir: Path, adapter_path: Path, seed: int, iters: int) -> Namespace:
    cfg = dict(CONFIG_DEFAULTS)
    cfg.update(
        {
            "model": MODEL_NAME,
            "train": True,
            "test": False,
            "fine_tune_type": "lora",
            "data": str(data_dir),
            "seed": seed,
            "num_layers": LORA_CONFIG["num_layers"],
            "batch_size": BATCH_SIZE,
            "iters": iters,
            "val_batches": max(1, VAL_SUBSET_SIZE // BATCH_SIZE),
            "learning_rate": LEARNING_RATE,
            "steps_per_report": max(1, iters // 20),
            "steps_per_eval": max(1, iters // 4),
            "adapter_path": str(adapter_path),
            "save_every": max(1, iters),  # only save at the end -- no intermediate checkpoints needed
            "max_seq_length": MAX_SEQ_LENGTH,
            "grad_checkpoint": GRAD_CHECKPOINT,
            "grad_accumulation_steps": GRAD_ACCUMULATION_STEPS,
            "lora_parameters": {
                "rank": LORA_CONFIG["rank"],
                "dropout": LORA_CONFIG["dropout"],
                "scale": LORA_CONFIG["scale"],
            },
            "mask_prompt": True,  # only the completion (label) tokens contribute to the loss
            "resume_adapter_file": None,
            "report_to": None,
            "project_name": None,
        }
    )
    return Namespace(**cfg)


def run_one(regime_label: str, n_per_class: int | None, seed: int, args, rows, splits, exp_cfg) -> dict:
    train_ids_all = splits["train_ids"]
    train_ids = stratified_sample_per_class(
        [{"id": i, "component": rows[i]["component"]} for i in train_ids_all],
        "component",
        n_per_class,
        seed=exp_cfg["split"]["split_seed"],
    )
    val_ids = uniform_random_subset(splits["val_ids"], VAL_SUBSET_SIZE, seed=exp_cfg["split"]["split_seed"])

    train_examples = build_completion_examples(train_ids, rows)
    val_examples = build_completion_examples(val_ids, rows)

    data_dir = args.data_dir / f"regime_{regime_label}_seed{seed}"
    write_jsonl(train_examples, data_dir / "train.jsonl")
    write_jsonl(val_examples, data_dir / "valid.jsonl")

    iters = compute_iters(len(train_ids))
    adapter_path = args.checkpoint_dir / f"regime_{regime_label}_seed{seed}"
    adapter_path.mkdir(parents=True, exist_ok=True)

    mlx_args = build_args_namespace(data_dir, adapter_path, seed, iters)

    print(f"  n_train={len(train_ids)} n_val={len(val_ids)} iters={iters}")
    print(f"  effective_batch_size={BATCH_SIZE * GRAD_ACCUMULATION_STEPS} epochs_target={EPOCHS}")

    mx.reset_peak_memory()
    t0 = time.time()
    run(mlx_args)
    train_seconds = time.time() - t0
    peak_memory_gb = mx.get_peak_memory() / 1e9

    return {
        "status": "MEASURED",
        "arm": "arm4_qlora_finetune",
        "model_name": MODEL_NAME,
        "model_precision": PRECISION,
        "regime": regime_label,
        "n_per_class": n_per_class,
        "seed": seed,
        "n_train_examples": len(train_ids),
        "n_val_examples": len(val_ids),
        "lora_config": {
            "rank": LORA_CONFIG["rank"],
            "dropout": LORA_CONFIG["dropout"],
            "scale_mlx": LORA_CONFIG["scale"],
            "alpha_peft_equivalent": LORA_CONFIG["scale"] * LORA_CONFIG["rank"],
            "num_layers": LORA_CONFIG["num_layers"],
            "target_modules": TARGET_MODULES,
        },
        "training_config": {
            "learning_rate": LEARNING_RATE,
            "epochs_target": EPOCHS,
            "iters": iters,
            "batch_size": BATCH_SIZE,
            "grad_accumulation_steps": GRAD_ACCUMULATION_STEPS,
            "effective_batch_size": BATCH_SIZE * GRAD_ACCUMULATION_STEPS,
            "max_seq_length": MAX_SEQ_LENGTH,
            "mask_prompt": True,
            "grad_checkpoint": GRAD_CHECKPOINT,
        },
        "mlx_version": __import__("mlx").__dict__.get("__version__", "unknown"),
        "mlx_lm_version": __import__("mlx_lm").__dict__.get("__version__", "unknown"),
        "train_seconds": round(train_seconds, 1),
        "peak_memory_gb": round(peak_memory_gb, 3),
        "adapter_path": str(adapter_path),
        "hardware": f"{platform.platform()} device=mps (Apple M5, local $0-cost run, mlx-lm QLoRA)",
        "git_commit": get_git_commit(),
        "split": get_split_metadata(args.splits),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-ready", default="data/processed/platform_model_ready.jsonl", type=Path)
    ap.add_argument("--splits", default="reports/results/splits/platform_splits.json", type=Path)
    ap.add_argument("--experiments-config", default="configs/experiments.yaml", type=Path)
    ap.add_argument("--data-dir", default="data/processed/arm4_lora", type=Path)
    ap.add_argument("--checkpoint-dir", default="data/checkpoints/arm4", type=Path)
    ap.add_argument("--output-dir", default="reports/results/arm4", type=Path)
    ap.add_argument("--regime", default=None, help="single regime, e.g. '50'/'200'/'1000'/'full'. Default: all.")
    ap.add_argument("--seeds", default=None, help="comma-separated seeds, e.g. '0' or '0,1,2'. Default: all 3.")
    args = ap.parse_args()

    exp_cfg = yaml.safe_load(args.experiments_config.read_text())
    print("Loading model-ready data and frozen splits...")
    rows = load_model_ready(args.model_ready)
    splits = load_splits(args.splits)

    all_regimes = [str(r["examples_per_class"]) for r in exp_cfg["data_efficiency_regimes"]]
    regimes = [args.regime] if args.regime else all_regimes
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else [0, 1, 2]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for regime_label in regimes:
        n_per_class = None if regime_label == "full" else int(regime_label)
        for seed in seeds:
            print(f"\n=== Arm 4 QLoRA: regime {regime_label}, seed {seed} ===")
            result = run_one(regime_label, n_per_class, seed, args, rows, splits, exp_cfg)
            out_path = args.output_dir / f"train_regime_{regime_label}_seed{seed}.json"
            out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(f"  train_seconds={result['train_seconds']} peak_memory_gb={result['peak_memory_gb']}")
            print(f"  wrote {out_path}")


if __name__ == "__main__":
    main()

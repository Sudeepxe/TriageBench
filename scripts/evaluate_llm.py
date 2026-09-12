#!/usr/bin/env python3
"""Arms 2 (naive/frozen-base prompt) and 3 (engineered, frozen prompt):
evaluate the same small LLM on a fixed random subset of each frozen test
split (see configs/experiments.yaml: llm_arms -- subset size/seed frozen
before any prompt was written or any result existed).

Both arms share one model load and one generation call per example
(only the prompt template differs), so this script runs both in one pass
to avoid loading the model twice.
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
from mlx_lm import generate, load  # noqa: E402

from triagebench.data.dataset_io import load_model_ready, load_splits  # noqa: E402
from triagebench.data.splitting import uniform_random_subset  # noqa: E402
from triagebench.evaluation.long_tail_policy import evaluate_with_policy, primary_macro_f1_metric  # noqa: E402
from triagebench.evaluation.metrics import bootstrap_ci, micro_f1_metric  # noqa: E402
from triagebench.models.llm_prompting import (  # noqa: E402
    build_engineered_prompt,
    build_naive_prompt,
    match_component_label,
)
from triagebench.utils.reproducibility import get_git_commit, get_split_metadata  # noqa: E402

ARMS = {
    "arm2_naive": {"arm": "arm2_llm_base_naive_prompt", "prompt_fn": build_naive_prompt},
    "arm3_engineered": {"arm": "arm3_llm_prompted_engineered", "prompt_fn": build_engineered_prompt},
}
SPLITS = ["test_in_distribution", "test_temporal_shift"]


def generate_predictions(model, tokenizer, prompts: list[str], max_new_tokens: int) -> tuple[list[str], list[float]]:
    raw_outputs = []
    latencies_ms = []
    for prompt in prompts:
        messages = [{"role": "user", "content": prompt}]
        formatted = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        t0 = time.perf_counter()
        response = generate(model, tokenizer, prompt=formatted, max_tokens=max_new_tokens, verbose=False)
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        raw_outputs.append(response)
    return raw_outputs, latencies_ms


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-ready", default="data/processed/platform_model_ready.jsonl", type=Path)
    ap.add_argument("--splits", default="reports/results/splits/platform_splits.json", type=Path)
    ap.add_argument("--experiments-config", default="configs/experiments.yaml", type=Path)
    ap.add_argument("--output-dir-arm2", default="reports/results/arm2", type=Path)
    ap.add_argument("--output-dir-arm3", default="reports/results/arm3", type=Path)
    ap.add_argument("--arms", default="arm2_naive,arm3_engineered", help="comma-separated subset of arms to run")
    args = ap.parse_args()

    exp_cfg = yaml.safe_load(args.experiments_config.read_text())
    llm_cfg = exp_cfg["llm_arms"]
    bootstrap_resamples = exp_cfg["statistics"]["bootstrap_resamples"]

    print(f"Loading model: {llm_cfg['base_model']}")
    model, tokenizer = load(llm_cfg["base_model"])

    print("Loading model-ready data and frozen splits...")
    rows = load_model_ready(args.model_ready)
    splits = load_splits(args.splits)

    subset_by_split = {
        split: uniform_random_subset(
            splits[f"{split}_ids"], llm_cfg["eval_subset_size_per_split"], llm_cfg["eval_subset_seed"]
        )
        for split in SPLITS
    }
    for split, ids in subset_by_split.items():
        print(f"  {split} subset: {len(ids)} examples (seed={llm_cfg['eval_subset_seed']})")

    arms_to_run = args.arms.split(",")
    output_dirs = {"arm2_naive": args.output_dir_arm2, "arm3_engineered": args.output_dir_arm3}

    for arm_key in arms_to_run:
        arm_meta = ARMS[arm_key]
        prompt_fn = arm_meta["prompt_fn"]
        output_dir = output_dirs[arm_key]
        output_dir.mkdir(parents=True, exist_ok=True)

        for split in SPLITS:
            ids = subset_by_split[split]
            labels = [rows[i]["component"] for i in ids]
            prompts = [prompt_fn(rows[i]["summary"], rows[i]["description"]) for i in ids]

            print(f"\n=== {arm_key} / {split}: generating {len(prompts)} predictions ===")
            t0 = time.time()
            raw_outputs, latencies_ms = generate_predictions(model, tokenizer, prompts, llm_cfg["max_new_tokens"])
            elapsed = time.time() - t0
            print(f"  done in {elapsed:.1f}s ({elapsed/len(prompts):.2f}s/example)")

            matched = [match_component_label(text) for text in raw_outputs]
            n_unparseable = sum(1 for m in matched if m is None)
            # Unparseable/hallucinated outputs are never coerced to a
            # default class -- treated as a distinct, always-wrong
            # sentinel label so they count against the model's score
            # exactly like any other incorrect prediction.
            preds = [m if m is not None else "__UNPARSEABLE__" for m in matched]

            policy_report = evaluate_with_policy(labels, preds)
            primary_ci = bootstrap_ci(labels, preds, primary_macro_f1_metric, n_resamples=bootstrap_resamples, seed=0)
            micro_ci = bootstrap_ci(labels, preds, micro_f1_metric, n_resamples=bootstrap_resamples, seed=0)

            latencies_ms_sorted = sorted(latencies_ms)

            def pct(p: float, arr=latencies_ms_sorted) -> float:
                idx = min(len(arr) - 1, int(round(p * (len(arr) - 1))))
                return arr[idx]

            result = {
                "status": "MEASURED",
                "arm": arm_meta["arm"],
                "model_name": llm_cfg["base_model"],
                "model_license": llm_cfg["base_model_license"],
                "inference_backend": llm_cfg["inference_backend"],
                "prompt_style": "naive" if arm_key == "arm2_naive" else "engineered",
                "max_new_tokens": llm_cfg["max_new_tokens"],
                "eval_split": split,
                "eval_subset_size": len(ids),
                "eval_subset_seed": llm_cfg["eval_subset_seed"],
                "note_statistical_power": (
                    f"Evaluated on a fixed {len(ids)}-example random subset of the frozen "
                    f"{split} split, not the full set -- generation is far slower per example "
                    "than the classifier-head arms. This subset has lower statistical power "
                    "than the full-test-set evaluations used for Arms 0/1."
                ),
                "n_unparseable_predictions": n_unparseable,
                "unparseable_rate_pct": round(100 * n_unparseable / len(ids), 2),
                "policy_evaluation": policy_report,
                "primary_macro_f1_bootstrap_ci": primary_ci,
                "full_micro_f1_bootstrap_ci": micro_ci,
                "latency": {
                    "environment": f"{platform.platform()} device=mps (Apple M5, local $0-cost run, mlx-lm)",
                    "n_measurements": len(latencies_ms),
                    "p50_ms": round(pct(0.50), 1),
                    "p95_ms": round(pct(0.95), 1),
                    "p99_ms": round(pct(0.99), 1),
                    "total_generation_seconds": round(elapsed, 1),
                },
                "git_commit": get_git_commit(),
                "split": get_split_metadata(args.splits),
            }

            out_path = output_dir / f"{split}.json"
            out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(f"  primary_macro_f1={policy_report['primary_macro_f1']:.4f} unparseable={n_unparseable}/{len(ids)}")
            print(f"  wrote {out_path}")


if __name__ == "__main__":
    main()

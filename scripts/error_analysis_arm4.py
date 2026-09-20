#!/usr/bin/env python3
"""Arm 4 error analysis, inference only (NO training, no methodology change).

The Arm 4 evaluation artifacts store aggregate metrics but not per-example
predictions, so per-class / confusion analysis cannot be computed from them.
This script re-runs greedy generation with a saved adapter on the SAME frozen
500-example subset, prompt and decoding settings as scripts/evaluate_lora.py,
and first verifies that the recomputed primary macro-F1 matches the value
recorded in the existing evaluation artifact. If it does not match, the
analysis is still written but flagged `matches_recorded_metric: false`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402
from mlx_lm import generate, load  # noqa: E402

from triagebench.data.dataset_io import load_model_ready, load_splits  # noqa: E402
from triagebench.data.splitting import uniform_random_subset  # noqa: E402
from triagebench.evaluation.long_tail_policy import PRIMARY_CLASSES, evaluate_with_policy  # noqa: E402
from triagebench.evaluation.metrics import classification_report  # noqa: E402
from triagebench.models.llm_prompting import build_engineered_prompt, match_component_label  # noqa: E402
from triagebench.utils.reproducibility import get_git_commit, get_split_metadata  # noqa: E402

SPLITS = ["test_in_distribution", "test_temporal_shift"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--adapter-path", required=True, type=Path)
    ap.add_argument("--regime", required=True)
    ap.add_argument("--seed", required=True, type=int)
    ap.add_argument("--output-dir", default="reports/results/arm4", type=Path)
    args = ap.parse_args()

    cfg = yaml.safe_load(Path("configs/experiments.yaml").read_text())["llm_arms"]
    rows = load_model_ready(Path("data/processed/platform_model_ready.jsonl"))
    splits = load_splits(Path("reports/results/splits/platform_splits.json"))
    model, tokenizer = load(cfg["base_model"], adapter_path=str(args.adapter_path))

    out = {
        "status": "MEASURED",
        "kind": "inference-only re-scoring of a saved adapter; no training",
        "arm": "arm4_qlora_finetune",
        "regime": args.regime,
        "seed": args.seed,
        "eval_subset_size": cfg["eval_subset_size_per_split"],
        "eval_subset_seed": cfg["eval_subset_seed"],
        "git_commit": get_git_commit(),
        "split": get_split_metadata(Path("reports/results/splits/platform_splits.json")),
        "splits": {},
    }
    for split in SPLITS:
        ids = uniform_random_subset(splits[f"{split}_ids"], cfg["eval_subset_size_per_split"], cfg["eval_subset_seed"])
        labels = [rows[i]["component"] for i in ids]
        preds, raws = [], []
        for i in ids:
            prompt = build_engineered_prompt(rows[i]["summary"], rows[i]["description"])
            formatted = tokenizer.apply_chat_template(
                [{"role": "user", "content": prompt}], add_generation_prompt=True, tokenize=False
            )
            raw = generate(model, tokenizer, prompt=formatted, max_tokens=cfg["max_new_tokens"], verbose=False)
            m = match_component_label(raw)
            raws.append(raw)
            preds.append(m if m is not None else "__UNPARSEABLE__")

        recorded = json.loads(
            (args.output_dir / f"eval_regime_{args.regime}_seed{args.seed}_{split}.json").read_text()
        )["policy_evaluation"]["primary_macro_f1"]
        recomputed = evaluate_with_policy(labels, preds)["primary_macro_f1"]
        rep = classification_report(labels, preds)
        out["splits"][split] = {
            "ids": ids,
            "labels": labels,
            "predictions": preds,
            "raw_outputs": raws,
            "recorded_primary_macro_f1": recorded,
            "recomputed_primary_macro_f1": recomputed,
            "matches_recorded_metric": abs(recorded - recomputed) < 1e-12,
            "n_unparseable": sum(p == "__UNPARSEABLE__" for p in preds),
            "per_class": rep.per_class,
            "support": rep.support,
            "confusion_matrix": rep.confusion_matrix,
            "top_confusions": Counter(
                (t, p) for t, p in zip(labels, preds, strict=True) if t != p
            ).most_common(15),
            "primary_classes_absent_from_subset": sorted(PRIMARY_CLASSES - set(labels)),
        }
        print(f"{split}: recorded={recorded:.6f} recomputed={recomputed:.6f} "
              f"match={out['splits'][split]['matches_recorded_metric']}")

    path = args.output_dir / f"predictions_regime_{args.regime}_seed{args.seed}.json"
    path.write_text(json.dumps(out, indent=1, default=list), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()

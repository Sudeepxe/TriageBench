#!/usr/bin/env python3
"""Arm 1: fine-tune a small pretrained encoder (DistilBERT-base-uncased)
for Component classification, across the same data-efficiency regimes as
Arm 0, on the same frozen splits, with the same long-tail policy and
bootstrap CI methodology.

Originally targeted answerdotai/ModernBERT-base; measured at ~2.11
samples/sec on Apple M5/MPS in the full training pipeline (see
docs/EXPERIMENT_LOG.md EXP-004), extrapolating to ~28.7 hours for the
full-data regime alone. Rather than spend on a cloud GPU (a paid-compute
plan the user withdrew approval for entirely), switched to
DistilBERT-base-uncased -- measured at ~3x ModernBERT's throughput in an
isolated timing probe on the same hardware, well within local M5/MPS
practicality at $0 cost. See docs/DESIGN_DECISIONS.md for the full
substitution rationale (why the switch doesn't change the scientific
task, leakage policy, splits, or evaluation methodology).

Run with --regime to pilot a single regime first (recommended: start
with the smallest) before committing to the full sweep.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import yaml  # noqa: E402
from torch.utils.data import Dataset  # noqa: E402
from transformers import (  # noqa: E402
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorWithPadding,
    Trainer,
    TrainingArguments,
)

from triagebench.data.dataset_io import load_model_ready, load_splits, texts_and_labels  # noqa: E402
from triagebench.data.splitting import stratified_sample_per_class  # noqa: E402
from triagebench.evaluation.long_tail_policy import (  # noqa: E402
    ALL_CLASSES,
    MINIMUM_SUPPORT_THRESHOLD,
    evaluate_with_policy,
    primary_macro_f1_metric,
)
from triagebench.evaluation.metrics import bootstrap_ci, micro_f1_metric  # noqa: E402
from triagebench.models.baseline import combine_text  # noqa: E402
from triagebench.utils.reproducibility import get_git_commit, get_split_metadata  # noqa: E402

MODEL_NAME = "distilbert-base-uncased"
MAX_LENGTH = 256  # covers median (~500 combined chars ~ 100-150 tokens) and most of the p75; documented truncation
ARM_SEEDS = [0, 1, 2]

LABELS = sorted(ALL_CLASSES)  # full 21-class taxonomy preserved as the model's output space
LABEL_TO_ID = {label: i for i, label in enumerate(LABELS)}
ID_TO_LABEL = dict(enumerate(LABELS))


class TextClassificationDataset(Dataset):
    def __init__(self, texts: list[str], labels: list[str], tokenizer, max_length: int):
        self.encodings = tokenizer(texts, truncation=True, max_length=max_length, padding=False)
        self.label_ids = [LABEL_TO_ID[label] for label in labels]

    def __len__(self):
        return len(self.label_ids)

    def __getitem__(self, idx):
        item = {k: torch.tensor(v[idx]) for k, v in self.encodings.items()}
        item["labels"] = torch.tensor(self.label_ids[idx])
        return item


def get_device(override: str | None = None) -> str:
    if override:
        if override == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("--device cuda requested but torch.cuda.is_available() is False")
        if override == "mps" and not torch.backends.mps.is_available():
            raise RuntimeError("--device mps requested but torch.backends.mps.is_available() is False")
        return override
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def configure_determinism(seed: int, device: str) -> None:
    """Best-effort determinism. CUDA determinism additionally requires
    disabling cuDNN's autotuned/non-deterministic kernels -- without this,
    two runs with the same seed can still diverge slightly on GPU."""
    torch.manual_seed(seed)
    if device == "cuda":
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def describe_hardware(device: str) -> str:
    base = platform.platform()
    if device == "cuda" and torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        return f"{base} device=cuda ({name}, {vram_gb:.1f}GB VRAM, CUDA {torch.version.cuda})"
    if device == "mps":
        chip = "unknown chip"
        try:
            import subprocess

            chip = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5
            ).stdout.strip() or chip
        except Exception:
            pass
        return f"{base} device=mps ({chip}, local $0-cost run)"
    return f"{base} device={device}"


def predict_labels(model, tokenizer, texts: list[str], device: str, batch_size: int = 32) -> list[str]:
    model.eval()
    preds: list[str] = []
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            enc = tokenizer(batch, truncation=True, max_length=MAX_LENGTH, padding=True, return_tensors="pt")
            enc = {k: v.to(device) for k, v in enc.items()}
            logits = model(**enc).logits
            batch_ids = logits.argmax(dim=-1).cpu().numpy()
            preds.extend(ID_TO_LABEL[int(i)] for i in batch_ids)
    return preds


def measure_latency(model, tokenizer, device: str, sample_texts: list[str], n_single: int = 50) -> dict:
    model.eval()
    warmup = min(5, len(sample_texts))
    for t in sample_texts[:warmup]:
        with torch.no_grad():
            enc = tokenizer([t], truncation=True, max_length=MAX_LENGTH, return_tensors="pt")
            enc = {k: v.to(device) for k, v in enc.items()}
            model(**enc)

    singles_ms = []
    single_texts = (sample_texts * (n_single // max(1, len(sample_texts)) + 1))[:n_single]
    for t in single_texts:
        t0 = time.perf_counter()
        with torch.no_grad():
            enc = tokenizer([t], truncation=True, max_length=MAX_LENGTH, return_tensors="pt")
            enc = {k: v.to(device) for k, v in enc.items()}
            model(**enc)
        singles_ms.append((time.perf_counter() - t0) * 1000)
    singles_ms.sort()

    def pct(p: float) -> float:
        idx = min(len(singles_ms) - 1, int(round(p * (len(singles_ms) - 1))))
        return singles_ms[idx]

    batch_size = 32
    batch = (sample_texts * (batch_size // max(1, len(sample_texts)) + 1))[:batch_size]
    with torch.no_grad():
        enc = tokenizer(batch, truncation=True, max_length=MAX_LENGTH, padding=True, return_tensors="pt")
        enc = {k: v.to(device) for k, v in enc.items()}
        t0 = time.perf_counter()
        model(**enc)
        batch_elapsed = time.perf_counter() - t0
    throughput = batch_size / batch_elapsed if batch_elapsed > 0 else float("inf")

    return {
        "environment": describe_hardware(device),
        "n_single_measurements": len(singles_ms),
        "p50_ms": round(pct(0.50), 3),
        "p95_ms": round(pct(0.95), 3),
        "p99_ms": round(pct(0.99), 3),
        "batch_size": batch_size,
        "batch_throughput_per_sec": round(throughput, 1),
        "max_length": MAX_LENGTH,
    }


def evaluate_split(model, tokenizer, ids: list[str], rows: dict, device: str, bootstrap_resamples: int) -> dict:
    texts, labels = texts_and_labels(ids, rows, combine_text)
    preds = predict_labels(model, tokenizer, texts, device)
    policy_report = evaluate_with_policy(labels, preds)
    primary_ci = bootstrap_ci(labels, preds, primary_macro_f1_metric, n_resamples=bootstrap_resamples, seed=0)
    micro_ci = bootstrap_ci(labels, preds, micro_f1_metric, n_resamples=bootstrap_resamples, seed=0)

    stable_ids = [i for i in ids if rows[i]["label_stable"]]
    stable_report = None
    if stable_ids:
        s_texts, s_labels = texts_and_labels(stable_ids, rows, combine_text)
        s_preds = predict_labels(model, tokenizer, s_texts, device)
        stable_report = evaluate_with_policy(s_labels, s_preds)

    return {
        "n": len(ids),
        "policy_evaluation": policy_report,
        "primary_macro_f1_bootstrap_ci": primary_ci,
        "full_micro_f1_bootstrap_ci": micro_ci,
        "n_stable_label": len(stable_ids),
        "stable_label_slice_evaluation": stable_report,
    }


def run_one(regime_label: str, n_per_class: int | None, seed: int, args, rows, splits, exp_cfg) -> dict:
    device = get_device(args.device)
    configure_determinism(seed, device)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

    train_ids_all = splits["train_ids"]
    train_ids = stratified_sample_per_class(
        [{"id": i, "component": rows[i]["component"]} for i in train_ids_all],
        "component",
        n_per_class,
        seed=exp_cfg["split"]["split_seed"],
    )
    train_texts, train_labels = texts_and_labels(train_ids, rows, combine_text)
    val_texts, val_labels = texts_and_labels(splits["val_ids"], rows, combine_text)

    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME, num_labels=len(LABELS), id2label=ID_TO_LABEL, label2id=LABEL_TO_ID
    ).to(device)

    train_ds = TextClassificationDataset(train_texts, train_labels, tokenizer, MAX_LENGTH)
    val_ds = TextClassificationDataset(val_texts, val_labels, tokenizer, MAX_LENGTH)

    output_dir = args.checkpoint_dir / f"regime_{regime_label}_seed{seed}"
    training_args = TrainingArguments(
        output_dir=str(output_dir),
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        # No per-epoch eval on the 15,585-row val set: there is no early
        # stopping/checkpoint-selection to justify that recurring cost --
        # validation is used once, exactly like Arm 0's pilot, not
        # monitored epoch-by-epoch. This was needless overhead in the
        # interrupted MPS pilot (docs/EXPERIMENT_LOG.md EXP-004).
        eval_strategy="no",
        save_strategy="no",
        logging_steps=50,
        seed=seed,
        report_to=[],
    )

    def compute_metrics(eval_pred):
        logits, label_ids = eval_pred
        preds = np.argmax(logits, axis=-1)
        y_true = [ID_TO_LABEL[int(i)] for i in label_ids]
        y_pred = [ID_TO_LABEL[int(i)] for i in preds]
        return {"primary_macro_f1": evaluate_with_policy(y_true, y_pred)["primary_macro_f1"]}

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        compute_metrics=compute_metrics,
        data_collator=DataCollatorWithPadding(tokenizer),
    )

    t0 = time.time()
    trainer.train()
    train_seconds = time.time() - t0

    print("  evaluating on test_in_distribution (FROZEN)...")
    test_id_eval = evaluate_split(
        model, tokenizer, splits["test_in_distribution_ids"], rows, device, args.bootstrap_resamples
    )
    print(f"    test_id primary macro-F1={test_id_eval['policy_evaluation']['primary_macro_f1']:.4f}")

    print("  evaluating on test_temporal_shift (FROZEN)...")
    test_shift_eval = evaluate_split(
        model, tokenizer, splits["test_temporal_shift_ids"], rows, device, args.bootstrap_resamples
    )
    print(f"    test_shift primary macro-F1={test_shift_eval['policy_evaluation']['primary_macro_f1']:.4f}")

    print("  measuring latency...")
    latency = measure_latency(model, tokenizer, device, train_texts[:20] or ["placeholder"])

    return {
        "status": "MEASURED",
        "arm": "arm1_encoder_finetune",
        "model_name": MODEL_NAME,
        "max_length": MAX_LENGTH,
        "regime": regime_label,
        "n_per_class": n_per_class,
        "seed": seed,
        "hyperparameters": {
            "batch_size": args.batch_size,
            "epochs": args.epochs,
            "learning_rate": args.learning_rate,
        },
        "long_tail_policy": {"minimum_support_threshold": MINIMUM_SUPPORT_THRESHOLD},
        "n_train_examples": len(train_ids),
        "n_train_classes_present": len(set(train_labels)),
        "train_seconds": round(train_seconds, 1),
        "train_loss_history": [
            {"step": h.get("step"), "loss": h.get("loss"), "epoch": h.get("epoch")}
            for h in trainer.state.log_history
            if "loss" in h
        ],
        "test_in_distribution": test_id_eval,
        "test_temporal_shift": test_shift_eval,
        "latency": latency,
        "hardware": describe_hardware(device),
        "bootstrap_resamples": args.bootstrap_resamples,
        "git_commit": get_git_commit(),
        "split": get_split_metadata(args.splits),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-ready", default="data/processed/platform_model_ready.jsonl", type=Path)
    ap.add_argument("--splits", default="reports/results/splits/platform_splits.json", type=Path)
    ap.add_argument("--experiments-config", default="configs/experiments.yaml", type=Path)
    ap.add_argument("--output-dir", default="reports/results/arm1", type=Path)
    ap.add_argument("--checkpoint-dir", default="data/checkpoints/arm1", type=Path)
    ap.add_argument("--regime", default=None, help="single regime, e.g. '50'/'200'/'1000'/'full'. Default: all.")
    ap.add_argument("--seeds", default=None, help="comma-separated seeds, e.g. '0' or '0,1,2'. Default: all 3.")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--learning-rate", type=float, default=2e-5)
    ap.add_argument("--bootstrap-resamples", type=int, default=None)
    ap.add_argument(
        "--device", default=None, choices=["cuda", "mps", "cpu"],
        help="force a specific device instead of auto-detecting (mps > cuda > cpu).",
    )
    args = ap.parse_args()

    exp_cfg = yaml.safe_load(args.experiments_config.read_text())
    args.bootstrap_resamples = args.bootstrap_resamples or exp_cfg["statistics"]["bootstrap_resamples"]

    print(f"Device: {get_device(args.device)}")
    print("Loading model-ready data and frozen splits...")
    rows = load_model_ready(args.model_ready)
    splits = load_splits(args.splits)

    all_regimes = [str(r["examples_per_class"]) for r in exp_cfg["data_efficiency_regimes"]]
    regimes = [args.regime] if args.regime else all_regimes
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else ARM_SEEDS

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for regime_label in regimes:
        n_per_class = None if regime_label == "full" else int(regime_label)
        for seed in seeds:
            print(f"\n=== Regime {regime_label}, seed {seed} ===")
            result = run_one(regime_label, n_per_class, seed, args, rows, splits, exp_cfg)
            out_path = args.output_dir / f"regime_{regime_label}_seed{seed}.json"
            out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(f"  wrote {out_path}")


if __name__ == "__main__":
    main()

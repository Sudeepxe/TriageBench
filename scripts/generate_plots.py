#!/usr/bin/env python3
"""Generate every final plot from stored result artifacts only -- each
plotted value traces back to a file under reports/results/.

Evaluation-set distinction (applied consistently): Arm 0/1 are scored on the
full frozen test splits (filled markers, solid lines); Arms 2/3/4 are scored
on a fixed 500-example random subset of each split (open markers, dashed
lines, with the subset's bootstrap 95% CI shown), so they are never drawn as
if they shared an evaluation set with Arms 0/1.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REGIME_ORDER = ["50", "200", "1000", "full"]
SUBSET_NOTE = "Arms 0/1: full test split (filled). Arm 4/Arms 2-3: fixed 500-example subset (open, dashed)."
ARM_STYLE = {
    "arm0_tfidf_logreg": {"label": "Arm 0: TF-IDF + LogReg (full test split)", "color": "#1f77b4", "marker": "o",
                          "subset": False},
    "arm1_distilbert_finetune": {"label": "Arm 1: DistilBERT fine-tune (full test split)", "color": "#d62728",
                                 "marker": "s", "subset": False},
    "arm4_qlora_finetune": {"label": "Arm 4: QLoRA Qwen2.5-1.5B (500-ex subset)", "color": "#2ca02c", "marker": "^",
                            "subset": True},
}
SPLITS = [("test_in_distribution", "In-distribution test"), ("test_temporal_shift", "Temporal-shift test")]


def _draw(ax, style, xs, ys, yerr=None):
    kw = {"color": style["color"], "marker": style["marker"], "linewidth": 1.8, "markersize": 6}
    if style["subset"]:
        kw.update(linestyle="--", markerfacecolor="white", markeredgewidth=1.6)
    if yerr is not None:
        ax.errorbar(xs, ys, yerr=yerr, label=style["label"], capsize=3, **kw)
    else:
        ax.plot(xs, ys, label=style["label"], **kw)


def plot_data_efficiency(summary: dict, output_path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5.2), sharey=True)
    for ax, (split_key, split_title) in zip(axes, SPLITS, strict=True):
        for arm_key, style in ARM_STYLE.items():
            by_regime = summary["arms"][arm_key]
            xs, ys, yerr, lo, hi = [], [], [], [], []
            for regime in REGIME_ORDER:
                if regime not in by_regime:
                    continue
                d = by_regime[regime]
                m = d[split_key]["primary_macro_f1"]
                xs.append(d["n_train_examples"])
                ys.append(m["mean"])
                yerr.append(m["stdev"])
                if style["subset"]:
                    ci = d[split_key]["bootstrap_ci_mean_over_seeds"]
                    lo.append(ci["ci_low"])
                    hi.append(ci["ci_high"])
            _draw(ax, style, xs, ys, yerr)
            if style["subset"]:
                ax.fill_between(xs, lo, hi, color=style["color"], alpha=0.12, linewidth=0)
        ax.set_xscale("log")
        ax.set_xlabel("Training examples (log scale)")
        ax.set_title(split_title)
        ax.grid(True, which="both", alpha=0.25)
    axes[0].set_ylabel("Primary macro-F1 (20 primary classes)")
    axes[0].legend(loc="lower right", fontsize=8)
    fig.suptitle("TriageBench: data-efficiency (Eclipse Platform Component classification)")
    fig.text(0.5, 0.005,
             SUBSET_NOTE + "\nError bars: stdev over seeds (Arm 4 full regime: 1 seed, no bar). "
             "Green band: mean per-seed bootstrap 95% CI on the 500-example subset.",
             ha="center", fontsize=7.5)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {output_path}")


def plot_micro_f1(summary: dict, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 5))
    for arm_key, style in ARM_STYLE.items():
        by_regime = summary["arms"][arm_key]
        xs = [by_regime[r]["n_train_examples"] for r in REGIME_ORDER if r in by_regime]
        ys = [by_regime[r]["test_in_distribution"]["full_micro_f1"]["mean"] for r in REGIME_ORDER if r in by_regime]
        _draw(ax, style, xs, ys)
    ax.set_xscale("log")
    ax.set_xlabel("Training examples (log scale)")
    ax.set_ylabel("Full micro-F1 (all 21 classes), in-distribution test")
    ax.set_title("TriageBench: micro-F1 vs. data regime")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(loc="lower right", fontsize=8)
    fig.text(0.5, 0.005, SUBSET_NOTE + " Mean over seeds.", ha="center", fontsize=7)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {output_path}")


def plot_production(results: Path, output_path: Path) -> None:
    """Full-regime primary macro-F1 (in-distribution) vs. measured median latency."""
    sysd = json.loads((results / "production/systems_summary.json").read_text())["arms"]
    f1 = {
        "arm0_tfidf_logreg": (json.loads((results / "arm0/regime_full.json").read_text())
                              ["test_in_distribution"]["policy_evaluation"]["primary_macro_f1"], False),
        "arm1_distilbert": (
            float(np.mean([json.loads(f.read_text())["test_in_distribution"]["policy_evaluation"]["primary_macro_f1"]
                           for f in sorted((results / "arm1").glob("regime_full_seed*.json"))])), False),
        "arm2_llm_naive": (json.loads((results / "arm2/test_in_distribution.json").read_text())
                           ["policy_evaluation"]["primary_macro_f1"], True),
        "arm3_llm_engineered": (json.loads((results / "arm3/test_in_distribution.json").read_text())
                                ["policy_evaluation"]["primary_macro_f1"], True),
        "arm4_qlora": (json.loads((results / "arm4/eval_regime_full_seed0_test_in_distribution.json").read_text())
                       ["policy_evaluation"]["primary_macro_f1"], True),
    }
    names = {"arm0_tfidf_logreg": "Arm 0", "arm1_distilbert": "Arm 1", "arm2_llm_naive": "Arm 2",
             "arm3_llm_engineered": "Arm 3", "arm4_qlora": "Arm 4"}
    fig, ax = plt.subplots(figsize=(7, 5))
    for k, (f, subset) in f1.items():
        ax.scatter(sysd[k]["p50_ms"], f, s=90, marker="o", edgecolor="black",
                   facecolor="white" if subset else "#4c72b0", zorder=3)
        ax.annotate(names[k], (sysd[k]["p50_ms"], f), textcoords="offset points", xytext=(7, 5), fontsize=9)
    ax.set_xscale("log")
    ax.set_xlabel("Median single-request latency, ms (log scale; Apple M5 laptop, unbatched)")
    ax.set_ylabel("Primary macro-F1, in-distribution test (full-data regime)")
    ax.set_title("Accuracy vs. latency (measured points; no arm declared best)")
    ax.grid(True, which="both", alpha=0.25)
    fig.text(0.5, 0.005,
             "Filled: full test split. Open: 500-example subset (wide CIs; Arm 4 ID 95% CI [0.569, 0.705]). "
             "Arm 1 F1 = mean of 3 seeds.", ha="center", fontsize=7)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {output_path}")


def plot_arm4_per_class(pred_path: Path, output_path: Path) -> None:
    d = json.loads(pred_path.read_text())["splits"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 6), sharex=False)
    for ax, (split_key, title) in zip(axes, SPLITS, strict=True):
        v = d[split_key]
        classes = sorted(v["support"], key=lambda c: -v["support"][c])
        f1 = [v["per_class"][c]["f1"] for c in classes]
        labels = [f"{c[:16]} (n={v['support'][c]})" for c in classes]
        ax.barh(range(len(classes)), f1, color="#2ca02c", alpha=0.8)
        ax.set_yticks(range(len(classes)))
        ax.set_yticklabels(labels, fontsize=7)
        ax.invert_yaxis()
        ax.set_xlim(0, 1)
        ax.set_xlabel("Per-class F1")
        ax.set_title(f"Arm 4 full regime, seed 0: {title}\n(500-example subset; classes with n<10 are very noisy)",
                     fontsize=9)
        ax.grid(True, axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {output_path}")


def plot_arm4_confusion(pred_path: Path, output_path: Path) -> None:
    d = json.loads(pred_path.read_text())["splits"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 6))
    for ax, (split_key, title) in zip(axes, SPLITS, strict=True):
        v = d[split_key]
        classes = [c for c in sorted(v["support"], key=lambda c: -v["support"][c])][:10]
        m = np.zeros((len(classes), len(classes)))
        for i, t in enumerate(classes):
            tot = v["support"][t]
            for j, p in enumerate(classes):
                m[i, j] = v["confusion_matrix"][t].get(p, 0) / tot
        im = ax.imshow(m, cmap="Blues", vmin=0, vmax=1)
        ax.set_xticks(range(len(classes)))
        ax.set_yticks(range(len(classes)))
        ax.set_xticklabels([c[:10] for c in classes], rotation=60, ha="right", fontsize=7)
        ax.set_yticklabels([f"{c[:10]} (n={v['support'][c]})" for c in classes], fontsize=7)
        ax.set_xlabel("Predicted")
        ax.set_ylabel("True")
        ax.set_title(
            f"Arm 4 full regime, seed 0: {title}\nrow-normalized by true class; 10 most frequent classes only\n"
            "(rows can sum to <1: other predicted classes omitted)",
            fontsize=8,
        )
        for i in range(len(classes)):
            for j in range(len(classes)):
                if m[i, j] >= 0.1:
                    ax.text(j, i, f"{m[i, j]:.2f}", ha="center", va="center", fontsize=6,
                            color="white" if m[i, j] > 0.5 else "black")
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {output_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", default="reports/results/data_efficiency/summary.json", type=Path)
    ap.add_argument("--output-dir", default="reports/results/plots", type=Path)
    args = ap.parse_args()

    summary = json.loads(args.summary.read_text())
    results = Path("reports/results")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plot_data_efficiency(summary, args.output_dir / "data_efficiency_curves.png")
    plot_micro_f1(summary, args.output_dir / "micro_f1_vs_regime.png")
    plot_production(results, args.output_dir / "accuracy_vs_latency.png")
    pred = results / "arm4/predictions_regime_full_seed0.json"
    plot_arm4_per_class(pred, args.output_dir / "arm4_per_class_f1.png")
    plot_arm4_confusion(pred, args.output_dir / "arm4_confusion.png")


if __name__ == "__main__":
    main()

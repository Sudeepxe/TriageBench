#!/usr/bin/env python3
"""Generate plots from reports/results/data_efficiency/summary.json --
every plotted value traces back to an actual stored result file, nothing
is invented for the chart.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REGIME_ORDER = ["50", "200", "1000", "full"]
ARM_STYLE = {
    "arm0_tfidf_logreg": {"label": "Arm 0: TF-IDF + LogReg", "color": "#1f77b4", "marker": "o"},
    "arm1_distilbert_finetune": {"label": "Arm 1: DistilBERT fine-tune", "color": "#d62728", "marker": "s"},
}


def plot_data_efficiency(summary: dict, output_path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)

    for split_idx, (split_key, split_title) in enumerate(
        [("test_in_distribution", "In-distribution test"), ("test_temporal_shift", "Temporal-shift test")]
    ):
        ax = axes[split_idx]
        for arm_key, style in ARM_STYLE.items():
            by_regime = summary["arms"][arm_key]
            xs, ys, yerr, n_seeds = [], [], [], []
            for regime in REGIME_ORDER:
                if regime not in by_regime:
                    continue
                d = by_regime[regime]
                xs.append(d["n_train_examples"])
                metric = d[split_key]["primary_macro_f1"]
                ys.append(metric["mean"])
                yerr.append(metric["stdev"])
                n_seeds.append(d["n_seeds"])
            ax.errorbar(
                xs, ys, yerr=yerr, label=style["label"], color=style["color"], marker=style["marker"],
                capsize=3, linewidth=1.8, markersize=6,
            )
        ax.set_xscale("log")
        ax.set_xlabel("Training examples (log scale)")
        ax.set_title(split_title)
        ax.grid(True, which="both", alpha=0.25)

    axes[0].set_ylabel("Primary macro-F1 (20 primary classes)")
    axes[0].legend(loc="lower right", fontsize=9)
    fig.suptitle("TriageBench: data-efficiency curves (Eclipse Platform Component classification)")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {output_path}")


def plot_micro_f1(summary: dict, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    for arm_key, style in ARM_STYLE.items():
        by_regime = summary["arms"][arm_key]
        xs, ys = [], []
        for regime in REGIME_ORDER:
            if regime not in by_regime:
                continue
            d = by_regime[regime]
            xs.append(d["n_train_examples"])
            ys.append(d["test_in_distribution"]["full_micro_f1"]["mean"])
        ax.plot(xs, ys, label=style["label"], color=style["color"], marker=style["marker"], linewidth=1.8)
    ax.set_xscale("log")
    ax.set_xlabel("Training examples (log scale)")
    ax.set_ylabel("Full micro-F1 (all 21 classes), in-distribution test")
    ax.set_title("TriageBench: micro-F1 vs. data regime")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {output_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", default="reports/results/data_efficiency/summary.json", type=Path)
    ap.add_argument("--output-dir", default="reports/results/plots", type=Path)
    args = ap.parse_args()

    summary = json.loads(args.summary.read_text())
    plot_data_efficiency(summary, args.output_dir / "data_efficiency_curves.png")
    plot_micro_f1(summary, args.output_dir / "micro_f1_vs_regime.png")


if __name__ == "__main__":
    main()

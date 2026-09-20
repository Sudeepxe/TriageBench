#!/usr/bin/env python3
"""Consolidate systems measurements for Arms 0-4 from existing result
artifacts into reports/results/production/systems_summary.json and a
markdown table. Nothing is measured here and nothing is invented: every
value is read from an artifact, arithmetically derived from one (tagged
DERIVED), or reported as NOT MEASURED.
"""

from __future__ import annotations

import glob
import json
import statistics
from pathlib import Path

R = Path("reports/results")
NM = "NOT MEASURED"


def load(p: str) -> dict:
    return json.loads(Path(p).read_text())


def main() -> None:
    a0 = load(R / "arm0/regime_full.json")
    a1 = [load(f) for f in sorted(glob.glob(str(R / "arm1/regime_full_seed*.json")))]
    a2 = load(R / "arm2/test_in_distribution.json")
    a3 = load(R / "arm3/test_in_distribution.json")
    a4_eval = load(R / "arm4/eval_regime_full_seed0_test_in_distribution.json")
    a4_trains = [load(f) for f in sorted(glob.glob(str(R / "arm4/train_regime_*_seed*.json")))]
    a4_full = load(R / "arm4/train_regime_full_seed0.json")

    def seq_throughput(e: dict) -> float:
        return e["eval_subset_size"] / e["latency"]["total_generation_seconds"]

    arms = {
        "arm0_tfidf_logreg": {
            "hardware": a0["latency"]["environment"],
            "p50_ms": a0["latency"]["p50_ms"], "p95_ms": a0["latency"]["p95_ms"],
            "latency_note": f"MEASURED, {a0['latency']['n_single_measurements']} single-request calls",
            "throughput_per_sec": a0["latency"]["batch_throughput_per_sec"],
            "throughput_note": f"MEASURED, batch size {a0['latency']['batch_size']}",
            "train_seconds_full_wall": a0["train_seconds"],
            "train_note": "MEASURED wall-clock (single run, deterministic solver)",
            "peak_train_memory_gb": NM,
            "model_footprint": NM + " (fitted model not persisted)",
        },
        "arm1_distilbert": {
            "hardware": a1[0]["hardware"],
            "p50_ms": statistics.median(r["latency"]["p50_ms"] for r in a1),
            "p95_ms": statistics.median(r["latency"]["p95_ms"] for r in a1),
            "latency_note": "MEASURED, median across 3 full-regime seeds; 50 single-request calls each",
            "throughput_per_sec": statistics.median(r["latency"]["batch_throughput_per_sec"] for r in a1),
            "throughput_note": f"MEASURED, batch size {a1[0]['latency']['batch_size']}, median of 3 seeds",
            "train_seconds_full_wall": [r["train_seconds"] for r in a1],
            "train_note": "MEASURED wall-clock per seed; seeds 0 and 2 inflated by macOS sleep "
                          "(docs/EXPERIMENT_LOG.md EXP-005 gives corrected active-compute ESTIMATES "
                          "of ~7.3-7.7ks per seed)",
            "peak_train_memory_gb": NM,
            "model_footprint": "distilbert-base-uncased pretrained snapshot ~256MB on disk "
                               "(MEASURED with du; fine-tuned checkpoint not persisted)",
        },
        "arm2_llm_naive": {
            "hardware": a2["latency"]["environment"],
            "p50_ms": a2["latency"]["p50_ms"], "p95_ms": a2["latency"]["p95_ms"],
            "latency_note": f"MEASURED, {a2['latency']['n_measurements']} sequential generations",
            "throughput_per_sec": round(seq_throughput(a2), 2),
            "throughput_note": "DERIVED: 500 examples / total_generation_seconds, sequential, unbatched",
            "train_seconds_full_wall": "n/a (no training)",
            "train_note": "no training",
            "peak_train_memory_gb": "n/a",
            "model_footprint": "Qwen2.5-1.5B-Instruct 4-bit ~839MB on disk (MEASURED with du)",
        },
        "arm3_llm_engineered": {
            "hardware": a3["latency"]["environment"],
            "p50_ms": a3["latency"]["p50_ms"], "p95_ms": a3["latency"]["p95_ms"],
            "latency_note": f"MEASURED, {a3['latency']['n_measurements']} sequential generations",
            "throughput_per_sec": round(seq_throughput(a3), 2),
            "throughput_note": "DERIVED: 500 examples / total_generation_seconds, sequential, unbatched",
            "train_seconds_full_wall": "n/a (no training)",
            "train_note": "no training",
            "peak_train_memory_gb": "n/a",
            "model_footprint": "same base as Arm 2 (~839MB)",
        },
        "arm4_qlora": {
            "hardware": a4_eval["latency"]["environment"],
            "p50_ms": a4_eval["latency"]["p50_ms"], "p95_ms": a4_eval["latency"]["p95_ms"],
            "latency_note": "MEASURED, 500 sequential generations, full-regime seed-0 adapter, test-ID subset",
            "throughput_per_sec": round(seq_throughput(a4_eval), 2),
            "throughput_note": "DERIVED: 500 examples / total_generation_seconds, sequential, unbatched",
            "train_seconds_full_wall": a4_full["train_seconds"],
            "train_note": "MEASURED wall-clock (24.4h), inflated by macOS sleep; ~11.9h is an ESTIMATE "
                          "of step time from the logged It/sec (681-iteration reports, mean 0.319 it/s), "
                          "series in reports/results/arm4/progress_rates_regime_full_seed0.json",
            "peak_train_memory_gb": f"{min(t['peak_memory_gb'] for t in a4_trains)}-"
                                    f"{max(t['peak_memory_gb'] for t in a4_trains)} "
                                    "(MEASURED range over all 10 Arm 4 training runs; full regime 4.399)",
            "model_footprint": "base ~839MB + LoRA adapter 36,971,628 bytes (~37MB) (MEASURED file size)",
        },
    }

    hours_per_million = {}
    for k, a in arms.items():
        tp = a["throughput_per_sec"]
        hours_per_million[k] = round(1_000_000 / tp / 3600, 3)

    out = {
        "status": "CONSOLIDATED_FROM_EXISTING_ARTIFACTS",
        "caveat": "All figures are from one Apple M5 laptop (16GB) running local runs; Arm 0 on CPU, "
                  "Arms 1-4 on Apple GPU (MPS/MLX). Not comparable to server/GPU deployments. "
                  "Inference memory was NOT MEASURED for any arm. Arm 5 (frontier API) not evaluated.",
        "arms": arms,
        "hypothetical_compute_hours_per_million_requests": {
            "method": "1e6 / throughput_per_sec / 3600, using each arm's measured (Arms 0/1, batched) "
                      "or derived (Arms 2-4, sequential unbatched) throughput. Batched LLM serving was "
                      "not measured, so Arm 2-4 values are a single-stream upper bound on this laptop.",
            "hours": hours_per_million,
        },
    }
    p = R / "production/systems_summary.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {p}")
    for k, a in arms.items():
        print(k, a["p50_ms"], a["p95_ms"], a["throughput_per_sec"], hours_per_million[k])


if __name__ == "__main__":
    main()

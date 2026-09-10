#!/usr/bin/env python3
"""Build and freeze the train/val/test-in-distribution/test-temporal-shift
ID splits from the model-ready dataset, using the frozen boundary and
fractions in configs/experiments.yaml. Writes only IDs (+ counts), never
raw text, to reports/results/splits/ -- safe to commit, and the frozen
artifact every later script must load rather than recompute.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from triagebench.data.splitting import temporal_split  # noqa: E402


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            rows.append(
                {
                    "id": r["id"],
                    "Summary": r["summary"],
                    "Description": r["description"],
                    "creation_time": r["creation_time"],
                    "component": r["component"],
                }
            )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="data/processed/platform_model_ready.jsonl", type=Path)
    ap.add_argument("--experiments-config", default="configs/experiments.yaml", type=Path)
    ap.add_argument("--output", default="reports/results/splits/platform_splits.json", type=Path)
    args = ap.parse_args()

    cfg = yaml.safe_load(args.experiments_config.read_text())["split"]
    if not cfg["frozen"]:
        print(
            "ERROR: split.frozen is not True in configs/experiments.yaml -- refusing to build splits",
            file=sys.stderr,
        )
        sys.exit(1)

    rows = load_rows(args.input)
    result = temporal_split(
        rows,
        boundary_date=cfg["boundary_date"],
        val_fraction_of_older=cfg["older_era_fractions"]["val"],
        test_fraction_of_older=cfg["older_era_fractions"]["test_in_distribution"],
        seed=cfg["split_seed"],
    )

    total = len(result.train_ids) + len(result.val_ids) + len(result.test_in_distribution_ids) + len(
        result.test_temporal_shift_ids
    )
    assert total == len(rows), f"split lost rows: {total} != {len(rows)}"
    assert len(set(result.train_ids) & set(result.val_ids)) == 0
    assert len(set(result.train_ids) & set(result.test_in_distribution_ids)) == 0
    assert len(set(result.train_ids) & set(result.test_temporal_shift_ids)) == 0
    assert len(set(result.val_ids) & set(result.test_in_distribution_ids)) == 0

    output = {
        "status": "MEASURED",
        "source": str(args.input),
        "boundary_date": cfg["boundary_date"],
        "older_era_fractions": cfg["older_era_fractions"],
        "split_seed": cfg["split_seed"],
        "counts": {
            "train": len(result.train_ids),
            "val": len(result.val_ids),
            "test_in_distribution": len(result.test_in_distribution_ids),
            "test_temporal_shift": len(result.test_temporal_shift_ids),
            "total": total,
        },
        "duplicate_groups_spanning_boundary": result.duplicate_groups_spanning_boundary,
        "train_ids": sorted(result.train_ids),
        "val_ids": sorted(result.val_ids),
        "test_in_distribution_ids": sorted(result.test_in_distribution_ids),
        "test_temporal_shift_ids": sorted(result.test_temporal_shift_ids),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"Wrote {args.output}")
    for k, v in output["counts"].items():
        print(f"  {k}: {v}")
    print(f"  duplicate_groups_spanning_boundary: {result.duplicate_groups_spanning_boundary}")


if __name__ == "__main__":
    main()

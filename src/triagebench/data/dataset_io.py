"""Shared loaders for the compact model-ready dataset and frozen splits,
used identically by every model arm's training/evaluation script so
there is exactly one way to read them.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_model_ready(path: str | Path) -> dict[str, dict]:
    """Load data/processed/platform_model_ready.jsonl into a dict keyed
    by row id. Each row has: id, summary, description, component,
    creation_time, label_stable."""
    rows: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            rows[r["id"]] = r
    return rows


def load_splits(path: str | Path) -> dict:
    """Load the frozen split ID lists written by scripts/build_splits.py."""
    return json.loads(Path(path).read_text())


def texts_and_labels(
    ids: list[str], rows: dict[str, dict], text_fn
) -> tuple[list[str], list[str]]:
    """Build (texts, labels) for a list of ids, using `text_fn(summary,
    description) -> str` to build model input text -- e.g.
    triagebench.models.baseline.combine_text, shared across arms so every
    arm sees the filing-time text built the same way."""
    texts = [text_fn(rows[i]["summary"], rows[i]["description"]) for i in ids]
    labels = [rows[i]["component"] for i in ids]
    return texts, labels

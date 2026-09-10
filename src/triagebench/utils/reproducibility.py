"""Reproducibility metadata every experiment result must carry: which git
commit produced it and which frozen split it was evaluated against.
Shared so every arm's result JSON has an identical, complete metadata
shape rather than each script inventing its own subset.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def get_git_commit(dirty_ok: bool = True) -> str:
    """Current HEAD commit hash, with a `-dirty` suffix if the working
    tree has uncommitted changes (so a result can never be silently
    mistaken for coming from a clean, pushed commit when it didn't)."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return "UNKNOWN (git rev-parse failed)"

    if dirty_ok:
        return commit
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5).stdout
    return f"{commit}-dirty" if status.strip() else commit


def get_split_metadata(splits_path: str | Path) -> dict:
    """Reference to exactly which frozen split file/boundary a result was
    evaluated against -- not the full ID lists (too large to repeat in
    every result file), just enough to unambiguously identify it."""
    import json

    splits_path = Path(splits_path)
    data = json.loads(splits_path.read_text())
    return {
        "splits_file": str(splits_path),
        "boundary_date": data.get("boundary_date"),
        "split_seed": data.get("split_seed"),
        "counts": data.get("counts"),
    }

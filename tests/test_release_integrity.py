"""Release-integrity checks over the committed artifacts: frozen split and
long-tail policy consistency, uniform split metadata across every result
file, no tracked raw data / checkpoints, and no fabricated Arm 5 results."""

import glob
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from triagebench.evaluation.long_tail_policy import MINIMUM_SUPPORT_THRESHOLD, PRIMARY_CLASSES, RARE_CLASSES

ROOT = Path(__file__).resolve().parents[1]
RESULT_FILES = sorted(glob.glob(str(ROOT / "reports/results/arm*/*.json")))


def _exp_cfg() -> dict:
    return yaml.safe_load((ROOT / "configs/experiments.yaml").read_text())


def test_result_artifacts_exist_for_every_evaluated_arm():
    for arm in ["arm0", "arm1", "arm2", "arm3", "arm4"]:
        assert glob.glob(str(ROOT / f"reports/results/{arm}/*.json")), f"no result files for {arm}"


def test_no_arm5_result_artifacts_exist():
    assert not glob.glob(str(ROOT / "reports/results/arm5*")), "Arm 5 was not evaluated; no results may exist"


def test_every_result_file_uses_the_frozen_split_and_records_a_commit():
    cfg = _exp_cfg()
    splits = json.loads((ROOT / "reports/results/splits/platform_splits.json").read_text())
    signatures = set()
    for f in RESULT_FILES:
        d = json.loads(Path(f).read_text())
        if "split" not in d:
            continue
        s = d["split"]
        assert s["boundary_date"] == cfg["split"]["boundary_date"], f
        assert s["split_seed"] == cfg["split"]["split_seed"], f
        assert d.get("git_commit"), f"{f} has no git_commit"
        signatures.add(json.dumps(s["counts"], sort_keys=True))
    assert len(signatures) == 1
    counts = json.loads(next(iter(signatures)))
    for key in ["train", "val", "test_in_distribution", "test_temporal_shift"]:
        assert counts[key] == len(splits[f"{key}_ids"])


def test_frozen_long_tail_policy_matches_config():
    cfg = _exp_cfg()["long_tail_policy"]
    assert MINIMUM_SUPPORT_THRESHOLD == cfg["minimum_support_threshold"] == 50
    assert set(PRIMARY_CLASSES) == set(cfg["primary_classes"])
    assert set(RARE_CLASSES) == set(cfg["rare_classes"]) == {"Incubator"}
    assert len(PRIMARY_CLASSES) == 20


def test_frozen_temporal_boundary():
    assert _exp_cfg()["split"]["boundary_date"] == "2015-01-01T00:00:00Z"


def test_arm4_predictions_reproduce_recorded_metrics():
    f = ROOT / "reports/results/arm4/predictions_regime_full_seed0.json"
    d = json.loads(f.read_text())
    for split, v in d["splits"].items():
        assert v["matches_recorded_metric"], split


def test_no_raw_data_or_checkpoints_are_tracked():
    try:
        files = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    bad = [f for f in files if f.startswith("data/") or f.endswith((".safetensors", ".pt", ".bin", ".gguf", ".zip"))]
    assert not bad, bad
    csvs = [f for f in files if f.endswith(".csv") and not f.startswith("tests/fixtures/")]
    assert not csvs, csvs


def test_model_config_has_no_stale_status_and_records_substitution():
    text = (ROOT / "configs/models.yaml").read_text()
    assert "not started" not in text
    assert "ModernBERT" in text and "superseded" in text
    assert "not evaluated" in text

"""Sanity checks on the pinned dataset config. These guard against the
exact failure mode this project explicitly wants to avoid: silently
drifting to a different upstream source/version, or losing a checksum.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_CONFIG = REPO_ROOT / "configs" / "dataset.yaml"
EXPERIMENTS_CONFIG = REPO_ROOT / "configs" / "experiments.yaml"


@pytest.fixture(scope="module")
def dataset_cfg():
    return yaml.safe_load(DATASET_CONFIG.read_text())


def test_dataset_config_has_single_pinned_doi(dataset_cfg):
    ds = dataset_cfg["dataset"]
    assert ds["concept_doi"] == "10.5281/zenodo.15348468"
    assert ds["version"] == "v1.0.1"
    assert ds["license"] == "CC-BY-SA-4.0"


def test_dataset_config_files_have_md5_checksums(dataset_cfg):
    files = dataset_cfg["dataset"]["files"]
    for key in ("sample", "full_archive", "readme"):
        assert key in files, f"missing file entry: {key}"
        entry = files[key]
        assert "md5" in entry and len(entry["md5"]) == 32
        assert "filename" in entry and entry["filename"]


def test_scope_excludes_cross_product_transfer(dataset_cfg):
    assert dataset_cfg["scope"]["cross_product_transfer"] is False
    assert dataset_cfg["scope"]["primary_product"] == "Platform"


def test_csv_parsing_config_matches_parser_module(dataset_cfg):
    from triagebench.data.csv_parser import ENCODING, ESCAPECHAR

    csv_cfg = dataset_cfg["csv_parsing"]
    assert csv_cfg["escapechar"] == ESCAPECHAR
    assert csv_cfg["encoding"] == ENCODING


def test_temporal_split_boundary_is_frozen_and_unchanged():
    # Guards against silently moving the split boundary after it was
    # frozen from the real Phase 1 Platform measurement (see
    # reports/phase1_platform.json and docs/EXPERIMENT_LOG.md). Changing
    # this value requires a deliberate, documented decision -- never an
    # incidental edit.
    cfg = yaml.safe_load(EXPERIMENTS_CONFIG.read_text())
    split = cfg["split"]
    assert split["frozen"] is True
    assert split["boundary_date"] == "2015-01-01T00:00:00Z"
    assert split["strategy"] == "temporal_within_platform"
    assert split["cross_product_transfer"] is False


def test_invalid_config_missing_required_key_is_detectable():
    # A config missing "files" should not silently pass as valid -- this
    # documents the expectation for any future config-loading code that
    # wraps this file (e.g. scripts/download_dataset.py).
    minimal_cfg = {"dataset": {"name": "x"}}
    with pytest.raises(KeyError):
        _ = minimal_cfg["dataset"]["files"]

import json

from triagebench.utils.reproducibility import get_git_commit, get_split_metadata


def test_get_git_commit_returns_a_40_char_hash_in_this_repo():
    commit = get_git_commit()
    # either a real 40-hex-char SHA (possibly with -dirty), or the
    # explicit failure sentinel -- never silently empty.
    assert commit == "UNKNOWN (git rev-parse failed)" or len(commit.replace("-dirty", "")) == 40


def test_get_split_metadata_extracts_expected_fields(tmp_path):
    path = tmp_path / "splits.json"
    path.write_text(json.dumps({
        "boundary_date": "2015-01-01T00:00:00Z",
        "split_seed": 0,
        "counts": {"train": 10, "val": 2},
        "train_ids": ["a", "b"],  # should NOT be echoed into the metadata
    }))
    meta = get_split_metadata(path)
    assert meta["boundary_date"] == "2015-01-01T00:00:00Z"
    assert meta["split_seed"] == 0
    assert meta["counts"] == {"train": 10, "val": 2}
    assert "train_ids" not in meta
    assert meta["splits_file"] == str(path)

import json

from triagebench.data.dataset_io import load_model_ready, load_splits, texts_and_labels


def test_load_model_ready_keys_by_id(tmp_path):
    path = tmp_path / "model_ready.jsonl"
    rows = [
        {"id": "1", "summary": "s1", "description": "d1", "component": "UI",
         "creation_time": "2015-01-01T00:00:00Z", "label_stable": True},
        {"id": "2", "summary": "s2", "description": "d2", "component": "SWT",
         "creation_time": "2016-01-01T00:00:00Z", "label_stable": False},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    loaded = load_model_ready(path)
    assert set(loaded.keys()) == {"1", "2"}
    assert loaded["1"]["component"] == "UI"
    assert loaded["2"]["label_stable"] is False


def test_load_splits_roundtrip(tmp_path):
    path = tmp_path / "splits.json"
    data = {"train_ids": ["1", "2"], "val_ids": ["3"]}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_splits(path) == data


def test_texts_and_labels_uses_provided_text_fn():
    rows = {"1": {"summary": "sum1", "description": "desc1", "component": "UI"}}
    texts, labels = texts_and_labels(["1"], rows, lambda s, d: f"{s}|{d}")
    assert texts == ["sum1|desc1"]
    assert labels == ["UI"]

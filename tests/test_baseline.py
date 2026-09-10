from triagebench.models.baseline import (
    BaselineConfig,
    combine_text,
    predict,
    train_baseline,
)


def _synthetic_dataset(n_per_class: int = 30):
    # Trivially separable-by-keyword synthetic data, just to confirm the
    # pipeline can learn *something* and round-trips correctly -- not a
    # claim about real-world accuracy, which requires the real dataset.
    texts, labels = [], []
    keyword_by_class = {"UI": "button rendering layout", "Core": "startup plugin registry"}
    for label, keyword in keyword_by_class.items():
        for i in range(n_per_class):
            texts.append(combine_text(f"Issue {i} about {keyword}", f"Details on {keyword} problem {i}"))
            labels.append(label)
    return texts, labels


def test_combine_text_includes_both_fields():
    combined = combine_text("my summary", "my description")
    assert "my summary" in combined
    assert "my description" in combined


def test_train_baseline_learns_separable_classes():
    texts, labels = _synthetic_dataset()
    pipeline = train_baseline(texts, labels, BaselineConfig(min_df=1))
    preds = predict(pipeline, texts)
    accuracy = sum(p == y for p, y in zip(preds, labels, strict=True)) / len(labels)
    assert accuracy > 0.95, f"expected near-perfect fit on trivially separable synthetic data, got {accuracy}"


def test_train_baseline_generalizes_to_held_out_examples():
    texts, labels = _synthetic_dataset(n_per_class=50)
    train_texts, train_labels = texts[:80], labels[:80]
    held_out_texts, held_out_labels = texts[80:], labels[80:]
    pipeline = train_baseline(train_texts, train_labels, BaselineConfig(min_df=1))
    preds = predict(pipeline, held_out_texts)
    accuracy = sum(p == y for p, y in zip(preds, held_out_labels, strict=True)) / len(held_out_labels)
    assert accuracy > 0.8


def test_train_baseline_rejects_mismatched_lengths():
    import pytest

    with pytest.raises(ValueError):
        train_baseline(["a", "b"], ["only-one-label"])


def test_train_baseline_rejects_empty_input():
    import pytest

    with pytest.raises(ValueError):
        train_baseline([], [])

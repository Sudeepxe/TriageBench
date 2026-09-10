"""Arm 0: TF-IDF + Logistic Regression classical baseline.

This is the mandatory first model (see docs/DESIGN_DECISIONS.md headroom
gate). Kept intentionally simple -- a strong, standard TF-IDF + linear
classifier configuration -- since the point of this arm is to establish
whether more complex approaches (encoder, LLM, fine-tuning) actually earn
their added complexity and cost.
"""

from __future__ import annotations

from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline


@dataclass
class BaselineConfig:
    max_features: int = 50_000
    ngram_range: tuple[int, int] = (1, 2)
    min_df: int = 2
    sublinear_tf: bool = True
    C: float = 1.0
    max_iter: int = 1000
    class_weight: str | None = "balanced"
    random_state: int = 0


def build_pipeline(config: BaselineConfig | None = None) -> Pipeline:
    config = config or BaselineConfig()
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    max_features=config.max_features,
                    ngram_range=config.ngram_range,
                    min_df=config.min_df,
                    sublinear_tf=config.sublinear_tf,
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    C=config.C,
                    max_iter=config.max_iter,
                    class_weight=config.class_weight,
                    random_state=config.random_state,
                ),
            ),
        ]
    )


def combine_text(summary: str, description: str) -> str:
    """The single text field fed to the vectorizer. Kept as one explicit
    function so every arm that needs "the filing-time text as one string"
    (this baseline, and any text-dump used for LLM prompting) builds it
    identically -- see FILING_TIME_FEATURE_ALLOWLIST in csv_parser.py for
    why only these two fields are here."""
    return f"{summary}\n\n{description}"


def train_baseline(texts: list[str], labels: list[str], config: BaselineConfig | None = None) -> Pipeline:
    if len(texts) != len(labels):
        raise ValueError("texts and labels must have equal length")
    if not texts:
        raise ValueError("cannot train on zero examples")
    pipeline = build_pipeline(config)
    pipeline.fit(texts, labels)
    return pipeline


def predict(pipeline: Pipeline, texts: list[str]) -> list[str]:
    return list(pipeline.predict(texts))

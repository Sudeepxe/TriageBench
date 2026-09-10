"""Smoke test for the Arm 1 training mechanics (tokenization, padding,
collation, forward+backward pass, optimizer step) using a tiny,
randomly-initialized transformer -- NOT the real ModernBERT-base weights,
so this runs instantly and requires no network access, keeping it safe
for CI (no GPU, no download).

This exists because two real bugs in scripts/train_encoder.py were only
caught by actually running the full (slow, network-dependent) pilot: a
removed TrainingArguments kwarg, and a missing padding collator that
crashed on the very first batch. Either would have been caught in
milliseconds by this test instead of after a lengthy real run. See
docs/EXPERIMENT_LOG.md (EXP-004) for the incident this test targets.

Runs on CPU always; additionally runs on CUDA when available (skipped
otherwise) as the pre-flight check required before any cloud GPU run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import torch
from transformers import (
    BertConfig,
    BertForSequenceClassification,
    BertTokenizerFast,
    DataCollatorWithPadding,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

_spec = importlib.util.spec_from_file_location("train_encoder", REPO_ROOT / "scripts" / "train_encoder.py")
train_encoder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(train_encoder)

TINY_VOCAB = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"] + [f"tok{i}" for i in range(50)]


def _build_tiny_model_and_tokenizer(num_labels: int = 4):
    import tempfile

    vocab_dir = Path(tempfile.mkdtemp())
    vocab_file = vocab_dir / "vocab.txt"
    vocab_file.write_text("\n".join(TINY_VOCAB), encoding="utf-8")
    tokenizer = BertTokenizerFast(vocab_file=str(vocab_file))

    config = BertConfig(
        vocab_size=len(TINY_VOCAB),
        hidden_size=16,
        num_hidden_layers=1,
        num_attention_heads=2,
        intermediate_size=32,
        max_position_embeddings=64,
        num_labels=num_labels,
    )
    model = BertForSequenceClassification(config)
    return model, tokenizer


def _run_forward_backward_on(device: str) -> float:
    """Returns the loss value after one optimizer step, to confirm both
    the forward and backward pass actually executed (a NaN or exception
    means something is broken, not just slow)."""
    model, tokenizer = _build_tiny_model_and_tokenizer()
    model = model.to(device)

    texts = ["tok1 tok2 tok3", "tok4 tok5", "tok6 tok7 tok8 tok9 tok10"]  # deliberately variable length
    labels = [0, 1, 2]

    class TinyDataset(torch.utils.data.Dataset):
        def __init__(self):
            self.encodings = tokenizer(texts, truncation=True, max_length=16, padding=False)

        def __len__(self):
            return len(labels)

        def __getitem__(self, idx):
            item = {k: torch.tensor(v[idx]) for k, v in self.encodings.items()}
            item["labels"] = torch.tensor(labels[idx])
            return item

    ds = TinyDataset()
    collator = DataCollatorWithPadding(tokenizer)
    batch = collator([ds[i] for i in range(len(ds))])
    batch = {k: v.to(device) for k, v in batch.items()}

    # This is exactly the failure mode caught in EXP-004: without a
    # padding-aware collator, variable-length examples can't be stacked
    # into one batch tensor at all.
    assert batch["input_ids"].shape[0] == 3
    assert batch["attention_mask"].shape == batch["input_ids"].shape

    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    model.train()
    outputs = model(**batch)
    loss = outputs.loss
    assert loss is not None and torch.isfinite(loss)
    loss.backward()
    optimizer.step()
    optimizer.zero_grad()
    return float(loss.item())


def test_forward_backward_pass_on_cpu():
    loss = _run_forward_backward_on("cpu")
    assert loss > 0  # cross-entropy loss on an untrained head should be positive


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA not available in this environment")
def test_forward_backward_pass_on_cuda():
    """Kept for a possible future paid-cloud run (currently withdrawn --
    see docs/DESIGN_DECISIONS.md, Arm 1 is $0-cost local MPS/CPU only).
    If this is ever needed again: it must pass before launching any real
    cloud GPU run."""
    loss = _run_forward_backward_on("cuda")
    assert loss > 0


@pytest.mark.skipif(not torch.backends.mps.is_available(), reason="MPS not available in this environment")
def test_forward_backward_pass_on_mps():
    """The mandatory pre-flight check before any real Arm 1 MPS run
    (the local, $0-cost execution path) -- if this fails, don't launch
    the real pilot."""
    loss = _run_forward_backward_on("mps")
    assert loss > 0


def test_get_device_override_rejects_unavailable_device():
    if not torch.cuda.is_available():
        with pytest.raises(RuntimeError):
            train_encoder.get_device(override="cuda")
    if not torch.backends.mps.is_available():
        with pytest.raises(RuntimeError):
            train_encoder.get_device(override="mps")


def test_get_device_override_accepts_available_device():
    if torch.cuda.is_available():
        assert train_encoder.get_device(override="cuda") == "cuda"
    if torch.backends.mps.is_available():
        assert train_encoder.get_device(override="mps") == "mps"
    assert train_encoder.get_device(override="cpu") == "cpu"


def test_get_device_auto_detects_something_valid():
    device = train_encoder.get_device(override=None)
    assert device in ("cuda", "mps", "cpu")


def test_configure_determinism_does_not_raise_on_cpu():
    train_encoder.configure_determinism(seed=0, device="cpu")


def test_describe_hardware_includes_device_string():
    desc = train_encoder.describe_hardware("cpu")
    assert "device=cpu" in desc

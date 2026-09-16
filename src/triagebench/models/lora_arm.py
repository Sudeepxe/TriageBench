"""Arm 4: QLoRA fine-tuning of the same Qwen2.5-1.5B-Instruct-4bit base
model used by Arms 2/3, via mlx-lm's native LoRA support.

"QLoRA" is the accurate term here, not just "LoRA": the base model
(mlx-community/Qwen2.5-1.5B-Instruct-4bit) is already 4-bit quantized,
and only the small LoRA adapter matrices are trained on top of it in
float16 -- exactly the QLoRA pattern (LoRA adapters over a frozen
quantized base), not full-precision-base LoRA.

Every hyperparameter below is frozen BEFORE any Arm 4 training run, per
the project's no-post-hoc-tuning rule -- this module is imported by both
the training script and the result-recording code so the two can never
silently disagree about what was actually used.
"""

from __future__ import annotations

import json
from pathlib import Path

from triagebench.models.llm_prompting import build_engineered_prompt

MODEL_NAME = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"  # same base as Arms 2/3

# Frozen 2026-09-16, before any Arm 4 result existed. Chosen as standard,
# well-documented values (not swept) -- see docs/EXPERIMENT_LOG.md for
# the rationale.
LORA_CONFIG = {
    "rank": 8,  # mlx-lm default
    "dropout": 0.05,  # light regularization; standard LoRA practice for small fine-tuning sets
    "scale": 20.0,  # mlx-lm's LoRA scale == the peft-style alpha/rank ratio directly;
    # peft-equivalent alpha = scale * rank = 160
    "num_layers": 28,  # ALL transformer blocks (Qwen2.5-1.5B has 28) -- broader adaptation
    # than mlx-lm's default of 16, since this is a genuinely different task
    # (classification) from generic instruction-following, not a continuation of it.
}
TARGET_MODULES = (
    "auto (mlx-lm default): every nn.Linear/QuantizedLinear/nn.Embedding submodule "
    "within the selected layers -- for Qwen2.5's architecture this covers "
    "q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj in every block."
)

LEARNING_RATE = 1e-4  # standard LoRA learning rate (mlx-lm's own default, 1e-5, is tuned
# for a generic/conservative case; 1e-4 is the widely-used LoRA-paper-scale value)
EPOCHS = 3  # matches Arm 1's epoch count for methodological parity
BATCH_SIZE = 4
GRAD_ACCUMULATION_STEPS = 4  # effective batch size 16, matching Arm 1's batch size
MAX_SEQ_LENGTH = 512
PRECISION = "4-bit quantized frozen base (QLoRA); LoRA adapters trained in float16"
SEEDS = [0, 1, 2]

# Set True after the pilot's first attempt hit a genuine Metal
# out-of-memory error on this 16GB M5 during the very first training
# step (full, non-checkpointed backprop through all 28 layers of a
# 1.5B-parameter model). This is a pure memory/compute tradeoff
# (recompute activations during backward instead of storing all of
# them) -- it does not change batch size, learning rate, LoRA rank, or
# any other experimental variable, so it does not alter the frozen
# methodology. See docs/EXPERIMENT_LOG.md for the diagnosis.
GRAD_CHECKPOINT = True


def compute_iters(
    n_train_examples: int,
    epochs: int = EPOCHS,
    effective_batch_size: int = BATCH_SIZE * GRAD_ACCUMULATION_STEPS,
) -> int:
    """mlx-lm's LoRA trainer runs a fixed step count (`iters`), not an
    epoch count -- this converts our epoch target into the equivalent
    number of steps for a given regime's training set size, so every
    regime gets the same effective number of passes over its data."""
    steps_per_epoch = max(1, n_train_examples // effective_batch_size)
    return steps_per_epoch * epochs


def build_completion_examples(ids: list[str], rows: dict[str, dict]) -> list[dict]:
    """Build {"prompt": ..., "completion": ...} records for mlx-lm's
    CompletionsDataset -- the same engineered prompt template as Arm 3
    (so any quality difference is attributable to the LoRA adapter, not
    a different prompt), with the true Component label as the target
    completion."""
    examples = []
    for i in ids:
        row = rows[i]
        prompt = build_engineered_prompt(row["summary"], row["description"])
        examples.append({"prompt": prompt, "completion": f" {row['component']}"})
    return examples


def write_jsonl(examples: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

"""Smoke test for Arm 4's LoRA mechanics -- fast (<1s), no network, no
real model download. Exercises the exact primitives scripts/train_lora.py
depends on (linear_to_lora_layers, forward+backward through a LoRA
layer, saving trainable-parameter checkpoints) using a tiny synthetic
mlx.nn.Linear rather than the real 1.5B Qwen2.5 model.

MLX only runs on Apple Silicon (no Linux/CPU-only build), so this whole
module is skipped when `mlx` isn't importable -- e.g. on the Linux CI
runner, which never installs the optional "llm" extra (see pyproject.toml:
mlx-lm is Darwin-only). This mirrors how tests/test_encoder_training_smoke.py
skips its CUDA case.
"""

import importlib.util

import pytest

mlx_available = importlib.util.find_spec("mlx") is not None and importlib.util.find_spec("mlx_lm") is not None

pytestmark = pytest.mark.skipif(not mlx_available, reason="mlx/mlx-lm not installed (Apple Silicon only)")

if mlx_available:
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx_lm.tuner.lora import LoRALinear


def test_lora_layer_wraps_base_linear_and_freezes_base_weights():
    base = nn.Linear(16, 16)
    base.freeze()
    lora_layer = LoRALinear.from_base(base, r=4, scale=2.0, dropout=0.0)

    trainable = lora_layer.trainable_parameters()
    assert "lora_a" in trainable
    assert "lora_b" in trainable
    assert "weight" not in trainable  # base weight must stay frozen


def test_lora_forward_pass_produces_correct_output_shape():
    base = nn.Linear(16, 8)
    base.freeze()
    lora_layer = LoRALinear.from_base(base, r=4, scale=2.0, dropout=0.0)

    x = mx.random.normal((3, 16))
    y = lora_layer(x)
    assert y.shape == (3, 8)


def test_lora_backward_pass_updates_only_adapter_weights():
    # Note on lora_a's gradient at step 0: mlx-lm initializes lora_b to
    # all zeros (standard LoRA init, so the adapter contributes nothing
    # before training). Because the output depends on lora_a only through
    # lora_b (output = base(x) + scale * lora_b(lora_a(x))), the gradient
    # w.r.t. lora_a is mathematically exactly zero on the very first
    # backward pass -- this is correct LoRA behavior, not a bug. Only
    # lora_b receives a nonzero gradient initially; once an optimizer
    # step moves lora_b away from zero, lora_a starts receiving gradient
    # too. This test checks both phases explicitly instead of assuming
    # both are nonzero from step 0.
    base = nn.Linear(16, 8)
    base.freeze()
    lora_layer = LoRALinear.from_base(base, r=4, scale=2.0, dropout=0.0)

    x = mx.random.normal((5, 16))
    target = mx.random.normal((5, 8))

    def loss_fn(model, x, target):
        pred = model(x)
        return nn.losses.mse_loss(pred, target)

    loss_and_grad = nn.value_and_grad(lora_layer, loss_fn)
    loss_before, grads = loss_and_grad(lora_layer, x, target)

    assert "lora_a" in grads and "lora_b" in grads
    assert mx.abs(grads["lora_a"]).sum().item() == 0  # expected at step 0, given zero-init lora_b
    assert mx.abs(grads["lora_b"]).sum().item() > 0  # lora_b is what actually starts learning

    optimizer = optim.Adam(learning_rate=1e-2)
    optimizer.update(lora_layer, grads)
    mx.eval(lora_layer.parameters())

    # After the update, lora_b is no longer zero, so a second backward
    # pass should now show a nonzero gradient for lora_a too.
    _, grads_after = loss_and_grad(lora_layer, x, target)
    assert mx.abs(grads_after["lora_a"]).sum().item() > 0

    # After an optimizer step, a second forward pass should produce a
    # different (ideally lower) loss -- confirms the adapter actually
    # trains, not just that gradients exist.
    loss_after, _ = loss_and_grad(lora_layer, x, target)
    assert loss_before.item() != loss_after.item()


def test_lora_checkpoint_can_be_saved_and_reloaded(tmp_path):
    from mlx.utils import tree_flatten

    base = nn.Linear(16, 8)
    base.freeze()
    lora_layer = LoRALinear.from_base(base, r=4, scale=2.0, dropout=0.0)

    checkpoint_path = tmp_path / "adapter.safetensors"
    flat_params = dict(tree_flatten(lora_layer.trainable_parameters()))
    mx.save_safetensors(str(checkpoint_path), flat_params)

    assert checkpoint_path.exists()
    reloaded = mx.load(str(checkpoint_path))
    assert set(reloaded.keys()) == set(flat_params.keys())
    for key in flat_params:
        assert mx.array_equal(reloaded[key], flat_params[key])


def test_linear_to_lora_layers_reports_small_trainable_fraction():
    """End-to-end-ish check on a tiny multi-layer stand-in model (not the
    real Qwen2.5) that mlx_lm's own layer-conversion utility produces the
    expected small trainable-parameter ratio, the same code path
    scripts/train_lora.py calls against the real model."""
    from mlx_lm.tuner.utils import linear_to_lora_layers

    class TinyBlock(nn.Module):
        def __init__(self):
            super().__init__()
            self.self_attn = nn.Module()
            self.self_attn.q_proj = nn.Linear(32, 32)
            self.self_attn.v_proj = nn.Linear(32, 32)

        def __call__(self, x):
            return self.self_attn.v_proj(self.self_attn.q_proj(x))

    class TinyModel(nn.Module):
        def __init__(self, n_layers=2):
            super().__init__()
            self.layers = [TinyBlock() for _ in range(n_layers)]

        def __call__(self, x):
            for layer in self.layers:
                x = layer(x)
            return x

    model = TinyModel(n_layers=2)
    model.freeze()
    linear_to_lora_layers(model, num_layers=2, config={"rank": 4, "scale": 2.0, "dropout": 0.0})

    from mlx.utils import tree_flatten

    trainable = dict(tree_flatten(model.trainable_parameters()))
    all_params = dict(tree_flatten(model.parameters()))
    n_trainable = sum(v.size for v in trainable.values())
    n_total = sum(v.size for v in all_params.values())
    assert 0 < n_trainable < n_total  # some params are trainable (LoRA), not all (base frozen)

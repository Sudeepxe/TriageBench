# Production / systems analysis

Source of every number: `reports/results/production/systems_summary.json`,
built by `scripts/build_production_table.py` from existing result artifacts.
Nothing here was newly benchmarked. Tags: **MEASURED** (read from an artifact),
**DERIVED** (arithmetic on measured values), **ESTIMATE** (inferred, method
stated), **NOT MEASURED**.

**Hardware, for every number below:** one Apple M5 MacBook Pro, 16 GB unified
memory. Arm 0 ran on CPU (scikit-learn); Arms 1-4 on the Apple GPU (PyTorch MPS
for Arm 1, MLX for Arms 2-4). No server, NVIDIA/CUDA or cloud measurement exists
in this project (the project budget for external compute and paid APIs is $0).
These figures are not transferable to other hardware.

## Arms 0-4 side by side (full-data regime where a regime applies)

| | Arm 0 TF-IDF+LogReg | Arm 1 DistilBERT | Arm 2 LLM naive prompt | Arm 3 LLM engineered prompt | Arm 4 QLoRA |
|---|---|---|---|---|---|
| Median latency, single request | 0.211 ms (M) | 6.83 ms (M, median of 3 seeds) | 151.8 ms (M) | 146.6 ms (M) | 218.4 ms (M) |
| p95 latency | 0.330 ms (M) | 10.33 ms (M) | 197.6 ms (M) | 188.2 ms (M) | 304.5 ms (M) |
| Latency measurement | 200 single calls, CPU | 50 single calls per seed | 500 sequential generations | 500 sequential generations | 500 sequential generations |
| Throughput | 18,313 /s (M, batch 64) | 10,969 /s (M, batch 32, median of 3 seeds) | 6.46 /s (D, sequential, unbatched) | 6.64 /s (D) | 4.36 /s (D) |
| Peak training memory | NOT MEASURED | NOT MEASURED | n/a | n/a | 4.399-6.05 GB (M, range over all 10 Arm 4 runs; 4.399 at full) |
| Inference memory | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED |
| Model footprint | not persisted; NOT MEASURED | pretrained snapshot ~256 MB (M, `du`); fine-tuned checkpoint not persisted | 4-bit base ~839 MB (M, `du`) | same as Arm 2 | base ~839 MB + LoRA adapter 36,971,628 B (~37 MB) (M) |
| Training wall-clock, full data | 50.9 s (M) | 57,327 / 7,742 / 24,168 s for seeds 0/1/2 (M, sleep-inflated) | none | none | 87,684 s = 24.4 h (M, sleep-inflated) |
| Training active-compute | ~51 s | ~7.3-7.7 ks per seed, about 2.0-2.2 h (E; EXP-005) | none | none | ~11.9 h (E; see below) |
| Trainable parameters | n/a | full fine-tune of a ~67M-parameter encoder | none | none | 9.232 M of 1,543.714 M (0.598%) (M, mlx-lm log) |
| Primary macro-F1, ID / shift (see caveat) | 0.6295 / 0.4261 | 0.6123 ± 0.0203 / 0.4449 | 0.1284 / 0.2247 | 0.1664 / 0.1996 | 0.6262 / 0.4723 |
| Evaluated on | full test split | full test split | 500-example subset | 500-example subset | 500-example subset |
| Seeds at full data | 1 (deterministic) | 3 | n/a | n/a | 1 |

M = measured, D = derived, E = estimate. Arm 5 (frontier API): **not
evaluated**; no credentials and no paid API use, so no latency, cost or
accuracy exists for it and none is inferred.

## Notes and caveats on the measured values

- **Latency is not identical across protocols.** Arm 0 latency is CPU
  single-request scikit-learn inference; Arm 1 is a single-request MPS forward
  pass (max length 256); Arms 2-4 are unbatched autoregressive generation of at
  most 20 new tokens. Arm 4's median varied from 216 to 287 ms across its ten
  evaluated adapters (all evaluated on the same subset), a reminder that
  single-run laptop latency carries several tens of percent of run-to-run
  spread. Latency was not measured under load, with concurrency, or at cold
  start; model load time was not measured.
- **Throughput for Arms 2-4 is derived** as 500 / total generation seconds for
  sequential single-stream generation. Batched or server-side LLM serving
  (which would raise throughput substantially) was not measured and is not
  assumed.
- **Training wall-clock is contaminated by laptop sleep.** `caffeinate -i` does
  not prevent lid-closed sleep. `pmset -g log` diagnoses in EXP-005 (Arm 1) and
  EXP-007 (Arm 4) show Clamshell and maintenance-sleep intervals inflating
  several runs: Arm 1 full seed 0 reported 57,327 s against ~7,330 s of active
  compute; Arm 4 1000/class seed 0 reported 57,854 s (16.1 h) against a
  corrected ~3.5 h; Arm 4 full-regime seed 0 reported 87,684 s (24.4 h). The
  raw values are kept unedited in the result files; they are not compute
  costs. Two Arm 4 runs at 1000/class (seeds 1 and 2) ran without sleep and
  took 2.58 h and 3.22 h wall-clock. One Arm 4 full-regime attempt was lost to
  an unexplained reboot (EXP-007) and rerun unchanged; its wall-clock is not
  included above.
- **Arm 4 full-regime active-compute (~11.9 h) is an ESTIMATE**: the sum of
  681 iterations divided by each logged It/sec figure over the 20 progress
  reports (mean 0.319 it/s), taken from `data/train_full_seed0.log` (not tracked); the extracted
  series is stored in
  `reports/results/arm4/progress_rates_regime_full_seed0.json`. It excludes the four validation passes (about 35 s each)
  and any sleep-induced distortion of the logged rates.
- **Peak memory is MLX-tracked GPU memory** (`mx.get_peak_memory()`), not
  process resident memory, and covers training only. Arm 1's training memory and
  every arm's inference memory were not recorded. (An earlier, superseded
  ModernBERT pilot observed a ~9.7 GB process footprint; that model is not part
  of the results.)
- **Accuracy figures are not on one evaluation set.** Arms 0/1 use the full
  test splits; Arms 2-4 use a fixed 500-example subset per split. The
  accuracy row is context for the cost columns, not a ranking (see
  `docs/RESULTS.md`; Arm 4's ID 95% interval is [0.569, 0.705]).

## Transparent economics (measured inputs, hypothetical prices)

No price for any resource was measured. The only assumption introduced is a
hypothetical machine price `R` in dollars per hour, applied to measured or
derived throughput. It is a knob, not a fact: choose the rate of whatever
hardware you would actually serve from.

**Serving compute per one million requests**, at each arm's measured (Arms 0/1,
batched) or derived (Arms 2-4, sequential) throughput on this laptop:

| Arm | Compute-hours per 1M requests (D) | Cost at hypothetical R = $1/h | Kind of throughput |
|---|---:|---:|---|
| 0 | 0.015 h (about 55 s) | $0.015 | batched, measured |
| 1 | 0.025 h (about 91 s) | $0.025 | batched, measured |
| 2 | 43.0 h | $43.0 | sequential, derived |
| 3 | 41.8 h | $41.8 | sequential, derived |
| 4 | 63.7 h | $63.7 | sequential, derived |

Read this as a single-stream, laptop-hardware upper bound for the LLM arms: the
batched-versus-unbatched difference is exactly the quantity that was not
measured, and Arm 0/1 were measured batched. Costs scale linearly in `R`.

**Training compute per fit (full data)**, hypothetical R = $1/h applied to the
active-compute values above: Arm 0 about $0.0001; Arm 1 about $2 (about 2 h);
Arm 4 about $12 (estimated 11.9 h active; $24 if the sleep-inflated 24.4 h were
billed). At 1000 examples per class Arm 4 needed ~3.5 h of active compute per
seed (EXP-007, sleep-free runs 2.58-3.22 h wall-clock) versus about 27 minutes
of wall-clock for Arm 1 seeds 1-2 and 13 s for Arm 0.

**Labelling cost was not measured** and is deliberately not modelled. The data
efficiency results (`docs/RESULTS.md`) show how many labelled examples each arm
needed to reach a given macro-F1, so a team can price labels itself.

**Volume illustration (D):** at 10,000 requests per day the measured throughput
implies about 0.5 s (Arm 0), 0.9 s (Arm 1) and about 38 min (Arm 4, sequential)
of compute per day on this laptop. All three fit within one machine at that
volume; the differences that matter operationally are the retraining effort,
the serving stack, and the latency budget, not raw daily compute.

## What the measurements do and do not support

- Arm 0 is orders of magnitude cheaper to train and serve than the LLM arms
  and has sub-millisecond latency. Its primary macro-F1 at full data is
  statistically indistinguishable from Arm 1 and Arm 4 on ID data.
- Arm 4 costs the most in training time and per-request latency on this
  hardware. Its full-data accuracy gain over Arm 0/1 is not established
  (overlapping intervals, subset evaluation, one seed); at 1000 examples per
  class its point estimate exceeds Arms 0/1 on all three seeds, but by margins
  comparable to the subset's confidence-interval half-width.
- No arm is declared best. Whether Arm 4's extra cost is justified depends on
  requirements not measured here: accuracy needed under temporal drift, latency
  budget, serving hardware, retraining cadence and labelling cost.

## Limitations of this analysis

- Single machine, single OS build, laptop thermal behaviour, no concurrency.
- Inference memory, energy, model load time, cold start and tail latency under
  load: NOT MEASURED.
- No GPU-server, CPU-only-LLM, or batched-LLM measurement.
- Arm 5 (frontier API) has no measurements: no credentials and a $0 API budget.
- Economics use one hypothetical hourly rate and no measured prices; they
  compare relative compute effort only.

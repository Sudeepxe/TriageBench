# Conclusion

**Question.** For production technical-issue routing (single-label Component
classification of Eclipse Platform issues from filing-time text), what does the
evidence say about the trade-off between classical ML, a fine-tuned encoder,
prompted small LLMs, and a fine-tuned small LLM?

**Short answer.** With enough labelled data, classical TF-IDF + logistic
regression, a fine-tuned DistilBERT and a QLoRA-fine-tuned 1.5B LLM reach
statistically indistinguishable in-distribution accuracy on this task, while
prompting the same small LLM without fine-tuning is far worse. The arms differ
mainly in data efficiency, reliability at small data, and cost. The measurements
do not establish a winner, and they do not rule out that the simplest model is
sufficient.

## Measured evidence

- **Full data, in-distribution primary macro-F1:** Arm 0 (TF-IDF+LogReg)
  0.6295, Arm 1 (DistilBERT) 0.6123 ± 0.0203, Arm 4 (QLoRA, one seed) 0.6262
  (95% interval [0.569, 0.705]). Arms 2/3 (prompting only): 0.1284 / 0.1664.
- **Full data, temporal shift:** Arm 0 0.4261, Arm 1 0.4449, Arm 4 0.4723
  (interval [0.401, 0.588]), Arms 2/3 0.2247 / 0.1996. Every trained arm loses
  0.15-0.20 macro-F1 from the in-distribution to the temporal split.
- **Data efficiency:** at 50 examples per class TF-IDF is clearly ahead
  (0.396 vs. 0.167 DistilBERT, 0.212 ± 0.133 QLoRA). At 200 the QLoRA arm is
  close to TF-IDF (0.470 vs. 0.499). At 1000 the QLoRA point estimate
  (0.627 ± 0.029) is above TF-IDF (0.575) and DistilBERT (0.534) on all three
  seeds, then stays flat at 0.626 at full data while the others catch up.
- **Reliability:** one of three QLoRA seeds at 50 per class collapsed to
  predicting a single class (macro-F1 0.024); no collapse occurred at 200 or
  more per class.
- **Cost (local Apple M5, measured or derived):** TF-IDF trains in about 51 s and
  serves at 0.21 ms per request; DistilBERT about 2 h of active training and
  6.8 ms per request; the QLoRA arm an estimated 11.9 h of active training at
  full data and 218 ms per request unbatched, with a 37 MB adapter on an 839 MB
  4-bit base and 4.4 GB peak training memory.
- **Error structure (Arm 4 only):** errors concentrate on a few classes, with
  UI acting as a sink; temporal shift is largely a class-prior shift (IDE is 2.5%
  of training data and 10.3% of the temporal split) that the model handles
  poorly (IDE recall 0.05).

## Uncertainty

- Arm 4's accuracy comes from a 500-example subset per split. Its interval is
  roughly ±0.06 wide, comparable to the gaps that separate it from the other
  arms at 1000 per class, and all its comparisons involve full-test-set
  estimates for Arms 0/1. No paired test was possible.
- The full-data Arm 4 result is a single seed.
- Differences among Arms 0, 1 and 4 at full data, on both splits, lie within
  the overlapping intervals; the ordering of their point estimates should not be
  read as a ranking.

## Evaluation-design limitations

- Subset versus full-test-set evaluation (above); Arms 0/1 predictions and
  models were not stored, so cross-arm error analysis and disagreement analysis
  could not be done.
- Incubator (the rare class) is absent from Arm 4's subsets, so its rare-class
  behaviour is unmeasured.
- The encoder is DistilBERT, a substitute for the originally planned
  ModernBERT, chosen for local feasibility; a stronger encoder might behave
  differently.
- The frontier API arm (Arm 5) was not evaluated (no credentials, $0 budget), so
  there is no upper-bound reference from a large model.
- Labels are the settled Component, with 17.81% label instability; a share of
  all arms' errors is not recoverable from filing-time text.
- One dataset, one product (Eclipse Platform), one machine.

## Production considerations

- **When classical ML is enough.** If an ID macro-F1 near 0.63 is adequate, TF-IDF
  + logistic regression delivers it with seconds of training, sub-millisecond
  latency, a small footprint and a trivially reproducible pipeline; the
  measurements here show no accuracy penalty for choosing it at full data. It
  also led decisively when labelled data was scarce (50 per class).
- **When more complexity might be justified.** The QLoRA arm's advantage, if any,
  appears in a mid-data regime (about 1000 examples per class) and possibly
  under temporal shift; both are within the noise of this evaluation. It should
  be chosen only if a properly powered evaluation on the deployment data shows a
  gain large enough to justify a request latency about three orders of magnitude higher than TF-IDF
  (218 ms vs. 0.21 ms, unbatched, on this laptop), hours instead of seconds of
  training, GPU-backed generation on this hardware (CPU-only or batched LLM
  serving was not measured), and the need to validate every fine-tuning run
  against collapse.
- **Prompt-only use of the one small LLM tested is not a substitute** for
  supervised fine-tuning on this task (Qwen2.5-1.5B, two prompts). Other models
  or larger LLMs were not tested.
- **Temporal drift is the largest measured effect.** The 0.15-0.20 macro-F1 lost
  from the in-distribution to the temporal split is several times larger than the
  differences among Arms 0, 1 and 4 within either split. That points to
  monitoring and periodic retraining as important, and cheaper-to-retrain
  models as more attractive; this is an inference from the measurements, not a
  tested claim (no retraining-cadence experiment was run).
- **Decision rule suggested by the evidence** (not a finding): start from the
  simplest model, measure it on recent held-out data, and move to a costlier
  model only when its measured gain under the same evaluation covers its
  measured latency, training and reliability costs.

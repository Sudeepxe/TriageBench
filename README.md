# TriageBench

**Data-Efficient Model Selection for Production Technical-Issue Routing**

> Status: Phase 1 (data measurement) in progress. This README is a stub and
> will be replaced with real findings once results exist — see
> [Section 40 policy](docs/DESIGN_DECISIONS.md): conclusions are written
> after experiments, never before.

## What is this?

A controlled, reproducible engineering experiment comparing model
strategies — classical ML, an encoder, prompting, LoRA fine-tuning, and a
frontier API — for routing newly filed technical issues to the correct
software component, under real constraints: labeled-data availability,
latency, temporal distribution shift, and cost.

## Why does it matter?

Engineering teams building an issue-triage system must decide whether to
use classical ML, an encoder model, prompting, or a fine-tuned small LLM.
That decision should be evidence-driven, not assumed. This project does not
presuppose that the LLM wins.

## Data

Eclipse Platform issue reports from the [Eclipse Issue Report Dataset](https://zenodo.org/doi/10.5281/zenodo.15348468)
(Zenodo, DOI `10.5281/zenodo.15348468`, v1.0.1, CC-BY-SA-4.0). See
[docs/DATASET_CARD.md](docs/DATASET_CARD.md) for provenance, license, and
measured statistics. The raw dataset is **not** redistributed in this repo —
see `configs/dataset.yaml` and `make inspect-sample` / `make inspect-full`.

## Status

See [docs/EXPERIMENT_LOG.md](docs/EXPERIMENT_LOG.md) for what has actually
been run, and [docs/RESULTS.md](docs/RESULTS.md) for measured outcomes
(populated as experiments complete).

## Reproducing this project

```
uv venv && uv pip install -e ".[dev]"
make inspect-sample   # validates schema against the small upstream sample
make inspect-full     # after the sample passes, measures the full Platform data
```

## License

Code: MIT (see [LICENSE](LICENSE)). Dataset: CC-BY-SA-4.0, not redistributed
here — see [docs/DATASET_CARD.md](docs/DATASET_CARD.md).

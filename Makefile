.PHONY: setup test lint inspect-sample download-full inspect-full clean

setup:
	uv venv
	uv pip install -e ".[dev]"

test:
	uv run pytest -q

lint:
	uv run ruff check src tests scripts

# Phase 1, step 1: validate schema/parsing against the small upstream sample
# BEFORE touching the 17GB archive. Never skip this.
inspect-sample:
	uv run python scripts/download_dataset.py sample
	uv run python scripts/download_dataset.py readme
	uv run python scripts/inspect_phase1.py \
		--input data/raw/sample_data.csv \
		--output reports/phase1_sample.json

# Phase 1, step 2: only after inspect-sample has been reviewed and passes.
# Streams *only* the Platform ZIP member over HTTP range requests (the
# archive stores each of the 9 Eclipse projects as an independent ZIP
# entry), so we never download the other 8 projects (~9GB saved) or the
# full 17.2GB archive to disk.
inspect-full:
	uv run python scripts/prepare_data.py extract-platform \
		--output data/raw/platform_full.csv
	uv run python scripts/inspect_phase1.py \
		--input data/raw/platform_full.csv \
		--output reports/phase1_platform.json \
		--product-filter Platform \
		--stated-total 122497

# Fallback: download the full 17.2GB archive (checksum-verified) if you
# need the other 8 projects too, or if range requests are unavailable.
download-full:
	uv run python scripts/download_dataset.py full_archive

clean:
	rm -rf .pytest_cache .ruff_cache

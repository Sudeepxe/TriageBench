#!/usr/bin/env python3
"""Deterministic, checksum-verified download of the pinned Eclipse dataset.

Reads the pin from configs/dataset.yaml — never hardcodes a URL/checksum
here — so there is exactly one place that defines which upstream version
this project uses.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs" / "dataset.yaml"
API_BASE = "https://zenodo.org/api/records"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def md5sum(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        written = 0
        last_pct = -1
        with tmp.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                f.write(chunk)
                written += len(chunk)
                if total:
                    pct = int(100 * written / total)
                    if pct != last_pct and pct % 5 == 0:
                        print(f"  {pct}% ({written / 1e9:.2f} GB / {total / 1e9:.2f} GB)")
                        last_pct = pct
    tmp.rename(dest)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("which", choices=["sample", "full_archive", "readme"])
    ap.add_argument(
        "--force", action="store_true",
        help="re-download even if the file already exists with a matching checksum",
    )
    args = ap.parse_args()

    cfg = load_config()
    concept_record_id = cfg["dataset"]["concept_doi"].split(".")[-1]
    file_cfg = cfg["dataset"]["files"][args.which]
    expected_md5 = file_cfg["md5"]
    filename = file_cfg["filename"]

    local_paths = cfg["dataset"]["download"]["local_paths"] if args.which != "readme" else {}
    if args.which == "sample":
        dest = REPO_ROOT / local_paths["sample_csv"]
    elif args.which == "full_archive":
        dest = REPO_ROOT / local_paths["full_archive"]
    else:
        dest = REPO_ROOT / "data" / "raw" / "upstream_README.md"

    if dest.exists() and not args.force:
        actual = md5sum(dest)
        if actual == expected_md5:
            print(f"OK: {dest} already present with matching MD5 ({actual}).")
            return
        print(f"WARNING: {dest} exists but MD5 mismatch (got {actual}, expected {expected_md5}). Re-downloading.")

    url = f"{API_BASE}/{concept_record_id}/files/{filename}/content"
    print(f"Downloading {filename} ({file_cfg['size_bytes'] / 1e9:.3f} GB expected) from {url}")
    download(url, dest)

    actual_md5 = md5sum(dest)
    if actual_md5 != expected_md5:
        print(
            f"ERROR: checksum mismatch for {dest}. Expected {expected_md5}, got {actual_md5}. "
            "Deleting the downloaded file.",
            file=sys.stderr,
        )
        dest.unlink(missing_ok=True)
        sys.exit(1)

    print(f"OK: downloaded and verified {dest} (MD5 {actual_md5}).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Stream the full Platform CSV (15.34GB) exactly once and produce a
compact, model-ready dataset with only what every downstream arm needs:
id, filing-time text (Summary/Description), the Component label,
Creation time (for the frozen temporal split), and a label_stable flag
(whether the issue ever had a recorded Component-change event -- the
same computation Phase 1 used in aggregate, applied per-row here).

This is also the primary "headroom gate" measurement for raw-data
handling: it streams row-by-row (see triagebench.data.csv_parser.iter_rows)
rather than loading the 15.34GB file into memory, so peak memory is
bounded by a handful of in-flight rows, not file size. Actual peak RSS is
measured and recorded in reports/headroom_gate.json.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from triagebench.data.csv_parser import (  # noqa: E402
    PRIMARY_PRODUCT,
    build_filing_time_features,
    component_change_events,
    iter_rows,
    parse_history,
)


def peak_rss_mb() -> float:
    # ru_maxrss is bytes on macOS/BSD, KB on Linux.
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 1e6 if sys.platform == "darwin" else raw / 1e3


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--headroom-report", type=Path, default=Path("reports/headroom_gate.json"))
    args = ap.parse_args()

    if not args.input.exists():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    input_size_bytes = args.input.stat().st_size
    n_total = 0
    n_written = 0
    n_missing_summary_and_description = 0
    n_missing_component = 0
    n_stable = 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as out:
        for row in iter_rows(args.input):
            n_total += 1
            if row.get("__malformed__"):
                continue
            if row.get("Product") != PRIMARY_PRODUCT:
                continue

            features = build_filing_time_features(row)
            summary, description = features["Summary"], features["Description"]
            component = row.get("Component", "")
            creation_time = row.get("Creation time", "")

            if not summary.strip() and not description.strip():
                n_missing_summary_and_description += 1
                continue  # no usable filing-time text at all
            if not component:
                n_missing_component += 1
                continue  # no usable label

            history = parse_history(row.get("History/Activity Log", ""))
            events = component_change_events(history)
            label_stable = len(events) == 0
            if label_stable:
                n_stable += 1

            record = {
                "id": row.get("ID", ""),
                "summary": summary,
                "description": description,
                "component": component,
                "creation_time": creation_time,
                "label_stable": label_stable,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            n_written += 1

            if n_total % 20000 == 0:
                print(f"  {n_total} rows scanned, {n_written} written, peak RSS so far: {peak_rss_mb():.1f} MB")

    elapsed = time.time() - t0
    output_size_bytes = args.output.stat().st_size
    peak_mb = peak_rss_mb()

    print(f"Done in {elapsed:.1f}s. {n_written}/{n_total} rows written to {args.output}")
    print(f"  output size: {output_size_bytes/1e6:.1f} MB (from {input_size_bytes/1e9:.2f} GB input)")
    print(f"  peak RSS: {peak_mb:.1f} MB")

    report = {
        "status": "MEASURED",
        "purpose": (
            "Headroom gate: verify the raw dataset and model-ready extraction "
            "can be handled safely on available hardware (no unbounded in-memory load)."
        ),
        "input_file": str(args.input),
        "input_size_bytes": input_size_bytes,
        "output_file": str(args.output),
        "output_size_bytes": output_size_bytes,
        "rows_scanned": n_total,
        "rows_written": n_written,
        "rows_dropped_missing_text": n_missing_summary_and_description,
        "rows_dropped_missing_component": n_missing_component,
        "rows_label_stable": n_stable,
        "rows_label_unstable": n_written - n_stable,
        "elapsed_seconds": round(elapsed, 1),
        "peak_rss_mb": round(peak_mb, 1),
        "processing_strategy": (
            "streaming row-by-row via csv.reader (triagebench.data.csv_parser.iter_rows); "
            "the 15.34GB raw file is never loaded into memory at once."
        ),
        "hardware": "Apple M5 MacBook Pro, 16GB unified memory",
        "verdict": "PASS" if peak_mb < 4000 else "REVIEW",
    }
    args.headroom_report.parent.mkdir(parents=True, exist_ok=True)
    args.headroom_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote headroom gate report to {args.headroom_report}")


if __name__ == "__main__":
    main()

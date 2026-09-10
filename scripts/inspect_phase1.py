#!/usr/bin/env python3
"""Phase 1 dataset inspection — measurement only, never assumption.

Every statistic below is computed directly from the CSV file passed in.
Nothing here invents class counts, duplicate rates, instability rates, or
completeness numbers. If a measurement can't be computed (e.g. Product
breakdown on a mixed sample too small to be representative), the report
says so explicitly rather than guessing.

Usage:
    python scripts/inspect_phase1.py --input data/raw/sample_data.csv \
        --output reports/phase1_sample.json [--stated-total 122497]

    python scripts/inspect_phase1.py --input data/raw/platform_full.csv \
        --output reports/phase1_platform.json --product-filter Platform \
        --stated-total 122497
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from triagebench.data.csv_parser import (  # noqa: E402
    EXPECTED_COLUMNS,
    PRIMARY_PRODUCT,
    component_change_events,
    iter_rows,
    parse_history,
)


def pct(n: int, d: int) -> float | None:
    if d == 0:
        return None
    return round(100.0 * n / d, 3)


def length_stats(lengths: list[int]) -> dict:
    if not lengths:
        return {"count": 0, "note": "NOT MEASURED (no rows)"}
    lengths_sorted = sorted(lengths)
    n = len(lengths_sorted)

    def pctl(p: float) -> float:
        idx = min(n - 1, int(round(p * (n - 1))))
        return lengths_sorted[idx]

    return {
        "count": n,
        "min": lengths_sorted[0],
        "max": lengths_sorted[-1],
        "mean": round(statistics.mean(lengths_sorted), 2),
        "median": statistics.median(lengths_sorted),
        "stdev": round(statistics.pstdev(lengths_sorted), 2) if n > 1 else 0.0,
        "p10": pctl(0.10),
        "p90": pctl(0.90),
        "p99": pctl(0.99),
    }


def run_inspection(input_path: Path, product_filter: str | None, stated_total: int | None) -> dict:
    report: dict = {
        "input_file": str(input_path),
        "input_size_bytes": input_path.stat().st_size,
        "product_filter_applied": product_filter,
        "schema": {},
        "rows": {},
        "products": {},
        "component_taxonomy": {},
        "status_distribution": {},
        "resolution_distribution": {},
        "creation_year_distribution": {},
        "text_length": {},
        "missing_fields": {},
        "duplicates": {},
        "history": {},
        "completeness": {},
    }

    total_rows = 0
    malformed_rows = 0
    malformed_examples: list[list[str]] = []
    product_counts: Counter = Counter()
    status_counts_all: Counter = Counter()
    resolution_counts_all: Counter = Counter()

    # Below this point, all counters are scoped to product_filter rows only
    # (if given) — this is the Platform-relevant slice of the inspection.
    scoped_rows = 0
    component_counts: Counter = Counter()
    status_counts: Counter = Counter()
    resolution_counts: Counter = Counter()
    creation_year_counts: Counter = Counter()
    summary_lengths: list[int] = []
    description_lengths: list[int] = []
    missing_summary = 0
    missing_description = 0
    missing_component = 0
    missing_creation_time = 0
    ids_seen: Counter = Counter()
    exact_dup_text: Counter = Counter()
    id_values: list[int] = []
    id_parse_failures = 0

    history_present = 0
    history_parse_failures = 0
    history_empty = 0
    component_change_counts: list[int] = []
    issues_with_component_change = 0
    filing_time_component_matches_current = 0
    filing_time_component_mismatches = 0
    filing_time_component_reconstructed = 0

    for row in iter_rows(input_path):
        total_rows += 1
        if row.get("__malformed__"):
            malformed_rows += 1
            if len(malformed_examples) < 5:
                malformed_examples.append(row.get("__raw__", []))
            continue

        product = row.get("Product", "")
        product_counts[product] += 1
        status_counts_all[row.get("Status", "")] += 1
        resolution_counts_all[row.get("Resolution", "")] += 1

        if product_filter and product != product_filter:
            continue
        scoped_rows += 1

        rid = row.get("ID", "")
        ids_seen[rid] += 1
        try:
            id_values.append(int(rid))
        except (ValueError, TypeError):
            id_parse_failures += 1

        component = row.get("Component", "")
        if not component:
            missing_component += 1
        component_counts[component] += 1

        status_counts[row.get("Status", "")] += 1
        resolution_counts[row.get("Resolution", "")] += 1

        summary = row.get("Summary", "")
        description = row.get("Description", "")
        if not summary.strip():
            missing_summary += 1
        else:
            summary_lengths.append(len(summary))
        if not description.strip():
            missing_description += 1
        else:
            description_lengths.append(len(description))

        exact_dup_text[(summary, description)] += 1

        creation_time = row.get("Creation time", "")
        if not creation_time:
            missing_creation_time += 1
        else:
            year = creation_time[:4]
            if year.isdigit():
                creation_year_counts[year] += 1

        raw_history = row.get("History/Activity Log", "")
        history_is_empty = not raw_history or not raw_history.strip()
        events: list[dict] = []
        if history_is_empty:
            history_empty += 1
        else:
            history_present += 1
            parsed = parse_history(raw_history)
            if not parsed and raw_history.strip() not in ("[]", ""):
                history_parse_failures += 1
            events = component_change_events(parsed)
            component_change_counts.append(len(events))

        # Reconstruction logic applies uniformly whether history was empty
        # (no record captured at all) or present but with zero
        # component-change events -- in both cases the strongest available
        # evidence is "no change was ever recorded," so filing-time
        # component is inferred to equal the current one. The two cases
        # remain separately visible via history_empty/history_present so
        # this inference's evidence base is never hidden (never silently
        # convert a missing observation into an unqualified fact).
        if events:
            issues_with_component_change += 1
            first_component = events[0]["removed"]
            if first_component:
                filing_time_component_reconstructed += 1
                if first_component == component:
                    filing_time_component_matches_current += 1
                else:
                    filing_time_component_mismatches += 1
        else:
            filing_time_component_reconstructed += 1
            filing_time_component_matches_current += 1

    report["schema"]["expected_columns"] = EXPECTED_COLUMNS
    report["schema"]["header_matched"] = True  # iter_rows raises otherwise

    report["rows"] = {
        "total_rows_in_file": total_rows,
        "malformed_rows": malformed_rows,
        "malformed_row_examples": malformed_examples,
        "scoped_rows_after_product_filter": scoped_rows if product_filter else None,
    }

    report["products"] = {
        "counts": dict(product_counts.most_common()),
        "distinct_products": len(product_counts),
    }

    if product_filter:
        report["completeness"] = {
            "product_filter": product_filter,
            "measured_row_count": scoped_rows,
            "stated_upstream_count": stated_total,
            "gap": (stated_total - scoped_rows) if stated_total is not None else None,
            "gap_pct": pct(
                stated_total - scoped_rows, stated_total
            ) if stated_total is not None and stated_total > 0 else None,
            "note": (
                "Compares measured row count against the upstream-stated count "
                "from configs/dataset.yaml. A material gap must be documented, "
                "not silently treated as a complete census."
                if stated_total is not None
                else "NOT MEASURED: no --stated-total provided"
            ),
        }

    n_head = 10
    head_share = sum(c for _, c in component_counts.most_common(n_head))
    not_measured = "NOT MEASURED (no product filter)"
    report["component_taxonomy"] = {
        "distinct_components": len(component_counts),
        "counts": dict(component_counts.most_common()),
        f"head_{n_head}_share_pct": pct(head_share, scoped_rows) if product_filter else not_measured,
        "classes_below_50_support": [c for c, n in component_counts.items() if n < 50],
        "classes_below_10_support": [c for c, n in component_counts.items() if n < 10],
    }

    report["status_distribution"] = {
        "scoped": dict(status_counts.most_common()) if product_filter else "NOT MEASURED (no product filter)",
        "all_products": dict(status_counts_all.most_common()),
    }
    report["resolution_distribution"] = {
        "scoped": dict(resolution_counts.most_common()) if product_filter else "NOT MEASURED (no product filter)",
        "all_products": dict(resolution_counts_all.most_common()),
    }
    report["creation_year_distribution"] = dict(sorted(creation_year_counts.items()))

    report["text_length"] = {
        "summary_chars": length_stats(summary_lengths),
        "description_chars": length_stats(description_lengths),
    }

    report["missing_fields"] = {
        "missing_summary": missing_summary,
        "missing_summary_pct": pct(missing_summary, scoped_rows) if product_filter else None,
        "missing_description": missing_description,
        "missing_description_pct": pct(missing_description, scoped_rows) if product_filter else None,
        "missing_component": missing_component,
        "missing_creation_time": missing_creation_time,
        "id_parse_failures": id_parse_failures,
    }

    duplicate_id_count = sum(1 for _, c in ids_seen.items() if c > 1)
    exact_dup_groups = {k: c for k, c in exact_dup_text.items() if c > 1 and (k[0] or k[1])}
    exact_dup_rows = sum(c - 1 for c in exact_dup_groups.values())  # extra copies beyond the first
    report["duplicates"] = {
        "duplicate_ids": duplicate_id_count,
        "exact_duplicate_text_groups": len(exact_dup_groups),
        "exact_duplicate_extra_rows": exact_dup_rows,
        "id_range": {"min": min(id_values), "max": max(id_values)} if id_values else "NOT MEASURED (no parseable IDs)",
    }

    report["history"] = {
        "history_present": history_present,
        "history_empty": history_empty,
        "history_parse_failures": history_parse_failures,
        "issues_with_component_change": issues_with_component_change,
        "post_filing_routing_label_instability_rate_pct": (
            pct(issues_with_component_change, scoped_rows) if product_filter else not_measured
        ),
        "component_change_count_distribution": (
            dict(Counter(component_change_counts).most_common()) if component_change_counts else {}
        ),
        "filing_time_component_reconstructed": filing_time_component_reconstructed,
        "filing_time_component_reconstruction_rate_pct": (
            pct(filing_time_component_reconstructed, scoped_rows) if product_filter else not_measured
        ),
        "filing_time_vs_current_component_matches": filing_time_component_matches_current,
        "filing_time_vs_current_component_mismatches": filing_time_component_mismatches,
        "note": (
            "post_filing_routing_label_instability_rate is the fraction of "
            "issues with at least one Component change event in the history "
            "log. This is NOT a human-accuracy ceiling -- see docs/METHODOLOGY.md. "
            "filing_time_component_reconstruction treats history_empty rows "
            "(no history record captured at all) the same as history_present "
            "rows with zero component-change events: both are inferred to have "
            "filing-time component == current component, since neither carries "
            "any positive evidence of a change. history_empty is reported "
            "separately above so this inference's weaker evidence base for "
            "those rows is never hidden."
        ),
    }

    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--product-filter", default=None, help=f"e.g. {PRIMARY_PRODUCT!r}")
    ap.add_argument("--stated-total", type=int, default=None)
    args = ap.parse_args()

    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    report = run_inspection(args.input, args.product_filter, args.stated_total)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {args.output}")
    print(f"  total_rows_in_file={report['rows']['total_rows_in_file']}")
    print(f"  malformed_rows={report['rows']['malformed_rows']}")
    print(f"  distinct_products={report['products']['distinct_products']}")
    if args.product_filter:
        print(f"  scoped_rows({args.product_filter})={report['rows']['scoped_rows_after_product_filter']}")


if __name__ == "__main__":
    main()

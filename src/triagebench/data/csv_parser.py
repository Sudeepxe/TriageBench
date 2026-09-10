"""Parser for Issuex-extracted Eclipse Bugzilla CSV exports.

The Issuex extractor writes CSV with a non-default escape character ("\\")
rather than the standard doubled-quote convention. A naive
``pandas.read_csv()`` without ``escapechar="\\\\"`` silently misparses rows
whose free-text fields (Summary, Description, Comments) contain embedded
commas, quotes, or newlines, because it has no way to distinguish an
escaped delimiter from a real one.

On top of that, after correct CSV-level unescaping, some free-text fields
still contain literal two-character sequences (backslash + "n", backslash +
"r") standing in for real newlines/carriage returns — an artifact of how
Issuex serialized the original Bugzilla text. ``unescape_text_field``
resolves that second layer.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

ESCAPECHAR = "\\"
ENCODING = "utf-8"

# Columns present in the Issuex export, in upstream order. Kept explicit
# (rather than trusting the header row alone) so a schema drift shows up
# immediately as a loud mismatch instead of a silent reindex.
EXPECTED_COLUMNS = [
    "Issue URL", "ID", "Alias", "Classification", "Component", "Product",
    "Version", "Platform", "Op sys", "Status", "Resolution", "Depends on",
    "Dupe of", "Blocks", "Groups", "Flags", "Severity", "Priority",
    "Deadline", "Target Milestone", "Creator", "Creator Detail",
    "Creation time", "Assigned to", "Assigned to detail", "CC", "CC detail",
    "Is CC accessible", "Is confirmed", "Is open", "Is creator accessible",
    "Summary", "Description", "URL", "Whiteboard", "Keywords", "See also",
    "Last change time", "QA contact", "History/Activity Log", "Comments",
    "Attachments",
]

# Fields that may contain filing-time-unsafe or post-hoc information and
# must never be used as model features. Enforced by
# tests/test_feature_allowlist.py.
POST_HOC_FIELDS = {
    "Comments", "Status", "Resolution", "Assigned to", "Assigned to detail",
    "Target Milestone", "Dupe of", "Depends on", "Blocks", "Whiteboard",
    "Keywords", "Flags", "CC", "CC detail", "See also", "Last change time",
    "QA contact", "History/Activity Log", "Attachments",
}

# The only fields the model is allowed to see as input (V1 primary input).
FILING_TIME_FEATURE_ALLOWLIST = {"Summary", "Description"}

# IMPORTANT NAME COLLISION: the "Product" column (e.g. "Platform", "CDT",
# "JDT") identifies which Eclipse project the issue belongs to. The
# unrelated "Platform" *column* is the Bugzilla hardware/OS platform field
# (values like "PC", "Mac", "All"). This project's primary product filter
# is `row["Product"] == PRIMARY_PRODUCT`, never the "Platform" column.
PRIMARY_PRODUCT = "Platform"
PRODUCT_COLUMN = "Product"
OS_PLATFORM_COLUMN = "Platform"  # unrelated to PRIMARY_PRODUCT; do not confuse


def build_filing_time_features(row: dict[str, Any]) -> dict[str, str]:
    """The single, canonical way to turn a parsed row into model input.

    Every training/inference pipeline (baseline, encoder, LLM prompting,
    LoRA) must build its input through this function rather than reading
    row["Summary"]/row["Description"] ad hoc, so the leakage allowlist is
    enforced by construction at one call site instead of by convention
    across every script. tests/test_feature_allowlist.py imports this
    exact function -- it is not a separate test-only reimplementation.
    """
    return {k: row.get(k, "") for k in FILING_TIME_FEATURE_ALLOWLIST}


def raise_csv_field_size_limit(limit: int = 50_000_000) -> None:
    """Some Description/Comments/History fields exceed the csv module default."""
    csv.field_size_limit(limit)


def unescape_text_field(value: str) -> str:
    """Resolve literal two-character ``\\n`` / ``\\r`` sequences left after CSV parsing.

    Verified against the real upstream sample_data.csv: a real embedded
    quote character round-trips correctly through
    ``csv.reader(..., escapechar="\\\\")`` on its own (the escapechar
    directly yields a bare ``"``, no residual marker). Real newlines,
    however, survive CSV parsing as a literal two-character ``\\n``/``\\r``
    token (confirmed by grepping the raw file for doubled backslashes before
    "n"), so a second, explicit unescape pass is required for those only.
    The ``\\"`` replacement below is kept as a defensive no-op for input
    that bypassed correct escapechar parsing (e.g. a naive read elsewhere).
    """
    if not value:
        return value
    return (
        value.replace('\\"', '"')
        .replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
    )


def iter_rows(path: str | Path) -> Any:
    """Yield dict rows from an Issuex CSV file with correct escaping.

    Raises ValueError if the header does not match ``EXPECTED_COLUMNS``,
    so a schema drift in upstream data is a loud failure, not a silent
    column misalignment.
    """
    raise_csv_field_size_limit()
    path = Path(path)
    with path.open(encoding=ENCODING, newline="") as f:
        reader = csv.reader(f, escapechar=ESCAPECHAR)
        try:
            header = next(reader)
        except StopIteration:
            return
        if header != EXPECTED_COLUMNS:
            raise ValueError(
                "CSV header does not match EXPECTED_COLUMNS.\n"
                f"Got:      {header}\n"
                f"Expected: {EXPECTED_COLUMNS}"
            )
        for row in reader:
            if len(row) != len(header):
                # Malformed row (parse desync). Surface it to the caller
                # rather than silently zip()-truncating/padding.
                yield {"__malformed__": True, "__raw__": row}
                continue
            record = dict(zip(header, row, strict=True))
            record["Summary"] = unescape_text_field(record.get("Summary", ""))
            record["Description"] = unescape_text_field(record.get("Description", ""))
            yield record


def parse_history(raw_history: str) -> list[dict[str, Any]]:
    """Parse the JSON-encoded History/Activity Log field.

    Unlike Summary/Description, this field is already well-formed JSON once
    CSV-level unescaping has run (verified against real data), and JSON has
    its own backslash-escaping convention for quotes inside string values
    (e.g. ``"removed \\"Managed Make\\""``). Do NOT run
    ``unescape_text_field`` on it first — doing so corrupts valid escaped
    quotes into bare unescaped ones and breaks json.loads.

    Returns [] for empty/unparseable history rather than raising, since
    history completeness itself is a Phase 1 measurement target (see
    ``history parsing failures`` in reports/phase1_*.json) — a parse
    failure is data to report, not a reason to crash the pipeline.
    """
    if not raw_history or not raw_history.strip():
        return []
    try:
        parsed = json.loads(raw_history)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return parsed


def component_change_events(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract Component-field change events from a parsed history log."""
    events = []
    for entry in history:
        for change in entry.get("changes", []) or []:
            if change.get("field_name") == "component":
                events.append(
                    {
                        "when": entry.get("when"),
                        "who": entry.get("who"),
                        "removed": change.get("removed"),
                        "added": change.get("added"),
                    }
                )
    return events

"""Tests for the Issuex CSV parser, using a synthetic fixture (not the real
dataset, which is not redistributed) that exercises the documented escaping
edge cases: embedded commas, embedded quotes, escaped \\n/\\r sequences,
a large field, and a cross-product row for filter testing.
"""

from pathlib import Path

import pytest

from triagebench.data.csv_parser import (
    EXPECTED_COLUMNS,
    PRIMARY_PRODUCT,
    component_change_events,
    iter_rows,
    parse_history,
    unescape_text_field,
)

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_sample.csv"


def test_header_matches_expected_columns():
    rows = list(iter_rows(FIXTURE))
    assert len(rows) == 3
    # every yielded row has exactly the expected keys (well-formed rows)
    for r in rows:
        assert not r.get("__malformed__")
        assert set(r.keys()) == set(EXPECTED_COLUMNS)


def test_embedded_comma_preserved():
    rows = list(iter_rows(FIXTURE))
    row1 = next(r for r in rows if r["ID"] == "1")
    assert row1["Summary"] == "Simple, comma-containing summary"


def test_escaped_newline_unescaped_to_real_newline():
    rows = list(iter_rows(FIXTURE))
    row1 = next(r for r in rows if r["ID"] == "1")
    assert "\n" in row1["Description"]
    assert row1["Description"].splitlines()[0] == "Line one"
    assert "comma and a" in row1["Description"]


def test_embedded_quote_resolved_by_csv_layer_alone():
    # A real embedded quote round-trips through csv.reader(escapechar=...)
    # without needing a second unescape pass (verified against real data).
    rows = list(iter_rows(FIXTURE))
    row1 = next(r for r in rows if r["ID"] == "1")
    assert '"quoted"' in row1["Description"]
    row2 = next(r for r in rows if r["ID"] == "2")
    assert '"embedded quotes"' in row2["Summary"]


def test_escaped_r_and_n_both_unescaped_to_real_newline():
    rows = list(iter_rows(FIXTURE))
    row2 = next(r for r in rows if r["ID"] == "2")
    lines = row2["Description"].splitlines()
    assert lines[:3] == ["Multi", "line", "description with a trailing backslash \\ here."]


def test_large_field_parsed_fully():
    rows = list(iter_rows(FIXTURE))
    row3 = next(r for r in rows if r["ID"] == "3")
    assert len(row3["Description"]) == 5000


def test_product_filter_distinguishes_from_os_platform_column():
    rows = list(iter_rows(FIXTURE))
    platform_rows = [r for r in rows if r["Product"] == PRIMARY_PRODUCT]
    assert len(platform_rows) == 2
    # the unrelated "Platform" (OS) column must not be used for filtering
    assert {r["Platform"] for r in platform_rows} == {"PC", "Mac"}


def test_unescape_text_field_idempotent_on_plain_text():
    assert unescape_text_field("no special chars") == "no special chars"
    assert unescape_text_field("") == ""


def test_parse_history_valid_json():
    history = parse_history('[{"who": "a@example.com", "when": "t", "changes": []}]')
    assert history == [{"who": "a@example.com", "when": "t", "changes": []}]


def test_parse_history_malformed_returns_empty_not_raises():
    assert parse_history("{not valid json") == []
    assert parse_history("") == []
    assert parse_history(None) == []


def test_parse_history_does_not_corrupt_json_internal_escaped_quotes():
    # Regression test: History/Activity Log is already valid JSON after
    # CSV-level unescaping, and JSON has its own \" convention for quotes
    # inside string values. Running the Description-style unescape on it
    # first would turn \" into a bare " and break json.loads.
    raw = (
        '[{"who": "a@example.com", "when": "t", "changes": '
        '[{"field_name": "summary", "removed": "opt \\"Managed Make\\" here", '
        '"added": "new"}]}]'
    )
    history = parse_history(raw)
    assert history != []
    assert history[0]["changes"][0]["removed"] == 'opt "Managed Make" here'


def test_component_change_events_extracted():
    rows = list(iter_rows(FIXTURE))
    row2 = next(r for r in rows if r["ID"] == "2")
    history = parse_history(row2["History/Activity Log"])
    events = component_change_events(history)
    assert len(events) == 1
    assert events[0]["removed"] == "UI"
    assert events[0]["added"] == "Core"

    row1 = next(r for r in rows if r["ID"] == "1")
    history1 = parse_history(row1["History/Activity Log"])
    assert component_change_events(history1) == []


def test_malformed_header_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("A,B,C\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        list(iter_rows(bad))

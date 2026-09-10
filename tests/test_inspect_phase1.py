"""Verifies the Phase 1 inspection script's aggregate computations --
especially post_filing_routing_label_instability_rate and the filing-time
component reconstruction logic -- against a small synthetic fixture with a
hand-computed expected answer. This is deliberately separate from
test_csv_parser.py's lower-level parser tests: it exercises the actual
run_inspection() aggregation code path end-to-end, on synthetic data only
(never the real dataset, and never a claim about real Platform statistics).
"""

import csv
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

_spec = importlib.util.spec_from_file_location("inspect_phase1", REPO_ROOT / "scripts" / "inspect_phase1.py")
inspect_phase1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inspect_phase1)

HEADER = [
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


def _row(**overrides) -> list[str]:
    base = {c: "" for c in HEADER}
    base.update(overrides)
    return [base[c] for c in HEADER]


def _write_fixture(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, escapechar="\\", doublequote=False, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(HEADER)
        for row in rows:
            writer.writerow(row)


def _history_with_component_change(removed: str, added: str) -> str:
    return json.dumps([{"who": "a@example.com", "when": "2016-01-01T00:00:00Z",
                         "changes": [{"field_name": "component", "removed": removed, "added": added}]}])


def _history_no_component_change() -> str:
    return json.dumps([{"who": "a@example.com", "when": "2016-01-01T00:00:00Z",
                         "changes": [{"field_name": "priority", "removed": "P3", "added": "P2"}]}])


def test_instability_rate_and_reconstruction_hand_computed(tmp_path):
    # 5 Platform rows, hand-designed outcome:
    #   row1: no history event at all -> no component change -> stable, filing==current
    #   row2: history with no component-field change -> stable, filing==current
    #   row3: one component change UI->Core, current is "Core" -> unstable, filing(UI) != current(Core)
    #   row4: one component change Debug->Debug (no-op value, still an event) -> unstable, filing==current
    #   row5: non-Platform row (JDT) -> must not affect Platform-scoped stats at all
    rows = [
        _row(ID="1", Product="Platform", Component="UI", Status="NEW",
             **{"Creation time": "2015-01-01T00:00:00Z"},
             Summary="s1", Description="d1", **{"History/Activity Log": ""}),
        _row(ID="2", Product="Platform", Component="Core", Status="NEW",
             **{"Creation time": "2015-01-02T00:00:00Z"},
             Summary="s2", Description="d2",
             **{"History/Activity Log": _history_no_component_change()}),
        _row(ID="3", Product="Platform", Component="Core", Status="RESOLVED", Resolution="FIXED",
             **{"Creation time": "2015-01-03T00:00:00Z"},
             Summary="s3", Description="d3",
             **{"History/Activity Log": _history_with_component_change("UI", "Core")}),
        _row(ID="4", Product="Platform", Component="Debug", Status="NEW",
             **{"Creation time": "2015-01-04T00:00:00Z"},
             Summary="s4", Description="d4",
             **{"History/Activity Log": _history_with_component_change("Debug", "Debug")}),
        _row(ID="5", Product="JDT", Component="Text", Status="NEW",
             **{"Creation time": "2015-01-05T00:00:00Z"},
             Summary="s5", Description="d5",
             **{"History/Activity Log": _history_with_component_change("X", "Y")}),
    ]
    fixture_path = tmp_path / "synthetic_platform.csv"
    _write_fixture(fixture_path, rows)

    report = inspect_phase1.run_inspection(fixture_path, product_filter="Platform", stated_total=4)

    # Scoping: the JDT row must be excluded from every Platform-scoped stat.
    assert report["rows"]["scoped_rows_after_product_filter"] == 4
    assert report["completeness"]["measured_row_count"] == 4
    assert report["completeness"]["gap"] == 0

    # Instability: 2 of 4 Platform rows (row3, row4) have >=1 component
    # change event -> 50%. Rows 1-2 are stable.
    history = report["history"]
    assert history["issues_with_component_change"] == 2
    assert history["post_filing_routing_label_instability_rate_pct"] == 50.0

    # Filing-time reconstruction: all 4 rows are reconstructible (row1/2
    # have no change -> filing==current by definition; row3/4 have an
    # explicit "removed" value as their first recorded component).
    assert history["filing_time_component_reconstructed"] == 4
    assert history["filing_time_component_reconstruction_rate_pct"] == 100.0

    # Filing-time vs. current mismatch: only row3 actually changed value
    # (UI -> Core); row4's "change" is a no-op (Debug -> Debug), so filing
    # time value still equals current value.
    assert history["filing_time_vs_current_component_matches"] == 3  # rows 1, 2, 4
    assert history["filing_time_vs_current_component_mismatches"] == 1  # row 3


def test_history_empty_and_no_op_change_do_not_count_as_instability(tmp_path):
    rows = [
        _row(ID="1", Product="Platform", Component="UI",
             **{"Creation time": "2015-01-01T00:00:00Z"},
             Summary="s", Description="d", **{"History/Activity Log": "[]"}),
        _row(ID="2", Product="Platform", Component="UI",
             **{"Creation time": "2015-01-01T00:00:00Z"},
             Summary="s", Description="d", **{"History/Activity Log": ""}),
    ]
    fixture_path = tmp_path / "stable_only.csv"
    _write_fixture(fixture_path, rows)

    report = inspect_phase1.run_inspection(fixture_path, product_filter="Platform", stated_total=None)
    assert report["history"]["issues_with_component_change"] == 0
    assert report["history"]["post_filing_routing_label_instability_rate_pct"] == 0.0


def test_malformed_history_json_is_counted_as_parse_failure_not_silently_dropped(tmp_path):
    rows = [
        _row(ID="1", Product="Platform", Component="UI",
             **{"Creation time": "2015-01-01T00:00:00Z"},
             Summary="s", Description="d",
             **{"History/Activity Log": "{not valid json at all"}),
    ]
    fixture_path = tmp_path / "malformed_history.csv"
    _write_fixture(fixture_path, rows)

    report = inspect_phase1.run_inspection(fixture_path, product_filter="Platform", stated_total=None)
    assert report["history"]["history_parse_failures"] == 1
    # a parse failure must not be silently miscounted as "stable" via 0 events
    assert report["history"]["issues_with_component_change"] == 0


def test_completeness_gap_is_computed_against_stated_total(tmp_path):
    rows = [
        _row(ID=str(i), Product="Platform", Component="UI",
             **{"Creation time": "2015-01-01T00:00:00Z"}, Summary="s", Description="d")
        for i in range(3)
    ]
    fixture_path = tmp_path / "completeness.csv"
    _write_fixture(fixture_path, rows)

    report = inspect_phase1.run_inspection(fixture_path, product_filter="Platform", stated_total=10)
    assert report["completeness"]["measured_row_count"] == 3
    assert report["completeness"]["stated_upstream_count"] == 10
    assert report["completeness"]["gap"] == 7
    assert report["completeness"]["gap_pct"] == 70.0


def test_run_inspection_marks_successful_report_as_measured(tmp_path):
    rows = [_row(ID="1", Product="Platform", Component="UI",
                  **{"Creation time": "2015-01-01T00:00:00Z"}, Summary="s", Description="d")]
    fixture_path = tmp_path / "measured.csv"
    _write_fixture(fixture_path, rows)
    report = inspect_phase1.run_inspection(fixture_path, product_filter="Platform", stated_total=None)
    assert report["status"] == "MEASURED"


def test_write_blocked_report_produces_well_formed_json_on_missing_input(tmp_path):
    missing_input = tmp_path / "does_not_exist.csv"
    output_path = tmp_path / "blocked.json"
    report = inspect_phase1.write_blocked_report(output_path, "input file not found", missing_input, "Platform")

    assert report["status"] == "BLOCKED"
    assert "reason" in report and report["reason"]
    assert output_path.exists()

    on_disk = json.loads(output_path.read_text())
    assert on_disk["status"] == "BLOCKED"
    assert on_disk["input_file"] == str(missing_input)
    assert on_disk["product_filter_applied"] == "Platform"
    assert "blocked_at" in on_disk  # a timestamp, so staleness is detectable later


def test_non_platform_only_file_reports_zero_scoped_rows_not_a_crash(tmp_path):
    rows = [_row(ID="1", Product="JDT", Component="Text",
                  **{"Creation time": "2015-01-01T00:00:00Z"}, Summary="s", Description="d")]
    fixture_path = tmp_path / "no_platform.csv"
    _write_fixture(fixture_path, rows)

    report = inspect_phase1.run_inspection(fixture_path, product_filter="Platform", stated_total=None)
    assert report["rows"]["scoped_rows_after_product_filter"] == 0
    assert report["completeness"]["measured_row_count"] == 0

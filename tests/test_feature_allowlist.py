"""Guards the filing-time input leakage rule (see docs/METHODOLOGY.md).

The model may only ever see information available at issue-filing time:
Summary + Description. This test fails loudly if a post-hoc field (status,
resolution, assignee, comments, history, ...) is ever added to the
feature allowlist, so leakage can't be introduced silently in a future edit.
"""

from triagebench.data.csv_parser import (
    EXPECTED_COLUMNS,
    FILING_TIME_FEATURE_ALLOWLIST,
    POST_HOC_FIELDS,
    build_filing_time_features,
)


def test_allowlist_is_exactly_summary_and_description():
    assert FILING_TIME_FEATURE_ALLOWLIST == {"Summary", "Description"}


def test_allowlist_and_post_hoc_fields_are_disjoint():
    assert FILING_TIME_FEATURE_ALLOWLIST.isdisjoint(POST_HOC_FIELDS)


def test_post_hoc_fields_covers_all_known_leakage_risks():
    required = {
        "Comments", "Status", "Resolution", "Assigned to",
        "Assigned to detail", "Target Milestone", "Dupe of", "Depends on",
        "Blocks", "Whiteboard", "Keywords", "Flags", "CC", "CC detail",
        "See also", "Last change time", "QA contact",
        "History/Activity Log", "Attachments",
    }
    assert required <= POST_HOC_FIELDS


def test_every_allowlisted_and_post_hoc_field_is_a_real_column():
    assert FILING_TIME_FEATURE_ALLOWLIST <= set(EXPECTED_COLUMNS)
    assert POST_HOC_FIELDS <= set(EXPECTED_COLUMNS)


def test_build_filing_time_features_excludes_post_hoc_fields():
    # This imports the actual production function from csv_parser.py (not
    # a test-local reimplementation), so a future edit that weakens it in
    # src/ is caught here directly rather than only in a parallel copy.
    row = {c: f"value-{c}" for c in EXPECTED_COLUMNS}
    features = build_filing_time_features(row)
    assert set(features.keys()) == {"Summary", "Description"}
    assert not (set(features.keys()) & POST_HOC_FIELDS)


def test_build_filing_time_features_ignores_extra_keys_present_on_row():
    # Regression test: even if a caller passes a full row dict with every
    # post-hoc field still attached (Status, Resolution, History/Activity
    # Log, ...), the returned features must never include their values --
    # not even under an unexpected key.
    row = {c: f"leaked-{c}" for c in EXPECTED_COLUMNS}
    features = build_filing_time_features(row)
    leaked_values = [v for v in features.values() if not v.startswith(("leaked-Summary", "leaked-Description"))]
    assert leaked_values == []


def test_row_to_text_in_baseline_model_only_uses_allowlisted_fields():
    # End-to-end regression test at the actual model-input boundary: Arm
    # 0's row_to_text() must produce text containing only Summary/
    # Description content, even when given a row where every other field
    # is populated with an easily-greppable sentinel value.
    from triagebench.models.baseline import row_to_text

    row = {c: f"LEAK_{c.upper().replace(' ', '_')}" for c in EXPECTED_COLUMNS}
    text = row_to_text(row)
    assert "LEAK_SUMMARY" in text
    assert "LEAK_DESCRIPTION" in text
    for field in POST_HOC_FIELDS:
        sentinel = f"LEAK_{field.upper().replace(' ', '_')}"
        assert sentinel not in text, f"post-hoc field {field!r} leaked into model input text"

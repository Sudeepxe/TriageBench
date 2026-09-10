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


def build_feature_dict(row: dict) -> dict:
    """Reference implementation of what any training/inference pipeline
    must use to build model input. Import and reuse this in real pipeline
    code rather than re-deriving the allowlist ad hoc, so this test stays
    the single source of truth.
    """
    return {k: row.get(k, "") for k in FILING_TIME_FEATURE_ALLOWLIST}


def test_build_feature_dict_excludes_post_hoc_fields():
    row = {c: f"value-{c}" for c in EXPECTED_COLUMNS}
    features = build_feature_dict(row)
    assert set(features.keys()) == {"Summary", "Description"}
    assert not (set(features.keys()) & POST_HOC_FIELDS)

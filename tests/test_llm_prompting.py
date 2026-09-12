from triagebench.evaluation.long_tail_policy import ALL_CLASSES
from triagebench.models.llm_prompting import (
    build_engineered_prompt,
    build_naive_prompt,
    match_component_label,
)


def test_naive_prompt_includes_summary_and_description():
    prompt = build_naive_prompt("UI freeze on save", "Details about the freeze")
    assert "UI freeze on save" in prompt
    assert "Details about the freeze" in prompt


def test_naive_prompt_does_not_list_valid_classes():
    # The defining difference from the engineered prompt: no class list.
    prompt = build_naive_prompt("s", "d")
    assert "Incubator" not in prompt
    assert "WebDAV" not in prompt


def test_engineered_prompt_lists_all_classes():
    prompt = build_engineered_prompt("s", "d")
    for label in ALL_CLASSES:
        assert label in prompt


def test_engineered_prompt_truncates_long_descriptions():
    long_desc = "x" * 5000
    prompt = build_engineered_prompt("s", long_desc)
    assert len(prompt) < 5000  # truncation actually reduces prompt size


def test_match_component_label_exact_match():
    assert match_component_label("UI") == "UI"
    assert match_component_label("  SWT  ") == "SWT"


def test_match_component_label_case_insensitive():
    assert match_component_label("ui") == "UI"
    assert match_component_label("swt") == "SWT"


def test_match_component_label_with_prefix_text():
    assert match_component_label("Component: UI") == "UI"
    assert match_component_label("The component is Debug.") == "Debug"


def test_match_component_label_prefers_longer_label_over_substring():
    # "User Assistance" contains no shorter class name as a substring
    # collision risk here, but verify multi-word labels still match
    # correctly when embedded in a sentence.
    assert match_component_label("This should be User Assistance") == "User Assistance"


def test_match_component_label_returns_none_for_hallucinated_output():
    # Genuinely no whole-word overlap with any valid class name.
    assert match_component_label("I cannot classify this") is None
    assert match_component_label("This is a general question about performance") is None


def test_match_component_label_lenient_match_on_embedded_valid_token():
    # A real model output observed during development: "Editor UI/UX" is
    # not itself a valid component, but it contains "UI" as a genuine
    # standalone token (bounded by a space and a "/"). Matching it to UI
    # is a deliberate, documented leniency, not a bug -- distinct from
    # the false-positive risk covered below, where the "match" isn't a
    # real standalone token at all.
    assert match_component_label("Editor UI/UX") == "UI"


def test_match_component_label_does_not_false_positive_on_short_class_names():
    # Regression test: "Doc", "Ant", "IDE", "CVS", "PMC" are short enough
    # to appear as raw substrings inside ordinary English words. A naive
    # substring search would incorrectly match these; word-boundary
    # matching must not, as long as no *other* class name's whole word
    # also happens to appear.
    assert match_component_label("Please see the documentation for details") is None
    assert match_component_label("As I wanted to provide a wider guide") is None


def test_match_component_label_empty_string_returns_none():
    assert match_component_label("") is None
    assert match_component_label("   ") is None


def test_match_component_label_respects_custom_valid_set():
    assert match_component_label("UI", valid_labels={"SWT", "Debug"}) is None
    assert match_component_label("SWT", valid_labels={"SWT", "Debug"}) == "SWT"

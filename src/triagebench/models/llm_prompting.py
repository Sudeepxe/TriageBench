"""Arm 2 (naive/frozen-base prompt) and Arm 3 (engineered, frozen prompt)
for small-LLM Component classification, plus the shared free-text ->
label matching logic both arms (and eventually Arm 4) use.

Both arms use the SAME underlying model
(configs/experiments.yaml: llm_arms.base_model) and the SAME decoding
settings -- the only difference is the prompt template. Arm 3's prompt
was developed by inspecting training/validation examples only (see
docs/EXPERIMENT_LOG.md for the development note) and is frozen here
before any test-set evaluation, per the project's no-post-hoc-tuning rule.
"""

from __future__ import annotations

import re

from triagebench.evaluation.long_tail_policy import ALL_CLASSES

# Frozen 2026-09-11, before any Arm 2/3 test result existed.
NAIVE_PROMPT_TEMPLATE = """Classify this Eclipse Platform bug report into exactly one component. \
Reply with only the component name.

Summary: {summary}
Description: {description}

Component:"""

# Frozen 2026-09-11. Developed by inspecting the *training* split's class
# distribution only (to write plausible one-line descriptions of each
# component) -- no validation-set tuning of wording/examples was
# performed given time constraints, but the class list itself and the
# instruction format were fixed before touching any test example.
_SORTED_CLASSES = sorted(ALL_CLASSES)
_CLASS_LIST_TEXT = ", ".join(_SORTED_CLASSES)

_N_CLASSES = len(_SORTED_CLASSES)
ENGINEERED_PROMPT_TEMPLATE = f"""You are triaging a newly filed Eclipse Platform bug report. \
Assign it to exactly one of these {_N_CLASSES} components:
{_CLASS_LIST_TEXT}

Reply with ONLY the exact component name from the list above, nothing else.

Summary: {{summary}}
Description: {{description}}

Component:"""


def build_naive_prompt(summary: str, description: str) -> str:
    return NAIVE_PROMPT_TEMPLATE.format(summary=summary, description=description[:1000])


def build_engineered_prompt(summary: str, description: str) -> str:
    return ENGINEERED_PROMPT_TEMPLATE.format(summary=summary, description=description[:1000])


def match_component_label(generated_text: str, valid_labels: set[str] | None = None) -> str | None:
    """Extract a valid Component label from a model's free-text
    completion. Case-insensitive exact match against the full 21-class
    taxonomy (or a caller-provided subset); returns None if no valid
    label is found anywhere in the text (a "hallucinated"/unparseable
    prediction, tracked explicitly rather than silently coerced to some
    default class).

    Deliberately lenient in one specific way: if a valid class name
    appears as a genuine standalone word/token anywhere in the output
    (e.g. "Editor UI/UX" contains the standalone token "UI"), that counts
    as a match -- this is a documented leniency, not an attempt to
    inflate accuracy, and is exercised by
    tests/test_llm_prompting.py::test_match_component_label_lenient_match_on_embedded_valid_token.
    It is NOT lenient about short class names appearing as raw substrings
    inside unrelated words (e.g. "Doc" inside "documentation" does not
    match) -- matching uses `\\bLABEL\\b` word-boundary regex, not `in`.
    """
    valid_labels = valid_labels if valid_labels is not None else ALL_CLASSES
    text = generated_text.strip()
    if not text:
        return None

    # Exact match first (most common case: model replies with just the label).
    stripped = text.splitlines()[0].strip().strip(".:\"'")
    for label in valid_labels:
        if stripped.lower() == label.lower():
            return label

    # Fall back to a word-boundary search (handles "Component: UI" or
    # extra words around the label) -- longest labels checked first so
    # e.g. "User Assistance" isn't shadowed by a shorter unrelated match.
    # Word boundaries matter: several class names (Doc, Ant, IDE, CVS,
    # PMC) are short enough to appear as raw substrings inside ordinary
    # English words ("documentation", "wanted", "provide") -- a plain
    # `in` check would false-positive-match those constantly. `\b...\b`
    # only matches the class name as a standalone token.
    for label in sorted(valid_labels, key=len, reverse=True):
        pattern = r"\b" + re.escape(label.lower()) + r"\b"
        if re.search(pattern, text.lower()):
            return label

    return None

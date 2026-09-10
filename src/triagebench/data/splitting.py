"""Split construction: temporal train/val/test partitioning with duplicate
isolation, and stratified sampling for the data-efficiency experiment.

Generic over any list of row dicts carrying at least an id, a creation
timestamp, a label, and the text used for duplicate detection -- so this
can be built and unit-tested against synthetic data before the real
Platform dataset exists, and reused unchanged once it does.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class SplitResult:
    train_ids: list[str]
    val_ids: list[str]
    test_in_distribution_ids: list[str]
    test_temporal_shift_ids: list[str]
    duplicate_groups_spanning_boundary: int


def _dup_key(row: dict, text_fields: tuple[str, ...]) -> tuple:
    return tuple(row.get(f, "") for f in text_fields)


def group_exact_duplicates(
    rows: list[dict], text_fields: tuple[str, ...] = ("Summary", "Description")
) -> dict[tuple, list[str]]:
    """Group row IDs by exact-duplicate text. Only groups with 2+ members
    are actual duplicate groups; callers should keep singletons as-is."""
    groups: dict[tuple, list[str]] = defaultdict(list)
    for row in rows:
        key = _dup_key(row, text_fields)
        groups[key].append(row["id"])
    return {k: v for k, v in groups.items() if len(v) > 1}


def temporal_split(
    rows: list[dict],
    boundary_date: str,
    val_fraction_of_older: float = 0.15,
    test_fraction_of_older: float = 0.15,
    text_fields: tuple[str, ...] = ("Summary", "Description"),
    seed: int = 0,
) -> SplitResult:
    """Split rows into train/val/test-in-distribution (all from the older
    era, before `boundary_date`) and test-temporal-shift (on/after
    `boundary_date`, i.e. the entire recent era).

    Built as a group-first assignment rather than per-row assignment with
    after-the-fact patching: every exact-duplicate text group (see
    `group_exact_duplicates`) is treated as one indivisible unit from the
    start, so it is structurally impossible for a duplicate group to end
    up split across any of the four buckets -- there is no later "fix-up"
    step that could miss a case.

    A group's side (older/recent) is decided by its earliest member's
    creation time. Within the older era, units are shuffled once (given
    `seed`) and sliced into val / test-in-distribution / train in that
    order, so val_fraction_of_older and test_fraction_of_older are both
    measured against the older era's total row count.
    `duplicate_groups_spanning_boundary` counts how many groups actually
    contained members on both sides of `boundary_date` before being
    unified onto the earliest member's side, as a diagnostic signal (not
    an error) worth reporting alongside the split.
    """
    rows_by_id = {r["id"]: r for r in rows}
    dup_groups = group_exact_duplicates(rows, text_fields)
    grouped_ids = {i for ids in dup_groups.values() for i in ids}

    # Build indivisible units: each duplicate group is one unit (all its
    # ids move together); every other row is its own singleton unit.
    units: list[list[str]] = list(dup_groups.values())
    units += [[r["id"]] for r in rows if r["id"] not in grouped_ids]

    spanning = 0
    older_units: list[list[str]] = []
    recent_units: list[list[str]] = []
    for unit in units:
        times = [rows_by_id[i]["creation_time"] for i in unit]
        if len(unit) > 1 and (min(times) < boundary_date) != (max(times) < boundary_date):
            spanning += 1
        earliest_time = min(times)
        (older_units if earliest_time < boundary_date else recent_units).append(unit)

    rng = random.Random(seed)
    rng.shuffle(older_units)

    total_older_rows = sum(len(u) for u in older_units)
    val_target_n = int(total_older_rows * val_fraction_of_older)
    test_target_n = int(total_older_rows * test_fraction_of_older)

    val_ids: list[str] = []
    test_id_ids: list[str] = []
    train_ids: list[str] = []
    for unit in older_units:
        if len(val_ids) < val_target_n:
            val_ids.extend(unit)
        elif len(test_id_ids) < test_target_n:
            test_id_ids.extend(unit)
        else:
            train_ids.extend(unit)

    recent_era_ids = [i for unit in recent_units for i in unit]

    return SplitResult(
        train_ids=train_ids,
        val_ids=val_ids,
        test_in_distribution_ids=test_id_ids,
        test_temporal_shift_ids=recent_era_ids,
        duplicate_groups_spanning_boundary=spanning,
    )


def stratified_sample_per_class(
    rows: list[dict],
    label_field: str,
    n_per_class: int | None,
    seed: int = 0,
) -> list[str]:
    """Sample up to n_per_class IDs per class (or all available if
    n_per_class is None, i.e. the "full" data-efficiency regime).
    Deterministic given the same seed."""
    by_class: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_class[row[label_field]].append(row["id"])

    rng = random.Random(seed)
    sampled: list[str] = []
    for _label, ids in by_class.items():
        pool = ids[:]
        rng.shuffle(pool)
        take = len(pool) if n_per_class is None else min(n_per_class, len(pool))
        sampled.extend(pool[:take])
    return sampled

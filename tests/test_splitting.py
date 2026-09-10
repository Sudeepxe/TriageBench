from triagebench.data.splitting import (
    group_exact_duplicates,
    stratified_sample_per_class,
    temporal_split,
)


def make_row(id_, summary, description, creation_time, label="UI"):
    return {
        "id": id_,
        "Summary": summary,
        "Description": description,
        "creation_time": creation_time,
        "label": label,
    }


def test_group_exact_duplicates_finds_matching_text():
    rows = [
        make_row("1", "same summary", "same desc", "2010-01-01"),
        make_row("2", "same summary", "same desc", "2010-02-01"),
        make_row("3", "different", "text", "2010-03-01"),
    ]
    groups = group_exact_duplicates(rows)
    assert len(groups) == 1
    assert sorted(next(iter(groups.values()))) == ["1", "2"]


def test_group_exact_duplicates_ignores_singletons():
    rows = [make_row(str(i), f"unique {i}", f"desc {i}", "2010-01-01") for i in range(5)]
    assert group_exact_duplicates(rows) == {}


def test_no_id_appears_in_more_than_one_split_bucket():
    rows = [make_row(str(i), f"s{i}", f"d{i}", f"20{10 + i % 10}-01-01") for i in range(60)]
    result = temporal_split(rows, boundary_date="2018-01-01", seed=0)
    all_ids = (
        result.train_ids + result.val_ids + result.test_in_distribution_ids + result.test_temporal_shift_ids
    )
    assert len(all_ids) == len(set(all_ids)), "an id appeared in more than one split bucket"
    assert len(all_ids) == len(rows), "every row must land in exactly one bucket"


def test_temporal_boundary_is_respected():
    rows = [make_row(str(i), f"s{i}", f"d{i}", f"20{10 + i % 10}-01-01") for i in range(60)]
    result = temporal_split(rows, boundary_date="2018-01-01", seed=0)
    rows_by_id = {r["id"]: r for r in rows}
    for i in result.test_temporal_shift_ids:
        assert rows_by_id[i]["creation_time"] >= "2018-01-01"
    for i in result.train_ids + result.val_ids:
        assert rows_by_id[i]["creation_time"] < "2018-01-01"


def test_duplicate_group_never_split_across_temporal_boundary():
    # Same (Summary, Description) text, one copy filed before the
    # boundary, one after -- this is exactly the leakage scenario the
    # group-first algorithm must prevent.
    rows = [
        make_row("old", "dup text", "dup desc", "2015-01-01"),
        make_row("new", "dup text", "dup desc", "2020-01-01"),
    ] + [make_row(f"filler{i}", f"s{i}", f"d{i}", "2015-06-01") for i in range(10)]
    result = temporal_split(rows, boundary_date="2018-01-01", seed=0)
    train_val = set(result.train_ids) | set(result.val_ids)
    shift = set(result.test_temporal_shift_ids)
    # both members of the duplicate group must land on the same side
    both_in_train_val = {"old", "new"} <= train_val
    both_in_shift = {"old", "new"} <= shift
    assert both_in_train_val or both_in_shift
    assert result.duplicate_groups_spanning_boundary == 1


def test_duplicate_group_never_split_across_train_val():
    rows = [make_row(f"dup{i}", "same", "same", "2015-01-01") for i in range(3)]
    rows += [make_row(f"filler{i}", f"s{i}", f"d{i}", "2015-01-01") for i in range(20)]
    result = temporal_split(rows, boundary_date="2018-01-01", val_fraction_of_older=0.5, seed=1)
    dup_ids = {f"dup{i}" for i in range(3)}
    in_train = dup_ids & set(result.train_ids)
    in_val = dup_ids & set(result.val_ids)
    assert not (in_train and in_val), "duplicate group split across train/val"


def test_multiple_spanning_duplicate_groups_all_counted():
    rows = []
    for g in range(3):
        rows.append(make_row(f"old{g}", f"dup{g}", f"deskdup{g}", "2015-01-01"))
        rows.append(make_row(f"new{g}", f"dup{g}", f"deskdup{g}", "2020-01-01"))
    rows += [make_row(f"filler{i}", f"s{i}", f"d{i}", "2015-01-01") for i in range(10)]
    result = temporal_split(rows, boundary_date="2018-01-01", seed=0)
    assert result.duplicate_groups_spanning_boundary == 3
    # each pair must still land together on one side
    for g in range(3):
        train_val = set(result.train_ids) | set(result.val_ids)
        shift = set(result.test_temporal_shift_ids)
        pair = {f"old{g}", f"new{g}"}
        assert (pair <= train_val) or (pair <= shift)


def test_empty_rows_returns_empty_split_without_error():
    result = temporal_split([], boundary_date="2018-01-01", seed=0)
    assert result.train_ids == []
    assert result.val_ids == []
    assert result.test_temporal_shift_ids == []
    assert result.duplicate_groups_spanning_boundary == 0


def test_zero_val_and_test_fraction_puts_everything_in_train():
    rows = [make_row(str(i), f"s{i}", f"d{i}", "2015-01-01") for i in range(20)]
    result = temporal_split(
        rows, boundary_date="2018-01-01", val_fraction_of_older=0.0, test_fraction_of_older=0.0, seed=0
    )
    assert result.val_ids == []
    assert result.test_in_distribution_ids == []
    assert len(result.train_ids) == 20


def test_all_rows_are_one_duplicate_group_stays_together():
    rows = [make_row(str(i), "identical", "identical", "2015-01-01") for i in range(10)]
    result = temporal_split(
        rows, boundary_date="2018-01-01", val_fraction_of_older=0.5, test_fraction_of_older=0.0, seed=0
    )
    # the whole group of 10 must go entirely to train or entirely to val
    assert len(result.val_ids) in (0, 10)
    assert len(result.train_ids) in (0, 10)
    assert len(result.val_ids) + len(result.train_ids) == 10


def test_val_and_test_fraction_are_approximately_respected():
    rows = [make_row(str(i), f"s{i}", f"d{i}", "2015-01-01") for i in range(200)]
    result = temporal_split(
        rows, boundary_date="2018-01-01", val_fraction_of_older=0.2, test_fraction_of_older=0.15, seed=0
    )
    total_older = len(result.train_ids) + len(result.val_ids) + len(result.test_in_distribution_ids)
    assert total_older == 200
    assert abs(len(result.val_ids) / total_older - 0.2) < 0.05
    assert abs(len(result.test_in_distribution_ids) / total_older - 0.15) < 0.05
    assert abs(len(result.train_ids) / total_older - 0.65) < 0.05


def test_test_in_distribution_never_overlaps_val_or_train():
    rows = [make_row(str(i), f"s{i}", f"d{i}", "2015-01-01") for i in range(300)]
    result = temporal_split(
        rows, boundary_date="2018-01-01", val_fraction_of_older=0.15, test_fraction_of_older=0.15, seed=1
    )
    assert set(result.test_in_distribution_ids).isdisjoint(result.val_ids)
    assert set(result.test_in_distribution_ids).isdisjoint(result.train_ids)
    assert len(result.test_in_distribution_ids) > 0


def test_stratified_sample_respects_per_class_cap():
    rows = [make_row(str(i), f"s{i}", f"d{i}", "2015-01-01", label="A") for i in range(100)]
    rows += [make_row(f"b{i}", f"s{i}", f"d{i}", "2015-01-01", label="B") for i in range(5)]
    sampled = stratified_sample_per_class(rows, "label", n_per_class=10, seed=0)
    by_class = {"A": 0, "B": 0}
    rows_by_id = {r["id"]: r for r in rows}
    for i in sampled:
        by_class[rows_by_id[i]["label"]] += 1
    assert by_class["A"] == 10
    assert by_class["B"] == 5  # fewer than 10 available -- takes all of them


def test_stratified_sample_full_regime_takes_everything():
    rows = [make_row(str(i), f"s{i}", f"d{i}", "2015-01-01", label="A") for i in range(7)]
    sampled = stratified_sample_per_class(rows, "label", n_per_class=None, seed=0)
    assert sorted(sampled) == sorted(r["id"] for r in rows)


def test_stratified_sample_is_deterministic_given_seed():
    rows = [make_row(str(i), f"s{i}", f"d{i}", "2015-01-01", label="A") for i in range(50)]
    s1 = stratified_sample_per_class(rows, "label", n_per_class=10, seed=3)
    s2 = stratified_sample_per_class(rows, "label", n_per_class=10, seed=3)
    assert s1 == s2

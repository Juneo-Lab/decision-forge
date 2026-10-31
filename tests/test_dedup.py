import pytest

from decision_forge.dedup import (
    deduplicate,
    duplicate_verdict,
    find_duplicates,
    jaccard_estimate,
    minhash_signature,
    shingle_hashes,
    structure_key,
)
from decision_forge.pipeline import build, load_run


def clone(episode, **overrides):
    copy = episode.model_copy(deep=True)
    for key, value in overrides.items():
        setattr(copy, key, value)
    return copy


def test_exact_duplicates_are_collapsed_within_a_split(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    pool = load_run(tmp_path / "run")[:4]
    duplicate = clone(pool[0], id="ep-duplicate")
    kept, report = deduplicate([*pool, duplicate])
    assert duplicate.id not in {e.id for e in kept}
    assert report["removed"] == 1
    assert report["dropped"][0]["case"] == "exact_duplicate"
    assert report["dropped"][0]["kept"] == pool[0].id


def test_paraphrases_of_the_same_situation_are_near_duplicates(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    parent = load_run(tmp_path / "run")[0]
    paraphrase = clone(parent, id="ep-paraphrase")
    paraphrase.question = parent.question.replace("?", " please?")
    assert structure_key(paraphrase) == structure_key(parent)
    assert duplicate_verdict(paraphrase, [parent]) in {"near", "exact"}
    kept, report = deduplicate([parent, paraphrase])
    assert len(kept) == 1
    assert report["groups"][0]["case"] == "near_duplicate"


def test_identical_wording_about_a_different_situation_is_not_a_duplicate(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    pool = load_run(tmp_path / "run")
    parent = pool[0]
    other = next(e for e in pool if structure_key(e) != structure_key(parent) and e.question == parent.question)
    assert duplicate_verdict(other, [parent]) is None
    kept, report = deduplicate([parent, other])
    assert len(kept) == 2 and report["removed"] == 0


def test_cross_split_duplicate_keeps_the_held_out_copy(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    pool = load_run(tmp_path / "run")
    train_one = next(e for e in pool if e.split == "train")
    leaked = clone(train_one, id="ep-leaked-train", split="train")
    held_out = clone(train_one, id="ep-held-out", split="test")
    kept, report = deduplicate([leaked, held_out])
    assert [e.id for e in kept] == ["ep-held-out"]
    assert report["cross_split_pairs"] == 1
    assert report["split_leaks"][0] == {
        "dropped": "ep-leaked-train",
        "dropped_split": "train",
        "kept": "ep-held-out",
        "kept_split": "test",
    }


def test_dedup_is_order_independent(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    pool = load_run(tmp_path / "run")[:20]
    duplicated = [*pool, clone(pool[0], id="ep-dup-a"), clone(pool[1], id="ep-dup-b")]
    first = find_duplicates(duplicated)
    second = find_duplicates(list(reversed(duplicated)))
    assert first["kept_ids"] == second["kept_ids"]
    assert first["dropped"] == second["dropped"]


def test_minhash_estimates_similarity_and_rejects_bad_thresholds():
    left = minhash_signature(shingle_hashes("a b c d e f g h"))
    right = minhash_signature(shingle_hashes("a b c d e f g h"))
    different = minhash_signature(shingle_hashes("x y z w v u t s"))
    assert jaccard_estimate(left, right) == 1.0
    assert jaccard_estimate(left, different) < 0.5
    with pytest.raises(ValueError, match="threshold"):
        find_duplicates([], threshold=0)


def test_duplicate_group_with_conflicting_metadata_is_reported(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    pool = load_run(tmp_path / "run")
    parent = pool[0]
    same_input_other_stratum = clone(parent, id="ep-other-stratum", stratum="label-conflict")
    _, report = deduplicate([parent, same_input_other_stratum])
    assert report["metadata_conflicts"], "a stratum disagreement must reach the reviewer"
    conflict = report["metadata_conflicts"][0]
    assert sorted(conflict["strata"]) == sorted([parent.stratum, "label-conflict"])
    assert report["removed"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("policy_context", ["Requests require approval under the updated policy."]),
        ("organization_id", "different-organization"),
        ("policy_version", "different-version"),
    ],
)
def test_different_visible_policies_never_collapse(example_path, tmp_path, field, value):
    build(example_path, tmp_path / "run")
    parent = load_run(tmp_path / "run")[0]
    changed = clone(parent, id="ep-different-policy", **{field: value})
    assert duplicate_verdict(changed, [parent]) is None
    kept, report = deduplicate([parent, changed])
    assert len(kept) == 2
    assert report["removed"] == 0
    assert report["structural_groups"] == 2

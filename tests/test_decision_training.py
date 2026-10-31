import copy
import json

import pytest

from decision_forge.exporters import export_run
from decision_forge.pipeline import build, load_run
from decision_forge.training import DecisionRow, decision_row, encode_decision


class Tokenizer:
    """Offline contract double; real-tokenizer parity is checked separately."""

    def encode(self, text, *, add_special_tokens):
        return [ord(text)] if len(text) == 1 else [1, 2]

    def apply_chat_template(self, messages, **kwargs):
        assert kwargs == dict(tokenize=True, add_generation_prompt=True, enable_thinking=False)
        return [1, 2, 3]


@pytest.fixture
def soft_row():
    return {
        "id": "soft-decision",
        "objective": "candidate_readout",
        "system": "Choose a candidate.",
        "input": {"history": [{"role": "user", "content": "Prior evidence"}], "state": {"budget": 10}},
        "options": [{"key": "accept", "description": "Accept"}, {"key": "review", "description": "Review"}],
        "target_probs": [0.2, 0.8],
        "root_id": "root",
        "normalized_group": "group",
        "scope": "fictional-executable-oracle",
        "provenance": {"oracle": "fixture"},
        "task_id": "allocation",
        "weight": 1.0,
    }


def test_soft_targets_and_history_survive_encoding(soft_row):
    before = copy.deepcopy(soft_row)
    tokenizer = Tokenizer()
    for seed in range(10):
        encoded = encode_decision(soft_row, tokenizer, seed=seed)
        for index, key in enumerate(encoded["label_to_key"].values()):
            original = next(i for i, option in enumerate(soft_row["options"]) if option["key"] == key)
            assert encoded["target_probs"][index] == soft_row["target_probs"][original]
        payload = json.loads(encoded["messages"][1]["content"])
        assert list(payload) == ["state", "history", "candidates"]
        assert payload["history"] == soft_row["input"]["history"]
        assert len(set(encoded["candidate_token_ids"])) == 2
        assert encoded["labels"] == [-100] * encoded["prefix_len"]
        assert encoded["target_ids"] == []
        assert encoded["input_ids"] == [1, 2, 3]
        assert "provenance" not in payload and "target_probs" not in payload
    assert soft_row == before
    assert DecisionRow.model_validate(soft_row).model_dump() == soft_row


@pytest.mark.parametrize(
    "change",
    [
        {"target_probs": [0.4, 0.4]},
        {"target_probs": [-0.1, 1.1]},
        {"target_probs": [float("nan"), 1.0]},
        {"target_probs": [1.0]},
        {"weight": 2.0},
        {"objective": "world_observation"},
        {"provenance": {}},
        {"input": {"candidates": []}},
        {"options": [{"key": "x", "description": "a"}, {"key": "x", "description": "b"}]},
    ],
)
def test_invalid_supervision_rejected(soft_row, change):
    with pytest.raises(ValueError):
        encode_decision({**soft_row, **change}, Tokenizer())


def test_never_truncates_or_reuses_label_tokens(soft_row):
    with pytest.raises(ValueError, match="truncation"):
        encode_decision(soft_row, Tokenizer(), max_tokens=2)

    class CollidingTokenizer(Tokenizer):
        def encode(self, text, *, add_special_tokens):
            return [42]

    with pytest.raises(ValueError, match="insufficient"):
        encode_decision(soft_row, CollidingTokenizer())


def test_export_retains_attributes_provenance_and_family_ownership(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    episodes = {e.id: e for e in load_run(tmp_path / "run")}
    counts = export_run(tmp_path / "run", tmp_path / "export", "decision")
    groups = {}
    for split in ("train", "development", "test"):
        rows = [json.loads(line) for line in (tmp_path / f"export/{split}.jsonl").read_text().splitlines()]
        assert len(rows) == counts[split] > 0
        for row in rows:
            DecisionRow.model_validate(row)
            episode = episodes[row["id"]]
            assert row["provenance"]["source_hashes"] == episode.source_hashes
            assert row["target_probs"] == [float(c.id in episode.valid_choices) for c in episode.candidates]
            assert row["root_id"] == row["normalized_group"]
            assert groups.setdefault(row["root_id"], split) == split
            for option, candidate in zip(row["options"], episode.candidates, strict=True):
                assert json.loads(option["description"])["attributes"] == candidate.attributes
            encoded = encode_decision(row, Tokenizer(), shuffle=False)
            assert list(encoded["label_to_key"].values()) == [c.id for c in episode.candidates]
            assert row["provenance"]["dataset_digest"] not in json.dumps(encoded["messages"])


def test_ambiguous_choices_are_counted_without_inventing_probabilities(example_path, tmp_path, monkeypatch):
    build(example_path, tmp_path / "run")
    episodes = load_run(tmp_path / "run")
    tied = episodes[0].model_copy(update={"valid_choices": [c.id for c in episodes[0].candidates]})
    with pytest.raises(ValueError, match="one verified"):
        decision_row(tied, "digest")
    monkeypatch.setattr("decision_forge.exporters.load_run", lambda _: [tied])
    counts = export_run(tmp_path / "run", tmp_path / "export", "decision")
    assert counts["skipped_multiple_valid_choices"] == 1
    metadata = json.loads((tmp_path / "export/export.json").read_text())
    assert metadata["skipped_by_split"][tied.split] == 1
    assert sum(counts[s] for s in ("train", "development", "test")) == 0


def test_identical_descriptions_retain_identity_for_id_based_rules(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    episode = load_run(tmp_path / "run")[0]
    candidates = [
        c.model_copy(update={"description": "Handle the request", "attributes": {}}) for c in episode.candidates[:2]
    ]
    episode = episode.model_copy(update={"candidates": candidates, "valid_choices": [candidates[0].id]})
    row = decision_row(episode, "digest")
    tokenizer = Tokenizer()
    seen_orders = set()
    for seed in range(10):
        encoded = encode_decision(row, tokenizer, seed=seed)
        choices = json.loads(encoded["messages"][1]["content"])["candidates"]
        visible_ids = [json.loads(c["description"])["id"] for c in choices]
        seen_orders.add(tuple(visible_ids))
        assert visible_ids == list(encoded["label_to_key"].values())
        assert encoded["target_probs"] == [float(key == candidates[0].id) for key in visible_ids]
    assert len(seen_orders) == 2

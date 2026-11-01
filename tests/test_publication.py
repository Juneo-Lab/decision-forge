import copy
import json
import runpy
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]
publish = runpy.run_path(str(ROOT / "scripts/publish_github.py"))["publish"]


class FakeGitHub:
    def __init__(self):
        self.milestones = []
        self.issues = []
        self.labels = []

    def pages(self, path):
        if path.endswith("labels"):
            return copy.deepcopy(self.labels)
        return copy.deepcopy(self.milestones if path.endswith("milestones") else self.issues)

    def request(self, method, path, json):
        if path.endswith("/labels") and "/issues/" in path:
            number = int(path.split("/")[-2])
            issue = next(item for item in self.issues if item["number"] == number)
            issue["labels"] += [{"name": name} for name in json["labels"]]
            return copy.deepcopy(issue["labels"])
        if "/labels" in path:
            if method == "POST":
                self.labels.append(dict(json))
                return dict(json)
            name = unquote(path.rsplit("/", 1)[-1])
            label = next(item for item in self.labels if item["name"] == name)
            label.update(json)
            return dict(label)
        target = self.milestones if "/milestones" in path else self.issues
        if method == "POST":
            item = {"number": len(target) + 1, "state": "open", **json}
            if target is self.issues:
                item["labels"] = [{"name": name} for name in json.get("labels", [])]
            target.append(item)
        else:
            number = int(path.rsplit("/", 1)[-1])
            item = next(item for item in target if item["number"] == number)
            item.update(json)
        return copy.deepcopy(item)


def load_roadmap():
    return json.loads((ROOT / ".github/roadmap.json").read_text(encoding="utf-8"))


def test_roadmap_publication_is_idempotent_and_preserves_remote_state():
    roadmap = load_roadmap()
    api = FakeGitHub()
    first = publish(api, "example/decision-forge", roadmap)
    assert first == {"labels_changed": 15, "milestones": 6, "issues_created": 18, "issues_updated": 0}
    assert len([issue for issue in api.issues if issue["state"] == "closed"]) == 3
    api.milestones[1]["state"] = "closed"
    api.issues[3]["state"] = "closed"
    second = publish(api, "example/decision-forge", roadmap)
    assert second == {"labels_changed": 0, "milestones": 6, "issues_created": 0, "issues_updated": 18}
    assert api.milestones[1]["state"] == "closed"
    assert api.issues[3]["state"] == "closed"
    assert len(api.milestones) == 6 and len(api.issues) == 18


def test_labels_follow_the_documented_taxonomy_and_preserve_triage():
    roadmap = load_roadmap()
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for label in roadmap["labels"]:
        assert f"| `{label['name']}` | {label['description']} |" in contributing
    for item in roadmap["issues"]:
        assert sum(name.startswith("type/") for name in item["labels"]) == 1
        assert any(name.startswith("area/") for name in item["labels"])

    api = FakeGitHub()
    api.labels.append({"name": "type/bug", "color": "000000", "description": "Outdated"})
    api.labels.append({"name": "dependencies", "color": "0366d6", "description": "Dependabot"})
    publish(api, "example/decision-forge", roadmap)
    assert next(label for label in api.labels if label["name"] == "type/bug")["color"] == "d73a4a"
    assert any(label["name"] == "dependencies" for label in api.labels)
    api.issues[5]["labels"].append({"name": "blocked"})
    api.issues[5]["labels"] = [label for label in api.issues[5]["labels"] if label["name"] != "type/feature"]
    publish(api, "example/decision-forge", roadmap)
    names = [label["name"] for label in api.issues[5]["labels"]]
    assert "blocked" in names and names.count("type/feature") == 1


def test_issues_cannot_use_undeclared_labels():
    roadmap = load_roadmap()
    roadmap["issues"][0]["labels"] = ["area/unknown"]
    with pytest.raises(ValueError, match="undeclared labels"):
        publish(FakeGitHub(), "example/decision-forge", roadmap)

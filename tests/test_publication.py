import copy
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
publish = runpy.run_path(str(ROOT / "scripts/publish_github.py"))["publish"]


class FakeGitHub:
    def __init__(self):
        self.milestones = []
        self.issues = []

    def pages(self, path):
        return copy.deepcopy(self.milestones if path.endswith("milestones") else self.issues)

    def request(self, method, path, json):
        target = self.milestones if "/milestones" in path else self.issues
        if method == "POST":
            item = {"number": len(target) + 1, "state": "open", **json}
            target.append(item)
        else:
            number = int(path.rsplit("/", 1)[-1])
            item = next(item for item in target if item["number"] == number)
            item.update(json)
        return copy.deepcopy(item)


def test_roadmap_publication_is_idempotent_and_preserves_remote_state():
    roadmap = json.loads((ROOT / ".github/roadmap.json").read_text())
    api = FakeGitHub()
    first = publish(api, "example/decision-forge", roadmap)
    assert first == {"milestones": 6, "issues_created": 18, "issues_updated": 0}
    assert len([issue for issue in api.issues if issue["state"] == "closed"]) == 3
    api.milestones[1]["state"] = "closed"
    api.issues[3]["state"] = "closed"
    second = publish(api, "example/decision-forge", roadmap)
    assert second == {"milestones": 6, "issues_created": 0, "issues_updated": 18}
    assert api.milestones[1]["state"] == "closed"
    assert api.issues[3]["state"] == "closed"
    assert len(api.milestones) == 6 and len(api.issues) == 18

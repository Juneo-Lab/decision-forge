import runpy
from pathlib import Path

import pytest

from decision_forge.io import write_json

check_results = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check_harbor_results.py"))[
    "check_results"
]


def test_harbor_controls_require_complete_successful_trials(tmp_path):
    manifest = tmp_path / "manifest.json"
    jobs = tmp_path / "jobs"
    write_json(manifest, {"episode_ids": ["ep-one", "ep-two"]})
    result = {
        "task_name": "ep-one",
        "finished_at": "2026-10-31T00:00:00Z",
        "agent_info": {"name": "oracle"},
        "verifier_result": {"rewards": {"reward": 1.0}},
        "exception_info": None,
    }
    write_json(jobs / "one/result.json", result)
    with pytest.raises(ValueError, match="Missing"):
        check_results(manifest, jobs, "oracle", 1)
    result["task_name"] = "ep-two"
    write_json(jobs / "two/result.json", result)
    assert check_results(manifest, jobs, "oracle", 1)["trials"] == 2
    with pytest.raises(ValueError, match="reward"):
        check_results(manifest, jobs, "oracle", 0)
    result["exception_info"] = {"exception_type": "EnvironmentStartTimeoutError"}
    write_json(jobs / "two/result.json", result)
    with pytest.raises(ValueError, match="failed trial"):
        check_results(manifest, jobs, "oracle", 1)

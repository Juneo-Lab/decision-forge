import httpx
import pytest
from test_review import accept_fixture, copied_project

from decision_forge.corpus import CallJournal, CompilationOptions, assemble_project
from decision_forge.io import write_json
from decision_forge.providers import OpenAICompatibleProvider
from decision_forge.review import review_status


def test_auto_negotiation_reserves_each_attempt_and_stops_before_second_http(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"message": "response_format unsupported"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = OpenAICompatibleProvider("https://example.com/v1", "fixture", retries=0, client=client)
        journal = CallJournal(tmp_path / "calls.json", CompilationOptions(max_calls=1))
        with pytest.raises(ValueError, match="budget exhausted"):
            journal.invoke(provider, "fixture", "system", "user", schema)
        assert len(requests) == len(journal.events) == 1
        assert journal.events[0]["status"] == "failed"
        assert journal.events[0]["response_mode"] == "json_schema"
        journal.options = CompilationOptions(max_calls=2)
        assert journal.invoke(provider, "fixture", "system", "user", schema) == {}
        assert [e["input_bytes"] for e in journal.events] == [len(r.content) for r in requests]
        assert journal.events[1]["response_mode"] == "json_object"


def test_endpoint_diagnostic_cannot_echo_credentials_or_source(tmp_path):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={"error": {"message": "PRIVATE_KEY and PRIVATE_SOURCE"}})
        )
    ) as client:
        provider = OpenAICompatibleProvider("https://example.com", "fixture", client=client, retries=0)
        with pytest.raises(ValueError) as exc:
            provider.generate("PRIVATE_SOURCE", "user", {})
        assert "PRIVATE" not in str(exc.value)
    write_json(tmp_path / "private.json", {"source": "private"})
    assert (tmp_path / "private.json").stat().st_mode & 0o077 == 0


def test_claim_id_disambiguates_identical_quotes_and_rejects_wrong_identity(project):
    evidence = project.organization.rules[0].evidence[0]
    rows = []
    for rule in project.organization.rules:
        for e in rule.evidence:
            if not any(r["quote"] == e.quote for r in rows):
                rows.append(
                    {
                        "id": str(len(rows)),
                        "source_id": e.source_id,
                        "quote": e.quote,
                        "start": 0,
                        "end": len(e.quote),
                        "locator": "first",
                    }
                )
    original = next(r for r in rows if r["quote"] == evidence.quote)
    duplicate = {**original, "id": "duplicate", "start": 1000, "end": 1000 + len(evidence.quote), "locator": "second"}
    rows.append(duplicate)
    with pytest.raises(ValueError, match="one extracted claim"):
        assemble_project(project.model_dump(mode="json"), project.sources, rows)
    evidence.claim_id = "duplicate"
    assembled = assemble_project(project.model_dump(mode="json"), project.sources, rows)
    assert assembled.organization.rules[0].evidence[0].start == 1000
    evidence.claim_id = "nonexistent"
    with pytest.raises(ValueError, match="one extracted claim"):
        assemble_project(project.model_dump(mode="json"), project.sources, rows)


@pytest.mark.parametrize("change", ["scenario", "access", "version", "recipe"])
def test_review_binds_scenario_permissions_and_recipe(example_path, tmp_path, change):
    from decision_forge.io import load_project

    spec = copied_project(example_path, tmp_path)
    accept_fixture(spec)
    project = load_project(spec)
    if change == "scenario":
        project.scenarios[0].state["new"] = "different business context"
    elif change == "access":
        project.sources[0].access["role"] = "restricted"
    elif change == "version":
        project.organization.policy_version = "changed"
    else:
        project.recipe.seed += 1
    write_json(spec, project.model_dump(mode="json"))
    status = review_status(spec)
    assert not status["ready"] and status["pending"] >= 1


def test_custom_provider_budget_measurement_and_token_reservations(tmp_path):
    class Custom:
        max_output_tokens = 20

        def input_bytes(self, *args):
            return 1000

        def generate(self, *args):
            raise AssertionError("Budget must stop before provider execution")

    journal = CallJournal(tmp_path / "calls.json", CompilationOptions(max_input_bytes=999))
    with pytest.raises(ValueError, match="budget exhausted"):
        journal.invoke(Custom(), "test", "sys", "user", {})
    journal.options = CompilationOptions(max_total_tokens=1019)
    with pytest.raises(ValueError, match="token budget exhausted"):
        journal.invoke(Custom(), "test", "sys", "user", {})
    assert journal.events == []


def test_evidence_cannot_extend_beyond_source(project, example_path):
    from decision_forge.io import load_sources

    source = project.sources[0]
    text = (example_path.parent / source.path).read_text().rstrip()
    evidence = project.organization.rules[0].evidence[0]
    evidence.quote, evidence.start, evidence.end = text, 0, 10000000
    with pytest.raises(ValueError, match="outside source bounds"):
        load_sources(project, example_path.parent)

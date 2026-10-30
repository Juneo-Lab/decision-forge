import json

import httpx
import pytest

from decision_forge.compiler import compile_documents
from decision_forge.providers import OpenAICompatibleProvider


class FixtureProvider:
    def __init__(self, project):
        self.project = project

    def generate(self, system, user, schema):
        data = self.project.model_dump(mode="json")
        data["sources"][0] = {"id": "source-0", "path": "sources/source-0.txt"}
        for rule in data["organization"]["rules"]:
            for evidence in rule["evidence"]:
                evidence["source_id"] = "source-0"
        for scenario in data["scenarios"]:
            scenario["source_ids"] = ["source-0"]
        return data


def test_compile_checks_real_evidence(project, example_path, tmp_path):
    result = compile_documents(
        [example_path.parent / "sources/policies.md"],
        tmp_path / "compiled",
        FixtureProvider(project),
    )
    assert result.organization.id == "northstar-demo"
    assert (tmp_path / "compiled/project.json").exists()
    assert json.loads((tmp_path / "compiled/compilation.json").read_text())["status"] == "draft"


def test_compile_rejects_fabricated_evidence(project, example_path, tmp_path):
    project.organization.rules[0].evidence[0].quote = "This policy was invented."
    with pytest.raises(ValueError, match="unsupported evidence"):
        compile_documents(
            [example_path.parent / "sources/policies.md"],
            tmp_path / "compiled",
            FixtureProvider(project),
        )
    assert not (tmp_path / "compiled").exists()


def test_provider_formats_request_and_parses_response():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["response_format"]["type"] == "json_schema"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider("https://model.example/v1", "fixture", client=client)
        assert provider.generate("system", "user", {}) == {"ok": True}


@pytest.mark.parametrize("url", ["http://remote.example/v1", "https://user:secret@example.com/v1", "file:///tmp/model"])
def test_provider_rejects_unsafe_endpoint_configuration(url):
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(url, "fixture")


def test_provider_errors_do_not_echo_source_content():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, text="PRIVATE SOURCE"))) as client:
        provider = OpenAICompatibleProvider("https://model.example/v1", "fixture", client=client)
        with pytest.raises(ValueError, match="HTTP 400") as error:
            provider.generate("system", "PRIVATE SOURCE", {})
        assert "PRIVATE SOURCE" not in str(error.value)

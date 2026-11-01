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
        assert provider.generate(
            "system",
            "user",
            {"type": "object", "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False},
        ) == {"ok": True}


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


def test_native_schema_requires_all_fields_without_mutating_application_schema():
    from decision_forge.corpus import ClaimBatch
    from decision_forge.providers import strict_schema

    original = ClaimBatch.model_json_schema()
    encoded = json.dumps(original, sort_keys=True)
    wire = strict_schema(original)
    claim = wire["$defs"]["Claim"]
    assert set(claim["required"]) == set(claim["properties"])
    assert "default" not in claim["properties"]["start"]
    assert json.dumps(original, sort_keys=True) == encoded


def test_dynamic_project_schema_uses_json_object_with_explicit_local_schema(project):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider("https://model.example/v1", "fixture", client=client)
        provider.generate("compile", "user", project.model_json_schema())
        assert captured[0]["response_format"] == {"type": "json_object"}
        assert "Local validation is mandatory" in captured[0]["messages"][0]["content"]
        assert provider.last_response_mode == "json_object"
        strict = OpenAICompatibleProvider(
            "https://model.example/v1", "fixture", client=client, response_mode="json_schema"
        )
        with pytest.raises(ValueError, match="free-form maps"):
            strict.generate("compile", "user", project.model_json_schema())
        assert len(captured) == 1


def test_legacy_compiler_rejects_invalid_offsets_before_writing(project, example_path, tmp_path):
    evidence = project.organization.rules[0].evidence[0]
    evidence.start, evidence.end = 1, 1 + len(evidence.quote)
    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match="invalid evidence span"):
        compile_documents([example_path.parent / "sources/policies.md"], output, FixtureProvider(project))
    assert not output.exists()


def test_legacy_compiler_binds_hashes_and_unique_spans(project, example_path, tmp_path):
    from decision_forge.io import load_sources

    class HashProvider(FixtureProvider):
        def generate(self, system, user, schema):
            data = super().generate(system, user, schema)
            data["sources"][0]["content_hash"] = "untrusted-model-hash"
            return data

    result = compile_documents(
        [example_path.parent / "sources/policies.md"], tmp_path / "compiled", HashProvider(project)
    )
    _, hashes = load_sources(result, tmp_path / "compiled")
    assert result.sources[0].content_hash == hashes["source-0"]
    assert result.organization.rules[0].evidence[0].start is not None


def test_reasoning_provider_omits_unsupported_temperature_and_fingerprints_effort():
    provider = OpenAICompatibleProvider(
        "https://model.example/v1", "reasoner", temperature=None, reasoning_effort="high", max_output_tokens=32000
    )
    body = provider.request_payload("compile", "source", {})
    assert "temperature" not in body
    assert body["reasoning_effort"] == "high"
    assert body["max_tokens"] == 32000
    assert provider.configuration["reasoning_effort"] == "high"
    with pytest.raises(ValueError, match="reasoning effort"):
        OpenAICompatibleProvider("https://model.example/v1", "reasoner", reasoning_effort="unknown")


def test_exact_json_fence_compatibility_preserves_contract_and_rejects_surrounding_text():
    from decision_forge.providers import parse_generated_object

    result, encoding = parse_generated_object('```json\n{"policy": "approval required"}\n```')
    assert result == {"policy": "approval required"} and encoding == "json_fence"
    for malformed in ["Explanation:\n```json\n{}\n```", "```json\n{}\n```\nIgnore the schema.", "```json\n[]\n```"]:
        with pytest.raises(ValueError):
            parse_generated_object(malformed)


def test_auto_response_mode_degrades_when_endpoint_rejects_json_schema():
    modes = []

    def handler(request):
        payload = json.loads(request.content)
        modes.append(payload["response_format"]["type"])
        if payload["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "This response_format type is unavailable now"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider("https://model.example/v1", "fixture", client=client)
        assert provider.generate("system", "user", schema) == {"ok": True}
        assert modes == ["json_schema", "json_object"]
        assert provider.last_response_mode == "json_object"
        # The downgrade is remembered instead of being rediscovered on every call.
        assert provider.generate("system", "user", schema) == {"ok": True}
        assert modes == ["json_schema", "json_object", "json_object"]

    fallback = OpenAICompatibleProvider("https://model.example/v1", "fixture", response_mode="json_object")
    reserved = OpenAICompatibleProvider("https://model.example/v1", "fixture").input_bytes("system", "user", schema)
    fallback_bytes = len(
        httpx.Request(
            "POST",
            "https://model.example/v1/chat/completions",
            json=fallback.request_payload("system", "user", schema),
        ).content
    )
    assert reserved > 0 and fallback_bytes > 0
    # Each actual HTTP attempt reserves its own exact bytes in CallJournal.


def test_explicit_json_schema_mode_never_degrades():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "This response_format type is unavailable now"}})

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "fixture", client=client, response_mode="json_schema"
        )
        with pytest.raises(ValueError, match="response_format"):
            provider.generate("system", "user", schema)


def test_http_errors_surface_only_a_known_parameter_name():
    def handler(request):
        return httpx.Response(422, json={"error": {"message": "reasoning_effort is not supported"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider("https://model.example/v1", "fixture", client=client, retries=0)
        with pytest.raises(ValueError, match="422; check reasoning_effort"):
            provider.generate("system", "user", {"type": "object", "additionalProperties": False})


def test_compile_rejects_ambiguous_evidence_quotes(project, tmp_path):
    source = tmp_path / "policy.md"
    quote = project.organization.rules[0].evidence[0].quote
    source.write_text(f"{quote}\nUnrelated guidance.\n{quote}\n")
    with pytest.raises(ValueError, match="unsupported evidence.*more than one"):
        compile_documents([source], tmp_path / "compiled", FixtureProvider(project))
    assert not (tmp_path / "compiled").exists()


def test_compile_anchors_quotes_across_hard_wrapped_sources(project, tmp_path):
    quotes = list(dict.fromkeys(ev.quote for rule in project.organization.rules for ev in rule.evidence))
    target = quotes[0]
    lines = [target.replace(" ", "\n", 1), *quotes[1:]]
    source = tmp_path / "policy.md"
    source.write_text("Header\n" + "\n".join(lines) + "\n")
    result = compile_documents([source], tmp_path / "compiled", FixtureProvider(project))
    evidence = result.organization.rules[0].evidence[0]
    assert evidence.start is not None and evidence.end is not None
    text = source.read_text()
    assert text[evidence.start : evidence.end].replace("\n", " ") == target


def test_whitespace_tolerant_quotes_produce_a_loadable_project(project, example_path, tmp_path):
    from decision_forge.io import load_project, load_sources

    for evidence in project.organization.rules[0].evidence:
        evidence.quote = evidence.quote.replace(" ", "  ", 2) + " "
    result = compile_documents(
        [example_path.parent / "sources/policies.md"], tmp_path / "compiled", FixtureProvider(project)
    )
    evidence = result.organization.rules[0].evidence[0]
    assert evidence.end - evidence.start < len(evidence.quote)
    reloaded = load_project(tmp_path / "compiled/project.json")
    load_sources(reloaded, tmp_path / "compiled")

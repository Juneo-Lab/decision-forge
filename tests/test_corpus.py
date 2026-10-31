import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from decision_forge.corpus import CompilationOptions, compile_corpus, extraction_metrics
from decision_forge.io import load_project, load_sources, read_json, write_json
from decision_forge.providers import OpenAICompatibleProvider
from decision_forge.review import review_status

POLICY = "Refunds up to 100 dollars may proceed. Refunds above 100 dollars require approval."


def corpus_fixture(tmp_path):
    (tmp_path / "policy.md").write_text(POLICY)
    manifest = tmp_path / "corpus.json"
    write_json(
        manifest,
        {
            "sources": [
                {
                    "id": "policy",
                    "path": "policy.md",
                    "version": "v1",
                    "access": {"classification": "confidential", "allowed_roles": ["support"]},
                }
            ]
        },
    )
    return manifest


def response_for(payload):
    user = json.loads(payload["messages"][1]["content"])
    if "text" in user:
        text = user["text"]
        if POLICY not in text:
            return {"claims": []}
        return {
            "claims": [
                {
                    "summary": POLICY,
                    "quote": POLICY,
                    "kind": "select_actions",
                }
            ]
        }
    evidence = {"source_id": "policy", "quote": POLICY}
    return {
        "organization": {
            "id": "fixture-company",
            "name": "Fixture Company",
            "policy_version": "v1",
            "rules": [
                {
                    "id": "small",
                    "description": "Proceed when amount is at most 100 dollars.",
                    "when": {
                        "all_of": [
                            {"path": "state.amount", "op": "lte", "value": 100},
                            {"path": "candidate.id", "op": "eq", "value": "proceed"},
                        ]
                    },
                    "effect": "allow",
                    "evidence": [evidence],
                },
                {
                    "id": "large",
                    "description": "Request approval above 100 dollars.",
                    "when": {
                        "all_of": [
                            {"path": "state.amount", "op": "gt", "value": 100},
                            {"path": "candidate.id", "op": "eq", "value": "approve"},
                        ]
                    },
                    "effect": "allow",
                    "evidence": [evidence],
                },
            ],
        },
        "sources": [{"id": "policy", "path": "sources/policy.txt", "access": {"classification": "public"}}],
        "nodes": [
            {
                "id": "refund",
                "kind": "select_actions",
                "question": "Choose the next action.",
                "candidates": [
                    {"id": "proceed", "description": "Proceed"},
                    {"id": "approve", "description": "Request approval"},
                ],
                "rule_ids": ["small", "large"],
            }
        ],
        "scenarios": [
            {
                "id": "synthetic-refund",
                "node_id": "refund",
                "family_id": "refund",
                "state": {"amount": 100},
                "source_ids": ["policy"],
            }
        ],
    }


class ScriptedProvider:
    """Deterministic protocol fixture; makes no claim to model extraction quality."""

    def __init__(self, fail_at=None, malformed_at=None):
        self.calls = 0
        self.fail_at = fail_at
        self.malformed_at = malformed_at

    def generate(self, system, user, schema):
        self.calls += 1
        if self.calls == self.fail_at:
            raise ValueError("Injected provider outage")
        response = response_for({"messages": [{"content": system}, {"content": user}]})
        if self.calls == self.malformed_at:
            if "claims" in response:
                response["claims"][0]["start"] = 3
            else:
                response["organization"]["rules"][0]["evidence"][0]["quote"] = "Fabricated"
        return response


def test_corpus_resume_preserves_completed_extraction_and_budget(tmp_path):
    manifest = corpus_fixture(tmp_path)
    provider = ScriptedProvider(fail_at=2)
    output = tmp_path / "compiled"
    with pytest.raises(ValueError, match="outage"):
        compile_corpus(manifest, output, provider, "fixture-v1")
    calls = read_json(output / "calls.json")
    assert [c["status"] for c in calls] == ["returned", "failed"]
    with pytest.raises(ValueError, match="budget exhausted"):
        compile_corpus(manifest, output, provider, "fixture-v1", CompilationOptions(max_calls=2))
    state = compile_corpus(manifest, output, provider, "fixture-v1")
    assert provider.calls == 3
    assert state["cache_hits"] == 1
    assert state["metered_calls"] == 0
    assert state["attempted_calls"] == 3
    project = load_project(output / "project.json")
    assert project.sources[0].access["classification"] == "confidential"
    assert project.sources[0].version == "v1"
    assert project.organization.rules[0].evidence[0].start == 0
    assert "line:1" in project.organization.rules[0].evidence[0].locator
    load_sources(project, output)
    assert not review_status(output / "project.json")["ready"]
    assert compile_corpus(manifest, output, provider, "fixture-v1")["resumed_complete"]
    assert provider.calls == 3


@pytest.mark.parametrize("malformed_at", [1, 2])
def test_invalid_model_response_is_not_cached_and_can_be_retried(tmp_path, malformed_at):
    manifest = corpus_fixture(tmp_path)
    provider = ScriptedProvider(malformed_at=malformed_at)
    with pytest.raises(ValueError, match="span"):
        compile_corpus(manifest, tmp_path / "compiled", provider, "fixture-v1")
    result = compile_corpus(manifest, tmp_path / "compiled", provider, "fixture-v1")
    assert result["status"] == "draft"
    assert provider.calls == 3


def test_cache_integrity_and_provider_identity(tmp_path):
    manifest = corpus_fixture(tmp_path)
    first = ScriptedProvider()
    compile_corpus(manifest, tmp_path / "a", first, "provider-a")
    same = ScriptedProvider()
    compile_corpus(manifest, tmp_path / "b", same, "provider-a")
    assert same.calls == 0
    provider_a_paths = set((tmp_path / ".forge-compile-cache").glob("*.json"))
    other = ScriptedProvider()
    compile_corpus(manifest, tmp_path / "c", other, "provider-b")
    assert other.calls == 2
    path = sorted(provider_a_paths)[0]
    row = read_json(path)
    row["result"]["tampered"] = True
    write_json(path, row)
    with pytest.raises(ValueError, match="integrity"):
        compile_corpus(manifest, tmp_path / "d", ScriptedProvider(), "provider-a")


def test_reviewer_edits_are_preserved_and_rebound(tmp_path):
    manifest = corpus_fixture(tmp_path)
    output = tmp_path / "compiled"
    compile_corpus(manifest, output, ScriptedProvider(), "fixture")
    project = read_json(output / "project.json")
    project["organization"]["rules"][0]["description"] = "Reviewer correction"
    write_json(output / "project.json", project)
    resumed = compile_corpus(manifest, output, ScriptedProvider(), "fixture")
    assert resumed["resumed_complete"]
    assert resumed["reviewer_edits"] == 1
    assert read_json(output / "project.json")["organization"]["rules"][0]["description"] == "Reviewer correction"


def test_edits_that_break_evidence_binding_are_refused(tmp_path):
    manifest = corpus_fixture(tmp_path)
    output = tmp_path / "compiled"
    compile_corpus(manifest, output, ScriptedProvider(), "fixture")
    project = read_json(output / "project.json")
    project["organization"]["rules"][0]["evidence"][0]["quote"] = "This policy was invented."
    write_json(output / "project.json", project)
    with pytest.raises(ValueError, match="evidence"):
        compile_corpus(manifest, output, ScriptedProvider(), "fixture")


def test_changed_sources_invalidate_output(tmp_path):
    manifest = corpus_fixture(tmp_path)
    output = tmp_path / "compiled"
    compile_corpus(manifest, output, ScriptedProvider(), "fixture")
    before = read_json(output / "project.json")
    (tmp_path / "policy.md").write_text(POLICY + "\nUpdated policy")
    with pytest.raises(ValueError, match="changed"):
        compile_corpus(manifest, output, ScriptedProvider(), "fixture")
    assert read_json(output / "project.json") == before


def test_budget_is_reserved_before_network_and_assembly_is_bounded(tmp_path):
    manifest = corpus_fixture(tmp_path)
    provider = ScriptedProvider()
    with pytest.raises(ValueError, match="budget exhausted"):
        compile_corpus(manifest, tmp_path / "tiny", provider, "fixture", CompilationOptions(max_input_bytes=1))
    assert provider.calls == 0
    with pytest.raises(ValueError, match="assembly budget"):
        compile_corpus(manifest, tmp_path / "assembly", provider, "fixture", CompilationOptions(max_assembly_bytes=1))
    assert provider.calls == 1


def test_actual_http_transport_compiles_with_metered_protocol_fixture(tmp_path):
    manifest = corpus_fixture(tmp_path)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            assert self.path == "/v1/chat/completions"
            response = {
                "choices": [{"message": {"content": json.dumps(response_for(payload))}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 80},
            }
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = OpenAICompatibleProvider(f"http://127.0.0.1:{server.server_port}/v1", "scripted-fixture", retries=0)
        output = tmp_path / "http-run"
        state = compile_corpus(manifest, output, provider, "http-fixture")
        assert state["metered_calls"] == 2
        assert len(requests) == 2
        assert [r["response_format"]["type"] for r in requests] == ["json_schema", "json_object"]
        assert read_json(output / "calls.json")[0]["usage"] == {"prompt_tokens": 120, "completion_tokens": 80}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_extraction_metrics_penalize_missing_and_unsupported_claims():
    a = {"source_id": "s", "start": 0, "end": 10, "kind": "select_actions"}
    b = {"source_id": "s", "start": 12, "end": 20, "kind": "share_information"}
    wrong = {**b, "source_id": "wrong"}
    metrics = extraction_metrics([a, a, wrong], [a, b])
    assert metrics["precision"] == metrics["recall"] == metrics["f1"] == 0.5
    assert metrics["matched"] == 1
    assert len(metrics["missing"]) == len(metrics["extra"]) == 1


def test_overlapping_chunks_deduplicate_spans_and_bind_global_offsets(tmp_path):
    manifest = corpus_fixture(tmp_path)
    text = "Preamble. " * 50 + POLICY + " Appendix. " * 50
    (tmp_path / "policy.md").write_text(text)
    provider = ScriptedProvider()
    result = compile_corpus(
        manifest,
        tmp_path / "chunks",
        provider,
        "fixture",
        CompilationOptions(chunk_characters=256, overlap_characters=128),
    )
    assert provider.calls > 3
    assert result["claims"] == 1
    claims = read_json(tmp_path / "chunks/claims.json")
    assert claims[0]["start"] == text.index(POLICY)
    project = load_project(tmp_path / "chunks/project.json")
    evidence = project.organization.rules[0].evidence[0]
    assert evidence.start == text.index(POLICY)
    assert text[evidence.start : evidence.end] == evidence.quote


def test_changed_document_reuses_unchanged_extraction(tmp_path):
    manifest = corpus_fixture(tmp_path)
    (tmp_path / "notes.md").write_text("No organizational policies in this note.")
    source_spec = read_json(manifest)
    source_spec["sources"].append({"id": "notes", "path": "notes.md", "version": "v1"})
    write_json(manifest, source_spec)

    class MultiSourceProvider(ScriptedProvider):
        def generate(self, system, user, schema):
            response = super().generate(system, user, schema)
            data = json.loads(user)
            if "sources" in data:
                response["sources"] = data["sources"]
            return response

    first = MultiSourceProvider()
    compile_corpus(manifest, tmp_path / "first", first, "multi")
    assert first.calls == 3
    (tmp_path / "notes.md").write_text("Updated note without an organizational decision.")
    second = MultiSourceProvider()
    state = compile_corpus(manifest, tmp_path / "second", second, "multi")
    assert second.calls == 2  # Changed note plus assembly; policy extraction reused.
    assert state["cache_hits"] == 1


def test_retrying_provider_is_rejected_before_any_network_call(tmp_path):
    provider = OpenAICompatibleProvider("https://model.example/v1", "fixture")
    with pytest.raises(ValueError, match="retries=0"):
        compile_corpus(corpus_fixture(tmp_path), tmp_path / "out", provider, "fixture")
    assert not (tmp_path / "out").exists()


def test_persisted_size_limit_rejects_pretty_print_expansion(tmp_path):
    class OversizedProvider(ScriptedProvider):
        def generate(self, system, user, schema):
            response = super().generate(system, user, schema)
            if "organization" in response:
                response["scenarios"][0]["state"]["values"] = [0] * 200000
            return response

    with pytest.raises(ValueError, match="specification size"):
        compile_corpus(corpus_fixture(tmp_path), tmp_path / "out", OversizedProvider(), "fixture")
    assert not (tmp_path / "out/project.json").exists()
    assert read_json(tmp_path / "out/compilation.json")["status"] != "draft"


def test_completed_resume_checks_sources_and_restores_review_queue(tmp_path):
    manifest = corpus_fixture(tmp_path)
    output = tmp_path / "out"
    provider = ScriptedProvider()
    compile_corpus(manifest, output, provider, "fixture")
    (output / "review-queue.json").unlink()
    compile_corpus(manifest, output, provider, "fixture")
    assert (output / "review-queue.json").exists()
    (output / "sources/policy.txt").write_text("tampered")
    with pytest.raises(ValueError, match="hash changed"):
        compile_corpus(manifest, output, provider, "fixture")
    assert provider.calls == 2


def test_unique_quotes_are_anchored_without_model_character_counting():
    from decision_forge.corpus import _validate_claims

    text = "Header\nRefunds need approval.\n"
    response = {"claims": [{"summary": "Approval is required", "quote": "Refunds need approval."}]}
    claim = _validate_claims(response, text)[0]
    assert claim.start == 7 and text[claim.start : claim.end] == claim.quote
    with pytest.raises(ValueError, match="more than one source span"):
        _validate_claims(response, text * 2)


def test_hard_wrapped_quotes_are_anchored_by_content_not_whitespace():
    from decision_forge.corpus import _validate_claims

    text = "Intro\nRefunds up to 1000 units may be\nexecuted immediately.\n"
    quote = "Refunds up to 1000 units may be executed immediately."
    response = {"claims": [{"summary": "Refund limit", "quote": quote}]}
    claim = _validate_claims(response, text)[0]
    assert (claim.start, claim.end) == (6, 59)
    assert text[claim.start : claim.end].replace("\n", " ") == claim.quote
    with pytest.raises(ValueError, match="not present"):
        _validate_claims({"claims": [{"summary": "s", "quote": "Refunds up to 2000 units."}]}, text)


def test_wire_byte_budget_includes_transport_schema_and_rejects_before_http(tmp_path, project):
    import httpx

    from decision_forge.corpus import CallJournal

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider("https://model.example/v1", "fixture", client=client, retries=0)
        schema = project.model_json_schema()
        size = provider.input_bytes("compile", "unicode: 文档", schema)
        journal = CallJournal(tmp_path / "calls.json", CompilationOptions(max_input_bytes=size - 1))
        with pytest.raises(ValueError, match="budget exhausted"):
            journal.invoke(provider, "test", "compile", "unicode: 文档", schema)
        assert requests == []
        assert journal.events == []
        journal.options = CompilationOptions(max_input_bytes=size)
        journal.invoke(provider, "test", "compile", "unicode: 文档", schema)
        assert len(requests[0].content) == size == journal.events[0]["input_bytes"]
        assert journal.events[0]["input_basis"] == "provider_request_body"


def test_failed_generated_json_retains_returned_usage_and_cost(tmp_path):
    import httpx

    from decision_forge.corpus import CallJournal

    responses = iter(
        [
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"truncated":'}}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 16000, "cost": 0.25},
                },
            ),
            httpx.Response(503),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda request: next(responses))) as client:
        provider = OpenAICompatibleProvider("https://model.example/v1", "fixture", client=client, retries=0)
        journal = CallJournal(tmp_path / "calls.json", CompilationOptions())
        with pytest.raises(ValueError, match="invalid structured response"):
            journal.invoke(provider, "first", "compile", "source", {})
        assert read_json(journal.path)[0]["usage"] == {
            "prompt_tokens": 1000,
            "completion_tokens": 16000,
            "cost": 0.25,
        }
        with pytest.raises(ValueError, match="HTTP 503"):
            journal.invoke(provider, "second", "compile", "source", {})
        events = read_json(journal.path)
        assert [event["status"] for event in events] == ["failed", "failed"]
        assert events[1]["usage"] is None


def test_extraction_wire_schema_delegates_offsets_to_compiler():
    from decision_forge.corpus import ExtractionBatch, _validate_claims

    properties = ExtractionBatch.model_json_schema()["$defs"]["QuotedClaim"]["properties"]
    assert "start" not in properties and "end" not in properties
    claims = _validate_claims({"claims": [{"summary": "A policy", "quote": "Refunds."}]}, "Header\nRefunds.")
    assert (claims[0].start, claims[0].end) == (7, 15)


def test_validation_repairs_resume_after_call_budget_without_reextracting(tmp_path):
    class RepairProvider(ScriptedProvider):
        def generate(self, system, user, schema):
            data = json.loads(user)
            if "original_request" in data:
                assert data["validation_errors"]
                user = data["original_request"]
            return super().generate(system, user, schema)

    manifest = corpus_fixture(tmp_path)
    provider = RepairProvider(malformed_at=2)
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="budget exhausted"):
        compile_corpus(manifest, output, provider, "repair", CompilationOptions(max_calls=2, validation_retries=1))
    assert provider.calls == 2
    assert read_json(output / "calls.json")[1]["validation"]["status"] == "rejected"
    assert (output / "rejected/2.json").exists()
    state = compile_corpus(manifest, output, provider, "repair", CompilationOptions(max_calls=3, validation_retries=1))
    assert state["status"] == "draft"
    assert provider.calls == 3
    assert not list((output / "repairs").glob("*.json"))
    assert read_json(output / "calls.json")[2]["validation"]["status"] == "accepted"


def test_validation_repair_checkpoint_detects_tampering(tmp_path):
    manifest = corpus_fixture(tmp_path)
    provider = ScriptedProvider(malformed_at=2)
    output = tmp_path / "out"
    options = CompilationOptions(max_calls=2, validation_retries=1)
    with pytest.raises(ValueError, match="budget exhausted"):
        compile_corpus(manifest, output, provider, "repair", options)
    path = next((output / "repairs").glob("*.json"))
    pending = read_json(path)
    pending["body"]["response"] = {}
    write_json(path, pending)
    with pytest.raises(ValueError, match="Repair checkpoint integrity"):
        compile_corpus(manifest, output, provider, "repair", options)
    assert provider.calls == 2


class MessageAwareProvider(ScriptedProvider):
    """Repairs only when the retry prompt explains the defect, as real models require."""

    def __init__(self):
        super().__init__()
        self.injected = False

    def generate(self, system, user, schema):
        self.calls += 1
        data = json.loads(user)
        if "original_request" in data:
            reported = " ".join(str(error.get("message", "")) for error in data["validation_errors"])
            if "requires a list" not in reported:
                return data["previous_response"]
            data = json.loads(data["original_request"])
        response = response_for({"messages": [{"content": system}, {"content": json.dumps(data)}]})
        if "claims" in data and "sources" in data and not self.injected:
            self.injected = True
            response["organization"]["rules"][0]["when"]["all_of"][0] = {
                "path": "state.amount",
                "op": "in",
                "value": 100,
            }
        return response


def test_repair_prompt_reports_validator_messages_not_only_locations(tmp_path):
    provider = MessageAwareProvider()
    state = compile_corpus(
        corpus_fixture(tmp_path),
        tmp_path / "repaired",
        provider,
        "fixture-v1",
        CompilationOptions(validation_retries=2),
    )
    assert state["status"] == "draft"
    assert provider.calls == 3
    rejected = next((tmp_path / "repaired/rejected").glob("*.json"))
    rejected = read_json(rejected)
    assert "requires a list" in rejected["errors"][0]["message"]
    assert rejected["errors"][0]["location"] == ["organization", "rules", 0, "when", "all_of", 0]


def test_unrepairable_response_still_fails_closed(tmp_path):
    class SilentProvider(MessageAwareProvider):
        def generate(self, system, user, schema):
            self.calls += 1
            data = json.loads(user)
            if "original_request" in data:
                return data["previous_response"]
            return super().generate(system, user, schema)

    with pytest.raises(ValueError):
        compile_corpus(
            corpus_fixture(tmp_path),
            tmp_path / "unrepaired",
            SilentProvider(),
            "fixture-v1",
            CompilationOptions(validation_retries=1),
        )
    assert not (tmp_path / "unrepaired/project.json").exists()

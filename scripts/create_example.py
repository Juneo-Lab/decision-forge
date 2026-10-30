"""Regenerate the fictional, English-only example specification."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def p(path, op, value):
    return {"path": path, "op": op, "value": value}


def rule(id, description, predicates, quote, score=0, any_of=None):
    return {
        "id": id,
        "description": description,
        "when": {"all_of": predicates, "any_of": any_of or []},
        "effect": "allow",
        "score": score,
        "evidence": [{"source_id": "handbook", "quote": quote}],
    }


def candidate(id, description, **attributes):
    return {"id": id, "description": description, "attributes": attributes}


def main():
    rules = [
        rule(
            "human",
            "A human specialist can handle every request, with fallback score 1.",
            [p("candidate.id", "eq", "human")],
            "A human specialist may handle every support request.",
            1,
        ),
        rule(
            "agent",
            "Prefer an available support agent for non-urgent, standard-sensitivity requests (score 2).",
            [
                p("candidate.id", "eq", "agent"),
                p("state.urgent", "eq", False),
                p("state.sensitivity", "eq", "standard"),
                p("candidate.available", "eq", True),
            ],
            "Prefer the support agent for non-urgent, standard-sensitivity requests when the agent is available.",
            2,
        ),
        rule(
            "internal-full",
            "Internal project members may receive the full record (score 3).",
            [p("candidate.id", "eq", "full"), p("state.recipient", "eq", "internal")],
            "Internal project members may receive the full customer record.",
            3,
        ),
        rule(
            "approved-full",
            "External partners with explicit full-record approval may receive the full record (score 3).",
            [
                p("candidate.id", "eq", "full"),
                p("state.recipient", "eq", "partner"),
                p("state.full_approval", "eq", True),
            ],
            "External partners may receive the full customer record only with explicit full-record approval.",
            3,
        ),
        rule(
            "partner-summary",
            "Authorized external partners may receive a redacted summary (score 2).",
            [
                p("candidate.id", "eq", "summary"),
                p("state.recipient", "eq", "partner"),
                p("state.partner_authorized", "eq", True),
            ],
            "Authorized external partners may receive a redacted summary.",
            2,
        ),
        rule(
            "reject",
            "Reject is the fallback (score 0); prefer a permitted disclosure when available.",
            [p("candidate.id", "eq", "reject")],
            "Prefer the most informative permitted disclosure; reject the request when no disclosure is permitted.",
        ),
        rule(
            "execute",
            "Execute when the amount is at most 1000 or the refund has approval (score 2).",
            [p("candidate.id", "eq", "execute"), p("state.amount", "exists", True)],
            "Refunds up to and including 1000 units may be executed immediately.",
            2,
            [p("state.amount", "lte", 1000), p("state.approved", "eq", True)],
        ),
        rule(
            "request-approval",
            "Request approval for an unapproved refund above 1000 (score 1).",
            [
                p("candidate.id", "eq", "request_approval"),
                p("state.amount", "gt", 1000),
                p("state.approved", "eq", False),
            ],
            "Refunds above 1000 units require approval before execution.",
            1,
        ),
        rule(
            "clarify",
            "Request the amount when it is missing (score 1).",
            [p("candidate.id", "eq", "clarify"), p("state.amount", "exists", False)],
            "Request the missing amount before deciding whether to execute a refund.",
            1,
        ),
        rule(
            "accept",
            "Accept only when acceptance checks pass and evidence is complete (score 2).",
            [
                p("candidate.id", "eq", "accept"),
                p("state.checks_pass", "eq", True),
                p("state.evidence_complete", "eq", True),
            ],
            "Accept a delivery only when its acceptance checks pass and the required evidence is complete.",
            2,
        ),
        rule(
            "revise",
            "Revision is available as a fallback (score 0); prefer acceptance when both criteria pass.",
            [p("candidate.id", "eq", "revise")],
            "Request revision when either acceptance checks or supporting evidence are incomplete.",
        ),
    ]
    rules[6]["evidence"].append(
        {
            "source_id": "handbook",
            "quote": "An approved refund may be executed regardless of amount.",
        }
    )
    nodes = [
        {
            "id": "routing",
            "kind": "allocate_work",
            "question": "Who should handle this support request?",
            "candidates": [
                candidate("human", "Human support specialist"),
                candidate("agent", "Support agent", available=True),
            ],
            "rule_ids": ["human", "agent"],
            "dimensions": [
                {"path": "state.urgent", "values": [False, True]},
                {"path": "state.sensitivity", "values": ["standard", "restricted"]},
                {"path": "candidates.agent.available", "values": [False, True]},
            ],
        },
        {
            "id": "disclosure",
            "kind": "share_information",
            "question": "What should be shared with this recipient?",
            "candidates": [
                candidate("full", "Full customer record"),
                candidate("summary", "Redacted summary"),
                candidate("reject", "Reject disclosure"),
            ],
            "rule_ids": ["internal-full", "approved-full", "partner-summary", "reject"],
            "dimensions": [
                {"path": "state.recipient", "values": ["internal", "partner"]},
                {"path": "state.full_approval", "values": [False, True]},
                {"path": "state.partner_authorized", "values": [False, True]},
            ],
        },
        {
            "id": "refund",
            "kind": "select_actions",
            "question": "What is the next action for this refund?",
            "candidates": [
                candidate("execute", "Execute refund"),
                candidate("request_approval", "Request authorization"),
                candidate("clarify", "Request the missing amount"),
            ],
            "rule_ids": ["execute", "request-approval", "clarify"],
            "dimensions": [
                {"path": "state.amount", "values": [500, 1000, 1001, 5000]},
                {"path": "state.approved", "values": [False, True]},
            ],
        },
        {
            "id": "acceptance",
            "kind": "evaluate_outcomes",
            "question": "Does this delivery meet the acceptance standard?",
            "candidates": [
                candidate("accept", "Accept delivery"),
                candidate("revise", "Request revision"),
            ],
            "rule_ids": ["accept", "revise"],
            "dimensions": [
                {"path": "state.checks_pass", "values": [False, True]},
                {"path": "state.evidence_complete", "values": [False, True]},
            ],
        },
    ]
    scenarios = [
        {
            "id": f"synthetic-{node['id']}-{i}",
            "family_id": f"support-case-{i}",
            "node_id": node["id"],
            "state": {"case_id": f"support-case-{i}", "product": product},
            "source_ids": ["handbook"],
            "stratum": "boundary" if i % 2 else "routine",
        }
        for i, product in enumerate(
            [
                "analytics",
                "storage",
                "search",
                "workflow",
                "reporting",
                "billing",
                "access",
                "export",
                "backup",
                "monitoring",
                "integration",
                "support",
            ]
        )
        for node in nodes
    ]
    project = {
        "schema_version": "1.0",
        "organization": {
            "id": "northstar-demo",
            "name": "Northstar Support (fictional)",
            "policy_version": "2026-10-demo",
            "rules": rules,
        },
        "sources": [{"id": "handbook", "path": "sources/policies.md"}],
        "nodes": nodes,
        "scenarios": scenarios,
        "recipe": {
            "seed": 42,
            "variants_per_scenario": 16,
            "splits": {"train": 0.6, "development": 0.2, "test": 0.2},
        },
    }
    target = ROOT / "examples/customer_support/project.yaml"
    target.write_text(yaml.safe_dump(project, sort_keys=False, width=100), encoding="utf-8")


if __name__ == "__main__":
    main()

"""Publish the checked-in roadmap and label taxonomy through GitHub's REST API.

Dry-run is the default. Use --apply after choosing the exact owner/repository.
Authentication is read from GH_TOKEN or GITHUB_TOKEN and is never logged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]


class GitHub:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def pages(self, path: str) -> list[dict[str, Any]]:
        result = []
        for page in range(1, 1001):
            rows = self.request("GET", path, params={"state": "all", "per_page": 100, "page": page})
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ValueError("Pagination exceeded the publication limit")


def ensure_repository(api: GitHub, repo: str, create: bool) -> None:
    try:
        metadata = api.request("GET", f"/repos/{repo}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404 or not create:
            raise
        owner, name = repo.split("/")
        identity = api.request("GET", f"/users/{owner}")
        if identity["type"] == "Organization":
            endpoint = f"/orgs/{owner}/repos"
        elif api.request("GET", "/user")["login"].lower() == owner.lower():
            endpoint = "/user/repos"
        else:
            raise ValueError("The authenticated user cannot create repositories for this owner") from None
        metadata = api.request(
            "POST",
            endpoint,
            json={
                "name": name,
                "private": False,
                "auto_init": False,
                "description": "Compile organizational workflows into verifiable decision training data.",
            },
        )
    if not metadata.get("permissions", {}).get("push"):
        raise ValueError("The authenticated account has no push permission for this repository")


def sync_labels(api: GitHub, repo: str, labels: list[dict[str, str]]) -> int:
    """Create or update declared labels. Labels that are not declared are left untouched."""
    base = f"/repos/{repo}"
    existing = {item["name"].lower(): item for item in api.pages(f"{base}/labels")}
    changed = 0
    for label in labels:
        body = {key: label[key] for key in ["name", "color", "description"]}
        current = existing.get(label["name"].lower())
        if current is None:
            api.request("POST", f"{base}/labels", json=body)
        elif any(current.get(key) != body[key] for key in body):
            api.request("PATCH", f"{base}/labels/{quote(current['name'], safe='')}", json=body)
        else:
            continue
        changed += 1
    return changed


def publish(api: GitHub, repo: str, roadmap: dict[str, Any]) -> dict[str, int]:
    base = f"/repos/{repo}"
    declared = {label["name"] for label in roadmap.get("labels", [])}
    undeclared = {name for item in roadmap["issues"] for name in item.get("labels", [])} - declared
    if undeclared:
        raise ValueError(f"Roadmap issues use undeclared labels: {sorted(undeclared)}")
    labels_changed = sync_labels(api, repo, roadmap["labels"]) if declared else 0
    existing_milestones = {item["title"]: item for item in api.pages(f"{base}/milestones")}
    milestones = {}
    for item in roadmap["milestones"]:
        body = {key: item[key] for key in ["title", "description", "state"]}
        if item["title"] in existing_milestones:
            current = existing_milestones[item["title"]]
            # Preserve manual state changes once an item exists.
            body.pop("state")
            current = api.request("PATCH", f"{base}/milestones/{current['number']}", json=body)
        else:
            current = api.request("POST", f"{base}/milestones", json=body)
        milestones[item["id"]] = current["number"]
    issues = api.pages(f"{base}/issues")
    created = updated = 0
    for item in roadmap["issues"]:
        marker = f"<!-- decision-forge:{item['id']} -->"
        existing = next((issue for issue in issues if marker in (issue.get("body") or "")), None)
        body = {
            "title": item["title"],
            "body": marker + "\n\n" + item["body"],
            "milestone": milestones[item["milestone"]],
        }
        if existing:
            api.request("PATCH", f"{base}/issues/{existing['number']}", json=body)
            # Add declared labels without removing labels applied during triage.
            current = {label["name"] for label in existing.get("labels", [])}
            missing = sorted(set(item.get("labels", [])) - current)
            if missing:
                api.request("POST", f"{base}/issues/{existing['number']}/labels", json={"labels": missing})
            updated += 1
        else:
            issue = api.request("POST", f"{base}/issues", json={**body, "labels": item.get("labels", [])})
            if item["state"] == "closed":
                api.request(
                    "PATCH", f"{base}/issues/{issue['number']}", json={"state": "closed", "state_reason": "completed"}
                )
            created += 1
    return {
        "labels_changed": labels_changed,
        "milestones": len(milestones),
        "issues_created": created,
        "issues_updated": updated,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Exact GitHub owner/repository")
    parser.add_argument("--create", action="store_true", help="Create a public repository if it is absent")
    parser.add_argument("--apply", action="store_true", help="Create/update remote milestones and issues")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("Expected owner/repository")
    roadmap = json.loads((ROOT / ".github/roadmap.json").read_text(encoding="utf-8"))
    if not args.apply:
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "repository": args.repo,
                    "labels": [label["name"] for label in roadmap.get("labels", [])],
                    "milestones": [m["title"] for m in roadmap["milestones"]],
                    "issues": len(roadmap["issues"]),
                },
                indent=2,
            )
        )
        return
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("Set GH_TOKEN or GITHUB_TOKEN with repository and issue-management access")
    api = GitHub(token)
    try:
        ensure_repository(api, args.repo, args.create)
        print(json.dumps(publish(api, args.repo, roadmap), indent=2))
    except httpx.HTTPStatusError as exc:
        parser.exit(
            1, f"GitHub returned HTTP {exc.response.status_code}; check account, repository, and permissions.\n"
        )
    finally:
        api.client.close()


if __name__ == "__main__":
    main()

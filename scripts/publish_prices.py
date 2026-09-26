"""Publish only validated price JSON from the current trusted workflow revision."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path

if __package__:
    from .update_prices import CATALOG, ROOT, catalog, validate_transition
else:
    from update_prices import CATALOG, ROOT, catalog, validate_transition

REPOSITORY = "megumin31/oai-usage"
CANDIDATE = ROOT / "candidate" / "prices.json"


def gh(*args: str, data: bytes = None) -> bytes:
    return subprocess.run(["gh", "api", *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, timeout=30).stdout


def publish(candidate: Path = CANDIDATE) -> bool:
    if (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY or
            os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise ValueError("Price publishing requires the trusted repository's main branch")
    head = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("Missing trusted workflow revision")
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("Price candidate must be a regular file")
    baseline = catalog.strict_json(catalog.read_limited(CATALOG))
    incoming = catalog.strict_json(catalog.read_limited(candidate))
    validate_transition(baseline, incoming)
    if baseline == incoming:
        print("Price catalog unchanged")
        return False
    current = gh(f"repos/{REPOSITORY}/commits/main", "--jq", ".sha").decode().strip()
    if current != head:
        raise ValueError("Main advanced during this run; rerun the workflow on the new revision")
    # The mutation changes only this fixed path and atomically rejects any HEAD
    # change, including one occurring after the preflight read above.
    content = (json.dumps(incoming, ensure_ascii=False, indent=2) + "\n").encode()
    mutation = "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }"
    payload = {"query": mutation, "variables": {"input": {
        "branch": {"repositoryNameWithOwner": REPOSITORY, "branchName": "main"},
        "expectedHeadOid": head, "message": {"headline": "Update OpenAI Standard prices"},
        "fileChanges": {"additions": [{"path": "prices.json", "contents": base64.b64encode(content).decode()}]}}}}
    commit = gh("graphql", "--input", "-", "--jq", ".data.createCommitOnBranch.commit.oid",
                data=json.dumps(payload).encode()).decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("GitHub did not confirm a price publication commit")
    print("Published price catalog: " + commit)
    return True


if __name__ == "__main__":
    try:
        publish()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"Price publication failed: {exc}")

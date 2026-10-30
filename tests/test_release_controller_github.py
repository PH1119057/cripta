"""Tests for credential-free GitHub evidence used by Release Controller (#288)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).resolve().parents[1]
    / "operations/infrastructure/release_controller_github.py"
)
spec = importlib.util.spec_from_file_location("release_controller_github", MODULE)
github = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = github
spec.loader.exec_module(github)

TARGET = "a" * 40
TREE = "b" * 40
HEAD = "c" * 40
BASELINE = "d" * 40


def merged_pr(number=290):
    return {
        "number": number,
        "merged_at": "2026-10-31T00:00:00Z",
        "merge_commit_sha": TARGET,
        "base": {"ref": "main"},
        "head": {
            "sha": HEAD,
            "repo": {"full_name": "PH1119057/cripta"},
        },
    }


def fake_get_json(path, params=None):
    del params
    if path == "/commits/main":
        return {"sha": TARGET, "commit": {"tree": {"sha": TREE}}}
    if path == "/rulesets":
        return [{"id": 7, "target": "branch", "enforcement": "active"}]
    if path == "/rulesets/7":
        return {
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
            "rules": [
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "required_status_checks": [
                            {"context": "scope", "integration_id": 15368}
                        ]
                    },
                }
            ],
        }
    if path == f"/commits/{TARGET}/pulls":
        return [merged_pr()]
    if path == f"/commits/{HEAD}/pulls":
        return [merged_pr()]
    if path == f"/commits/{HEAD}/check-runs":
        return {
            "check_runs": [
                {
                    "name": "scope",
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"id": 15368},
                }
            ]
        }
    if path == "/actions/runs":
        return {
            "workflow_runs": [
                {
                    "name": "quality",
                    "event": "pull_request",
                    "head_sha": HEAD,
                    "status": "completed",
                    "conclusion": "success",
                    "pull_requests": [],
                }
            ]
        }
    raise AssertionError(f"unexpected path {path}")


def test_release_evidence_requires_ruleset_scope_and_quality(monkeypatch):
    monkeypatch.setattr(github, "_get_json", fake_get_json)
    evidence = github.release_evidence(TARGET)
    assert evidence.remote_main == TARGET
    assert evidence.target_tree == TREE
    assert evidence.merged_pr_number == 290
    assert evidence.merged_pr_head == HEAD
    assert evidence.required_checks == {"scope": "success", "quality": "success"}
    assert evidence.ruleset_ids == (7,)


def test_release_evidence_rejects_reused_pr_head(monkeypatch):
    def reused(path, params=None):
        value = fake_get_json(path, params)
        if path == f"/commits/{HEAD}/pulls":
            return [merged_pr(290), merged_pr(291)]
        return value

    monkeypatch.setattr(github, "_get_json", reused)
    with pytest.raises(github.GitHubEvidenceError, match="PR_HEAD_REUSED"):
        github.release_evidence(TARGET)


def test_release_evidence_rejects_missing_scope_enforcement(monkeypatch):
    def no_scope(path, params=None):
        value = fake_get_json(path, params)
        if path == "/rulesets/7":
            return {
                "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
                "rules": [],
            }
        return value

    monkeypatch.setattr(github, "_get_json", no_scope)
    with pytest.raises(
        github.GitHubEvidenceError,
        match="SCOPE_NOT_REQUIRED_BY_RULESET",
    ):
        github.release_evidence(TARGET)



def test_source_can_fast_forward_uses_canonical_compare(monkeypatch):
    source = "e" * 40

    def compare_json(path, params=None):
        del params
        assert path == f"/compare/{source}...{TARGET}"
        return {
            "status": "ahead",
            "merge_base_commit": {"sha": source},
            "base_commit": {"sha": source},
            "behind_by": 0,
        }

    monkeypatch.setattr(github, "_get_json", compare_json)
    assert github.source_can_fast_forward(source, TARGET) is True


def test_source_can_fast_forward_rejects_diverged_history(monkeypatch):
    source = "e" * 40

    def compare_json(path, params=None):
        del params
        assert path == f"/compare/{source}...{TARGET}"
        return {
            "status": "diverged",
            "merge_base_commit": {"sha": "f" * 40},
            "base_commit": {"sha": source},
            "behind_by": 1,
        }

    monkeypatch.setattr(github, "_get_json", compare_json)
    assert github.source_can_fast_forward(source, TARGET) is False

def test_approval_candidates_use_only_owner_ledger_comments(monkeypatch):
    request = {
        "repository": "PH1119057/cripta",
        "purpose": "EXACT_VERIFIED_SOURCE_RELEASE",
        "target_commit": TARGET,
        "expected_baseline": BASELINE,
        "nonce": "nonce_00000000000000000001",
        "expires_at": 1793405700,
    }
    body = github.APPROVAL_PREFIX + json.dumps(request, separators=(",", ":"))

    def approval_json(path, params=None):
        del params
        if path == "/issues/291":
            return {"state": "open", "user": {"login": "PH1119057"}}
        if path == "/issues/291/comments":
            return [
                {
                    "id": 1,
                    "html_url": "https://github.com/PH1119057/cripta/issues/291#issuecomment-1",
                    "user": {"login": "outsider"},
                    "author_association": "NONE",
                    "body": body,
                    "created_at": "2026-10-31T00:00:00Z",
                    "updated_at": "2026-10-31T00:00:00Z",
                },
                {
                    "id": 2,
                    "html_url": "https://github.com/PH1119057/cripta/issues/291#issuecomment-2",
                    "user": {"login": "PH1119057"},
                    "author_association": "OWNER",
                    "body": body,
                    "created_at": "2026-10-31T00:01:00Z",
                    "updated_at": "2026-10-31T00:01:00Z",
                },
            ]
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(github, "_get_json", approval_json)
    approvals = github.approval_candidates(target=TARGET, baseline=BASELINE)
    assert len(approvals) == 1
    assert approvals[0].comment_id == 2
    assert approvals[0].actor == "PH1119057"
    assert approvals[0].unedited is True


def test_approval_candidates_mark_edited_owner_comment(monkeypatch):
    request = {
        "target_commit": TARGET,
        "expected_baseline": BASELINE,
    }

    def approval_json(path, params=None):
        del params
        if path == "/issues/291":
            return {"state": "open", "user": {"login": "PH1119057"}}
        if path == "/issues/291/comments":
            return [
                {
                    "id": 3,
                    "html_url": "https://github.com/example",
                    "user": {"login": "PH1119057"},
                    "author_association": "OWNER",
                    "body": github.APPROVAL_PREFIX + json.dumps(request),
                    "created_at": "2026-10-31T00:00:00Z",
                    "updated_at": "2026-10-31T00:00:01Z",
                }
            ]
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(github, "_get_json", approval_json)
    approvals = github.approval_candidates(target=TARGET, baseline=BASELINE)
    assert len(approvals) == 1
    assert approvals[0].unedited is False

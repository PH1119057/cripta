from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/lifecycle-protection-validation.yml"


def test_release_delta_uses_real_synthetic_merge_main_parent_not_stale_pr_base() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert source.count('git rev-list --parents -n 1 HEAD') == 2
    assert source.count('BASE_SHA="${parents[1]}"') == 2
    assert source.count("Expected exactly two parents on GitHub PR merge checkout") == 2
    assert "github.event.pull_request.base.sha" not in source
    assert 'git diff --name-only "$BASE_SHA" HEAD -- "*.py"' in source
    assert 'git worktree add --detach ../base-ruff "$BASE_SHA"' in source
    assert 'git worktree add --detach ../base-regression "$BASE_SHA"' in source


def test_release_delta_keeps_all_three_trust_checks_enabled() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "NEW_RUFF_DIAGNOSTICS" in source
    assert "NEW_TEST_FAILURES" in source
    assert "NEW_MYPY_DIAGNOSTICS" in source
    assert "raise SystemExit(1)" in source
    assert "continue-on-error: true" in source

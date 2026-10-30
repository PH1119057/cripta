"""Public GitHub evidence collector for bounded Release Controller (#288).

No GitHub credential is required: the canonical repository is public. The
collector never writes to GitHub. Owner authorization is a machine-readable
comment in the fixed approval-ledger Issue #291, authenticated by GitHub user
metadata and rechecked locally before any server mutation.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

REPOSITORY = "PH1119057/cripta"
OWNER_LOGIN = "PH1119057"
APPROVAL_ISSUE = 291
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
APPROVAL_PREFIX = "CRIPTA_RELEASE_REQUEST_V1\n"
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PAGES = 20


class GitHubEvidenceError(RuntimeError):
    """Fail-closed public GitHub evidence failure."""


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    request: dict[str, Any]
    actor: str
    created_at: datetime
    unedited: bool
    comment_id: int
    html_url: str


@dataclass(frozen=True, slots=True)
class ReleaseEvidence:
    remote_main: str
    target_tree: str
    merged_pr_number: int
    merged_pr_head: str
    required_checks: dict[str, str]
    ruleset_ids: tuple[int, ...]


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise GitHubEvidenceError("GITHUB_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GitHubEvidenceError("GITHUB_TIME_INVALID") from exc
    if parsed.tzinfo is None:
        raise GitHubEvidenceError("GITHUB_TIME_INVALID")
    return parsed.astimezone(UTC)


def _get_json(path: str, params: dict[str, object] | None = None) -> Any:
    if not path.startswith("/") or "://" in path or "\\" in path:
        raise GitHubEvidenceError("GITHUB_PATH_INVALID")
    query = urllib.parse.urlencode(params or {})
    url = API_ROOT + path + (f"?{query}" if query else "")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "cripta-release-controller/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise GitHubEvidenceError(f"GITHUB_HTTP_{response.status}")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise GitHubEvidenceError(f"GITHUB_HTTP_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GitHubEvidenceError("GITHUB_NETWORK_ERROR") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise GitHubEvidenceError("GITHUB_RESPONSE_TOO_LARGE")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubEvidenceError("GITHUB_JSON_INVALID") from exc


def _pages(
    path: str,
    *,
    list_key: str | None = None,
    params: dict[str, object] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    base = dict(params or {})
    for page in range(1, MAX_PAGES + 1):
        page_params = {**base, "per_page": 100, "page": page}
        payload = _get_json(path, page_params)
        rows = payload.get(list_key) if list_key else payload
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise GitHubEvidenceError("GITHUB_LIST_INVALID")
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise GitHubEvidenceError("GITHUB_PAGINATION_LIMIT")


def current_main() -> tuple[str, str]:
    payload = _get_json("/commits/main")
    sha = payload.get("sha")
    tree = ((payload.get("commit") or {}).get("tree") or {}).get("sha")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise GitHubEvidenceError("REMOTE_MAIN_INVALID")
    if not isinstance(tree, str) or not SHA.fullmatch(tree):
        raise GitHubEvidenceError("REMOTE_TREE_INVALID")
    return sha, tree


def _required_status_checks() -> tuple[dict[str, int | None], tuple[int, ...]]:
    summaries = _get_json("/rulesets")
    if not isinstance(summaries, list):
        raise GitHubEvidenceError("RULESET_LIST_INVALID")
    required: dict[str, int | None] = {}
    ids: list[int] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            raise GitHubEvidenceError("RULESET_LIST_INVALID")
        if summary.get("target") != "branch" or summary.get("enforcement") != "active":
            continue
        ruleset_id = summary.get("id")
        if not isinstance(ruleset_id, int):
            raise GitHubEvidenceError("RULESET_ID_INVALID")
        detail = _get_json(f"/rulesets/{ruleset_id}")
        includes = (((detail.get("conditions") or {}).get("ref_name") or {}).get("include") or [])
        if not isinstance(includes, list):
            raise GitHubEvidenceError("RULESET_CONDITION_INVALID")
        if not any(value in {"~DEFAULT_BRANCH", "refs/heads/main", "main"} for value in includes):
            continue
        ids.append(ruleset_id)
        rules = detail.get("rules")
        if not isinstance(rules, list):
            raise GitHubEvidenceError("RULESET_RULES_INVALID")
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            statuses = (rule.get("parameters") or {}).get("required_status_checks")
            if not isinstance(statuses, list):
                raise GitHubEvidenceError("RULESET_STATUS_INVALID")
            for status in statuses:
                if not isinstance(status, dict):
                    raise GitHubEvidenceError("RULESET_STATUS_INVALID")
                context = status.get("context")
                integration_id = status.get("integration_id")
                if not isinstance(context, str) or not context:
                    raise GitHubEvidenceError("RULESET_STATUS_INVALID")
                if integration_id is not None and not isinstance(integration_id, int):
                    raise GitHubEvidenceError("RULESET_INTEGRATION_INVALID")
                previous = required.get(context)
                if context in required and previous != integration_id:
                    raise GitHubEvidenceError("RULESET_STATUS_CONFLICT")
                required[context] = integration_id
    if not ids:
        raise GitHubEvidenceError("MAIN_RULESET_MISSING")
    if "scope" not in required:
        raise GitHubEvidenceError("SCOPE_NOT_REQUIRED_BY_RULESET")
    return required, tuple(sorted(ids))


def _merged_pr(target: str) -> dict[str, Any]:
    rows = _pages(f"/commits/{target}/pulls")
    matches = [
        row
        for row in rows
        if row.get("merged_at")
        and row.get("merge_commit_sha") == target
        and (row.get("base") or {}).get("ref") == "main"
        and (((row.get("head") or {}).get("repo") or {}).get("full_name") == REPOSITORY)
    ]
    if len(matches) != 1:
        raise GitHubEvidenceError("MERGED_PR_AMBIGUOUS")
    pr = matches[0]
    number = pr.get("number")
    head_sha = (pr.get("head") or {}).get("sha")
    if not isinstance(number, int) or not isinstance(head_sha, str) or not SHA.fullmatch(head_sha):
        raise GitHubEvidenceError("MERGED_PR_INVALID")

    head_rows = _pages(f"/commits/{head_sha}/pulls")
    exact_head_prs = {
        row.get("number")
        for row in head_rows
        if ((row.get("head") or {}).get("sha") == head_sha)
    }
    if exact_head_prs != {number}:
        raise GitHubEvidenceError("PR_HEAD_REUSED")
    return pr


def _status_checks(
    *,
    head_sha: str,
    required: dict[str, int | None],
) -> dict[str, str]:
    check_runs = _pages(f"/commits/{head_sha}/check-runs", list_key="check_runs")
    result: dict[str, str] = {}
    for context, integration_id in required.items():
        matches = []
        for run in check_runs:
            if run.get("name") != context:
                continue
            if integration_id is not None and ((run.get("app") or {}).get("id") != integration_id):
                continue
            matches.append(run)
        if not matches:
            raise GitHubEvidenceError(f"REQUIRED_CHECK_MISSING:{context}")
        if any(
            run.get("status") != "completed" or run.get("conclusion") != "success"
            for run in matches
        ):
            raise GitHubEvidenceError(f"REQUIRED_CHECK_NOT_PASS:{context}")
        result[context] = "success"

    workflow_runs = _pages(
        "/actions/runs",
        list_key="workflow_runs",
        params={"head_sha": head_sha, "event": "pull_request"},
    )
    quality = [
        run
        for run in workflow_runs
        if run.get("name") == "quality"
        and run.get("head_sha") == head_sha
    ]
    if not quality:
        raise GitHubEvidenceError("QUALITY_WORKFLOW_MISSING")
    if any(
        run.get("status") != "completed" or run.get("conclusion") != "success"
        for run in quality
    ):
        raise GitHubEvidenceError("QUALITY_WORKFLOW_NOT_PASS")
    result["quality"] = "success"
    return result


def release_evidence(target: str) -> ReleaseEvidence:
    if not isinstance(target, str) or not SHA.fullmatch(target):
        raise GitHubEvidenceError("TARGET_SHA_INVALID")
    remote_main, tree = current_main()
    if remote_main != target:
        raise GitHubEvidenceError("TARGET_NOT_CURRENT_MAIN")
    required, ruleset_ids = _required_status_checks()
    pr = _merged_pr(target)
    pr_number = pr["number"]
    head_sha = pr["head"]["sha"]
    checks = _status_checks(head_sha=head_sha, required=required)
    return ReleaseEvidence(
        remote_main=remote_main,
        target_tree=tree,
        merged_pr_number=pr_number,
        merged_pr_head=head_sha,
        required_checks=checks,
        ruleset_ids=ruleset_ids,
    )


def approval_candidates(*, target: str, baseline: str) -> list[ApprovalEvidence]:
    issue = _get_json(f"/issues/{APPROVAL_ISSUE}")
    if issue.get("state") != "open" or (issue.get("user") or {}).get("login") != OWNER_LOGIN:
        raise GitHubEvidenceError("APPROVAL_LEDGER_INVALID")
    rows = _pages(f"/issues/{APPROVAL_ISSUE}/comments")
    result: list[ApprovalEvidence] = []
    for row in reversed(rows):
        user = row.get("user") or {}
        body = row.get("body")
        if user.get("login") != OWNER_LOGIN or row.get("author_association") != "OWNER":
            continue
        if not isinstance(body, str) or not body.startswith(APPROVAL_PREFIX):
            continue
        payload_text = body[len(APPROVAL_PREFIX):].strip()
        try:
            request = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise GitHubEvidenceError("OWNER_APPROVAL_JSON_INVALID") from exc
        if not isinstance(request, dict):
            raise GitHubEvidenceError("OWNER_APPROVAL_JSON_INVALID")
        if request.get("target_commit") != target or request.get("expected_baseline") != baseline:
            continue
        comment_id = row.get("id")
        html_url = row.get("html_url")
        if not isinstance(comment_id, int) or not isinstance(html_url, str):
            raise GitHubEvidenceError("OWNER_APPROVAL_METADATA_INVALID")
        created = _parse_time(row.get("created_at"))
        updated = _parse_time(row.get("updated_at"))
        result.append(
            ApprovalEvidence(
                request=request,
                actor=OWNER_LOGIN,
                created_at=created,
                unedited=(created == updated),
                comment_id=comment_id,
                html_url=html_url,
            )
        )
    return result

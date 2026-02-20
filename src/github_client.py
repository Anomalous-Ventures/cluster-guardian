"""
GitHub PR client for Cluster Guardian.

Creates branches, commits files, and opens pull requests via the GitHub REST API.
Uses httpx (already a dependency) with Bearer token authentication.
"""

import base64
from typing import Optional

import httpx
import structlog

from .config import settings

logger = structlog.get_logger(__name__)

API_BASE = "https://api.github.com"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo_url() -> str:
    return f"{API_BASE}/repos/{settings.github_owner}/{settings.github_repo}"


async def _get_base_sha() -> str:
    """Get the SHA of the tip of the base branch."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_repo_url()}/git/ref/heads/{settings.github_base_branch}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()["object"]["sha"]


async def create_branch(branch_name: str) -> str:
    """Create a new branch from the base branch.

    Args:
        branch_name: Name for the new branch (e.g. "guardian/fix-sonarr-memory").

    Returns:
        The SHA of the new branch head.
    """
    sha = await _get_base_sha()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_repo_url()}/git/refs",
            headers=_headers(),
            json={"ref": f"refs/heads/{branch_name}", "sha": sha},
        )
        resp.raise_for_status()
        logger.info("github_branch_created", branch=branch_name, sha=sha)
        return sha


async def create_or_update_file(
    branch: str,
    path: str,
    content: str,
    message: str,
) -> str:
    """Create or update a file on a branch.

    Args:
        branch: Target branch name.
        path: File path in the repo (e.g. "pulumi/stacks/07-media/values.yaml").
        content: New file content (plain text, will be base64-encoded).
        message: Commit message.

    Returns:
        The commit SHA.
    """
    encoded = base64.b64encode(content.encode()).decode()

    # Check if file already exists to get its SHA (needed for updates)
    existing_sha: Optional[str] = None
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_repo_url()}/contents/{path}",
            headers=_headers(),
            params={"ref": branch},
        )
        if resp.status_code == 200:
            existing_sha = resp.json().get("sha")

    payload: dict = {
        "message": message,
        "content": encoded,
        "branch": branch,
    }
    if existing_sha:
        payload["sha"] = existing_sha

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(
            f"{_repo_url()}/contents/{path}",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        commit_sha = resp.json()["commit"]["sha"]
        logger.info("github_file_committed", path=path, sha=commit_sha)
        return commit_sha


async def create_pull_request(
    title: str,
    body: str,
    branch: str,
) -> dict:
    """Open a pull request against the base branch.

    Args:
        title: PR title.
        body: PR body (markdown).
        branch: Head branch name.

    Returns:
        Dict with "number", "url", and "html_url" of the created PR.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_repo_url()}/pulls",
            headers=_headers(),
            json={
                "title": title,
                "body": body,
                "head": branch,
                "base": settings.github_base_branch,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        pr_info = {
            "number": data["number"],
            "url": data["url"],
            "html_url": data["html_url"],
        }
        logger.info("github_pr_created", **pr_info)
        return pr_info


async def add_pr_comment(pr_number: int, body: str) -> bool:
    """Add a comment to an existing pull request.

    Args:
        pr_number: PR number.
        body: Comment body (markdown).

    Returns:
        True if the comment was posted.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_repo_url()}/issues/{pr_number}/comments",
            headers=_headers(),
            json={"body": body},
        )
        resp.raise_for_status()
        logger.info("github_pr_comment_added", pr_number=pr_number)
        return True

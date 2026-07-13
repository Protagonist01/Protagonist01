#!/usr/bin/env python3
"""Update the live GitHub totals displayed in the profile SVG."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
GRAPHQL_URL = f"{API_ROOT}/graphql"
DEFAULT_USERNAME = "Protagonist01"
DEFAULT_SVG = Path(__file__).resolve().parents[1] / "media" / "profile-card.svg"


def github_json(path: str, token: str | None) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Protagonist01-profile-stats",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(f"{API_ROOT}{path}", headers=headers)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code < 500 or attempt == 2:
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GitHub API returned {error.code}: {detail}"
                ) from error
        except URLError as error:
            if attempt == 2:
                raise RuntimeError(
                    f"Could not reach the GitHub API after 3 attempts: {error.reason}"
                ) from error
        time.sleep(2**attempt)

    raise RuntimeError("GitHub API request failed unexpectedly")


def github_graphql(
    query: str, variables: dict[str, str], token: str | None
) -> dict[str, object]:
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN or GH_TOKEN is required to fetch contribution commits"
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Protagonist01-profile-stats",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = Request(GRAPHQL_URL, data=payload, headers=headers, method="POST")

    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise RuntimeError("Unexpected response from the GitHub GraphQL API")
            if result.get("errors"):
                raise RuntimeError(f"GitHub GraphQL returned errors: {result['errors']}")
            data = result.get("data")
            if not isinstance(data, dict):
                raise RuntimeError("GitHub GraphQL response did not include data")
            return data
        except HTTPError as error:
            if error.code < 500 or attempt == 2:
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GitHub GraphQL returned {error.code}: {detail}"
                ) from error
        except URLError as error:
            if attempt == 2:
                raise RuntimeError(
                    "Could not reach the GitHub GraphQL API after 3 attempts: "
                    f"{error.reason}"
                ) from error
        time.sleep(2**attempt)

    raise RuntimeError("GitHub GraphQL request failed unexpectedly")


def fetch_current_year_commits(username: str, token: str | None) -> int:
    now = datetime.now(timezone.utc)
    start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    query = """
        query CurrentYearCommits($login: String!, $from: DateTime!, $to: DateTime!) {
          user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
              totalCommitContributions
            }
          }
        }
    """
    variables = {
        "login": username,
        "from": start.isoformat().replace("+00:00", "Z"),
        "to": now.isoformat().replace("+00:00", "Z"),
    }
    data = github_graphql(query, variables, token)
    user = data.get("user")
    if not isinstance(user, dict):
        raise RuntimeError(f"GitHub user {username!r} was not found")
    contributions = user.get("contributionsCollection")
    if not isinstance(contributions, dict):
        raise RuntimeError("GitHub did not return a contributions collection")
    return int(contributions["totalCommitContributions"])


def fetch_stats(username: str, token: str | None) -> dict[str, int]:
    user = github_json(f"/users/{username}", token)
    if not isinstance(user, dict):
        raise RuntimeError("Unexpected response from the GitHub user endpoint")

    repos: list[dict[str, object]] = []
    page = 1
    while True:
        batch = github_json(
            f"/users/{username}/repos?type=owner&per_page=100&page={page}", token
        )
        if not isinstance(batch, list):
            raise RuntimeError("Unexpected response from the GitHub repositories endpoint")
        repos.extend(repo for repo in batch if isinstance(repo, dict))
        if len(batch) < 100:
            break
        page += 1

    return {
        "repo-count": int(user["public_repos"]),
        "star-count": sum(int(repo.get("stargazers_count", 0)) for repo in repos),
        "follower-count": int(user["followers"]),
        "following-count": int(user["following"]),
        "commit-count": fetch_current_year_commits(username, token),
    }


def update_svg(svg_path: Path, stats: dict[str, int]) -> bool:
    source = svg_path.read_text(encoding="utf-8")
    updated = source

    for element_id, value in stats.items():
        pattern = re.compile(
            rf'(<text\b[^>]*\bid="{re.escape(element_id)}"[^>]*>)([^<]*)(</text>)'
        )
        updated, replacements = pattern.subn(rf"\g<1>{value}\g<3>", updated)
        if replacements != 1:
            raise RuntimeError(
                f"Expected one SVG element with id={element_id!r}; found {replacements}"
            )

    if updated == source:
        return False

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=svg_path.parent, delete=False
    ) as temporary:
        temporary.write(updated)
        temporary_path = Path(temporary.name)
    temporary_path.replace(svg_path)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--svg", type=Path, default=DEFAULT_SVG)
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    stats = fetch_stats(args.username, token)
    changed = update_svg(args.svg, stats)
    state = "updated" if changed else "already current"
    print(f"Profile stats {state}: {stats}")


if __name__ == "__main__":
    main()

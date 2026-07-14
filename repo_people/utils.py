import csv
import os
import time
import re
from typing import Dict, Iterable, List, Optional

import requests


def validate_owner_repo(owner: str, repo: str) -> None:
    """
    Validate a GitHub owner (user/organisation) and repository name, raising an
    error if either contains characters that are not valid in a GitHub name.
    GitHub permits only alphanumeric characters, hyphens, underscores and dots;
    rejecting anything else here prevents URL/path injection further downstream.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation) name to validate.
    :repo: str
        GitHub repository name to validate.

    Returns
    =======
    None

    Raises
    ======
    ValueError:
        When the owner or repo is empty or contains any character other than
        alphanumerics, hyphens, underscores or dots.
    """
    _SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")
    if not owner or not _SAFE.match(owner):
        raise ValueError(
            f"Invalid owner {owner!r}: must contain only alphanumeric characters, "
            "hyphens, underscores, or dots."
        )
    if not repo or not _SAFE.match(repo):
        raise ValueError(
            f"Invalid repo {repo!r}: must contain only alphanumeric characters, "
            "hyphens, underscores, or dots."
        )


def _is_bot(login: str, user_type: str = "") -> bool:
    """
    Determine whether a GitHub account is a bot. Uses the same criteria as the
    sync path in ``users.py`` so both callers share a single source of truth: a
    ``user_type`` of ``"bot"`` (case-insensitive), or a login ending in
    ``[bot]`` or ``-bot``.

    Parameters
    ==========
    :login: str
        the account's GitHub login/username.
    :user_type: str (default="")
        the account's GitHub type (e.g. ``"User"``, ``"Bot"``), if known.

    Returns
    =======
    :is_bot: bool
        True if the account is identified as a bot, otherwise False.
    """
    if (user_type or "").lower() == "bot":
        return True
    if login.endswith("[bot]") or login.endswith("-bot"):
        return True
    return False


def _headers(token: Optional[str], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Build the standard set of GitHub API request headers, optionally injecting a
    bearer token and any caller-supplied extra headers.

    Parameters
    ==========
    :token: str/None
        GitHub personal access token to send as a Bearer authorization header.
        If None, no authorization header is added (unauthenticated request).
    :extra: dict/None (default=None)
        additional headers to merge into the returned dict, overriding defaults.

    Returns
    =======
    :h: dict
        dict of request headers ready to pass to ``requests``.
    """
    h = {"Accept": "application/vnd.github+json", "User-Agent": "gh-census/0.1"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if extra:
        h.update(extra)
    return h


def _sleep_if_ratelimited(resp: requests.Response):
    """
    Sleep until a GitHub rate-limit window expires when a 403 or 429 response is
    returned, so the caller can safely retry the request. The wait is derived
    from the ``X-RateLimit-Reset`` header (falling back to ``Retry-After``), and
    is capped so an excessively long wait is skipped rather than blocking.

    Parameters
    ==========
    :resp: requests.Response
        the response object to inspect for a rate-limit status and headers.

    Returns
    =======
    :result: bool/str
        False if the response is not rate limited; True after sleeping (retry is
        advised); the string ``"skip"`` if the required wait exceeds the maximum
        allowed and the request should be abandoned.
    """
    MAX_SLEEP = 60  # seconds
    if resp.status_code not in (403, 429):
        return False
    # Prefer X-RateLimit-Reset (Unix timestamp); fall back to Retry-After (seconds, used by 429)
    reset = resp.headers.get("X-RateLimit-Reset")
    if reset and reset.isdigit():
        wait_s = max(0, int(reset) - int(time.time()) + 1)
    else:
        retry_after = resp.headers.get("Retry-After")
        wait_s = int(retry_after) if (retry_after and retry_after.isdigit()) else 0
    if wait_s == 0:
        # No Retry-After or X-RateLimit-Reset header — use a short fixed back-off
        # rather than silently giving up, so the caller can retry the request.
        wait_s = 10
    if wait_s > MAX_SLEEP:
        print(f"Rate limit wait ({wait_s}s) exceeds maximum allowed ({MAX_SLEEP}s). Skipping request.", flush=True)
        return "skip"
    print(f"Hit rate limit ({resp.status_code}). Sleeping for {wait_s}s...", flush=True)
    time.sleep(wait_s)
    return True


def paginate(url: str, token: Optional[str], params: Optional[Dict] = None, accept: Optional[str] = None) -> Iterable[Dict]:
    """
    Generic paginator for the GitHub REST API that transparently follows the
    ``Link`` header, handles rate limiting (403/429) with bounded retries, and
    yields each item across all pages. Both list responses and search-style
    ``{"items": [...]}`` responses are supported.

    Parameters
    ==========
    :url: str
        the initial GitHub API URL to request.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.
    :params: dict/None (default=None)
        query-string parameters for the first request. ``per_page`` defaults to
        100 if not supplied.
    :accept: str/None (default=None)
        custom ``Accept`` header value (e.g. for a preview media type).

    Returns
    =======
    :item: dict
        yields each item dict from every page in turn.

    Raises
    ======
    requests.exceptions.HTTPError:
        When a non-200 response other than 403/429/404 is returned.
    """
    params = dict(params or {})
    params.setdefault("per_page", 100)
    _h = _headers(token, {"Accept": accept} if accept else None)
    while url:
        resp = requests.get(url, headers=_h, params=params, timeout=30)
        # Handle rate limit (403 / 429): sleep and retry
        rl_result = _sleep_if_ratelimited(resp)
        # ponytail: cap retries so a persistent 403 that isn't a rate limit
        # (e.g. forbidden resource with no reset header) can't loop forever.
        attempts = 0
        while resp.status_code in (403, 429) and rl_result is True and attempts < 5:
            attempts += 1
            resp = requests.get(url, headers=_h, params=params, timeout=30)
            rl_result = _sleep_if_ratelimited(resp)
        if resp.status_code in (403, 429) and rl_result == "skip":
            return
        if resp.status_code == 404:
            return
        if resp.status_code != 200:
            resp.raise_for_status()
        items = resp.json()
        if isinstance(items, list):
            for it in items:
                yield it
        else:
            for it in items.get("items", []):
                yield it
        link = resp.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part[part.find("<") + 1:part.find(">")]
                break
        url = next_url
        params = None


def write_csv(path: str, header: List[str], rows: Iterable[Iterable]) -> None:
    """
    Write rows to a CSV file at the given path, creating any missing parent
    directories first.

    Parameters
    ==========
    :path: str
        destination file path for the CSV output.
    :header: list
        list of column header strings written as the first row.
    :rows: iterable
        an iterable of row iterables, each written as a CSV record.

    Returns
    =======
    None
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)

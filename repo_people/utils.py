import csv
import os
import time
from typing import Dict, Iterable, List, Optional

import requests


def _headers(token: Optional[str], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build standard GitHub API request headers, optionally injecting a token."""
    h = {"Accept": "application/vnd.github+json", "User-Agent": "gh-census/0.1"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if extra:
        h.update(extra)
    return h


def _sleep_if_ratelimited(resp: requests.Response):
    """Sleep until the rate-limit reset time if a 403 is returned; returns 'skip' if wait is too long."""
    MAX_SLEEP = 60  # seconds
    if resp.status_code == 403:
        reset = resp.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            wait_s = max(0, int(reset) - int(time.time()) + 1)
            if wait_s > MAX_SLEEP:
                print(f"Rate limit wait ({wait_s}s) exceeds maximum allowed ({MAX_SLEEP}s). Skipping request.", flush=True)
                return "skip"
            print(f"Hit rate limit. Sleeping for {wait_s}s...", flush=True)
            time.sleep(wait_s)
            return True
    return False


def paginate(url: str, token: Optional[str], params: Optional[Dict] = None, accept: Optional[str] = None) -> Iterable[Dict]:
    """Generic paginator for the GitHub REST API with rate-limit handling."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    _h = _headers(token, {"Accept": accept} if accept else None)
    while url:
        resp = requests.get(url, headers=_h, params=params)
        # Handle rate limit: sleep and retry
        rl_result = _sleep_if_ratelimited(resp)
        while resp.status_code == 403 and rl_result is True:
            resp = requests.get(url, headers=_h, params=params)
            rl_result = _sleep_if_ratelimited(resp)
        if resp.status_code == 403 and rl_result == "skip":
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
    """Write a CSV file, creating parent directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)

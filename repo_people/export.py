import csv
import os
import time
import base64
from typing import Dict, Iterable, List, Optional, Tuple
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from .utils import _headers, _sleep_if_ratelimited, paginate, write_csv

API_BASE_URL = "https://api.github.com"
BASE = "https://github.com"


def export_commit_authors(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False) -> List[str]:
    """
    Export all unique commit authors (usernames) for a repository.

    Pages through /commits and collects unique author.login values, so there is no
    hard cap on the number of results returned.  Always returns the list of logins;
    the ``return_data`` parameter is kept for backwards compatibility but is ignored.

    .. note::
        ``export_contributors`` and ``export_commit_authors`` walk the same ``/commits``
        endpoint and return equivalent results.  They are aliases of each other.
    """
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/commits"
    authors: set = set()
    # Page through all commits and collect unique authenticated author logins
    for commit in paginate(url, token):
        author = commit.get("author") or {}
        login = author.get("login")
        if login:
            authors.add(login)
    usernames = sorted(authors)
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_commit_authors.csv"), ["login"], [[u] for u in usernames])
    return usernames


def export_dependents(owner: str, repo: str, outdir: str, return_data: bool = True, export_csv: bool = False, limit: Optional[int] = None, sleep: float = 1.0) -> List[str]:
    """
    Scrape and export the list of dependent users (usernames) for a repo.

    Always returns the list of logins; ``return_data`` is kept for backwards
    compatibility but is ignored.  Uses exponential back-off on non-200 responses.

    Parameters
    ----------
    limit:
        Maximum number of unique dependent repositories to collect before stopping.
        ``None`` (default) collects all pages.  Pass ``0`` for an empty result.
    sleep:
        Base sleep interval (seconds) between pages.  Doubles on each failed page
        request up to a maximum of 60 seconds.
    """
    url = f"{BASE}/{owner}/{repo}/network/dependents?dependent_type=REPOSITORY"
    session = requests.Session()
    session.headers.update({
        "User-Agent": "dep-scraper/1.0 (+https://github.com)",
        "Accept": "text/html,application/xhtml+xml",
    })
    seen, out = set(), []
    # Short-circuit: limit=0 means caller wants an empty result
    if limit is not None and limit == 0:
        return []
    page_num = 0
    current_sleep = sleep
    while url:
        page_num += 1
        r = session.get(url, timeout=30, allow_redirects=True)
        if r.status_code != 200:
            # Exponential back-off: double sleep up to 60 s, then give up
            current_sleep = min(current_sleep * 2, 60.0)
            print(f"  [WARN] export_dependents: page {page_num} returned {r.status_code}; "
                  f"sleeping {current_sleep:.0f}s before retry.", flush=True)
            time.sleep(current_sleep)
            break
        current_sleep = sleep  # reset on success
        soup = BeautifulSoup(r.text, "html.parser")
        container = soup.select_one("div.paginate-container")
        rows = container.select("div.Box-row") if container else soup.select("div.Layout div.Layout-main div.Box-row")
        if not rows:
            rows = soup.select("div.Box-row")
        fulls = []
        for row in rows:
            a = row.select_one('a[data-hovercard-type="repository"]')
            if not a:
                a = row.select_one('a[href^="/"][href*="/"]')
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("/"):
                continue
            full = href.strip("/")
            fulls.append(full)
        needle = f"{owner}/{repo}".lower()
        filtered = []
        for full in fulls:
            if "/" not in full:
                continue
            if full.lower() == needle:
                continue
            if full not in seen:
                seen.add(full)
                filtered.append(full)
        out.extend(filtered)
        if limit is not None and len(out) >= limit:
            break
        next_a = soup.select_one('div.paginate-container a.next_page:not(.disabled), div.paginate-container a[rel="next"]:not(.disabled)')
        next_url = urljoin(BASE, next_a["href"]) if next_a and next_a.get("href") else None
        if not next_url:
            a = soup.select_one('a[href*="dependents_after="]:not(.disabled)')
            next_url = urljoin(BASE, a["href"]) if a and a.get("href") else None
        if not next_url:
            break
        url = next_url
        time.sleep(sleep)
    usernames = sorted({full.split("/", 1)[0] for full in out})
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_dependents.csv"), ["login"], [[u] for u in usernames])
    return usernames

def export_contributors(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False) -> List[str]:
    """
    Export all unique contributors (usernames) for a repository.

    Bypasses the /contributors endpoint's hard 100-item cap by paging through /commits
    and collecting unique author.login values — the same commit-walk approach used by
    ``export_commit_authors``.  Both functions return equivalent sets of usernames and
    are aliases of each other.

    Always returns the list of logins; ``return_data`` is kept for backwards compatibility
    but is ignored.
    """
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/commits"
    authors: set = set()
    # Page through all commits; skip anonymous commits (no linked GitHub account)
    for commit in paginate(url, token):
        author = commit.get("author") or {}
        login = author.get("login")
        if login:
            authors.add(login)
    usernames = sorted(authors)
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_contributors.csv"), ["login"], [[u] for u in usernames])
    return usernames


def fetch_codeowners(owner: str, repo: str, token: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    
    candidates = [".github/CODEOWNERS", "docs/CODEOWNERS", "CODEOWNERS"]
    for path in candidates:
        url = f"{API_BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        resp = requests.get(url, headers=_headers(token))
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("encoding") == "base64":
                txt = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
                return path, txt
    return None, None


def parse_codeowners_owners(text: str) -> List[str]:
    owners = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            for token in parts[1:]:
                token = token.strip()
                if token.startswith("@"):
                    owners.add(token.lstrip("@"))
    return sorted(owners)



def export_stargazers(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False) -> List[str]:
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/stargazers"
    usernames = []
    try:
        for s in paginate(url, token, accept="application/vnd.github.star+json"):
            user = s.get("user", {})
            login = user.get("login", "")
            if login:
                usernames.append(login)
    except requests.exceptions.HTTPError as e:
        if token is None and getattr(e.response, "status_code", None) == 401:
            usernames = []
        else:
            raise
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_stargazers.csv"), ["login"], [[u] for u in usernames])
    return usernames


def export_watchers(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False) -> List[str]:
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/subscribers"
    usernames = []
    try:
        for w in paginate(url, token):
            login = w.get("login", "")
            if login:
                usernames.append(login)
    except requests.exceptions.HTTPError as e:
        if token is None and getattr(e.response, "status_code", None) == 401:
            usernames = []
        else:
            raise
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_watchers.csv"), ["login"], [[u] for u in usernames])
    return usernames


def export_issue_authors(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False) -> List[str]:
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/issues"
    usernames = set()
    try:
        for it in paginate(url, token, params={"state": "all"}):
            u = it.get("user") or {}
            login = u.get("login")
            if login:
                usernames.add(login)
    except requests.exceptions.HTTPError as e:
        if token is None and getattr(e.response, "status_code", None) == 401:
            usernames = set()
        else:
            raise
    usernames = sorted(usernames)
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_issue_authors.csv"), ["login"], [[u] for u in usernames])
    return usernames


def export_pr_authors(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False) -> List[str]:
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/pulls"
    usernames = set()
    try:
        for pr in paginate(url, token, params={"state": "all"}):
            u = pr.get("user") or {}
            login = u.get("login")
            if login:
                usernames.add(login)
    except requests.exceptions.HTTPError as e:
        if token is None and getattr(e.response, "status_code", None) == 401:
            usernames = set()
        else:
            raise
    usernames = sorted(usernames)
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_pr_authors.csv"), ["login"], [[u] for u in usernames])
    return usernames

def export_maintainers(owner: str, repo: str, token: Optional[str], outdir: str, skip_codeowners: bool, skip_collaborators: bool, return_data: bool = True, export_csv: bool = False) -> List[str]:
    """
    Export maintainers for a repository to CSV and/or return as list.

    Collects maintainers from two sources (both can be toggled off):
      - CODEOWNERS file: parses @-mentions from .github/CODEOWNERS, docs/CODEOWNERS, or CODEOWNERS.
      - Collaborators API: includes users with admin, maintain, or push permissions.

    Deduplicates across both sources before returning.
    """
    rows = []
    if not skip_codeowners:
        path, text = fetch_codeowners(owner, repo, token)
        if text:
            owners = parse_codeowners_owners(text)
            for o in owners:
                rows.append({
                    "login_or_team": o,
                    "source": "CODEOWNERS",
                    "permissions": "",
                    "url": f"https://github.com/{o}"
                })
    if not skip_collaborators:
        url = f"{API_BASE_URL}/repos/{owner}/{repo}/collaborators"
        try:
            collabs = list(paginate(url, token, params={"per_page": 100}))
        except requests.exceptions.HTTPError as e:
            # If unauthorized and no token, skip collaborators for public repos
            if token is None and getattr(e.response, "status_code", None) == 401:
                collabs = []
            else:
                raise
        if collabs:
            for c in collabs:
                perms = c.get("permissions", {}) or {}
                if any(perms.get(k) for k in ("admin", "maintain", "push")):
                    rows.append({
                        "login_or_team": c.get("login"),
                        "source": "collaborator",
                        "permissions": ";".join([k for k,v in perms.items() if v]),
                        "url": c.get("html_url")
                    })
    # dedupe by login/team name only — the same person in both CODEOWNERS and
    # collaborators is still one maintainer, regardless of which source listed them.
    seen = set()
    usernames = []
    for r in rows:
        key = r["login_or_team"]
        if key in seen:
            continue
        seen.add(key)
        usernames.append(r["login_or_team"])
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_maintainers.csv"), ["login"], [[u] for u in usernames])
    return usernames

def export_fork_owners(owner: str, repo: str, token: str = None, outdir: str = None, return_data: bool = True, export_csv: bool = False) -> List[str]:
    """
    Export the owners of all forks for a repository to CSV and/or return as list.
    """
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/forks"
    usernames = []
    # Use the shared paginate() utility — handles auth, rate limits, and Link-header pagination
    for fork in paginate(url, token):
        login = (fork.get("owner") or {}).get("login", "")
        if login:
            usernames.append(login)
    if export_csv and outdir:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_fork_owners.csv"), ["login"], [[u] for u in usernames])
    return usernames


def export_pr_reviewers(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False) -> List[str]:
    """
    Export all unique PR reviewer usernames for a repository.

    Pages through all open and closed PRs, then fetches the review list for
    each PR to collect unique reviewer logins.  This requires one API call per
    PR in addition to the initial PR listing, so it can be slow on repos with
    many pull requests.

    Always returns the list of logins; ``return_data`` is kept for backwards
    compatibility but is ignored.
    """
    prs_url = f"{API_BASE_URL}/repos/{owner}/{repo}/pulls"
    pr_numbers: List[int] = []
    for pr in paginate(prs_url, token, params={"state": "all"}):
        pr_num = pr.get("number")
        if pr_num:
            pr_numbers.append(pr_num)

    reviewers: set = set()
    for pr_num in pr_numbers:
        reviews_url = f"{API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_num}/reviews"
        for review in paginate(reviews_url, token):
            user = review.get("user") or {}
            login = user.get("login")
            if login:
                reviewers.add(login)

    usernames = sorted(reviewers)
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_pr_reviewers.csv"), ["login"], [[u] for u in usernames])
    return usernames


import os
import threading
import time
import base64
from typing import Dict, List, Optional, Tuple
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from .utils import (
    USER_AGENT,
    _headers,
    _same_host,
    graphql,
    paginate,
    write_csv,
)

API_BASE_URL = "https://api.github.com"
BASE = "https://github.com"

# ponytail: process-wide memo of the commit-author walk, keyed by (owner, repo,
# token, use_cache). export_contributors and export_commit_authors are the same
# query, and collect_all_usernames runs them concurrently by default, so without
# this the full commit history gets paginated twice on every run. The key holds
# the token itself, not just whether one was supplied: two tokens can see
# different sets of commits, and keying on presence alone served one token's
# results to the other. Bounded by the number of distinct repos touched in one
# process, which is ~1 in practice.
# Upgrade path: pass an explicit per-run context object if a long-lived process
# ever needs to collect many repos and cares about the retained memory.
_commit_authors_cache: Dict[tuple, List[str]] = {}
_commit_authors_lock = threading.Lock()


def _walk_commit_authors(owner: str, repo: str, token: Optional[str], use_cache: bool = True) -> List[str]:
    """
    Page through a repository's ``/commits`` endpoint and return the sorted set
    of unique ``author.login`` values, memoised per repository.

    This is the single implementation behind both :func:`export_contributors` and
    :func:`export_commit_authors`, which are the same query against the same
    endpoint. Memoising it means requesting both roles costs one commit walk
    rather than two.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.
    :use_cache: bool (default=True)
        when True, allow the on-disk ETag cache for the underlying requests.

    Returns
    =======
    :usernames: list
        sorted list of unique commit-author login strings.
    """
    key = (owner, repo, token, use_cache)
    with _commit_authors_lock:
        if key in _commit_authors_cache:
            return list(_commit_authors_cache[key])

    url = f"{API_BASE_URL}/repos/{owner}/{repo}/commits"
    authors: set = set()
    # Page through all commits; skip anonymous commits (no linked GitHub account)
    for commit in paginate(url, token, use_cache=use_cache):
        author = commit.get("author") or {}
        login = author.get("login")
        if login:
            authors.add(login)
    usernames = sorted(authors)

    with _commit_authors_lock:
        _commit_authors_cache[key] = usernames
    return list(usernames)


def clear_commit_author_cache() -> None:
    """
    Clear the in-process commit-author memo used by :func:`export_contributors`
    and :func:`export_commit_authors`. Mainly useful in tests, and for long-lived
    processes that want to re-read a repository after new commits land.

    Returns
    =======
    None
    """
    with _commit_authors_lock:
        _commit_authors_cache.clear()


def export_commit_authors(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False, use_cache: bool = True) -> List[str]:
    """
    Export all unique commit authors (usernames) for a repository. Pages through
    the ``/commits`` endpoint and collects unique ``author.login`` values, so
    there is no hard cap on the number of results returned.

    ``export_contributors`` and ``export_commit_authors`` are aliases — both walk
    the same ``/commits`` endpoint. The walk is memoised per repository, so
    requesting both roles in one run performs it only once.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.
    :outdir: str
        output directory used when writing the CSV file.
    :return_data: bool (default=True)
        kept for backwards compatibility and ignored; the list is always returned.
    :export_csv: bool (default=False)
        when True, also write the usernames to ``<owner>_<repo>_commit_authors.csv``.
    :use_cache: bool (default=True)
        when True, allow the on-disk ETag cache for the underlying requests.

    Returns
    =======
    :usernames: list
        sorted list of unique commit-author login strings.
    """
    usernames = _walk_commit_authors(owner, repo, token, use_cache=use_cache)
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_commit_authors.csv"), ["login"], [[u] for u in usernames])
    return usernames


def export_dependents(owner: str, repo: str, outdir: str, return_data: bool = True, export_csv: bool = False, limit: Optional[int] = None, sleep: float = 1.0) -> List[str]:
    """
    Scrape and export the list of dependent users (usernames) for a repository
    from GitHub's HTML dependents page. Stops paginating on the first non-200
    response and deduplicates dependent repositories before returning.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :outdir: str
        output directory used when writing the CSV file.
    :return_data: bool (default=True)
        kept for backwards compatibility and ignored; the list is always returned.
    :export_csv: bool (default=False)
        when True, also write the usernames to ``<owner>_<repo>_dependents.csv``.
    :limit: int/None (default=None)
        maximum number of unique dependent repositories to collect before
        stopping. None collects all pages; 0 returns an empty result.
    :sleep: float (default=1.0)
        sleep interval, in seconds, between successful page requests to stay
        polite to GitHub's HTML endpoint.

    Returns
    =======
    :usernames: list
        sorted list of unique dependent-owner login strings.
    """
    url = f"{BASE}/{owner}/{repo}/network/dependents?dependent_type=REPOSITORY"
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    })
    seen, out = set(), []
    # Short-circuit: limit=0 means caller wants an empty result
    if limit is not None and limit == 0:
        return []
    page_num = 0
    while url:
        page_num += 1
        r = session.get(url, timeout=30, allow_redirects=True)
        if r.status_code != 200:
            # ponytail: no retry follows, so don't sleep before giving up — just stop.
            print(f"  [WARN] export_dependents: page {page_num} returned {r.status_code}; stopping.",
                  flush=True)
            break
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
            # Truncate to the requested cap — a page can push us past it.
            del out[limit:]
            break
        next_a = soup.select_one('div.paginate-container a.next_page:not(.disabled), div.paginate-container a[rel="next"]:not(.disabled)')
        next_url = urljoin(BASE, next_a["href"]) if next_a and next_a.get("href") else None
        if not next_url:
            a = soup.select_one('a[href*="dependents_after="]:not(.disabled)')
            next_url = urljoin(BASE, a["href"]) if a and a.get("href") else None
        if not next_url:
            break
        # The pagination href comes from scraped HTML; an absolute off-site URL
        # would make urljoin() hand us a third-party host to keep crawling.
        if not _same_host(BASE, next_url):
            print(f"  [WARN] export_dependents: refusing off-site pagination link; stopping.", flush=True)
            break
        url = next_url
        time.sleep(sleep)
    usernames = sorted({full.split("/", 1)[0] for full in out})
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_dependents.csv"), ["login"], [[u] for u in usernames])
    return usernames

def export_contributors(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False, use_cache: bool = True) -> List[str]:
    """
    Export all unique contributors (usernames) for a repository. Bypasses the
    ``/contributors`` endpoint's hard 100-item cap by paging through ``/commits``
    and collecting unique ``author.login`` values — the same commit walk used by
    :func:`export_commit_authors`, of which this is an alias. The walk is memoised
    per repository, so requesting both roles performs it only once.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.
    :outdir: str
        output directory used when writing the CSV file.
    :return_data: bool (default=True)
        kept for backwards compatibility and ignored; the list is always returned.
    :export_csv: bool (default=False)
        when True, also write the usernames to ``<owner>_<repo>_contributors.csv``.
    :use_cache: bool (default=True)
        when True, allow the on-disk ETag cache for the underlying requests.

    Returns
    =======
    :usernames: list
        sorted list of unique contributor login strings.
    """
    usernames = _walk_commit_authors(owner, repo, token, use_cache=use_cache)
    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_contributors.csv"), ["login"], [[u] for u in usernames])
    return usernames


def fetch_codeowners(owner: str, repo: str, token: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch a repository's CODEOWNERS file, checking each of the locations GitHub
    recognises (``.github/CODEOWNERS``, ``docs/CODEOWNERS``, ``CODEOWNERS``) and
    returning the first one found.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.

    Returns
    =======
    :(path, text): tuple
        tuple of the repository path where the CODEOWNERS file was found and its
        decoded text contents, or ``(None, None)`` if no CODEOWNERS file exists.
    """
    candidates = [".github/CODEOWNERS", "docs/CODEOWNERS", "CODEOWNERS"]
    for path in candidates:
        url = f"{API_BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        resp = requests.get(url, headers=_headers(token), timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("encoding") == "base64":
                txt = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
                return path, txt
    return None, None


def parse_codeowners_owners(text: str) -> List[str]:
    """
    Parse the @-mentioned owners out of the raw text of a CODEOWNERS file,
    ignoring blank lines and comments and stripping the leading ``@`` from each
    owner or team handle.

    Parameters
    ==========
    :text: str
        the raw text contents of a CODEOWNERS file.

    Returns
    =======
    :owners: list
        sorted list of unique owner/team handles (without the leading ``@``).
    """
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



def export_stargazers(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False, use_cache: bool = True) -> List[str]:
    """
    Export the usernames of all users who have starred a repository, paging
    through the ``/stargazers`` endpoint.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.
    :outdir: str
        output directory used when writing the CSV file.
    :return_data: bool (default=True)
        kept for backwards compatibility and ignored; the list is always returned.
    :export_csv: bool (default=False)
        when True, also write the usernames to ``<owner>_<repo>_stargazers.csv``.

    Returns
    =======
    :usernames: list
        list of stargazer login strings (empty if unauthenticated and the API
        returns 401).

    Raises
    ======
    requests.exceptions.HTTPError:
        For HTTP errors other than an unauthenticated 401 when no token is set.
    """
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/stargazers"
    usernames = []
    try:
        for s in paginate(url, token, accept="application/vnd.github.star+json", use_cache=use_cache):
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


def export_watchers(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False, use_cache: bool = True) -> List[str]:
    """
    Export the usernames of all users watching (subscribed to) a repository,
    paging through the ``/subscribers`` endpoint.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.
    :outdir: str
        output directory used when writing the CSV file.
    :return_data: bool (default=True)
        kept for backwards compatibility and ignored; the list is always returned.
    :export_csv: bool (default=False)
        when True, also write the usernames to ``<owner>_<repo>_watchers.csv``.

    Returns
    =======
    :usernames: list
        list of watcher login strings (empty if unauthenticated and the API
        returns 401).

    Raises
    ======
    requests.exceptions.HTTPError:
        For HTTP errors other than an unauthenticated 401 when no token is set.
    """
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/subscribers"
    usernames = []
    try:
        for w in paginate(url, token, use_cache=use_cache):
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


def export_issue_authors(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False, use_cache: bool = True) -> List[str]:
    """
    Export the unique usernames of all issue authors for a repository, paging
    through the ``/issues`` endpoint in all states (open and closed).

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.
    :outdir: str
        output directory used when writing the CSV file.
    :return_data: bool (default=True)
        kept for backwards compatibility and ignored; the list is always returned.
    :export_csv: bool (default=False)
        when True, also write the usernames to ``<owner>_<repo>_issue_authors.csv``.

    Returns
    =======
    :usernames: list
        sorted list of unique issue-author login strings (empty if
        unauthenticated and the API returns 401).

    Raises
    ======
    requests.exceptions.HTTPError:
        For HTTP errors other than an unauthenticated 401 when no token is set.
    """
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/issues"
    usernames = set()
    try:
        for it in paginate(url, token, params={"state": "all"}, use_cache=use_cache):
            # REST's /issues endpoint returns pull requests as well as issues
            # (they share a number space). Skip the PRs: they belong to the
            # separate pr_authors role, and excluding them here is what makes the
            # REST and GraphQL paths agree on what "issue author" means.
            if it.get("pull_request"):
                continue
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


def export_pr_authors(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False, use_cache: bool = True) -> List[str]:
    """
    Export the unique usernames of all pull-request authors for a repository,
    paging through the ``/pulls`` endpoint in all states (open and closed).

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.
    :outdir: str
        output directory used when writing the CSV file.
    :return_data: bool (default=True)
        kept for backwards compatibility and ignored; the list is always returned.
    :export_csv: bool (default=False)
        when True, also write the usernames to ``<owner>_<repo>_pr_authors.csv``.

    Returns
    =======
    :usernames: list
        sorted list of unique PR-author login strings (empty if unauthenticated
        and the API returns 401).

    Raises
    ======
    requests.exceptions.HTTPError:
        For HTTP errors other than an unauthenticated 401 when no token is set.
    """
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/pulls"
    usernames = set()
    try:
        for pr in paginate(url, token, params={"state": "all"}, use_cache=use_cache):
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

def export_maintainers(owner: str, repo: str, token: Optional[str], outdir: str, skip_codeowners: bool, skip_collaborators: bool, return_data: bool = True, export_csv: bool = False, use_cache: bool = True) -> List[str]:
    """
    Export the maintainers of a repository, collected from two sources (either
    of which can be skipped): @-mentions parsed from the CODEOWNERS file, and
    users with admin, maintain or push permission from the collaborators API.
    Results are deduplicated by login/team name across both sources.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.
    :outdir: str
        output directory used when writing the CSV file.
    :skip_codeowners: bool
        when True, do not read maintainers from the CODEOWNERS file.
    :skip_collaborators: bool
        when True, do not read maintainers from the collaborators API.
    :return_data: bool (default=True)
        kept for backwards compatibility and ignored; the list is always returned.
    :export_csv: bool (default=False)
        when True, also write the usernames to ``<owner>_<repo>_maintainers.csv``.

    Returns
    =======
    :usernames: list
        deduplicated list of maintainer login/team names.

    Raises
    ======
    requests.exceptions.HTTPError:
        For collaborator HTTP errors other than an unauthenticated 401 when no
        token is set.
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
            collabs = list(paginate(url, token, params={"per_page": 100}, use_cache=use_cache))
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

def export_fork_owners(owner: str, repo: str, token: Optional[str] = None, outdir: Optional[str] = None, return_data: bool = True, export_csv: bool = False, use_cache: bool = True) -> List[str]:
    """
    Export the owners of every fork of a repository, paging through the
    ``/forks`` endpoint.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None (default=None)
        GitHub personal access token for authenticated requests, or None.
    :outdir: str/None (default=None)
        output directory used when writing the CSV file; required for CSV export.
    :return_data: bool (default=True)
        kept for backwards compatibility and ignored; the list is always returned.
    :export_csv: bool (default=False)
        when True (and ``outdir`` is set), also write the usernames to
        ``<owner>_<repo>_fork_owners.csv``.

    Returns
    =======
    :usernames: list
        list of fork-owner login strings.
    """
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/forks"
    usernames = []
    # Use the shared paginate() utility — handles auth, rate limits, and Link-header pagination
    for fork in paginate(url, token, use_cache=use_cache):
        login = (fork.get("owner") or {}).get("login", "")
        if login:
            usernames.append(login)
    if export_csv and outdir:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_fork_owners.csv"), ["login"], [[u] for u in usernames])
    return usernames


# The five "flat list of logins" roles, and how to read each one out of GraphQL.
# Each entry is (connection field, GraphQL selection, path to the login).
_SIMPLE_ROLE_CONNECTIONS = {
    "stargazers": ("stargazers", "nodes { login }", ("login",)),
    "watchers": ("watchers", "nodes { login }", ("login",)),
    "fork_owners": ("forks", "nodes { owner { login } }", ("owner", "login")),
    "issue_authors": ("issues", "nodes { author { login } }", ("author", "login")),
    "pr_authors": (
        "pullRequests(states: [OPEN, CLOSED, MERGED])",
        "nodes { author { login } }",
        ("author", "login"),
    ),
}


def _build_simple_roles_query(roles: List[str]) -> str:
    """
    Build a single GraphQL document that fetches the first page of every
    requested simple-list role in one round trip.

    REST needs a separate paginated walk per role; this collapses the common case
    (a repository with under 100 of each) from five HTTP calls into one.

    Parameters
    ==========
    :roles: list
        role names to include; each must be a key of ``_SIMPLE_ROLE_CONNECTIONS``.

    Returns
    =======
    :query: str
        the GraphQL query document.
    """
    selections = []
    for role in roles:
        connection, node_selection, _ = _SIMPLE_ROLE_CONNECTIONS[role]
        # Alias each connection to its role name so results are unambiguous, and
        # splice the page-size argument into any existing argument list.
        if "(" in connection:
            field, args = connection.split("(", 1)
            args = args.rstrip(")")
            field_expr = f"{field}(first: 100, after: ${role}_cursor, {args})"
        else:
            field_expr = f"{connection}(first: 100, after: ${role}_cursor)"
        selections.append(
            f"    {role}: {field_expr} {{\n"
            f"      pageInfo {{ hasNextPage endCursor }}\n"
            f"      {node_selection}\n"
            f"    }}"
        )
    cursor_vars = "".join(f", ${role}_cursor: String" for role in roles)
    body = "\n".join(selections)
    return (
        f"query($owner: String!, $repo: String!{cursor_vars}) {{\n"
        f"  repository(owner: $owner, name: $repo) {{\n"
        f"{body}\n"
        f"  }}\n"
        f"}}\n"
    )


def _dig(node: Dict, path: Tuple[str, ...]) -> Optional[str]:
    """
    Walk a nested-dict path and return the final string value, tolerating nulls
    at any level (GraphQL returns ``author: null`` for deleted accounts).

    Parameters
    ==========
    :node: dict
        the node to read from.
    :path: tuple
        sequence of keys to follow.

    Returns
    =======
    :value: str/None
        the value at the end of the path, or None if any level is missing/null.
    """
    current = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) and current else None


def collect_simple_roles_graphql(
    owner: str,
    repo: str,
    token: str,
    roles: List[str],
) -> Optional[Dict[str, List[str]]]:
    """
    Collect several flat-list roles (stargazers, watchers, fork owners, issue
    authors, PR authors) using the GitHub GraphQL API.

    All requested roles are fetched in a single query, then only the connections
    that report ``hasNextPage`` are paged further — so a repository with fewer
    than 100 of each costs exactly one HTTP call instead of one paginated REST
    walk per role.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str
        GitHub personal access token (GraphQL rejects anonymous requests).
    :roles: list
        role names to collect; unsupported names are ignored.

    Returns
    =======
    :results: dict/None
        dict mapping each supported requested role to its sorted list of unique
        logins, or None when GraphQL is unavailable and the caller should fall
        back to REST.
    """
    wanted = [r for r in roles if r in _SIMPLE_ROLE_CONNECTIONS]
    if not token or not wanted:
        return None

    found: Dict[str, set] = {role: set() for role in wanted}
    cursors: Dict[str, Optional[str]] = {role: None for role in wanted}
    active = list(wanted)

    while active:
        # Rebuild the document each round with only the connections still paging,
        # so finished roles are not refetched.
        query = _build_simple_roles_query(active)
        variables: Dict[str, object] = {"owner": owner, "repo": repo}
        for role in active:
            variables[f"{role}_cursor"] = cursors[role]

        data = graphql(query, variables, token)
        if not data:
            return None
        repository = data.get("repository")
        if not repository:
            return None

        still_active = []
        for role in active:
            connection = repository.get(role) or {}
            _, _, path = _SIMPLE_ROLE_CONNECTIONS[role]
            for node in connection.get("nodes") or []:
                if not node:
                    continue
                login = _dig(node, path)
                if login:
                    found[role].add(login)
            page_info = connection.get("pageInfo") or {}
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                cursors[role] = page_info["endCursor"]
                still_active.append(role)
        active = still_active

    return {role: sorted(found[role]) for role in wanted}


# One GraphQL page returns up to 100 PRs, each with up to 100 nested reviews.
# The REST equivalent needs 1 call per PR, so this collapses ~N calls into ~N/100.
_PR_REVIEWERS_QUERY = """
query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(first: 100, after: $cursor, states: [OPEN, CLOSED, MERGED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        reviews(first: 100) { nodes { author { login } } }
      }
    }
  }
}
"""


def _pr_reviewers_graphql(owner: str, repo: str, token: str) -> Optional[List[str]]:
    """
    Collect PR reviewer logins using the GitHub GraphQL API.

    Fetches pull requests 100 at a time with their reviews nested, rather than
    issuing one REST call per pull request. Returns None (rather than an empty
    list) when GraphQL is unavailable so the caller can distinguish "GraphQL did
    not work, fall back to REST" from "this repo genuinely has no reviewers".

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str
        GitHub personal access token (GraphQL rejects anonymous requests).

    Returns
    =======
    :usernames: list/None
        sorted list of unique reviewer logins, or None if the query failed.
    """
    reviewers: set = set()
    cursor = None
    while True:
        data = graphql(_PR_REVIEWERS_QUERY, {"owner": owner, "repo": repo, "cursor": cursor}, token)
        if not data:
            return None
        repository = data.get("repository")
        if not repository:
            # Repo not visible to this token — treat as "no data", not "empty".
            return None
        prs = repository.get("pullRequests") or {}
        for pr_node in prs.get("nodes") or []:
            if not pr_node:
                continue
            for review in ((pr_node.get("reviews") or {}).get("nodes") or []):
                if not review:
                    continue
                author = review.get("author") or {}
                login = author.get("login")
                if login:
                    reviewers.add(login)
        page_info = prs.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break
    return sorted(reviewers)


def export_pr_reviewers(owner: str, repo: str, token: Optional[str], outdir: str, return_data: bool = True, export_csv: bool = False, use_cache: bool = True, use_graphql: bool = True) -> List[str]:
    """
    Export the unique usernames of everyone who has reviewed a pull request in a
    repository.

    Uses the GraphQL API when a token is available, fetching 100 pull requests
    per call with their reviews nested — roughly a 100x reduction in API calls
    versus the REST path, which needs one extra call per pull request. Falls back
    to REST automatically when there is no token or GraphQL fails.

    Parameters
    ==========
    :owner: str
        GitHub repository owner (user or organisation).
    :repo: str
        GitHub repository name.
    :token: str/None
        GitHub personal access token for authenticated requests, or None.
    :outdir: str
        output directory used when writing the CSV file.
    :return_data: bool (default=True)
        kept for backwards compatibility and ignored; the list is always returned.
    :export_csv: bool (default=False)
        when True, also write the usernames to ``<owner>_<repo>_pr_reviewers.csv``.
    :use_cache: bool (default=True)
        when True, allow the on-disk ETag cache for the REST fallback path.
    :use_graphql: bool (default=True)
        when True (and a token is set), use the GraphQL fast path.

    Returns
    =======
    :usernames: list
        sorted list of unique PR-reviewer login strings.
    """
    usernames: Optional[List[str]] = None
    if use_graphql and token:
        usernames = _pr_reviewers_graphql(owner, repo, token)

    if usernames is None:
        # REST fallback: one call to list PRs, then one per PR for its reviews.
        prs_url = f"{API_BASE_URL}/repos/{owner}/{repo}/pulls"
        pr_numbers: List[int] = []
        for pr in paginate(prs_url, token, params={"state": "all"}, use_cache=use_cache):
            pr_num = pr.get("number")
            if pr_num:
                pr_numbers.append(pr_num)

        reviewers: set = set()
        for pr_num in pr_numbers:
            reviews_url = f"{API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_num}/reviews"
            for review in paginate(reviews_url, token, use_cache=use_cache):
                user = review.get("user") or {}
                login = user.get("login")
                if login:
                    reviewers.add(login)
        usernames = sorted(reviewers)

    if export_csv:
        write_csv(os.path.join(outdir, f"{owner}_{repo}_pr_reviewers.csv"), ["login"], [[u] for u in usernames])
    return usernames

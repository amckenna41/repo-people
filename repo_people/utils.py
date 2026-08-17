import csv
import hashlib
import json
import os
import time
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests

# Single source of truth for the User-Agent sent on every REST call. GitHub asks
# that clients identify themselves; previously each call site invented its own.
USER_AGENT = "repo-people (+https://github.com/amckenna41/repo-people)"


def _same_host(url_a: str, url_b: str) -> bool:
    """
    Return whether two URLs share the same scheme and host (case-insensitive).

    Used by :func:`paginate` to refuse to follow a ``Link`` header that points
    off-host. The ``Link`` header is attacker-controllable in principle (a
    spoofed or compromised response can point ``rel="next"`` anywhere), and
    following it blindly would send the personal access token to a third party.
    Comparing against the caller-supplied starting URL rather than a hardcoded
    allowlist keeps GitHub Enterprise deployments working.

    Parameters
    ==========
    :url_a: str
        the first URL to compare.
    :url_b: str
        the second URL to compare.

    Returns
    =======
    :same: bool
        True if both URLs have the same scheme and hostname, otherwise False.
    """
    a, b = urlparse(url_a), urlparse(url_b)
    return (a.scheme.lower(), a.hostname or "") == (b.scheme.lower(), b.hostname or "")


def extract_token(gh: Any) -> Optional[str]:
    """
    Recover the personal access token from a PyGithub ``Github`` client.

    PyGithub exposes no public accessor for the raw token, so this reads it from
    the requester's ``auth`` object. The previously used private attribute
    ``Requester._Requester__authorizationHeader`` is only a class-level type
    annotation in PyGithub 2.x and is never assigned, so reading it always
    yielded None and silently downgraded authenticated calls to anonymous ones.

    Parameters
    ==========
    :gh: Github/None
        the PyGithub client to read the token from.

    Returns
    =======
    :token: str/None
        the personal access token, or None when the client is unauthenticated
        or the token cannot be recovered.
    """
    if gh is None:
        return None
    try:
        requester = getattr(gh, "_Github__requester", None)
        auth = getattr(requester, "auth", None)
        token = getattr(auth, "token", None)
        return token if isinstance(token, str) and token else None
    except Exception:
        return None


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
    h = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if extra:
        h.update(extra)
    return h


def _is_ratelimit_response(resp: requests.Response) -> bool:
    """
    Return whether a 403/429 response is genuinely a rate limit rather than an
    ordinary authorisation failure.

    GitHub uses 403 for both, and treating every 403 as a rate limit meant a
    permission error (a SAML-protected org, a token missing a scope) was slept
    on and retried five times — roughly 50 seconds burned per URL before the
    error surfaced anyway. A 429 is always a rate limit; a 403 only counts as
    one when GitHub says so, via an exhausted ``X-RateLimit-Remaining``, a
    ``Retry-After`` header (secondary limits), or a rate-limit message in the
    body (secondary limits that send no header).

    Parameters
    ==========
    :resp: requests.Response
        the response object to classify.

    Returns
    =======
    :is_ratelimit: bool
        True when the response should be retried as a rate limit, else False.
    """
    if resp.status_code == 429:
        return True
    if resp.status_code != 403:
        return False
    headers = resp.headers or {}
    if headers.get("X-RateLimit-Remaining") == "0":
        return True
    if headers.get("Retry-After"):
        return True
    # Secondary rate limits can arrive with neither header set, identifiable
    # only by the message body.
    try:
        body = resp.json()
    except Exception:
        return False
    if isinstance(body, dict) and "rate limit" in str(body.get("message", "")).lower():
        return True
    return False


def _sleep_if_ratelimited(resp: requests.Response):
    """
    Sleep until a GitHub rate-limit window expires when a response is a genuine
    rate limit, so the caller can safely retry the request. The wait is derived
    from the ``X-RateLimit-Reset`` header (falling back to ``Retry-After``), and
    is capped so an excessively long wait is skipped rather than blocking.

    A 403 that is *not* a rate limit (see :func:`_is_ratelimit_response`) returns
    False immediately, so a permission error surfaces to the caller straight away
    instead of being slept on and retried.

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
    if not _is_ratelimit_response(resp):
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


def _cache_dir() -> str:
    """
    Return the directory used for the on-disk HTTP response cache, honouring the
    ``REPO_PEOPLE_CACHE_DIR`` environment variable and otherwise falling back to
    ``XDG_CACHE_HOME`` (or ``~/.cache``) under a ``repo-people`` subdirectory.

    Returns
    =======
    :path: str
        absolute path of the cache directory (not created by this call).
    """
    override = os.environ.get("REPO_PEOPLE_CACHE_DIR")
    if override:
        return os.path.expanduser(override)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "repo-people")


def _cache_path(url: str, params: Optional[Dict], accept: Optional[str]) -> str:
    """
    Return the on-disk cache file path for a given request signature. The key is
    a SHA-256 digest of the URL, sorted query parameters and ``Accept`` header,
    so distinct requests never collide and the filename is always filesystem
    safe. The token is deliberately excluded from the key so cached ETags are
    reusable across tokens; cached bodies contain only public data.

    Parameters
    ==========
    :url: str
        the request URL.
    :params: dict/None
        the request query parameters.
    :accept: str/None
        the request ``Accept`` header value.

    Returns
    =======
    :path: str
        absolute path of the JSON cache file for this request.
    """
    signature = json.dumps(
        [url, sorted((params or {}).items(), key=lambda kv: str(kv[0])), accept or ""],
        default=str,
        sort_keys=True,
    )
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return os.path.join(_cache_dir(), f"{digest}.json")


def _cache_read(path: str) -> Optional[Dict]:
    """
    Read a cache entry from disk, returning None when it is missing or corrupt.

    Parameters
    ==========
    :path: str
        path of the cache file to read.

    Returns
    =======
    :entry: dict/None
        the cached entry (keys ``etag``, ``items``, ``link``), or None.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
        return entry if isinstance(entry, dict) and "etag" in entry else None
    except Exception:
        # A corrupt or unreadable cache entry must never break a real request.
        return None


def _cache_write(path: str, etag: str, items, link: str) -> None:
    """
    Write a cache entry to disk, silently giving up on any I/O error so that a
    read-only or full cache directory cannot break collection.

    Parameters
    ==========
    :path: str
        path of the cache file to write.
    :etag: str
        the ``ETag`` header value returned with the response.
    :items: list
        the parsed response payload to store.
    :link: str
        the response ``Link`` header, needed to resume pagination on a 304.

    Returns
    =======
    None
    """
    try:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        # Cached bodies can include private-repo membership. Keep the directory
        # owner-only rather than inheriting whatever the ambient umask allows.
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        # ponytail: write-then-rename so a crash mid-write can't leave a torn
        # entry that later reads have to defend against. The pid in the temp name
        # keeps two concurrent processes caching the same URL from clobbering
        # each other's partial write.
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"etag": etag, "items": items, "link": link}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        pass


def clear_cache() -> int:
    """
    Delete every entry in the on-disk HTTP response cache.

    Returns
    =======
    :removed: int
        the number of cache files removed (0 if the cache does not exist).
    """
    directory = _cache_dir()
    removed = 0
    if not os.path.isdir(directory):
        return 0
    for name in os.listdir(directory):
        # ".tmp" rather than ".json.tmp": temp names carry a pid between the two.
        if name.endswith(".json") or name.endswith(".tmp"):
            try:
                os.remove(os.path.join(directory, name))
                removed += 1
            except OSError:
                pass
    return removed


def _next_link(link_header: str) -> Optional[str]:
    """
    Extract the ``rel="next"`` URL from an HTTP ``Link`` header.

    Parameters
    ==========
    :link_header: str
        the raw ``Link`` header value (may be empty).

    Returns
    =======
    :next_url: str/None
        the next-page URL, or None when the header has no ``next`` relation.
    """
    for part in (link_header or "").split(","):
        if 'rel="next"' in part:
            start, end = part.find("<"), part.find(">")
            if start != -1 and end > start:
                return part[start + 1:end]
    return None


def paginate(
    url: str,
    token: Optional[str],
    params: Optional[Dict] = None,
    accept: Optional[str] = None,
    use_cache: bool = True,
) -> Iterable[Dict]:
    """
    Generic paginator for the GitHub REST API that transparently follows the
    ``Link`` header, handles rate limiting with bounded retries, and yields each
    item across all pages. Both list responses and search-style
    ``{"items": [...]}`` responses are supported.

    Only a *genuine* rate limit is retried (see :func:`_is_ratelimit_response`).
    GitHub also uses 403 for ordinary authorisation failures — a SAML-protected
    organisation, a token missing a scope — and those now raise straight away
    instead of being slept on and retried.

    When *use_cache* is set, each page's ``ETag`` is stored on disk and replayed
    as an ``If-None-Match`` header on later runs. GitHub answers an unchanged
    page with a ``304 Not Modified`` that **does not count against the rate
    limit**, so repeat collections against the same repository are both faster
    and much cheaper.

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
    :use_cache: bool (default=True)
        when True, use the on-disk ETag cache to issue conditional requests.

    Returns
    =======
    :item: dict
        yields each item dict from every page in turn.

    Raises
    ======
    requests.exceptions.HTTPError:
        When a non-200 response other than 404 or a retryable rate limit is
        returned — including a 403 that is an authorisation failure rather than
        a rate limit.
    """
    params = dict(params or {})
    params.setdefault("per_page", 100)
    _h = _headers(token, {"Accept": accept} if accept else None)
    start_url = url
    while url:
        cache_file = _cache_path(url, params, accept) if use_cache else None
        cached = _cache_read(cache_file) if cache_file else None
        request_headers = dict(_h)
        if cached:
            request_headers["If-None-Match"] = cached["etag"]

        resp = requests.get(url, headers=request_headers, params=params, timeout=30)
        # Handle rate limit (403 / 429): sleep and retry
        rl_result = _sleep_if_ratelimited(resp)
        # ponytail: cap retries so a persistent 403 that isn't a rate limit
        # (e.g. forbidden resource with no reset header) can't loop forever.
        attempts = 0
        while resp.status_code in (403, 429) and rl_result is True and attempts < 5:
            attempts += 1
            resp = requests.get(url, headers=request_headers, params=params, timeout=30)
            rl_result = _sleep_if_ratelimited(resp)
        if resp.status_code in (403, 429) and rl_result == "skip":
            return
        if resp.status_code == 404:
            return

        if resp.status_code == 304:
            if not cached:
                # A 304 we cannot replay: the cache entry vanished between the
                # read and the response (concurrent clear_cache, evicted file).
                # raise_for_status() does not fire on 3xx, so falling through
                # would parse an empty body and raise a JSON error instead.
                print(
                    "  [WARNING] Received 304 with no cached entry to replay; "
                    "stopping pagination.",
                    flush=True,
                )
                return
            # Unchanged since last run — replay the cached page for free.
            items = cached["items"]
            link = cached.get("link", "")
        else:
            if resp.status_code != 200:
                resp.raise_for_status()
            items = resp.json()
            link = resp.headers.get("Link", "")
            etag = resp.headers.get("ETag")
            if cache_file and etag:
                _cache_write(cache_file, etag, items, link)

        if isinstance(items, list):
            for it in items:
                yield it
        elif isinstance(items, dict):
            for it in items.get("items", []):
                yield it

        next_url = _next_link(link)
        # Never follow a Link header off the host we started on — doing so would
        # send the Authorization header to a third party.
        if next_url and not _same_host(start_url, next_url):
            print(
                f"  [WARNING] Refusing to follow cross-host pagination link to "
                f"{urlparse(next_url).hostname!r}; stopping.",
                flush=True,
            )
            return
        url = next_url
        params = None


# ponytail: a lookup table, not a geocoder. GitHub location strings are free
# text ("SF", "Berlin, Germany", "🇯🇵"), and the point of normalize_country() is
# only to make summarise()'s top_locations aggregate sanely instead of counting
# "SF" and "San Francisco" separately. It resolves the common cases and returns
# "" for anything it does not recognise — callers must treat "" as unknown, not
# as "not in a country". Upgrade path if this proves too coarse: swap the body
# for a geocoding service (geopy/Nominatim) behind the same signature.
_COUNTRY_ALIASES = {
    # Country names and common abbreviations -> ISO 3166-1 alpha-2
    "usa": "US", "u.s.a.": "US", "u.s.": "US", "us": "US", "america": "US",
    "united states": "US", "united states of america": "US",
    "uk": "GB", "u.k.": "GB", "united kingdom": "GB", "great britain": "GB",
    "england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "germany": "DE", "deutschland": "DE", "france": "FR", "spain": "ES",
    "españa": "ES", "italy": "IT", "italia": "IT", "netherlands": "NL",
    "holland": "NL", "belgium": "BE", "switzerland": "CH", "austria": "AT",
    "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI",
    "iceland": "IS", "ireland": "IE", "poland": "PL", "portugal": "PT",
    "czechia": "CZ", "czech republic": "CZ", "greece": "GR", "hungary": "HU",
    "romania": "RO", "bulgaria": "BG", "ukraine": "UA", "russia": "RU",
    "turkey": "TR", "türkiye": "TR", "israel": "IL", "india": "IN",
    "china": "CN", "japan": "JP", "south korea": "KR", "korea": "KR",
    "taiwan": "TW", "singapore": "SG", "malaysia": "MY", "indonesia": "ID",
    "thailand": "TH", "vietnam": "VN", "philippines": "PH", "pakistan": "PK",
    "bangladesh": "BD", "australia": "AU", "new zealand": "NZ",
    "canada": "CA", "mexico": "MX", "méxico": "MX", "brazil": "BR",
    "brasil": "BR", "argentina": "AR", "chile": "CL", "colombia": "CO",
    "peru": "PE", "uruguay": "UY", "south africa": "ZA", "nigeria": "NG",
    "kenya": "KE", "egypt": "EG", "morocco": "MA", "ghana": "GH",
    "ethiopia": "ET", "tanzania": "TZ", "uganda": "UG",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA",
    "qatar": "QA", "kuwait": "KW", "jordan": "JO", "lebanon": "LB",
    "iran": "IR", "iraq": "IQ", "nepal": "NP", "sri lanka": "LK",
    "belarus": "BY", "serbia": "RS", "croatia": "HR", "slovenia": "SI",
    "slovakia": "SK", "lithuania": "LT", "latvia": "LV", "estonia": "EE",
    "hong kong": "HK", "macau": "MO", "luxembourg": "LU", "malta": "MT",
    "cyprus": "CY", "georgia country": "GE", "armenia": "AM",
    "azerbaijan": "AZ", "kazakhstan": "KZ", "uzbekistan": "UZ",
    "venezuela": "VE", "ecuador": "EC", "bolivia": "BO", "paraguay": "PY",
    "costa rica": "CR", "panama": "PA", "cuba": "CU", "guatemala": "GT",
    "dominican republic": "DO", "puerto rico": "PR",
    # Well-known cities that appear without a country far more often than not
    "san francisco": "US", "sf": "US", "bay area": "US", "nyc": "US",
    "new york": "US", "new york city": "US", "seattle": "US", "austin": "US",
    "boston": "US", "chicago": "US", "los angeles": "US", "la": "US",
    "denver": "US", "atlanta": "US", "portland": "US", "silicon valley": "US",
    "london": "GB", "berlin": "DE", "munich": "DE", "münchen": "DE",
    "hamburg": "DE", "paris": "FR", "amsterdam": "NL", "madrid": "ES",
    "barcelona": "ES", "rome": "IT", "milan": "IT", "zurich": "CH",
    "zürich": "CH", "vienna": "AT", "stockholm": "SE", "oslo": "NO",
    "copenhagen": "DK", "helsinki": "FI", "dublin": "IE", "warsaw": "PL",
    "lisbon": "PT", "prague": "CZ", "moscow": "RU", "kyiv": "UA",
    "kiev": "UA", "istanbul": "TR", "tel aviv": "IL", "bangalore": "IN",
    "bengaluru": "IN", "mumbai": "IN", "delhi": "IN", "new delhi": "IN",
    "hyderabad": "IN", "chennai": "IN", "pune": "IN", "beijing": "CN",
    "shanghai": "CN", "shenzhen": "CN", "hangzhou": "CN", "tokyo": "JP",
    "osaka": "JP", "kyoto": "JP", "seoul": "KR", "taipei": "TW",
    "sydney": "AU", "melbourne": "AU", "brisbane": "AU", "auckland": "NZ",
    "toronto": "CA", "vancouver": "CA", "montreal": "CA", "ottawa": "CA",
    "são paulo": "BR", "sao paulo": "BR", "rio de janeiro": "BR",
    "buenos aires": "AR", "santiago": "CL", "bogota": "CO", "bogotá": "CO",
    "lima": "PE",
    "mexico city": "MX", "cape town": "ZA", "johannesburg": "ZA",
    "lagos": "NG", "nairobi": "KE", "cairo": "EG", "dubai": "AE",
}

# US state and territory postal codes — "Austin, TX" is by far the most common
# shape of an American GitHub location string.
_US_STATES = frozenset(
    "al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms "
    "mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv "
    "wi wy dc".split()
)

# Canadian province codes, for the same reason.
_CA_PROVINCES = frozenset("ab bc mb nb nl ns nt nu on pe qc sk yt".split())


def normalize_country(location: str) -> str:
    """
    Best-effort mapping of a free-text GitHub location string to an ISO 3166-1
    alpha-2 country code.

    GitHub's ``location`` field is unconstrained free text, so this is a
    heuristic lookup over common country names, abbreviations, major cities and
    US/Canadian subdivision codes — not a geocoder. Unrecognised input yields an
    empty string, which callers must read as "unknown" rather than "stateless".

    Parameters
    ==========
    :location: str
        the raw location string from a GitHub profile.

    Returns
    =======
    :country: str
        an upper-case ISO 3166-1 alpha-2 country code, or ``""`` when the
        location is empty or could not be recognised.
    """
    if not location:
        return ""
    cleaned = re.sub(r"[^\w\s,.\-']", " ", location.strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""

    # Whole-string match first — handles "united kingdom" and "san francisco".
    if cleaned in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[cleaned]

    # Otherwise work right-to-left through comma-separated parts: location
    # strings put the most significant component last ("Austin, TX, USA").
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    for part in reversed(parts):
        if part in _COUNTRY_ALIASES:
            return _COUNTRY_ALIASES[part]
        bare = part.replace(".", "")
        if bare in _US_STATES:
            return "US"
        if bare in _CA_PROVINCES:
            return "CA"
        if bare in _COUNTRY_ALIASES:
            return _COUNTRY_ALIASES[bare]
    return ""


GRAPHQL_URL = "https://api.github.com/graphql"


def graphql(
    query: str,
    variables: Dict[str, Any],
    token: str,
    max_retries: int = 5,
) -> Optional[Dict]:
    """
    Execute a single GitHub GraphQL query and return the ``data`` payload.

    The GraphQL API requires authentication, so callers must supply a token and
    fall back to REST when they have none. Rate limiting (403/429) is handled
    with the same bounded back-off used by :func:`paginate`.

    Parameters
    ==========
    :query: str
        the GraphQL query document to execute.
    :variables: dict
        variable values to bind into the query.
    :token: str
        GitHub personal access token (required — GraphQL rejects anonymous calls).
    :max_retries: int (default=5)
        maximum number of rate-limit retries before giving up.

    Returns
    =======
    :data: dict/None
        the ``data`` object from the response, or None when the query failed,
        was rate limited beyond the retry budget, or returned only errors.
    """
    if not token:
        return None
    headers = _headers(token, {"Accept": "application/vnd.github+json"})
    payload = {"query": query, "variables": variables}
    for _ in range(max_retries + 1):
        try:
            resp = requests.post(GRAPHQL_URL, json=payload, headers=headers, timeout=30)
        except requests.exceptions.RequestException as exc:
            print(f"  [WARNING] GraphQL request failed: {exc}", flush=True)
            return None
        rl_result = _sleep_if_ratelimited(resp)
        if rl_result is True:
            continue
        if rl_result == "skip":
            return None
        if resp.status_code == 401:
            print("  [WARNING] GraphQL rejected the token (401); falling back to REST.", flush=True)
            return None
        if resp.status_code != 200:
            print(f"  [WARNING] GraphQL returned HTTP {resp.status_code}; falling back to REST.", flush=True)
            return None
        try:
            body = resp.json()
        except ValueError:
            return None
        # GraphQL reports partial failures in "errors" while still returning 200.
        errors = body.get("errors") or []
        if errors and not body.get("data"):
            messages = "; ".join(str(e.get("message", e)) for e in errors[:3])
            print(f"  [WARNING] GraphQL error: {messages}", flush=True)
            return None
        return body.get("data")
    return None


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

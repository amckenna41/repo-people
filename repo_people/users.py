from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any, Tuple, Union, TYPE_CHECKING
from urllib.parse import urlparse
from datetime import datetime, timezone

import requests
from github import Github, Auth
from github.NamedUser import NamedUser
from github.GithubObject import IncompletableObject
from github.GithubException import RateLimitExceededException
import json

from .utils import USER_AGENT, _is_bot, extract_token, normalize_country

if TYPE_CHECKING:
    from github.Repository import Repository

@dataclass
class UserSnapshot:
    # Core identity
    login: str
    id: Optional[int]
    node_id: str
    type: str
    name: str
    company: str
    location: str
    email_public: str
    email_domain: str
    blog: str
    blog_host: str
    twitter: str
    bio: str
    avatar_url: str
    html_url: str
    hireable: bool
    site_admin: bool
    created_at: str
    updated_at: str

    # Counters
    followers: int
    following: int
    public_repos: int
    public_gists: int

    # Orgs
    public_orgs: List[str]
    orgs_public_count: int

    # Signals / derived
    is_bot: bool
    last_public_event_at: str

    # NEW — cheap flags / normalized
    has_public_email: bool = False
    has_blog: bool = False
    has_twitter: bool = False
    company_normalized: str = ""
    location_normalized: str = ""
    # Best-effort ISO 3166-1 alpha-2 code derived from the free-text location.
    # Empty string means "could not be determined", not "no country".
    location_country: str = ""

    # NEW — small computed metrics
    account_age_days: int = 0
    followers_following_ratio: float = 0.0
    repos_per_year: float = 0.0
    recently_active: bool = False  # activity in last N days

    # Optional aggregates (filled if computed)
    top_languages: Optional[List[Tuple[str, int]]] = None
    total_public_stars_sampled: Optional[int] = None
    total_public_forks_sampled: Optional[int] = None

    # NEW — optional bounded counts
    ssh_keys_count: Optional[int] = None
    gpg_keys_count: Optional[int] = None
    starred_repos_sampled: Optional[int] = None

    # Social accounts (provider -> url mapping from GitHub social accounts API)
    social_accounts: Optional[Dict[str, str]] = None

    # Repo-specific (filled if a repo was provided and you have rights)
    is_collaborator: Optional[bool] = None
    permission_on_repo: Optional[str] = None


class GitHubUserInfo:
    """
    Wrapper around a GitHub user (PyGithub NamedUser) that exposes
    cached, easy-to-use accessors and a single 'snapshot()' to dump
    all attributes as a dataclass.
    """
    def __init__(self, gh: Optional[Github] = None, username: Optional[str] = None, user_obj: Optional[NamedUser] = None, token: Optional[str] = None):
        """
        Initialise a wrapper for a single GitHub user, either by username or by
        an existing PyGithub ``NamedUser`` object. A GitHub client is reused if
        supplied, otherwise one is created (authenticated when a token is given).

        Parameters
        ==========
        :gh: Github/None (default=None)
            an existing PyGithub client to reuse; if None a new one is created.
        :username: str/None (default=None)
            the GitHub login to wrap. Required if *user_obj* is not given.
        :user_obj: NamedUser/None (default=None)
            an already-fetched PyGithub user object. Required if *username* is
            not given.
        :token: str/None (default=None)
            personal access token used only when *gh* is None, to build an
            authenticated client.

        Raises
        ======
        ValueError:
            When neither *username* nor *user_obj* is provided.
        """
        if not (username or user_obj):
            raise ValueError("Provide either username or user_obj")

        # Create GitHub client if not provided
        if gh is None:
            if token:
                self._gh = Github(auth=Auth.Token(token))
            else:
                self._gh = Github()
        else:
            self._gh = gh

        # Keep the raw token for the direct REST calls that bypass PyGithub (see
        # social_accounts). Prefer the explicit argument, otherwise recover it
        # from the client we were handed — without this, those calls silently go
        # out unauthenticated and get rate limited after 60 requests/hour.
        self._token = token or extract_token(self._gh)

        self._user_obj: Optional[NamedUser] = user_obj
        self._username = username or (user_obj.login if user_obj else None)
        self._cache: Dict[str, Any] = {}

    # ---------- internal helpers ----------
    def _user(self) -> NamedUser:
        """
        Return the underlying PyGithub ``NamedUser`` object, lazily fetching it
        from the API on first access. Fetch failures are logged and result in
        None rather than raising.

        Returns
        =======
        :_user_obj: NamedUser/None
            the resolved user object, or None if it could not be fetched.
        """
        if self._user_obj is None:
            try:
                self._user_obj = self._gh.get_user(self._username)
                if self._user_obj is None:
                    print(f"[DEBUG] GitHub API returned None for user: {self._username}")
            except Exception as e:
                print(f"[DEBUG] Failed to get user {self._username}: {e}")
                self._user_obj = None
        return self._user_obj

    def _get_basic(self, attr: str, default=None):
        """
        Safely read a single attribute from the underlying user object, returning
        a default instead of raising when the user cannot be fetched, the rate
        limit is hit, or the object is incomplete.

        Parameters
        ==========
        :attr: str
            the attribute name to read from the PyGithub user object.
        :default: any (default=None)
            value returned when the attribute is unavailable or an error occurs.

        Returns
        =======
        :result: any
            the attribute value, or *default* on any failure.
        """
        try:
            user_obj = self._user()
            if user_obj is None:
                print(f"[DEBUG] User object is None for {self._username}.{attr}")
                return default
            result = getattr(user_obj, attr, default)
            return result
        except RateLimitExceededException as e:
            print(f"[WARNING] Rate limit exceeded for {self._username}.{attr}: {e}")
            return default
        except IncompletableObject as e:
            print(f"[DEBUG] IncompletableObject error for {self._username}.{attr}: {e}")
            return default
        except Exception as e:
            print(f"[DEBUG] Exception getting {self._username}.{attr}: {e}")
            return default

    # NEW — small helpers
    def _normalized_company(self) -> str:
        """
        Return the user's company string normalised by trimming whitespace and
        stripping a leading ``@`` (as used in GitHub org handles).

        Returns
        =======
        :company: str
            the normalised company name.
        """
        c = (self.company or "").strip()
        return c[1:] if c.startswith("@") else c

    def _normalized_location(self) -> str:
        """
        Return the user's location string normalised to trimmed lower case for
        easier grouping and comparison.

        Returns
        =======
        :location: str
            the normalised location string.
        """
        return (self.location or "").strip().lower()

    def _location_country(self) -> str:
        """
        Return a best-effort ISO 3166-1 alpha-2 country code for the user's
        free-text location (see :func:`~repo_people.utils.normalize_country`).

        Returns
        =======
        :country: str
            an alpha-2 country code, or ``""`` when it cannot be determined.
        """
        return normalize_country(self.location)

    def _days_since(self, iso: str) -> int:
        """
        Return the whole number of days between an ISO-8601 timestamp and now
        (UTC), clamped to a minimum of 0.

        Parameters
        ==========
        :iso: str
            an ISO-8601 timestamp string (a trailing ``Z`` is accepted).

        Returns
        =======
        :days: int
            number of days elapsed, or 0 if *iso* is empty or unparseable.
        """
        if not iso:
            return 0
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return max(0, int((datetime.now(timezone.utc) - dt).days))
        except Exception:
            return 0

    def _followers_following_ratio(self) -> float:
        """
        Return the ratio of followers to following. When the user follows nobody,
        the follower count itself is returned to avoid division by zero.

        Returns
        =======
        :ratio: float
            followers divided by following, rounded to 2 decimal places.
        """
        f, g = self.followers, self.following
        return float(f) if g == 0 else round(f / g, 2)

    def _repos_per_year(self) -> float:
        """
        Return the user's average number of public repositories created per year,
        based on account age.

        Accounts younger than a year are treated as one year old so that a
        brand-new account with a handful of repos does not report an absurd rate
        (and so division by zero is impossible). The async path in
        ``repo_people.py`` applies the identical formula — the two must agree or
        the same user yields different numbers depending on which API was used.

        Returns
        =======
        :repos_per_year: float
            public repos per year, rounded to 2 decimal places.
        """
        days = self._days_since(self.created_at)
        years = max(days / 365.25, 1.0)
        return round(self.public_repos / years, 2)

    def _recently_active(self, days: int = 90) -> bool:
        """
        Return whether the user has had public activity within the last *days*
        days, based on their most recent public event.

        Parameters
        ==========
        :days: int (default=90)
            the activity window, in days.

        Returns
        =======
        :recently_active: bool
            True if the last public event falls within the window, else False.
        """
        return self._days_since(self.last_public_event_at) <= days if self.last_public_event_at else False

    # ---------- public lightweight properties (cheap) ----------
    @property
    def login(self) -> str:
        """
        Return the user's GitHub login, falling back to the username supplied at
        construction if the API does not provide one. Cached after first access.

        Returns
        =======
        :login: str
            the user's GitHub username.
        """
        if "login" not in self._cache:
            # Try to get login from API, but fall back to the username we were given
            api_login = self._get_basic("login", "")
            self._cache["login"] = api_login or self._username or ""
        return self._cache["login"]

    @property
    def id(self) -> Optional[int]:
        """
        Return the user's numeric GitHub ID. Cached after first access.

        Returns
        =======
        :id: int/None
            the user's GitHub ID, or None if unavailable.
        """
        if "id" not in self._cache:
            self._cache["id"] = self._get_basic("id", None)
        return self._cache["id"]

    @property
    def node_id(self) -> str:
        """
        Return the user's GraphQL global node ID. Cached after first access.

        Returns
        =======
        :node_id: str
            the user's node ID, or an empty string if unavailable.
        """
        if "node_id" not in self._cache:
            self._cache["node_id"] = self._get_basic("node_id", "") or ""
        return self._cache["node_id"]

    @property
    def type(self) -> str:
        """
        Return the user's account type (e.g. ``"User"``, ``"Organization"``,
        ``"Bot"``). Cached after first access.

        Returns
        =======
        :type: str
            the account type, or an empty string if unavailable.
        """
        if "type" not in self._cache:
            self._cache["type"] = self._get_basic("type", "") or ""
        return self._cache["type"]

    @property
    def name(self) -> str:
        """
        Return the user's display name. Cached after first access.

        Returns
        =======
        :name: str
            the display name, or an empty string if unavailable.
        """
        if "name" not in self._cache:
            self._cache["name"] = self._get_basic("name", "") or ""
        return self._cache["name"]

    @property
    def company(self) -> str:
        """
        Return the user's company as listed on their profile. Cached after first
        access.

        Returns
        =======
        :company: str
            the company string, or an empty string if unavailable.
        """
        if "company" not in self._cache:
            self._cache["company"] = self._get_basic("company", "") or ""
        return self._cache["company"]

    @property
    def location(self) -> str:
        """
        Return the user's location as listed on their profile. Cached after
        first access.

        Returns
        =======
        :location: str
            the location string, or an empty string if unavailable.
        """
        if "location" not in self._cache:
            self._cache["location"] = self._get_basic("location", "") or ""
        return self._cache["location"]

    @property
    def email_public(self) -> str:
        """
        Return the user's publicly listed email address. Cached after first
        access.

        Returns
        =======
        :email_public: str
            the public email, or an empty string if unavailable.
        """
        if "email_public" not in self._cache:
            self._cache["email_public"] = self._get_basic("email", "") or ""
        return self._cache["email_public"]

    @property
    def email_domain(self) -> str:
        """
        Return the lower-cased domain portion of the user's public email address.
        Cached after first access.

        Returns
        =======
        :email_domain: str
            the email domain, or an empty string if there is no public email.
        """
        if "email_domain" not in self._cache:
            try:
                self._cache["email_domain"] = (self.email_public or "").split("@", 1)[1].lower()
            except Exception:
                self._cache["email_domain"] = ""
        return self._cache["email_domain"]

    @property
    def blog(self) -> str:
        """
        Return the user's blog/website URL as listed on their profile. Cached
        after first access.

        Returns
        =======
        :blog: str
            the blog URL, or an empty string if unavailable.
        """
        if "blog" not in self._cache:
            self._cache["blog"] = self._get_basic("blog", "") or ""
        return self._cache["blog"]

    @property
    def blog_host(self) -> str:
        """
        Return the lower-cased host portion of the user's blog URL. Cached after
        first access.

        Returns
        =======
        :blog_host: str
            the blog hostname, or an empty string if there is no blog URL.
        """
        if "blog_host" not in self._cache:
            self._cache["blog_host"] = (urlparse(self.blog).hostname or "").lower() if self.blog else ""
        return self._cache["blog_host"]

    @property
    def twitter(self) -> str:
        """
        Return the user's Twitter/X username as listed on their profile. Cached
        after first access.

        Returns
        =======
        :twitter: str
            the Twitter username, or an empty string if unavailable.
        """
        if "twitter" not in self._cache:
            self._cache["twitter"] = self._get_basic("twitter_username", "") or ""
        return self._cache["twitter"]

    @property
    def bio(self) -> str:
        """
        Return the user's profile bio text. Cached after first access.

        Returns
        =======
        :bio: str
            the bio text, or an empty string if unavailable.
        """
        if "bio" not in self._cache:
            self._cache["bio"] = self._get_basic("bio", "") or ""
        return self._cache["bio"]

    @property
    def avatar_url(self) -> str:
        """
        Return the URL of the user's avatar image. Cached after first access.

        Returns
        =======
        :avatar_url: str
            the avatar URL, or an empty string if unavailable.
        """
        if "avatar_url" not in self._cache:
            self._cache["avatar_url"] = self._get_basic("avatar_url", "") or ""
        return self._cache["avatar_url"]

    @property
    def html_url(self) -> str:
        """
        Return the URL of the user's GitHub profile page. Cached after first
        access.

        Returns
        =======
        :html_url: str
            the profile URL, or an empty string if unavailable.
        """
        if "html_url" not in self._cache:
            self._cache["html_url"] = self._get_basic("html_url", "") or ""
        return self._cache["html_url"]

    @property
    def hireable(self) -> bool:
        """
        Return whether the user has flagged themselves as available for hire.
        Cached after first access.

        Returns
        =======
        :hireable: bool
            True if the user is marked hireable, otherwise False.
        """
        if "hireable" not in self._cache:
            self._cache["hireable"] = bool(self._get_basic("hireable", False))
        return self._cache["hireable"]

    @property
    def site_admin(self) -> bool:
        """
        Return whether the account is a GitHub site administrator. Cached after
        first access.

        Returns
        =======
        :site_admin: bool
            True if the account is a site admin, otherwise False.
        """
        if "site_admin" not in self._cache:
            self._cache["site_admin"] = bool(self._get_basic("site_admin", False))
        return self._cache["site_admin"]

    @property
    def created_at(self) -> str:
        """
        Return the account creation timestamp as an ISO-8601 string. Cached after
        first access.

        Returns
        =======
        :created_at: str
            the creation timestamp, or an empty string if unavailable.
        """
        if "created_at" not in self._cache:
            dt = self._get_basic("created_at", None)
            self._cache["created_at"] = dt.isoformat() if dt else ""
        return self._cache["created_at"]

    @property
    def updated_at(self) -> str:
        """
        Return the account's last-updated timestamp as an ISO-8601 string. Cached
        after first access.

        Returns
        =======
        :updated_at: str
            the last-updated timestamp, or an empty string if unavailable.
        """
        if "updated_at" not in self._cache:
            dt = self._get_basic("updated_at", None)
            self._cache["updated_at"] = dt.isoformat() if dt else ""
        return self._cache["updated_at"]

    @property
    def followers(self) -> int:
        """
        Return the user's follower count. Cached after first access.

        Returns
        =======
        :followers: int
            number of followers (0 if unavailable).
        """
        if "followers" not in self._cache:
            self._cache["followers"] = int(self._get_basic("followers", 0) or 0)
        return self._cache["followers"]

    @property
    def following(self) -> int:
        """
        Return the number of accounts the user follows. Cached after first
        access.

        Returns
        =======
        :following: int
            number of accounts followed (0 if unavailable).
        """
        if "following" not in self._cache:
            self._cache["following"] = int(self._get_basic("following", 0) or 0)
        return self._cache["following"]

    @property
    def public_repos(self) -> int:
        """
        Return the user's public repository count. Cached after first access.

        Returns
        =======
        :public_repos: int
            number of public repositories (0 if unavailable).
        """
        if "public_repos" not in self._cache:
            self._cache["public_repos"] = int(self._get_basic("public_repos", 0) or 0)
        return self._cache["public_repos"]

    @property
    def public_gists(self) -> int:
        """
        Return the user's public gist count. Cached after first access.

        Returns
        =======
        :public_gists: int
            number of public gists (0 if unavailable).
        """
        if "public_gists" not in self._cache:
            self._cache["public_gists"] = int(self._get_basic("public_gists", 0) or 0)
        return self._cache["public_gists"]

    @property
    def public_orgs(self) -> List[str]:
        """
        Return the list of organisation logins the user is publicly a member of.
        Cached after first access.

        Returns
        =======
        :public_orgs: list
            list of organisation login strings (empty on error).
        """
        if "public_orgs" not in self._cache:
            try:
                orgs = [o.login for o in self._user().get_orgs()]
            except Exception:
                orgs = []
            self._cache["public_orgs"] = orgs
        return self._cache["public_orgs"]

    @property
    def orgs_public_count(self) -> int:
        """
        Return the number of organisations the user is publicly a member of.
        Cached after first access.

        Returns
        =======
        :orgs_public_count: int
            count of public organisation memberships.
        """
        if "orgs_public_count" not in self._cache:
            self._cache["orgs_public_count"] = len(self.public_orgs)
        return self._cache["orgs_public_count"]

    @property
    def is_bot(self) -> bool:
        """
        Return whether the account is a bot, based on its type being ``"bot"`` or
        its login ending in ``[bot]`` or ``-bot``. Cached after first access.

        Returns
        =======
        :is_bot: bool
            True if the account is identified as a bot, otherwise False.
        """
        if "is_bot" not in self._cache:
            # Delegate to the shared helper rather than restating the rule here.
            # This property and utils._is_bot() had drifted apart once already,
            # and the async pipeline calls the helper directly.
            self._cache["is_bot"] = _is_bot(self.login, self.type)
        return self._cache["is_bot"]

    @property
    def last_public_event_at(self) -> str:
        """
        Return the ISO-8601 timestamp of the user's most recent public event.
        Cached after first access.

        Returns
        =======
        :last_public_event_at: str
            the timestamp of the last public event, or an empty string if none.
        """
        if "last_public_event_at" not in self._cache:
            try:
                ev = next(iter(self._user().get_public_events()), None)
                self._cache["last_public_event_at"] = ev.created_at.isoformat() if ev else ""
            except Exception:
                self._cache["last_public_event_at"] = ""
        return self._cache["last_public_event_at"]

    # ---------- heavier (optional) computations ----------
    def top_languages(self, max_repos: int = 50) -> List[Tuple[str, int]]:
        """
        Return the user's top three most-used programming languages, counted
        across their owned repositories. **Expensive**: iterates up to
        *max_repos* repositories. Result is cached per *max_repos* value.

        Parameters
        ==========
        :max_repos: int (default=50)
            maximum number of owned repositories to inspect.

        Returns
        =======
        :top: list
            list of up to three ``(language, count)`` tuples, most-used first.
        """
        key = f"top_languages_{max_repos}"
        if key in self._cache:
            return self._cache[key]
        langs: Dict[str, int] = {}
        try:
            for r in self._user().get_repos(type="owner")[:max_repos]:
                lang = getattr(r, "language", None)
                if lang:
                    langs[lang] = langs.get(lang, 0) + 1
        except Exception:
            pass
        top = sorted(langs.items(), key=lambda x: x[1], reverse=True)[:3]
        self._cache[key] = top
        return top

    def star_fork_sums(self, max_repos: int = 50) -> Tuple[int, int]:
        """
        Return the total stars and forks summed across the user's owned
        repositories. **Expensive**: iterates up to *max_repos* repositories.
        Result is cached per *max_repos* value.

        Parameters
        ==========
        :max_repos: int (default=50)
            maximum number of owned repositories to inspect.

        Returns
        =======
        :(stars, forks): tuple
            tuple of the summed stargazer count and summed fork count.
        """
        key = f"star_fork_sums_{max_repos}"
        if key in self._cache:
            return self._cache[key]
        stars = forks = 0
        try:
            for r in self._user().get_repos(type="owner")[:max_repos]:
                stars += getattr(r, "stargazers_count", 0)
                forks += getattr(r, "forks_count", 0)
        except Exception:
            pass
        self._cache[key] = (stars, forks)
        return stars, forks

    def social_accounts(self) -> Dict[str, str]:
        """
        Fetch the user's linked social accounts via the GitHub REST API and
        return them as a provider -> URL mapping. Uses ``requests.get`` directly
        because PyGithub exposes no accessor for this endpoint. Result is cached
        after first access.

        The token is taken from ``self._token``, which is resolved once at
        construction (see :meth:`__init__`). A non-200 response is reported
        rather than silently swallowed, since an unauthenticated or rate-limited
        call here previously produced an empty result with no explanation.

        Returns
        =======
        :result: dict
            dict mapping each lower-cased provider name to its account URL
            (empty if the user has no linked accounts, or on error).
        """
        if "social_accounts" in self._cache:
            return self._cache["social_accounts"]
        result: Dict[str, str] = {}
        headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"https://api.github.com/users/{self.login}/social_accounts"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                for entry in resp.json() or []:
                    provider = (entry.get("provider") or "").lower()
                    acct_url = entry.get("url") or ""
                    if provider and acct_url:
                        result[provider] = acct_url
            elif resp.status_code == 404:
                # User has no social accounts configured — a normal empty result.
                pass
            else:
                print(
                    f"  [WARNING] social_accounts for {self.login}: "
                    f"HTTP {resp.status_code}"
                    + ("" if self._token else " (no token — 60 requests/hour limit)")
                )
        except Exception as exc:
            print(f"  [WARNING] social_accounts for {self.login} failed: {exc}")
        self._cache["social_accounts"] = result
        return result

    # NEW — optional bounded counts (public data, but can be large: keep capped if you adapt)
    def ssh_keys_count(self, cap: int = 50) -> int:
        """
        Return the number of public SSH keys on the user's account, bounded by
        *cap* to avoid unbounded pagination.

        Parameters
        ==========
        :cap: int (default=50)
            maximum number of keys to count.

        Returns
        =======
        :count: int
            number of public SSH keys (up to *cap*), or 0 on error.
        """
        try:
            # PyGithub returns PaginatedList; slicing is efficient
            return len(self._user().get_keys()[:cap])
        except Exception:
            return 0

    def gpg_keys_count(self, cap: int = 50) -> int:
        """
        Return the number of public GPG keys on the user's account, bounded by
        *cap* to avoid unbounded pagination.

        Parameters
        ==========
        :cap: int (default=50)
            maximum number of keys to count.

        Returns
        =======
        :count: int
            number of public GPG keys (up to *cap*), or 0 on error.
        """
        try:
            return len(self._user().get_gpg_keys()[:cap])
        except Exception:
            return 0

    def starred_repos_sampled(self, cap: int = 200) -> int:
        """
        Return a bounded sample count of the repositories the user has starred,
        limited by *cap* to avoid unbounded pagination.

        Parameters
        ==========
        :cap: int (default=200)
            maximum number of starred repositories to count.

        Returns
        =======
        :count: int
            number of starred repositories sampled (up to *cap*), or 0 on error.
        """
        try:
            return len(self._user().get_starred()[:cap])
        except Exception:
            return 0

    # ---------- repo-specific (requires rights for private/collab info) ----------
    def repo_relationship(self, repo: 'Repository', check_permission: bool = True) -> Dict[str, Union[bool, str, None]]:
        """
        Determine the user's relationship to a specific repository: whether they
        are a collaborator and, optionally, their permission level. Requires
        sufficient rights on the repository; failures are swallowed and leave the
        corresponding value unset.

        Parameters
        ==========
        :repo: Repository
            the PyGithub repository object to check the relationship against.
        :check_permission: bool (default=True)
            when True, also look up the user's permission level on the repo.

        Returns
        =======
        :out: dict
            dict with keys ``"is_collaborator"`` (bool/None) and
            ``"permission_on_repo"`` (str).
        """
        out = {"is_collaborator": None, "permission_on_repo": ""}
        try:
            out["is_collaborator"] = bool(repo.has_in_collaborators(self._user()))
        except Exception:
            pass
        if check_permission:
            try:
                permission = repo.get_collaborator_permission(self._user())
                out["permission_on_repo"] = permission or ""
            except Exception:
                pass
        return out

    # ---------- one-shot snapshot ----------
    def snapshot(
        self,
        *,
        include_langs: bool = False,
        include_star_fork_sums: bool = False,
        langs_max_repos: int = 50,
        sums_max_repos: int = 50,
        include_keys_counts: bool = False,
        include_star_sample: bool = False,
        include_social_accounts: bool = False,
        recent_days: int = 90,
        repo=None
    ) -> UserSnapshot:
        """
        Collect all lightweight fields (plus any requested optional aggregates)
        into a single :class:`UserSnapshot` dataclass. Optional aggregates are
        off by default because they can be expensive (one or more API calls per
        repository).

        Parameters
        ==========
        :include_langs: bool (default=False)
            collect the top-3 languages from the user's repositories.
            **Expensive** — one API call per repository up to *langs_max_repos*.
        :include_star_fork_sums: bool (default=False)
            sum stars and forks across the user's repositories.
            **Expensive** — same cost profile as *include_langs*.
        :langs_max_repos: int (default=50)
            maximum repositories to inspect when *include_langs* is set.
        :sums_max_repos: int (default=50)
            maximum repositories to inspect when *include_star_fork_sums* is set.
        :include_keys_counts: bool (default=False)
            include bounded SSH and GPG public-key counts.
        :include_star_sample: bool (default=False)
            include a bounded sample count of starred repositories.
        :include_social_accounts: bool (default=False)
            include linked social accounts (one extra REST call per user).
        :recent_days: int (default=90)
            window, in days, used to compute the ``recently_active`` flag.
        :repo: Repository/None (default=None)
            if provided, also populate the repo-specific relationship fields.

        Returns
        =======
        :snap: UserSnapshot
            the fully populated snapshot dataclass for the user.
        """
        # Lightweight fields
        snap = UserSnapshot(
            login=self.login,
            id=self.id,
            node_id=self.node_id,
            type=self.type,
            name=self.name,
            company=self.company,
            location=self.location,
            email_public=self.email_public,
            email_domain=self.email_domain,
            blog=self.blog,
            blog_host=self.blog_host,
            twitter=self.twitter,
            bio=self.bio,
            avatar_url=self.avatar_url,
            html_url=self.html_url,
            hireable=self.hireable,
            site_admin=self.site_admin,
            created_at=self.created_at,
            updated_at=self.updated_at,
            followers=self.followers,
            following=self.following,
            public_repos=self.public_repos,
            public_gists=self.public_gists,
            public_orgs=self.public_orgs,
            orgs_public_count=self.orgs_public_count,
            is_bot=self.is_bot,
            last_public_event_at=self.last_public_event_at,
        )

        # NEW — cheap flags / normalized
        snap.has_public_email = bool(snap.email_public)
        snap.has_blog = bool(snap.blog)
        snap.has_twitter = bool(snap.twitter)
        snap.company_normalized = self._normalized_company()
        snap.location_normalized = self._normalized_location()
        snap.location_country = self._location_country()

        # NEW — small computed metrics
        snap.account_age_days = self._days_since(snap.created_at)
        snap.followers_following_ratio = self._followers_following_ratio()
        snap.repos_per_year = self._repos_per_year()
        snap.recently_active = self._recently_active(days=recent_days)

        # Optional aggregates
        if include_langs:
            snap.top_languages = self.top_languages(max_repos=langs_max_repos)
        if include_star_fork_sums:
            s, f = self.star_fork_sums(max_repos=sums_max_repos)
            snap.total_public_stars_sampled = s
            snap.total_public_forks_sampled = f

        # Optional bounded counts
        if include_keys_counts:
            snap.ssh_keys_count = self.ssh_keys_count(cap=50)
            snap.gpg_keys_count = self.gpg_keys_count(cap=50)
        if include_star_sample:
            snap.starred_repos_sampled = self.starred_repos_sampled(cap=200)

        # Social accounts (one extra REST call per user)
        if include_social_accounts:
            snap.social_accounts = self.social_accounts()

        # Repo-specific (if provided)
        if repo is not None:
            rel = self.repo_relationship(repo)
            snap.is_collaborator = rel.get("is_collaborator")
            snap.permission_on_repo = rel.get("permission_on_repo")

        return snap

    # Convenience: dict output
    def to_dict(self, **snapshot_kwargs) -> Dict[str, Any]:
        """
        Return the user's snapshot as a plain dict with keys sorted
        alphabetically.

        Parameters
        ==========
        :snapshot_kwargs: dict
            keyword arguments forwarded to :meth:`snapshot` (e.g.
            ``include_social_accounts=True``).

        Returns
        =======
        :data: dict
            the snapshot fields as a key-sorted dict.
        """
        return dict(sorted(asdict(self.snapshot(**snapshot_kwargs)).items()))

    # New export methods
    def to_csv_row(self, **snapshot_kwargs) -> List[str]:
        """
        Return a selected subset of the user's snapshot fields as a list of
        strings, suitable for writing as a single CSV row (see
        :meth:`csv_headers` for the matching column order).

        Parameters
        ==========
        :snapshot_kwargs: dict
            keyword arguments forwarded to :meth:`snapshot`.

        Returns
        =======
        :row: list
            list of stringified field values in the CSV column order.
        """
        snapshot = self.snapshot(**snapshot_kwargs)
        return [
            snapshot.login,
            snapshot.name or "",
            snapshot.company_normalized or "",
            snapshot.location_normalized or "",
            str(snapshot.followers or 0),
            str(snapshot.following or 0),
            str(snapshot.public_repos or 0),
            str(snapshot.public_gists or 0),
            snapshot.created_at or "",
            snapshot.email_public or "",
            snapshot.blog or "",
            snapshot.bio or "",
            str(snapshot.account_age_days or 0),
            str(snapshot.followers_following_ratio or 0.0),
            str(snapshot.repos_per_year or 0.0),
            str(snapshot.recently_active or False)
        ]

    def to_json(self, **snapshot_kwargs) -> str:
        """
        Return the user's snapshot as an indented JSON string.

        Parameters
        ==========
        :snapshot_kwargs: dict
            keyword arguments forwarded to :meth:`snapshot`.

        Returns
        =======
        :json: str
            the snapshot serialised as a pretty-printed JSON string.
        """
        return json.dumps(self.to_dict(**snapshot_kwargs), indent=2, default=str)

    @classmethod
    def csv_headers(cls) -> List[str]:
        """
        Return the CSV column headers that correspond, in order, to the values
        produced by :meth:`to_csv_row`.

        Returns
        =======
        :headers: list
            list of CSV column-name strings.
        """
        return [
            "login", "name", "company", "location", "followers", "following",
            "public_repos", "public_gists", "created_at", "email", "blog", "bio",
            "account_age_days", "followers_following_ratio", "repos_per_year", "recently_active"
        ]

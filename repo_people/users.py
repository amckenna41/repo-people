from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any, Tuple, Union, TYPE_CHECKING
from urllib.parse import urlparse
from datetime import datetime, timezone

from github import Github, Auth
from github.NamedUser import NamedUser
from github.GithubObject import IncompletableObject
from github.GithubException import RateLimitExceededException
import json

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
        
        self._user_obj: Optional[NamedUser] = user_obj
        self._username = username or (user_obj.login if user_obj else None)
        self._cache: Dict[str, Any] = {}

    # ---------- internal helpers ----------
    def _user(self) -> NamedUser:
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
        c = (self.company or "").strip()
        return c[1:] if c.startswith("@") else c

    def _normalized_location(self) -> str:
        return (self.location or "").strip().lower()

    def _days_since(self, iso: str) -> int:
        if not iso:
            return 0
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return max(0, int((datetime.now(timezone.utc) - dt).days))
        except Exception:
            return 0

    def _followers_following_ratio(self) -> float:
        f, g = self.followers, self.following
        return float(f) if g == 0 else round(f / g, 2)

    def _repos_per_year(self) -> float:
        days = max(1, self._days_since(self.created_at))
        years = days / 365.25
        return round(self.public_repos / years, 2) if years else float(self.public_repos)

    def _recently_active(self, days: int = 90) -> bool:
        return self._days_since(self.last_public_event_at) <= days if self.last_public_event_at else False

    # ---------- public lightweight properties (cheap) ----------
    @property
    def login(self) -> str:
        if "login" not in self._cache:
            # Try to get login from API, but fall back to the username we were given
            api_login = self._get_basic("login", "")
            self._cache["login"] = api_login or self._username or ""
        return self._cache["login"]

    @property
    def id(self) -> Optional[int]:
        if "id" not in self._cache:
            self._cache["id"] = self._get_basic("id", None)
        return self._cache["id"]

    @property
    def node_id(self) -> str:
        if "node_id" not in self._cache:
            self._cache["node_id"] = self._get_basic("node_id", "") or ""
        return self._cache["node_id"]

    @property
    def type(self) -> str:
        if "type" not in self._cache:
            self._cache["type"] = self._get_basic("type", "") or ""
        return self._cache["type"]

    @property
    def name(self) -> str:
        if "name" not in self._cache:
            self._cache["name"] = self._get_basic("name", "") or ""
        return self._cache["name"]

    @property
    def company(self) -> str:
        if "company" not in self._cache:
            self._cache["company"] = self._get_basic("company", "") or ""
        return self._cache["company"]

    @property
    def location(self) -> str:
        if "location" not in self._cache:
            self._cache["location"] = self._get_basic("location", "") or ""
        return self._cache["location"]

    @property
    def email_public(self) -> str:
        if "email_public" not in self._cache:
            self._cache["email_public"] = self._get_basic("email", "") or ""
        return self._cache["email_public"]

    @property
    def email_domain(self) -> str:
        if "email_domain" not in self._cache:
            try:
                self._cache["email_domain"] = (self.email_public or "").split("@", 1)[1].lower()
            except Exception:
                self._cache["email_domain"] = ""
        return self._cache["email_domain"]

    @property
    def blog(self) -> str:
        if "blog" not in self._cache:
            self._cache["blog"] = self._get_basic("blog", "") or ""
        return self._cache["blog"]

    @property
    def blog_host(self) -> str:
        if "blog_host" not in self._cache:
            self._cache["blog_host"] = (urlparse(self.blog).hostname or "").lower() if self.blog else ""
        return self._cache["blog_host"]

    @property
    def twitter(self) -> str:
        if "twitter" not in self._cache:
            self._cache["twitter"] = self._get_basic("twitter_username", "") or ""
        return self._cache["twitter"]

    @property
    def bio(self) -> str:
        if "bio" not in self._cache:
            self._cache["bio"] = self._get_basic("bio", "") or ""
        return self._cache["bio"]

    @property
    def avatar_url(self) -> str:
        if "avatar_url" not in self._cache:
            self._cache["avatar_url"] = self._get_basic("avatar_url", "") or ""
        return self._cache["avatar_url"]

    @property
    def html_url(self) -> str:
        if "html_url" not in self._cache:
            self._cache["html_url"] = self._get_basic("html_url", "") or ""
        return self._cache["html_url"]

    @property
    def hireable(self) -> bool:
        if "hireable" not in self._cache:
            self._cache["hireable"] = bool(self._get_basic("hireable", False))
        return self._cache["hireable"]

    @property
    def site_admin(self) -> bool:
        if "site_admin" not in self._cache:
            self._cache["site_admin"] = bool(self._get_basic("site_admin", False))
        return self._cache["site_admin"]

    @property
    def created_at(self) -> str:
        if "created_at" not in self._cache:
            dt = self._get_basic("created_at", None)
            self._cache["created_at"] = dt.isoformat() if dt else ""
        return self._cache["created_at"]

    @property
    def updated_at(self) -> str:
        if "updated_at" not in self._cache:
            dt = self._get_basic("updated_at", None)
            self._cache["updated_at"] = dt.isoformat() if dt else ""
        return self._cache["updated_at"]

    @property
    def followers(self) -> int:
        if "followers" not in self._cache:
            self._cache["followers"] = int(self._get_basic("followers", 0) or 0)
        return self._cache["followers"]

    @property
    def following(self) -> int:
        if "following" not in self._cache:
            self._cache["following"] = int(self._get_basic("following", 0) or 0)
        return self._cache["following"]

    @property
    def public_repos(self) -> int:
        if "public_repos" not in self._cache:
            self._cache["public_repos"] = int(self._get_basic("public_repos", 0) or 0)
        return self._cache["public_repos"]

    @property
    def public_gists(self) -> int:
        if "public_gists" not in self._cache:
            self._cache["public_gists"] = int(self._get_basic("public_gists", 0) or 0)
        return self._cache["public_gists"]

    @property
    def public_orgs(self) -> List[str]:
        if "public_orgs" not in self._cache:
            try:
                orgs = [o.login for o in self._user().get_orgs()]
            except Exception:
                orgs = []
            self._cache["public_orgs"] = orgs
        return self._cache["public_orgs"]

    @property
    def orgs_public_count(self) -> int:
        if "orgs_public_count" not in self._cache:
            self._cache["orgs_public_count"] = len(self.public_orgs)
        return self._cache["orgs_public_count"]

    @property
    def is_bot(self) -> bool:
        if "is_bot" not in self._cache:
            t = self.type.lower()
            self._cache["is_bot"] = (t == "bot") or self.login.endswith("[bot]") or self.login.endswith("-bot")
        return self._cache["is_bot"]

    @property
    def last_public_event_at(self) -> str:
        if "last_public_event_at" not in self._cache:
            try:
                ev = next(iter(self._user().get_public_events()), None)
                self._cache["last_public_event_at"] = ev.created_at.isoformat() if ev else ""
            except Exception:
                self._cache["last_public_event_at"] = ""
        return self._cache["last_public_event_at"]

    # ---------- heavier (optional) computations ----------
    def top_languages(self, max_repos: int = 50) -> List[Tuple[str, int]]:
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
        """Fetch social accounts via the GitHub REST API; returns provider -> url dict."""
        if "social_accounts" in self._cache:
            return self._cache["social_accounts"]
        result: Dict[str, str] = {}
        try:
            # Use PyGithub's built-in requester so auth + rate-limiting are handled
            _, data = self._gh._Github__requester.requestJsonAndCheck(
                "GET", f"/users/{self.login}/social_accounts"
            )
            for entry in data or []:
                provider = (entry.get("provider") or "").lower()
                url = entry.get("url") or ""
                if provider and url:
                    result[provider] = url
        except Exception:
            pass
        self._cache["social_accounts"] = result
        return result

    # NEW — optional bounded counts (public data, but can be large: keep capped if you adapt)
    def ssh_keys_count(self, cap: int = 50) -> int:
        try:
            # PyGithub returns PaginatedList; slicing is efficient
            return len(self._user().get_keys()[:cap])
        except Exception:
            return 0

    def gpg_keys_count(self, cap: int = 50) -> int:
        try:
            return len(self._user().get_gpg_keys()[:cap])
        except Exception:
            return 0

    def starred_repos_sampled(self, cap: int = 200) -> int:
        try:
            return len(self._user().get_starred()[:cap])
        except Exception:
            return 0

    # ---------- repo-specific (requires rights for private/collab info) ----------
    def repo_relationship(self, repo: 'Repository', check_permission: bool = True) -> Dict[str, Union[bool, str, None]]:
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
        include_langs: bool = True,
        include_star_fork_sums: bool = True,
        langs_max_repos: int = 50,
        sums_max_repos: int = 50,
        include_keys_counts: bool = False,
        include_star_sample: bool = False,
        include_social_accounts: bool = False,
        recent_days: int = 90,
        repo=None
    ) -> UserSnapshot:
        """Collects all lightweight fields + optional aggregates into a dataclass."""
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
        return asdict(self.snapshot(**snapshot_kwargs))
    
    # New export methods
    def to_csv_row(self, **snapshot_kwargs) -> List[str]:
        """Export user data as CSV row."""
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
        """Export user data as JSON string."""
        return json.dumps(self.to_dict(**snapshot_kwargs), indent=2, default=str)
    
    @classmethod
    def csv_headers(cls) -> List[str]:
        """Return CSV headers for to_csv_row method."""
        return [
            "login", "name", "company", "location", "followers", "following",
            "public_repos", "public_gists", "created_at", "email", "blog", "bio",
            "account_age_days", "followers_following_ratio", "repos_per_year", "recently_active"
        ]
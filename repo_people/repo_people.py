import os
import json
import csv
import logging
import re
import sqlite3
import warnings
import dataclasses
import threading
import concurrent.futures
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

from github import Github, Auth
from typing import Optional, List, Dict, Set, Union
from .users import GitHubUserInfo
from .utils import validate_owner_repo, _is_bot, normalize_country
from . import export

__all__ = ["RepoPeople", "UserDataView"]

# ponytail: PyGithub logs a warning line per rate-limit backoff, which turns a
# long collection into a wall of noise. Silence that one logger at module scope —
# but never touch warnings.filterwarnings() here: this is a library, and globally
# suppressing ResourceWarning (as an earlier version did) silently disabled it
# for the whole importing application, hiding real unclosed-socket bugs.
logging.getLogger("github.Requester").setLevel(logging.ERROR)


def _iso_utc(timestamp: str) -> str:
    """
    Normalise a GitHub ISO-8601 timestamp to the ``+00:00`` offset spelling.

    The REST API renders UTC as a trailing ``Z``, whereas the sync path formats a
    ``datetime`` via ``.isoformat()`` and gets ``+00:00``. Both denote the same
    instant, but the raw strings end up in JSON/CSV/SQLite output, so the two
    pipelines have to agree on one form.

    Parameters
    ==========
    :timestamp: str
        an ISO-8601 timestamp, possibly ending in ``Z``.

    Returns
    =======
    :normalised: str
        the timestamp with ``Z`` replaced by ``+00:00``, or the input unchanged
        when it is empty or unparseable.
    """
    if not timestamp:
        return ""
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return timestamp


def _check_identifier(name: str, kind: str) -> None:
    """
    Validate that *name* is safe to splice into a SQL statement as an identifier.

    SQL identifiers (table and column names) cannot be bound as parameters, so
    they have to be interpolated — which makes validating them the only thing
    standing between a caller-supplied dict key and SQL injection. Accepts only
    ASCII letters, digits and underscores, and rejects a leading digit.

    Parameters
    ==========
    :name: str
        the candidate identifier.
    :kind: str
        what the identifier is, used in the error message (e.g. "table name").

    Returns
    =======
    None

    Raises
    ======
    ValueError:
        When *name* is empty, starts with a digit, or contains any character
        outside ``[A-Za-z0-9_]``.
    """
    if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(
            f"Invalid {kind} {name!r}: must contain only letters, digits and "
            "underscores, and must not start with a digit."
        )


def _progress_iter(iterable, total: int, desc: str, enabled: bool):
    """
    Wrap an iterable in a ``tqdm`` progress bar when tqdm is installed and
    *enabled* is set, otherwise return the iterable untouched.

    tqdm is an optional dependency (the ``progress`` extra), so this degrades to
    a plain iterator rather than requiring it.

    Parameters
    ==========
    :iterable: iterable
        the iterable to wrap.
    :total: int
        the expected number of items, used to size the bar.
    :desc: str
        short label displayed alongside the bar.
    :enabled: bool
        when False, skip the bar entirely and return *iterable*.

    Returns
    =======
    :iterable: iterable
        a tqdm-wrapped iterable, or the original iterable.
    """
    if not enabled:
        return iterable
    try:
        from tqdm import tqdm
    except ImportError:
        return iterable
    return tqdm(iterable, total=total, desc=desc, unit="user")


class UserDataView(dict):
    """
    A ``dict`` subclass returned by :meth:`RepoPeople.get_users` and
    :meth:`RepoPeople.get_users_async`.

    Supports all standard ``dict`` operations. Additionally, any valid
    user-profile field name can be accessed via dot notation to retrieve
    that field across every collected user::

        user_data = rp.get_users()
        user_data.email_public
        # {"alice": {"email_public": "alice@example.com"}, "bob": {"email_public": ""}, ...}

    Raises :exc:`AttributeError` for names that are not valid profile fields.
    """

    _valid_fields: Optional[frozenset] = None

    @classmethod
    def _get_valid_fields(cls) -> frozenset:
        """
        Return the set of profile field names that may be accessed via dot
        notation on a :class:`UserDataView`, derived once (and then cached) from
        the fields of :class:`~repo_people.users.UserSnapshot` plus ``"roles"``.

        Returns
        =======
        :_valid_fields: frozenset
            frozenset of valid user-profile field names.
        """
        if cls._valid_fields is None:
            from .users import UserSnapshot
            cls._valid_fields = frozenset(
                f.name for f in dataclasses.fields(UserSnapshot)
            ) | frozenset(["roles"])
        return cls._valid_fields

    @classmethod
    def _clear_valid_fields_cache(cls) -> None:
        """
        Reset the cached set of valid profile fields so it is recomputed on next
        access. Useful in tests that patch :class:`UserSnapshot`.

        Returns
        =======
        None
        """
        cls._valid_fields = None

    def __getattr__(self, name: str):
        """
        Support dot-notation access to a single profile field across every
        collected user. For a valid field name, returns a dict mapping each
        username to a one-key record containing that field's value.

        Parameters
        ==========
        :name: str
            the profile field name being accessed as an attribute.

        Returns
        =======
        :field_view: dict
            dict of ``{username: {name: value}}`` for the requested field.

        Raises
        ======
        AttributeError:
            When *name* is a private/dunder name or not a valid profile field.
        """
        # Avoid intercepting dunder/private names (prevents pickle/copy issues)
        if name.startswith("_"):
            raise AttributeError(name)
        valid = self._get_valid_fields()
        if name in valid:
            return {
                username: {name: record.get(name)}
                for username, record in self.items()
            }
        raise AttributeError(
            f"'UserDataView' object has no attribute {name!r}. "
            f"Valid fields: {sorted(valid)}"
        )


class RepoPeople:
    """
    Collects and exports all user data for a given GitHub repository.

    Gathers users across every repo role (contributors, maintainers, stargazers,
    watchers, issue/PR authors, fork owners, commit authors, dependents), then
    fetches full GitHub profile details for each unique user via the GitHub API.

    Basic usage::

        rp = RepoPeople("owner", "repo", token="ghp_...")
        user_data = rp.get_users(export_json=True)
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        token: Optional[str] = None,
        outdir: Optional[str] = None,
        skip_codeowners: bool = False,
        skip_collaborators: bool = False,
        use_cache: bool = True,
        use_graphql: bool = True,
    ):
        """
        Initialise a :class:`RepoPeople` instance for a single GitHub repository,
        validating the owner/repo names, resolving the access token, warning when
        none is available (unauthenticated requests are capped at 60/hour),
        creating the PyGithub client and verifying the connection before any
        collection begins.

        When *token* is None the ``GITHUB_TOKEN`` environment variable is used if
        set, so library callers get the same fallback the CLI has always had.

        Parameters
        ==========
        :owner: str
            GitHub repository owner (user or organisation).
        :repo: str
            GitHub repository name.
        :token: str/None (default=None)
            GitHub personal access token. When None, ``GITHUB_TOKEN`` is read from
            the environment; if that is also unset, runs unauthenticated with a
            warning.
        :outdir: str/None (default=None)
            output directory for exported files; defaults to ``"outputs"``.
        :skip_codeowners: bool (default=False)
            when True, skip the CODEOWNERS file when collecting maintainers.
        :skip_collaborators: bool (default=False)
            when True, skip the collaborators API when collecting maintainers.
        :use_cache: bool (default=True)
            when True, use the on-disk ETag cache so unchanged pages are served
            by conditional requests that do not count against the rate limit.
        :use_graphql: bool (default=True)
            when True (and a token is available), use the GraphQL fast paths for
            role collection, falling back to REST automatically.

        Raises
        ======
        ValueError:
            When the owner or repo name is invalid.
        ConnectionError:
            When the GitHub connection/token check fails.
        """
        validate_owner_repo(owner, repo)
        self.owner = owner
        self.repo = repo
        # Resolve the token: explicit argument wins, then the environment. Doing
        # this here (not only in the CLI) is what the documentation has always
        # promised for library callers.
        resolved_token = token or os.environ.get("GITHUB_TOKEN") or None
        # Store token as a private attribute to reduce accidental exposure
        # (e.g. in repr(), vars(), or debug logs).
        self._token = resolved_token
        # Warn early: unauthenticated runs are capped at 60 requests/hour and will
        # crawl to a halt on any non-trivial repo. Surface it before the slow part.
        if resolved_token is None:
            warnings.warn(
                "No GitHub token provided — unauthenticated requests are limited to "
                "60/hour and will likely hit rate limits. Pass token=... or set GITHUB_TOKEN.",
                UserWarning,
                stacklevel=2,
            )
        # All files are stored flat in outputs/ with an owner_repo_ filename prefix
        self.outdir = outdir or "outputs"
        self.file_prefix = f"{owner}_{repo}_"
        self.skip_codeowners = skip_codeowners
        self.skip_collaborators = skip_collaborators
        self.use_cache = use_cache
        self.use_graphql = use_graphql
        # Initialise GitHub client (authenticated when token is provided)
        self.gh = Github(auth=Auth.Token(resolved_token)) if resolved_token else Github()
        # Fail fast if the token/connection is invalid
        try:
            self.gh.get_rate_limit()
        except Exception as e:
            raise ConnectionError(f"GitHub connection failed — verify your token. ({e})") from e
        self._repo_obj = None
        # Logins that the most recent fetch could not retrieve. Callers (and the
        # CLI's exit code) use this to tell a clean run from a partial one.
        self.last_failed: List[str] = []

    @staticmethod
    def _atomic_write_json(path: str, payload) -> None:
        """
        Write *payload* to *path* as JSON via a temporary file and an atomic
        rename, so an interrupted write cannot leave a truncated file behind.

        This matters for ``save_each_iteration``, whose whole purpose is surviving
        interruption — a plain in-place rewrite could be killed mid-flush and
        destroy the progress it was meant to protect.

        Parameters
        ==========
        :path: str
            destination file path.
        :payload: any
            JSON-serialisable object to write.

        Returns
        =======
        None
        """
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # The pid keeps two concurrent runs against the same repo and outdir from
        # writing the same temp file and corrupting each other — which is exactly
        # the interruption scenario save_each_iteration exists to survive.
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp, path)

    @property
    def repo_obj(self):
        """
        Return the PyGithub ``Repository`` object for this repo, fetched lazily on
        first access and cached thereafter.

        This used to be fetched eagerly in ``__init__``, which spent an API call
        on every instantiation even though nothing in the pipeline reads it, and
        raised PyGithub's ``UnknownObjectException`` — rather than the documented
        ``ValueError``/``ConnectionError`` — for a repo the token cannot see.

        Returns
        =======
        :repo_obj: Repository
            the PyGithub repository object.

        Raises
        ======
        ConnectionError:
            When the repository cannot be fetched (missing, private, or no access).
        """
        if self._repo_obj is None:
            try:
                self._repo_obj = self.gh.get_repo(f"{self.owner}/{self.repo}")
            except Exception as e:
                raise ConnectionError(
                    f"Could not fetch repository {self.owner}/{self.repo} — "
                    f"check it exists and your token has access. ({e})"
                ) from e
        return self._repo_obj

    @property
    def token(self) -> Optional[str]:
        """
        Return the GitHub personal access token for this instance. Stored
        privately and settable only via the constructor to reduce accidental
        exposure in ``repr()`` or logs.

        Returns
        =======
        :_token: str/None
            the personal access token, or None if running unauthenticated.
        """
        return self._token

    def __repr__(self) -> str:
        """
        Return an unambiguous string representation of the instance for
        debugging. The token is deliberately omitted.

        Returns
        =======
        :repr: str
            representation showing owner, repo, output directory and role count.
        """
        return (
            f"RepoPeople(owner={self.owner!r}, repo={self.repo!r}, "
            f"outdir={self.outdir!r}, valid_roles={len(self.VALID_ROLES)})"
        )

    def _print_rate_limit_status(self, context: str = "") -> None:
        """
        Print the current GitHub rate-limit window (remaining/total requests and
        minutes until reset) when the information is available, prefixed with an
        optional context label. Silently does nothing if the readout is
        unavailable.

        Parameters
        ==========
        :context: str (default="")
            optional label printed before the rate-limit line (e.g. "Preflight").

        Returns
        =======
        None
        """
        try:
            remaining, total_limit = self.gh.rate_limiting
            reset_epoch = self.gh.rate_limiting_resettime
            reset_in = max(0, int((reset_epoch - time.time()) / 60))
            auth_state = "authenticated" if self._token else "unauthenticated"
            prefix = f"{context} " if context else ""
            print(
                f"{prefix}Rate limit: {remaining}/{total_limit} remaining, "
                f"resets in {reset_in}m ({auth_state})"
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Step 1 - collect usernames from every repo role
    # ------------------------------------------------------------------

    # All valid role keys that can be passed to the roles parameter
    VALID_ROLES: Set[str] = {
        "contributors", "maintainers", "stargazers", "watchers",
        "issue_authors", "pr_authors", "pr_reviewers", "fork_owners",
        "commit_authors", "dependents",
    }

    @staticmethod
    def valid_fields() -> Set[str]:
        """
        Return every field name accepted by the ``fields`` parameter of
        :meth:`get_users` and :meth:`get_users_async`.

        This is the union of :class:`~repo_people.users.UserSnapshot`'s fields and
        the pipeline-added ``"roles"`` key. Deriving it in one place keeps the
        ``fields`` allow-list in step with :meth:`UserDataView.__getattr__` — when
        the two disagreed, ``fields=["roles"]`` was rejected as invalid even
        though every record carries a ``roles`` key.

        Returns
        =======
        :fields: set
            set of valid field-name strings.
        """
        from .users import UserSnapshot
        return {f.name for f in dataclasses.fields(UserSnapshot)} | {"roles"}

    def collect_all_usernames(
        self,
        roles: Optional[List[str]] = None,
    ) -> Dict[str, List[str]]:
        """
        Fetch usernames from each repository role and return them grouped by
        role. Roles are fetched concurrently, and if a subset of roles is
        requested only those are collected (avoiding unnecessary API calls).

        Role failures are isolated: if one role cannot be collected (a 5xx on
        ``/stargazers``, a scraping failure in ``dependents``) a warning is
        printed and that role comes back as an empty list, leaving every other
        role's result intact. Previously the exception propagated and discarded
        the whole collection — including an already completed commit walk.

        Parameters
        ==========
        :roles: list/None (default=None)
            list of role names to collect (e.g. ``["contributors", "stargazers"]``).
            If None, all valid roles are collected.

        Returns
        =======
        :results: dict
            dict mapping each requested role name to a list of GitHub login
            strings, with an empty list for any role that failed. Possible keys:
            contributors, maintainers, stargazers, watchers, issue_authors,
            pr_authors, pr_reviewers, fork_owners, commit_authors, dependents.

        Raises
        ======
        ValueError:
            When any requested role is not one of the valid role names.
        """
        # Validate any explicitly requested roles
        if roles is not None:
            invalid = set(roles) - self.VALID_ROLES
            if invalid:
                raise ValueError(f"Invalid role(s): {invalid}. Valid roles: {self.VALID_ROLES}")

        _cache = self.use_cache
        # Map each role name to a callable that fetches it
        role_fetchers = {
            "contributors": lambda: export.export_contributors(
                self.owner, self.repo, self.token, self.outdir, return_data=True, use_cache=_cache
            ),
            "maintainers": lambda: export.export_maintainers(
                self.owner, self.repo, self.token, self.outdir,
                self.skip_codeowners, self.skip_collaborators, return_data=True, use_cache=_cache
            ),
            "stargazers": lambda: export.export_stargazers(
                self.owner, self.repo, self.token, self.outdir, return_data=True, use_cache=_cache
            ),
            "watchers": lambda: export.export_watchers(
                self.owner, self.repo, self.token, self.outdir, return_data=True, use_cache=_cache
            ),
            "issue_authors": lambda: export.export_issue_authors(
                self.owner, self.repo, self.token, self.outdir, return_data=True, use_cache=_cache
            ),
            "pr_authors": lambda: export.export_pr_authors(
                self.owner, self.repo, self.token, self.outdir, return_data=True, use_cache=_cache
            ),
            "fork_owners": lambda: export.export_fork_owners(
                self.owner, self.repo, self.token, self.outdir, return_data=True, use_cache=_cache
            ),
            "commit_authors": lambda: export.export_commit_authors(
                self.owner, self.repo, self.token, self.outdir, return_data=True, use_cache=_cache
            ),
            "dependents": lambda: export.export_dependents(
                self.owner, self.repo, self.outdir, return_data=True
            ),
            "pr_reviewers": lambda: export.export_pr_reviewers(
                self.owner, self.repo, self.token, self.outdir, return_data=True,
                use_cache=_cache, use_graphql=self.use_graphql
            ),
        }
        # Only fetch the requested roles (lazy — avoids unnecessary API calls)
        active_roles = roles if roles is not None else list(role_fetchers)

        results: Dict[str, List[str]] = {}
        remaining = list(active_roles)

        # GraphQL fast path: fetch every flat-list role in one query (or a handful
        # of cursor pages) instead of a separate paginated REST walk per role.
        if self.use_graphql and self.token:
            graphql_roles = [r for r in remaining if r in export._SIMPLE_ROLE_CONNECTIONS]
            if graphql_roles:
                collected = export.collect_simple_roles_graphql(
                    self.owner, self.repo, self.token, graphql_roles
                )
                if collected is not None:
                    results.update(collected)
                    remaining = [r for r in remaining if r not in collected]

        def _fetch_role(role: str) -> tuple:
            """Fetch a single role's usernames, returning ``(role, usernames)``."""
            return role, role_fetchers[role]()

        if remaining:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(remaining), len(self.VALID_ROLES))
            ) as executor:
                futures = {executor.submit(_fetch_role, role): role for role in remaining}
                for future in concurrent.futures.as_completed(futures):
                    role = futures[future]
                    try:
                        role, data = future.result()
                    except Exception as e:
                        # One role failing must not discard the other nine. A 500
                        # on /stargazers or a scraping failure in dependents used
                        # to propagate out of here and throw away an already
                        # completed commit-history walk.
                        print(f"  [WARNING] Could not collect {role}: {e}")
                        data = []
                    results[role] = data

        # Return in the same order as active_roles for deterministic output
        return {role: results[role] for role in active_roles}

    # ------------------------------------------------------------------
    # Step 2 - fetch full GitHub profile for each unique user
    # ------------------------------------------------------------------

    def get_user_details(
        self,
        usernames: List[str],
        save_each_iteration: bool = False,
        limit: Optional[int] = None,
        exclude: Optional[List[str]] = None,
        exclude_bots: bool = False,
        resume: bool = False,
        verbose: bool = True,
        include_social_accounts: bool = False,
        workers: int = 1,
        progress: bool = False,
    ) -> Dict[str, dict]:
        """
        Fetch full GitHub profile details for each username via the GitHub API,
        returning a dict keyed by login containing all available user fields
        (profile info, counters, orgs, computed metrics, etc.). Usernames that
        cannot be fetched are skipped with a warning.

        Parameters
        ==========
        :usernames: list
            list of GitHub logins to fetch.
        :save_each_iteration: bool (default=False)
            when True, write ``user_details.json`` after every 10 successful
            fetches so progress survives interruption (batched to reduce I/O).
        :limit: int/None (default=None)
            fetch only the first N usernames. Usernames are sorted alphabetically
            before the limit is applied, so results are deterministic.
        :exclude: list/None (default=None)
            list of logins to skip entirely.
        :exclude_bots: bool (default=False)
            when True, skip logins ending in ``[bot]`` or ``-bot`` and profiles
            flagged as bots.
        :resume: bool (default=False)
            when True, load any existing ``user_details.json`` and skip logins
            already present in it.
        :verbose: bool (default=True)
            when False, suppress per-user fetch messages.
        :include_social_accounts: bool (default=False)
            when True, make an extra REST call per user to fetch linked social
            accounts (LinkedIn, Mastodon, YouTube, npm, etc.).
        :workers: int (default=1)
            number of concurrent fetch threads (1 = sequential). Capped at 32,
            with a warning, if a higher value is passed.
        :progress: bool (default=False)
            when True and *verbose* is False, show a ``tqdm`` progress bar if
            tqdm is installed (the ``progress`` extra); ignored otherwise.

        Returns
        =======
        :user_data: dict
            dict keyed by GitHub login containing each user's full profile data.
            Logins that could not be fetched are recorded in ``self.last_failed``.
        """
        save_path = os.path.join(self.outdir, f"{self.file_prefix}user_details.json")

        # Load existing data from disk when resuming
        if resume and os.path.isfile(save_path):
            with open(save_path, "r", encoding="utf-8") as f:
                user_data = json.load(f)
            print(f"  Resuming — {len(user_data)} users already fetched, skipping them.")
        else:
            user_data = {}

        # Build the exclusion set (already-fetched logins + explicit excludes)
        exclude_set: Set[str] = set(user_data.keys())
        if exclude:
            exclude_set.update(exclude)

        # Filter, apply bot exclusion, then apply limit. Use the shared _is_bot()
        # helper so the pre-fetch screen catches "-bot" suffixes too — checking
        # only "[bot]" here meant every foo-bot account cost a wasted API call
        # before being discarded by the post-fetch is_bot check.
        filtered = [
            login for login in usernames
            if login not in exclude_set
            and not (exclude_bots and _is_bot(login))
        ]
        filtered = filtered[:limit] if limit is not None else filtered

        if save_each_iteration or resume:
            os.makedirs(self.outdir, exist_ok=True)

        # Cap workers to a safe upper bound to prevent connection pool exhaustion
        _MAX_WORKERS = 32
        if workers > _MAX_WORKERS:
            warnings.warn(
                f"workers={workers} exceeds the maximum of {_MAX_WORKERS}; capping at {_MAX_WORKERS}.",
                UserWarning,
                stacklevel=2,
            )
            workers = _MAX_WORKERS

        total = len(filtered)
        completed = 0
        failed: List[str] = []
        # ponytail: no lock. Every mutation of user_data/failed below happens in
        # this thread inside the as_completed() loop — the workers only fetch and
        # return. The lock that used to guard these was protecting nothing.

        # PyGithub wraps a single non-thread-safe requests.Session, so sharing one
        # client across worker threads can corrupt responses/rate-limit state. When
        # running concurrently, give each thread its own client. The single-worker
        # path keeps using self.gh (no races) so the rate-limit readout stays live.
        _use_shared = workers <= 1
        _tl = threading.local()
        rate_client = {"gh": self.gh}  # most-recently-created client, for the rate readout

        def _client() -> Github:
            """Return the GitHub client for the current thread (shared when single-worker)."""
            if _use_shared:
                return self.gh
            gh = getattr(_tl, "gh", None)
            if gh is None:
                gh = Github(auth=Auth.Token(self._token)) if self._token else Github()
                _tl.gh = gh
                rate_client["gh"] = gh
            return gh

        def _fetch_one(login: str) -> dict:
            """Fetch and return one user's profile dict for the given login."""
            if verbose:
                print(f"  Fetching: {login}")
            info = GitHubUserInfo(_client(), username=login)
            return info.to_dict(include_social_accounts=include_social_accounts)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {executor.submit(_fetch_one, login): login for login in filtered}
            # A tqdm bar replaces the per-user prints when tqdm is installed and
            # verbose output is off; otherwise this is the plain iterator.
            completed_iter = _progress_iter(
                concurrent.futures.as_completed(futures),
                total=total,
                desc=f"{self.owner}/{self.repo}",
                enabled=progress and not verbose,
            )
            for future in completed_iter:
                login = futures[future]
                try:
                    data = future.result()
                    # Skip bots identified by profile flag in addition to login suffix
                    if exclude_bots and data.get("is_bot"):
                        pass
                    # Only store records with a valid login
                    elif data.get("login"):
                        user_data[data["login"]] = data
                        # Persist progress in batches of 10 to reduce I/O overhead
                        if save_each_iteration and len(user_data) % 10 == 0:
                            self._atomic_write_json(save_path, user_data)
                except Exception as e:
                    print(f"  [WARNING] Could not fetch data for {login}: {e}")
                    failed.append(login)

                completed += 1
                # Print rate-limit status every 50 users and at the end
                # Read from PyGithub's in-memory cache (populated by the last API
                # response) so we don't burn an extra API call per progress update.
                # Not gated on verbose: this is a rate-limit health readout, not a
                # per-user fetch message, and it is exactly what you still want
                # when you have turned the per-user noise off.
                if completed % 50 == 0 or completed == total:
                    try:
                        gh = rate_client["gh"]
                        remaining, total_limit = gh.rate_limiting
                        reset_epoch = gh.rate_limiting_resettime
                        reset_in = max(0, int((reset_epoch - time.time()) / 60))
                        print(
                            f"  [Progress: {completed}/{total} | "
                            f"Rate limit: {remaining}/{total_limit} remaining, "
                            f"resets in {reset_in}m]"
                        )
                    except Exception:
                        pass

        # Print summary of any users that could not be fetched
        if failed:
            print(f"  Skipped {len(failed)} user(s): {failed}")

        # Record failures so callers (and the CLI's exit code) can see that the
        # run was only partially successful.
        self.last_failed = list(failed)

        # Final flush — write whatever was collected that didn't hit a batch boundary
        if save_each_iteration and user_data:
            self._atomic_write_json(save_path, user_data)

        return user_data

    # ------------------------------------------------------------------
    # Step 3 - export to file
    # ------------------------------------------------------------------

    def export_to_json(
        self,
        user_data: Dict[str, dict],
        filename: Optional[str] = None,
        lines: bool = False,
    ) -> str:
        """
        Write the user-data dict to a JSON file inside the output directory and
        return the path written.

        Parameters
        ==========
        :user_data: dict
            dict of user records keyed by login to serialise.
        :filename: str/None (default=None)
            output filename; defaults to ``<owner>_<repo>_user_details.json``
            (or ``.jsonl`` when *lines* is True).
        :lines: bool (default=False)
            when True, write one JSON object per line (JSON Lines / JSONL format)
            instead of a single pretty-printed object, for streaming to
            downstream tools.

        Returns
        =======
        :path: str
            the path of the JSON file written.
        """
        if lines and filename is None:
            filename = f"{self.file_prefix}user_details.jsonl"
        else:
            filename = filename or f"{self.file_prefix}user_details.json"
        os.makedirs(self.outdir, exist_ok=True)
        path = os.path.join(self.outdir, filename)
        with open(path, "w", encoding="utf-8") as f:
            if lines:
                for record in user_data.values():
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            else:
                json.dump(user_data, f, indent=2, ensure_ascii=False, default=str)
        return path

    @staticmethod
    def _union_fields(user_data: Dict[str, dict]) -> List[str]:
        """
        Return the union of keys across every user record, in first-seen order.

        Records can legitimately differ from one another — a resumed run merges an
        older file, and ``fields`` filtering can vary between runs — so column
        sets must be derived from all records, not just the first one.

        Parameters
        ==========
        :user_data: dict
            dict of user records keyed by login.

        Returns
        =======
        :fields: list
            ordered list of every key appearing in any record.
        """
        fields: List[str] = []
        seen: Set[str] = set()
        for record in user_data.values():
            for key in record:
                if key not in seen:
                    seen.add(key)
                    fields.append(key)
        return fields

    def export_to_csv(
        self,
        user_data: Dict[str, dict],
        filename: Optional[str] = None,
    ) -> str:
        """
        Write flattened user data to a CSV file inside the output directory.
        Column names are the union of keys across all records (so records that
        differ, e.g. after a resume merge or field filtering, are not truncated),
        and list/tuple fields are serialised as semicolon-separated strings.

        Parameters
        ==========
        :user_data: dict
            dict of user records keyed by login to serialise.
        :filename: str/None (default=None)
            output filename; defaults to ``<owner>_<repo>_user_details.csv``.

        Returns
        =======
        :path: str
            the path of the CSV file written, or an empty string if *user_data*
            is empty.
        """
        if not user_data:
            return ""
        filename = filename or f"{self.file_prefix}user_details.csv"
        os.makedirs(self.outdir, exist_ok=True)
        path = os.path.join(self.outdir, filename)
        fields = self._union_fields(user_data)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for record in user_data.values():
                # Flatten list/tuple values to semicolon-separated strings
                row = {
                    k: (";".join(str(x) for x in v) if isinstance(v, (list, tuple)) else v)
                    for k, v in record.items()
                }
                writer.writerow(row)
        return path

    def export_to_xlsx(
        self,
        user_data: Dict[str, dict],
        filename: Optional[str] = None,
    ) -> str:
        """
        Write user data to an Excel (.xlsx) file inside the output directory.
        List/tuple fields are serialised as semicolon-separated strings.

        Parameters
        ==========
        :user_data: dict
            dict of user records keyed by login to serialise.
        :filename: str/None (default=None)
            output filename; defaults to ``<owner>_<repo>_user_details.xlsx``.

        Returns
        =======
        :path: str
            the path of the .xlsx file written, or an empty string if *user_data*
            is empty.

        Raises
        ======
        ImportError:
            When ``openpyxl`` is not installed (``pip install openpyxl`` or
            ``pip install repo-people[excel]``).
        """
        if not user_data:
            return ""
        try:
            import openpyxl
        except ImportError as exc:
            raise ImportError(
                "openpyxl is required for Excel export. "
                "Install it with: pip install openpyxl"
            ) from exc

        filename = filename or f"{self.file_prefix}user_details.xlsx"
        os.makedirs(self.outdir, exist_ok=True)
        path = os.path.join(self.outdir, filename)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Users"

        # Column names = union of keys across all records, matching export_to_csv.
        # Taking them from the first record alone silently dropped any field that
        # only later records carried (e.g. after a resume merges an older file).
        fields = self._union_fields(user_data)
        ws.append(fields)
        for record in user_data.values():
            row = []
            for field in fields:
                value = record.get(field)
                if isinstance(value, (list, tuple)):
                    value = ";".join(str(x) for x in value)
                elif value is None:
                    value = ""
                row.append(value)
            ws.append(row)

        wb.save(path)
        return path

    def export_to_markdown(
        self,
        user_data: Dict[str, dict],
        filename: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> str:
        """
        Write user data as a Markdown table to a file inside the output
        directory, escaping pipe characters within cell values.

        Parameters
        ==========
        :user_data: dict
            dict of user records keyed by login to render.
        :filename: str/None (default=None)
            output filename; defaults to ``<owner>_<repo>_user_details.md``.
        :fields: list/None (default=None)
            columns to include; defaults to a concise summary set of columns.

        Returns
        =======
        :path: str
            the path of the Markdown file written, or an empty string if
            *user_data* is empty.
        """
        if not user_data:
            return ""
        filename = filename or f"{self.file_prefix}user_details.md"
        # Default columns for a readable summary table
        default_fields = ["login", "name", "location", "company", "followers", "public_repos", "html_url"]
        cols = fields if fields is not None else default_fields
        os.makedirs(self.outdir, exist_ok=True)
        path = os.path.join(self.outdir, filename)
        with open(path, "w", encoding="utf-8") as f:
            # Header row
            f.write("| " + " | ".join(cols) + " |\n")
            f.write("| " + " | ".join(["---"] * len(cols)) + " |\n")
            for record in user_data.values():
                # Escape pipe characters inside cell values
                row = [str(record.get(c, "") or "").replace("|", "\\|") for c in cols]
                f.write("| " + " | ".join(row) + " |\n")
        return path

    def export_to_sqlite(
        self,
        user_data: Dict[str, dict],
        filename: Optional[str] = None,
        table: str = "users",
    ) -> str:
        """
        Write user data to a SQLite database inside the output directory, one row
        per user with ``login`` as the primary key.

        SQLite is in the standard library, so this adds no dependency, and it is a
        far better fit than CSV for the research and community-analysis workflows
        this package targets: snapshots can be joined, filtered and tracked over
        time with plain SQL instead of reloading files. List and dict values are
        stored as JSON text so nothing is lost in the round trip.

        Re-exporting to an existing database upserts by ``login``, which makes
        repeated runs accumulate into one queryable history rather than
        overwriting. A table this method created carries ``login`` as its primary
        key; one created by hand or by an older schema may not, in which case the
        rows being replaced are deleted first so the upsert still works without
        the constraint.

        Parameters
        ==========
        :user_data: dict
            dict of user records keyed by login to write.
        :filename: str/None (default=None)
            output filename; defaults to ``<owner>_<repo>_user_details.db``.
        :table: str (default="users")
            name of the table to write. Must be a plain identifier.

        Returns
        =======
        :path: str
            the path of the SQLite file written, or an empty string if
            *user_data* is empty.

        Raises
        ======
        ValueError:
            When *table* is not a valid SQL identifier.
        """
        if not user_data:
            return ""
        # SQL identifiers cannot be parameterised, so every table and column name
        # spliced into a statement below must be validated first. Record keys come
        # from the caller's dict, not just from UserSnapshot, so treating them as
        # trusted would be an injection hole.
        _check_identifier(table, "table name")

        filename = filename or f"{self.file_prefix}user_details.db"
        os.makedirs(self.outdir, exist_ok=True)
        path = os.path.join(self.outdir, filename)

        fields = self._union_fields(user_data)
        if "login" not in fields:
            # login is the primary key; without it there is nothing to key rows on.
            fields = ["login"] + fields
        for field in fields:
            _check_identifier(field, "field name")

        columns = ", ".join(
            f'"{f}" TEXT PRIMARY KEY' if f == "login" else f'"{f}"' for f in fields
        )
        placeholders = ", ".join("?" for _ in fields)
        quoted = ", ".join(f'"{f}"' for f in fields)

        conn = sqlite3.connect(path)
        try:
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({columns})')
            # An existing table may predate a field added since; widen it rather
            # than failing the export.
            info = list(conn.execute(f'PRAGMA table_info("{table}")'))
            existing = {row[1] for row in info}
            # PRAGMA table_info columns are (cid, name, type, notnull, default, pk).
            # A table this method created always has login as its primary key, but
            # one created by hand or by an older schema may not — and ON CONFLICT
            # needs the constraint to exist or it raises at execute time.
            login_is_key = any(row[1] == "login" and row[5] for row in info)
            for field in fields:
                if field not in existing:
                    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{field}"')

            rows = []
            for record in user_data.values():
                row = []
                for field in fields:
                    value = record.get(field)
                    if isinstance(value, (list, tuple, dict)):
                        value = json.dumps(value, ensure_ascii=False, default=str)
                    elif isinstance(value, bool):
                        # SQLite has no bool type; store 0/1 so SQL comparisons work.
                        value = int(value)
                    row.append(value)
                rows.append(row)

            updatable = [f for f in fields if f != "login"]
            # B608 (SQL built by string construction) is unavoidable in this
            # block: identifiers cannot be bound as parameters. Every identifier
            # spliced in has passed _check_identifier() above, and all values are
            # bound via executemany's placeholders.
            insert = (
                f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'  # nosec B608
            )
            if not login_is_key:
                # Without the primary key there is no conflict target to upsert
                # against, so clear the rows being replaced first. Same result,
                # no constraint required.
                login_index = fields.index("login")
                conn.executemany(
                    f'DELETE FROM "{table}" WHERE "login" = ?',  # nosec B608
                    [(row[login_index],) for row in rows],
                )
                statement = insert
            elif updatable:
                assignments = ", ".join(f'"{f}"=excluded."{f}"' for f in updatable)
                statement = (
                    f'{insert} ON CONFLICT("login") '  # nosec B608
                    f'DO UPDATE SET {assignments}'
                )
            else:
                # login is the only column, so there is nothing to update on
                # conflict — an empty SET clause is a syntax error.
                statement = f'{insert} ON CONFLICT("login") DO NOTHING'
            conn.executemany(statement, rows)
            conn.commit()
        finally:
            conn.close()
        return path

    def print_markdown(
        self,
        user_data: Dict[str, dict],
        fields: Optional[List[str]] = None,
    ) -> None:
        """
        Print a Markdown table of user data to stdout, using the same table
        format as :meth:`export_to_markdown` but writing to the terminal instead
        of a file. Useful for quick inspection in a terminal or notebook.

        Parameters
        ==========
        :user_data: dict
            dict of user records keyed by login to render.
        :fields: list/None (default=None)
            columns to include; defaults to a concise summary set of columns.

        Returns
        =======
        None
        """
        if not user_data:
            return
        default_fields = ["login", "name", "location", "company", "followers", "public_repos", "html_url"]
        cols = fields if fields is not None else default_fields
        print("| " + " | ".join(cols) + " |")
        print("| " + " | ".join(["---"] * len(cols)) + " |")
        for record in user_data.values():
            row = [str(record.get(c, "") or "").replace("|", "\\|") for c in cols]
            print("| " + " | ".join(row) + " |")

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    def summarise(self, user_data: Dict[str, dict], top_n: int = 5) -> dict:
        """
        Print and return a summary breakdown of the fetched user data, covering
        total users, the bot vs human split, top locations, top companies,
        account-age distribution (by band) and role distribution.

        Parameters
        ==========
        :user_data: dict
            dict of user records keyed by login to summarise.
        :top_n: int (default=5)
            how many of the top locations and companies to include.

        Returns
        =======
        :summary: dict
            dict of the computed summary statistics, or an empty dict when
            *user_data* is empty.
        """
        users = list(user_data.values())
        total = len(users)
        if not total:
            print("No user data to summarise.")
            return {}

        # Bot vs human
        bots = sum(1 for u in users if u.get("is_bot"))
        humans = total - bots

        # Top locations (skip empty)
        locations = Counter(
            u.get("location_normalized") or u.get("location")
            for u in users
            if u.get("location_normalized") or u.get("location")
        )

        # Top companies (skip empty)
        companies = Counter(
            u.get("company_normalized") or u.get("company")
            for u in users
            if u.get("company_normalized") or u.get("company")
        )

        # Country distribution — aggregates the free-text location field, so
        # "SF", "San Francisco" and "san francisco, ca" count as one country
        # instead of three separate locations.
        countries = Counter(
            u.get("location_country") or normalize_country(u.get("location") or "")
            for u in users
            if (u.get("location_country") or normalize_country(u.get("location") or ""))
        )

        # Account age distribution — split into four rough bands. No sort needed:
        # the bands are counted, not ordered.
        ages = [
            u.get("account_age_days", 0)
            for u in users
            if isinstance(u.get("account_age_days"), (int, float))
        ]
        def _band(days: int) -> str:
            """Map an account age in days to a human-readable age band label."""
            if days < 365:   return "< 1 year"
            if days < 1825:  return "1–5 years"
            if days < 3650:  return "5–10 years"
            return "> 10 years"
        age_bands = Counter(_band(d) for d in ages)

        summary = {
            "total": total,
            "humans": humans,
            "bots": bots,
            "top_locations": locations.most_common(top_n),
            "top_companies": companies.most_common(top_n),
            "top_countries": countries.most_common(top_n),
            "account_age_distribution": dict(age_bands),
        }

        # Role distribution — count how many users appear under each role
        role_distribution: Dict[str, int] = {}
        for u in users:
            for role in (u.get("roles") or []):
                role_distribution[role] = role_distribution.get(role, 0) + 1
        summary["role_distribution"] = role_distribution

        # Print formatted summary
        print(f"\n=== User Summary: {self.owner}/{self.repo} ===")
        print(f"  Total users : {total}")
        print(f"  Humans      : {humans}")
        print(f"  Bots        : {bots}")
        print(f"\n  Top {top_n} locations:")
        for loc, count in summary["top_locations"]:
            print(f"    {loc}: {count}")
        print(f"\n  Top {top_n} companies:")
        for co, count in summary["top_companies"]:
            print(f"    {co}: {count}")
        print(f"\n  Top {top_n} countries:")
        for country, count in summary["top_countries"]:
            print(f"    {country}: {count}")
        print("\n  Account age distribution:")
        for band in ["< 1 year", "1–5 years", "5–10 years", "> 10 years"]:
            print(f"    {band}: {age_bands.get(band, 0)}")
        if role_distribution:
            print("\n  Role distribution:")
            for role, count in sorted(role_distribution.items()):
                print(f"    {role}: {count}")
        print()

        return summary

    def top_users(
        self,
        user_data: Dict[str, dict],
        n: int = 10,
        by: str = "followers",
    ) -> List[dict]:
        """
        Return the top N users ranked in descending order by a numeric profile
        field. Users missing the field are treated as 0 and ranked last.

        Parameters
        ==========
        :user_data: dict
            dict of user records keyed by login to rank.
        :n: int (default=10)
            number of top users to return.
        :by: str (default="followers")
            numeric profile field to rank by (e.g. followers, public_repos,
            account_age_days, following, public_gists,
            total_public_stars_sampled).

        Returns
        =======
        :ranked: list
            list of the top N user record dicts, highest first.

        Raises
        ======
        ValueError:
            When *by* is not a valid field name. Without this check a typo
            silently ranked every user as 0 and returned an arbitrary order.
        """
        if by not in self.valid_fields():
            raise ValueError(
                f"Invalid field {by!r} for by=. "
                f"Valid fields are: {sorted(self.valid_fields())}"
            )
        ranked = sorted(
            user_data.values(),
            # Non-numeric values sort as 0 so a mixed field cannot raise TypeError.
            key=lambda u: (u.get(by) if isinstance(u.get(by), (int, float)) else 0),
            reverse=True,
        )
        return ranked[:n]

    def compare(
        self,
        other: "RepoPeople",
        user_data_self: Dict[str, dict],
        user_data_other: Dict[str, dict],
    ) -> Dict[str, object]:
        """
        Compare the user populations of this repository and another
        ``RepoPeople`` instance, reporting who is unique to each and who appears
        in both.

        Parameters
        ==========
        :other: RepoPeople
            the other ``RepoPeople`` instance to compare against.
        :user_data_self: dict
            user-data dict collected for this repository.
        :user_data_other: dict
            user-data dict collected for the *other* repository.

        Returns
        =======
        :comparison: dict
            dict with keys ``"only_in_self"`` (logins here but not in the other),
            ``"only_in_other"`` (logins in the other but not here) and
            ``"in_both"`` (logins in both), each a sorted list.

        Example::

            rp_a = RepoPeople("owner", "repo-a", token="ghp_...")
            rp_b = RepoPeople("owner", "repo-b", token="ghp_...")
            data_a = rp_a.get_users()
            data_b = rp_b.get_users()
            diff = rp_a.compare(rp_b, data_a, data_b)
            print(diff["in_both"])
        """
        logins_self = set(user_data_self.keys())
        logins_other = set(user_data_other.keys())
        return {
            "only_in_self": sorted(logins_self - logins_other),
            "only_in_other": sorted(logins_other - logins_self),
            "in_both": sorted(logins_self & logins_other),
        }

    @staticmethod
    def diff_snapshots(
        old: Union[Dict[str, dict], str],
        new: Union[Dict[str, dict], str],
    ) -> Dict[str, List[str]]:
        """
        Compare two user-data snapshots and report who joined, who left and who
        is unchanged between them. Each argument may be either a ``dict`` (as
        returned by :meth:`get_users`) or a path to a JSON file previously
        written by :meth:`export_to_json`.

        Parameters
        ==========
        :old: dict/str
            the earlier snapshot, as a user-data dict or a path to a JSON file.
        :new: dict/str
            the later snapshot, as a user-data dict or a path to a JSON file.

        Returns
        =======
        :diff: dict
            dict with keys ``"joined"`` (in *new* but not *old*), ``"left"`` (in
            *old* but not *new*) and ``"unchanged"`` (in both), each a sorted list.

        Example::

            diff = RepoPeople.diff_snapshots("snapshot_jan.json", "snapshot_feb.json")
            print(diff["joined"])   # new users
            print(diff["left"])     # users who disappeared
        """
        if isinstance(old, str):
            with open(old, "r", encoding="utf-8") as f:
                old = json.load(f)
        if isinstance(new, str):
            with open(new, "r", encoding="utf-8") as f:
                new = json.load(f)

        old_logins = set(old.keys())
        new_logins = set(new.keys())
        return {
            "joined": sorted(new_logins - old_logins),
            "left": sorted(old_logins - new_logins),
            "unchanged": sorted(old_logins & new_logins),
        }

    def get_users(
        self,
        export: bool = False,
        export_csv: bool = False,
        export_xlsx: bool = False,
        export_markdown: bool = False,
        export_sqlite: bool = False,
        save_each_iteration: bool = False,
        limit: Optional[int] = None,
        roles: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        exclude_bots: bool = False,
        resume: bool = False,
        verbose: bool = True,
        fields: Optional[List[str]] = None,
        include_social_accounts: bool = False,
        workers: int = 1,
        progress: bool = False,
    ) -> UserDataView:
        """
        Run the full pipeline for a repository: collect usernames from every
        requested role, deduplicate across roles, fetch each unique user's full
        GitHub profile, and optionally export the results. Each returned record
        always includes a ``"roles"`` key listing the role(s) the user appeared
        under, regardless of the *fields* parameter.

        Parameters
        ==========
        :export: bool (default=False)
            when True, save results to ``user_details.json``.
        :export_csv: bool (default=False)
            when True, save results to ``user_details.csv``.
        :export_xlsx: bool (default=False)
            when True, save results to ``user_details.xlsx`` (requires openpyxl).
        :export_markdown: bool (default=False)
            when True, save results to ``user_details.md`` as a Markdown table.
        :export_sqlite: bool (default=False)
            when True, save results to ``user_details.db`` as a SQLite database.
        :save_each_iteration: bool (default=False)
            when True, write ``user_details.json`` after successful fetches.
        :limit: int/None (default=None)
            stop after fetching this many user profiles.
        :roles: list/None (default=None)
            only collect users from these role categories (e.g.
            ``["contributors", "stargazers"]``); None collects all roles.
        :exclude: list/None (default=None)
            list of logins to skip entirely.
        :exclude_bots: bool (default=False)
            skip logins ending in ``[bot]`` and profiles with ``is_bot=True``.
        :resume: bool (default=False)
            load existing ``user_details.json`` and skip already-fetched users.
        :verbose: bool (default=True)
            print a line for each user being fetched.
        :fields: list/None (default=None)
            if set, keep only these attributes per user in the output (e.g.
            ``["login", "type", "updated_at"]``).
        :include_social_accounts: bool (default=False)
            fetch each user's linked social accounts (LinkedIn, Mastodon,
            YouTube, npm, …). Costs one extra API call per user.
        :workers: int (default=1)
            number of concurrent fetch threads (1 = sequential).
        :progress: bool (default=False)
            when True and *verbose* is False, show a ``tqdm`` progress bar if
            tqdm is installed (the ``progress`` extra).

        Returns
        =======
        :user_data: UserDataView
            dict-like view keyed by GitHub login with full user profile data.
            Logins that could not be fetched are listed in ``self.last_failed``.

        Raises
        ======
        ValueError:
            When any requested field or role name is invalid.
        """
        # Validate fields before any network calls
        if fields is not None:
            valid_fields = self.valid_fields()
            if isinstance(fields, str):
                fields = [fields]
            invalid = [f for f in fields if f not in valid_fields]
            if invalid:
                raise ValueError(
                    f"Invalid field(s): {invalid}. "
                    f"Valid fields are: {sorted(valid_fields)}"
                )

        # Validate roles before any network calls
        if roles is not None:
            if isinstance(roles, str):
                roles = [roles]
            invalid_roles = [r for r in roles if r not in self.VALID_ROLES]
            if invalid_roles:
                raise ValueError(
                    f"Invalid role(s): {invalid_roles}. "
                    f"Valid roles are: {sorted(self.VALID_ROLES)}"
                )

        # Step 1: collect usernames from the requested roles
        print(f"Collecting users for {self.owner}/{self.repo}...")
        username_groups = self.collect_all_usernames(roles=roles)

        # Build a login -> [roles] mapping for output annotation
        login_roles: Dict[str, List[str]] = {}
        for role, logins in username_groups.items():
            for login in logins:
                login_roles.setdefault(login, []).append(role)

        # Deduplicate across all collected roles into a single sorted list
        all_logins: Set[str] = {
            login
            for logins in username_groups.values()
            for login in logins
            if login
        }
        print(f"Found {len(all_logins)} unique users across all roles.")

        # Step 2: fetch full GitHub profile for each unique user
        print("Fetching user details from GitHub API...")
        self._print_rate_limit_status("Preflight")
        user_data = self.get_user_details(
            sorted(all_logins),
            save_each_iteration=save_each_iteration,
            limit=limit,
            exclude=exclude,
            exclude_bots=exclude_bots,
            resume=resume,
            verbose=verbose,
            include_social_accounts=include_social_accounts,
            workers=workers,
            progress=progress,
        )
        print(f"Retrieved profile data for {len(user_data)} users.")

        # Restrict each record to the requested subset of fields
        if fields:
            user_data = {
                login: {k: v for k, v in record.items() if k in fields}
                for login, record in user_data.items()
            }

        # Annotate each record with the roles the user appeared under
        for login, record in user_data.items():
            record["roles"] = sorted(login_roles.get(login, []))

        # Step 3: export to file(s)
        self._run_exports(
            user_data,
            export_json=export,
            export_csv=export_csv,
            export_xlsx=export_xlsx,
            export_markdown=export_markdown,
            export_sqlite=export_sqlite,
        )

        return UserDataView(user_data)

    def _run_exports(
        self,
        user_data: Dict[str, dict],
        export_json: bool = False,
        export_csv: bool = False,
        export_xlsx: bool = False,
        export_markdown: bool = False,
        export_sqlite: bool = False,
    ) -> None:
        """
        Write the requested export formats and print each path written.

        Shared by :meth:`get_users` and :meth:`get_users_async` so the two paths
        cannot drift apart on which formats they support.

        Parameters
        ==========
        :user_data: dict
            dict of user records keyed by login to export.
        :export_json: bool (default=False)
            write ``<owner>_<repo>_user_details.json``.
        :export_csv: bool (default=False)
            write ``<owner>_<repo>_user_details.csv``.
        :export_xlsx: bool (default=False)
            write ``<owner>_<repo>_user_details.xlsx`` (requires openpyxl).
        :export_markdown: bool (default=False)
            write ``<owner>_<repo>_user_details.md``.
        :export_sqlite: bool (default=False)
            write ``<owner>_<repo>_user_details.db``.

        Returns
        =======
        None
        """
        os.makedirs(self.outdir, exist_ok=True)
        writers = (
            (export_json, self.export_to_json),
            (export_csv, self.export_to_csv),
            (export_xlsx, self.export_to_xlsx),
            (export_markdown, self.export_to_markdown),
            (export_sqlite, self.export_to_sqlite),
        )
        for requested, writer in writers:
            if not requested:
                continue
            path = writer(user_data)
            if path:
                print(f"Exported to: {path}")

    # ------------------------------------------------------------------
    # Async API  (asyncio + aiohttp)
    # ------------------------------------------------------------------

    async def get_user_details_async(
        self,
        usernames: List[str],
        save_each_iteration: bool = False,
        limit: Optional[int] = None,
        exclude: Optional[List[str]] = None,
        exclude_bots: bool = False,
        resume: bool = False,
        verbose: bool = True,
        concurrency: int = 10,
        include_social_accounts: bool = False,
    ) -> Dict[str, dict]:
        """
        Async version of :meth:`get_user_details` using aiohttp. Fetches raw
        user profiles directly from the GitHub REST API (``GET /users/{login}``),
        using an ``asyncio.Semaphore`` to cap simultaneous connections, and
        assembles records matching the sync path's field set.

        Parameters
        ==========
        :usernames: list
            list of GitHub logins to fetch.
        :save_each_iteration: bool (default=False)
            when True, persist ``user_details.json`` after each fetch.
        :limit: int/None (default=None)
            cap the number of profiles fetched.
        :exclude: list/None (default=None)
            list of logins to skip.
        :exclude_bots: bool (default=False)
            skip logins ending in ``[bot]`` and profiles flagged as bots.
        :resume: bool (default=False)
            skip logins already present in ``user_details.json``.
        :verbose: bool (default=True)
            print a line per fetched user.
        :concurrency: int (default=10)
            maximum number of simultaneous aiohttp requests. Capped at 32, with a
            warning, if a higher value is passed.
        :include_social_accounts: bool (default=False)
            when True, make an extra request per user to fetch linked social
            accounts (LinkedIn, Mastodon, npm, …).

        Returns
        =======
        :user_data: dict
            dict keyed by GitHub login with profile-data dicts. Logins that could
            not be fetched are recorded in ``self.last_failed``.
        """
        import aiohttp
        import asyncio

        save_path = os.path.join(self.outdir, f"{self.file_prefix}user_details.json")

        # Load existing data when resuming
        if resume and os.path.isfile(save_path):
            with open(save_path, "r", encoding="utf-8") as f:
                user_data: Dict[str, dict] = json.load(f)
            print(f"  Resuming — {len(user_data)} users already fetched, skipping them.")
        else:
            user_data = {}

        # Build exclusion set from already-fetched and explicit excludes
        exclude_set: Set[str] = set(user_data.keys())
        if exclude:
            exclude_set.update(exclude)

        # Filter, strip bots by login, apply limit. Uses the shared _is_bot()
        # helper for the same reason the sync path does: matching only "[bot]"
        # here meant every foo-bot account cost a wasted request before being
        # discarded by the post-fetch check below.
        filtered = [
            login for login in usernames
            if login not in exclude_set
            and not (exclude_bots and _is_bot(login))
        ]
        filtered = filtered[:limit] if limit is not None else filtered

        if save_each_iteration or resume:
            os.makedirs(self.outdir, exist_ok=True)

        # Build auth headers for raw REST calls
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "repo-people/async",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        # Cap concurrency for the same reason the sync path caps workers: an
        # unbounded semaphore just converts a high number into connection-pool
        # exhaustion and secondary rate limiting.
        _MAX_CONCURRENCY = 32
        if concurrency > _MAX_CONCURRENCY:
            warnings.warn(
                f"concurrency={concurrency} exceeds the maximum of "
                f"{_MAX_CONCURRENCY}; capping at {_MAX_CONCURRENCY}.",
                UserWarning,
                stacklevel=2,
            )
            concurrency = _MAX_CONCURRENCY
        concurrency = max(1, concurrency)

        sem = asyncio.Semaphore(concurrency)
        failed: List[str] = []
        lock = asyncio.Lock()
        # Batch persistence like the sync path. Rewriting the whole file on every
        # single fetch (while holding the lock) made save_each_iteration O(n^2).
        save_counter = {"since_flush": 0}

        async def _get_json_with_retry(
            session: "aiohttp.ClientSession",
            url: str,
            params=None,
            max_retries: int = 5,
        ):
            """
            GET a URL and return parsed JSON, retrying on rate limits.

            Returns a ``(payload, error)`` tuple: *payload* is the parsed JSON on
            success, and *error* is a short description on failure. The async path
            previously mapped every non-200 to None, so a 403 rate limit was
            indistinguishable from a 404 and silently dropped the user.
            """
            for attempt in range(max_retries + 1):
                try:
                    async with session.get(url, headers=headers, params=params) as r:
                        if r.status == 200:
                            return await r.json(), None
                        if r.status in (403, 429):
                            # Honour the reset/retry headers, bounded like the sync path.
                            reset = r.headers.get("X-RateLimit-Reset")
                            retry_after = r.headers.get("Retry-After")
                            if reset and reset.isdigit():
                                wait_s = max(0, int(reset) - int(time.time()) + 1)
                            elif retry_after and retry_after.isdigit():
                                wait_s = int(retry_after)
                            else:
                                wait_s = 10
                            if wait_s > 60 or attempt == max_retries:
                                return None, f"rate limited (HTTP {r.status}), giving up"
                            print(
                                f"  Hit rate limit (HTTP {r.status}). Sleeping for {wait_s}s...",
                                flush=True,
                            )
                            await asyncio.sleep(wait_s)
                            continue
                        if r.status == 404:
                            return None, "not found (HTTP 404)"
                        return None, f"HTTP {r.status}"
                except asyncio.TimeoutError:
                    if attempt == max_retries:
                        return None, "timed out"
                    continue
                except Exception as exc:  # noqa: BLE001 - reported to the caller
                    return None, str(exc)
            return None, "exhausted retries"

        async def _fetch_one(session: aiohttp.ClientSession, login: str) -> None:
            """Fetch one user's profile via aiohttp and store it in ``user_data``."""
            async with sem:
                if verbose:
                    print(f"  Fetching: {login}")

                base_url = f"https://api.github.com/users/{login}"
                try:
                    # Fetch base profile, orgs, and latest public event concurrently
                    (raw, raw_err), (orgs_data, _), (events_data, _) = await asyncio.gather(
                        _get_json_with_retry(session, base_url),
                        _get_json_with_retry(session, f"{base_url}/orgs", {"per_page": 100}),
                        _get_json_with_retry(session, f"{base_url}/events/public", {"per_page": 1}),
                    )
                    if raw is None:
                        raise ValueError(raw_err or "error fetching base profile")
                except Exception as e:
                    print(f"  [WARNING] Could not fetch data for {login}: {e}")
                    async with lock:
                        failed.append(login)
                    return

                # Skip bot accounts flagged by profile type or login pattern
                if exclude_bots and _is_bot(login, raw.get("type", "")):
                    return

                # --- Derived string fields (no extra calls needed) ---
                email = raw.get("email") or ""
                email_domain = email.split("@", 1)[1].lower() if "@" in email else ""
                blog = raw.get("blog") or ""
                blog_host = (urlparse(blog).hostname or "").lower() if blog else ""
                company = raw.get("company") or ""
                company_normalized = company.strip()
                if company_normalized.startswith("@"):
                    company_normalized = company_normalized[1:]
                location = raw.get("location") or ""
                location_normalized = location.strip().lower()

                # --- Orgs ---
                orgs_list = orgs_data if isinstance(orgs_data, list) else []
                public_orgs = [o.get("login", "") for o in orgs_list if o.get("login")]

                # --- Last public event (for recently_active, matching sync path) ---
                events_list = events_data if isinstance(events_data, list) else []
                last_public_event_at = events_list[0].get("created_at", "") if events_list else ""

                # --- Computed date/ratio metrics ---
                # Normalise to the same ISO-8601 spelling the sync path emits.
                # GitHub's REST JSON uses a trailing "Z", while PyGithub hands the
                # sync path a datetime whose .isoformat() renders "+00:00" — so
                # without this the two pipelines export different strings for the
                # same timestamp.
                created_str = _iso_utc(raw.get("created_at", "") or "")
                updated_str = _iso_utc(raw.get("updated_at", "") or "")
                account_age_days = 0
                repos_per_year = 0.0
                if created_str:
                    try:
                        created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                        account_age_days = max(0, (datetime.now(timezone.utc) - created_dt).days)
                        # Must match GitHubUserInfo._repos_per_year() exactly (365.25
                        # days/year, clamped to a 1-year minimum) or the same user
                        # gets different numbers from the sync and async paths.
                        repos_per_year = round(
                            raw.get("public_repos", 0) / max(account_age_days / 365.25, 1.0), 2
                        )
                    except ValueError:
                        pass

                followers = raw.get("followers", 0) or 0
                following = raw.get("following", 0) or 0
                followers_following_ratio = round(
                    followers / following if following else float(followers), 2
                )

                # recently_active uses last_public_event_at (same signal as sync path)
                recently_active = False
                if last_public_event_at:
                    try:
                        ev_dt = datetime.fromisoformat(last_public_event_at.replace("Z", "+00:00"))
                        recently_active = (datetime.now(timezone.utc) - ev_dt).days <= 90
                    except ValueError:
                        pass

                # --- Assemble record matching GitHubUserInfo.to_dict() field set ---
                record = {
                    "login": raw.get("login", ""),
                    "id": raw.get("id"),
                    "node_id": raw.get("node_id", ""),
                    "type": raw.get("type", ""),
                    "name": raw.get("name") or "",
                    "company": company,
                    "location": location,
                    "email_public": email,
                    "email_domain": email_domain,
                    "blog": blog,
                    "blog_host": blog_host,
                    "twitter": raw.get("twitter_username") or "",
                    "bio": raw.get("bio") or "",
                    "avatar_url": raw.get("avatar_url", ""),
                    "html_url": raw.get("html_url", ""),
                    # UserSnapshot declares hireable as bool and the sync path
                    # coerces it; GitHub sends null for "not set", so coerce here
                    # too rather than leaking None into a bool field.
                    "hireable": bool(raw.get("hireable")),
                    "site_admin": raw.get("site_admin", False),
                    "created_at": created_str,
                    "updated_at": updated_str,
                    "followers": followers,
                    "following": following,
                    "public_repos": raw.get("public_repos", 0),
                    "public_gists": raw.get("public_gists", 0),
                    "public_orgs": public_orgs,
                    "orgs_public_count": len(public_orgs),
                    # is_bot: matches sync path in users.py (type, [bot] suffix, -bot suffix)
                    "is_bot": _is_bot(raw.get("login", login), raw.get("type", "")),
                    "last_public_event_at": last_public_event_at,
                    "has_public_email": bool(email),
                    "has_blog": bool(blog),
                    "has_twitter": bool(raw.get("twitter_username")),
                    "company_normalized": company_normalized,
                    "location_normalized": location_normalized,
                    "location_country": normalize_country(location),
                    "account_age_days": account_age_days,
                    "followers_following_ratio": followers_following_ratio,
                    "repos_per_year": repos_per_year,
                    "recently_active": recently_active,
                    # Aggregates are expensive and off by default — the sync path leaves
                    # these None unless explicitly requested, so the async path matches.
                    "top_languages": None,
                    "total_public_stars_sampled": None,
                    "total_public_forks_sampled": None,
                    # Optional fields not populated in async path (match sync defaults)
                    "ssh_keys_count": None,
                    "gpg_keys_count": None,
                    "starred_repos_sampled": None,
                    "social_accounts": None,
                    "is_collaborator": None,
                    "permission_on_repo": None,
                }

                # Optional social accounts, matching the sync path's opt-in field.
                if include_social_accounts:
                    social, _ = await _get_json_with_retry(
                        session, f"{base_url}/social_accounts"
                    )
                    record["social_accounts"] = {
                        (entry.get("provider") or "").lower(): entry.get("url") or ""
                        for entry in (social or [])
                        if entry.get("provider") and entry.get("url")
                    }

                if record.get("login"):
                    async with lock:
                        user_data[record["login"]] = record
                        if save_each_iteration:
                            save_counter["since_flush"] += 1
                            if save_counter["since_flush"] >= 10:
                                save_counter["since_flush"] = 0
                                self._atomic_write_json(save_path, user_data)

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            try:
                await asyncio.gather(*[_fetch_one(session, login) for login in filtered])
            except Exception as e:
                print(f"  [ERROR] Unexpected error during async fetch: {e}")

        if failed:
            print(f"  Skipped {len(failed)} user(s): {failed}")

        self.last_failed = list(failed)

        # Final flush — persist whatever did not land on a batch boundary.
        if save_each_iteration and user_data:
            self._atomic_write_json(save_path, user_data)

        return user_data

    async def get_users_async(
        self,
        export: bool = False,
        export_csv: bool = False,
        export_xlsx: bool = False,
        export_markdown: bool = False,
        export_sqlite: bool = False,
        save_each_iteration: bool = False,
        limit: Optional[int] = None,
        roles: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        exclude_bots: bool = False,
        resume: bool = False,
        verbose: bool = True,
        fields: Optional[List[str]] = None,
        concurrency: int = 10,
        include_social_accounts: bool = False,
    ) -> UserDataView:
        """
        Async version of :meth:`get_users`. Collects usernames synchronously
        (exactly as :meth:`get_users`), then fetches all profiles concurrently
        via aiohttp. Accepts the same parameters as :meth:`get_users`, except
        that *workers* is replaced by *concurrency*. Every returned record
        includes a ``"roles"`` key.

        Parameters
        ==========
        :export: bool (default=False)
            when True, save results to ``user_details.json``.
        :export_csv: bool (default=False)
            when True, save results to ``user_details.csv``.
        :export_xlsx: bool (default=False)
            when True, save results to ``user_details.xlsx`` (requires openpyxl).
        :export_markdown: bool (default=False)
            when True, save results to ``user_details.md`` as a Markdown table.
        :export_sqlite: bool (default=False)
            when True, save results to ``user_details.db`` as a SQLite database.
        :save_each_iteration: bool (default=False)
            when True, persist progress every 10 fetches.
        :limit: int/None (default=None)
            cap the number of profiles fetched.
        :roles: list/None (default=None)
            restrict which role categories are collected; None collects all.
        :exclude: list/None (default=None)
            list of logins to skip entirely.
        :exclude_bots: bool (default=False)
            skip bot accounts.
        :resume: bool (default=False)
            skip logins already present in ``user_details.json``.
        :verbose: bool (default=True)
            print per-user progress.
        :fields: list/None (default=None)
            restrict which fields appear in the output dict.
        :concurrency: int (default=10)
            maximum number of simultaneous aiohttp connections (max 32).
        :include_social_accounts: bool (default=False)
            fetch each user's linked social accounts (one extra call per user).

        Returns
        =======
        :user_data: UserDataView
            dict-like view keyed by GitHub login with profile data.

        Raises
        ======
        ValueError:
            When any requested field or role name is invalid.
        """
        # Validate fields before any network calls
        if fields is not None:
            valid_fields = self.valid_fields()
            if isinstance(fields, str):
                fields = [fields]
            invalid = [f for f in fields if f not in valid_fields]
            if invalid:
                raise ValueError(
                    f"Invalid field(s): {invalid}. "
                    f"Valid fields are: {sorted(valid_fields)}"
                )

        # Validate roles before any network calls
        if roles is not None:
            if isinstance(roles, str):
                roles = [roles]
            invalid_roles = [r for r in roles if r not in self.VALID_ROLES]
            if invalid_roles:
                raise ValueError(
                    f"Invalid role(s): {invalid_roles}. "
                    f"Valid roles are: {sorted(self.VALID_ROLES)}"
                )

        # Step 1: collect usernames synchronously (no async needed here)
        print(f"Collecting users for {self.owner}/{self.repo}...")
        username_groups = self.collect_all_usernames(roles=roles)

        # Build login -> [roles] mapping for output annotation
        login_roles: Dict[str, List[str]] = {}
        for role, logins in username_groups.items():
            for login in logins:
                login_roles.setdefault(login, []).append(role)

        all_logins: Set[str] = {
            login
            for logins in username_groups.values()
            for login in logins
            if login
        }
        print(f"Found {len(all_logins)} unique users across all roles.")

        # Step 2: fetch profiles asynchronously
        print("Fetching user details from GitHub API (async)...")
        user_data = await self.get_user_details_async(
            sorted(all_logins),
            save_each_iteration=save_each_iteration,
            limit=limit,
            exclude=exclude,
            exclude_bots=exclude_bots,
            resume=resume,
            verbose=verbose,
            concurrency=concurrency,
            include_social_accounts=include_social_accounts,
        )
        print(f"Retrieved profile data for {len(user_data)} users.")

        # Restrict to requested field subset
        if fields:
            user_data = {
                login: {k: v for k, v in record.items() if k in fields}
                for login, record in user_data.items()
            }

        # Annotate every record with the roles the user appeared under
        for login, record in user_data.items():
            record["roles"] = sorted(login_roles.get(login, []))

        # Step 3: export
        self._run_exports(
            user_data,
            export_json=export,
            export_csv=export_csv,
            export_xlsx=export_xlsx,
            export_markdown=export_markdown,
            export_sqlite=export_sqlite,
        )

        return UserDataView(user_data)

import os
import json
import csv
import logging
import warnings
import dataclasses
import threading
import concurrent.futures
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse
warnings.filterwarnings("ignore", category=ResourceWarning)
# Suppress PyGithub's verbose backoff messages
logging.getLogger("github.Requester").setLevel(logging.ERROR)
from github import Github, Auth
from typing import Optional, List, Dict, Set
from .users import GitHubUserInfo
from .utils import validate_owner_repo, _is_bot
from . import export

__all__ = ["RepoPeople", "UserDataView"]


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
    ):
        """
        Initialise a :class:`RepoPeople` instance for a single GitHub repository,
        validating the owner/repo names, warning when no token is supplied
        (unauthenticated requests are capped at 60/hour), creating the PyGithub
        client and verifying the connection before any collection begins.

        Parameters
        ==========
        :owner: str
            GitHub repository owner (user or organisation).
        :repo: str
            GitHub repository name.
        :token: str/None (default=None)
            GitHub personal access token; None runs unauthenticated with a warning.
        :outdir: str/None (default=None)
            output directory for exported files; defaults to ``"outputs"``.
        :skip_codeowners: bool (default=False)
            when True, skip the CODEOWNERS file when collecting maintainers.
        :skip_collaborators: bool (default=False)
            when True, skip the collaborators API when collecting maintainers.

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
        # Store token as a private attribute to reduce accidental exposure
        # (e.g. in repr(), vars(), or debug logs).
        self._token = token
        # Warn early: unauthenticated runs are capped at 60 requests/hour and will
        # crawl to a halt on any non-trivial repo. Surface it before the slow part.
        if token is None:
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
        # Initialise GitHub client (authenticated when token is provided)
        self.gh = Github(auth=Auth.Token(token)) if token else Github()
        # Fail fast if the token/connection is invalid
        try:
            self.gh.get_rate_limit()
        except Exception as e:
            raise ConnectionError(f"GitHub connection failed — verify your token. ({e})") from e
        self.repo_obj = self.gh.get_repo(f"{owner}/{repo}")

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

    def collect_all_usernames(
        self,
        roles: Optional[List[str]] = None,
    ) -> Dict[str, List[str]]:
        """
        Fetch usernames from each repository role and return them grouped by
        role. Roles are fetched concurrently, and if a subset of roles is
        requested only those are collected (avoiding unnecessary API calls).

        Parameters
        ==========
        :roles: list/None (default=None)
            list of role names to collect (e.g. ``["contributors", "stargazers"]``).
            If None, all valid roles are collected.

        Returns
        =======
        :results: dict
            dict mapping each requested role name to a list of GitHub login
            strings. Possible keys: contributors, maintainers, stargazers,
            watchers, issue_authors, pr_authors, pr_reviewers, fork_owners,
            commit_authors, dependents.

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

        # Map each role name to a callable that fetches it
        role_fetchers = {
            "contributors": lambda: export.export_contributors(
                self.owner, self.repo, self.token, self.outdir, return_data=True
            ),
            "maintainers": lambda: export.export_maintainers(
                self.owner, self.repo, self.token, self.outdir,
                self.skip_codeowners, self.skip_collaborators, return_data=True
            ),
            "stargazers": lambda: export.export_stargazers(
                self.owner, self.repo, self.token, self.outdir, return_data=True
            ),
            "watchers": lambda: export.export_watchers(
                self.owner, self.repo, self.token, self.outdir, return_data=True
            ),
            "issue_authors": lambda: export.export_issue_authors(
                self.owner, self.repo, self.token, self.outdir, return_data=True
            ),
            "pr_authors": lambda: export.export_pr_authors(
                self.owner, self.repo, self.token, self.outdir, return_data=True
            ),
            "fork_owners": lambda: export.export_fork_owners(
                self.owner, self.repo, self.token, self.outdir, return_data=True
            ),
            "commit_authors": lambda: export.export_commit_authors(
                self.owner, self.repo, self.token, self.outdir, return_data=True
            ),
            "dependents": lambda: export.export_dependents(
                self.owner, self.repo, self.outdir, return_data=True
            ),
            "pr_reviewers": lambda: export.export_pr_reviewers(
                self.owner, self.repo, self.token, self.outdir, return_data=True
            ),
        }
        # Only fetch the requested roles (lazy — avoids unnecessary API calls)
        active_roles = roles if roles is not None else list(role_fetchers)

        results: Dict[str, List[str]] = {}

        def _fetch_role(role: str) -> tuple:
            """Fetch a single role's usernames, returning ``(role, usernames)``."""
            return role, role_fetchers[role]()

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(active_roles), 9)) as executor:
            futures = {executor.submit(_fetch_role, role): role for role in active_roles}
            for future in concurrent.futures.as_completed(futures):
                role, data = future.result()
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

        Returns
        =======
        :user_data: dict
            dict keyed by GitHub login containing each user's full profile data.
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

        # Filter, apply bot exclusion, then apply limit
        filtered = [
            login for login in usernames
            if login not in exclude_set
            and not (exclude_bots and login.endswith("[bot]"))
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
        lock = threading.Lock()

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

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_fetch_one, login): login for login in filtered}
            for future in concurrent.futures.as_completed(futures):
                login = futures[future]
                try:
                    data = future.result()
                    # Skip bots identified by profile flag in addition to login suffix
                    if exclude_bots and data.get("is_bot"):
                        pass
                    # Only store records with a valid login
                    elif data.get("login"):
                        with lock:
                            user_data[data["login"]] = data
                            # Persist progress in batches of 10 to reduce I/O overhead
                            if save_each_iteration and len(user_data) % 10 == 0:
                                with open(save_path, "w", encoding="utf-8") as f:
                                    json.dump(user_data, f, indent=2, ensure_ascii=False, default=str)
                except Exception as e:
                    print(f"  [WARNING] Could not fetch data for {login}: {e}")
                    with lock:
                        failed.append(login)

                completed += 1
                # Print rate-limit status every 50 users and at the end
                # Read from PyGithub's in-memory cache (populated by the last API
                # response) so we don't burn an extra API call per progress update.
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

        # Final flush — write whatever was collected that didn't hit a batch boundary
        if save_each_iteration and user_data:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(user_data, f, indent=2, ensure_ascii=False, default=str)

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
        # Column names = union of keys across all records. Records can differ
        # (e.g. after resume merges an older file, or fields filtering), so
        # deriving columns from the first record alone would silently drop data.
        fields: List[str] = []
        seen_cols: Set[str] = set()
        for record in user_data.values():
            for k in record:
                if k not in seen_cols:
                    seen_cols.add(k)
                    fields.append(k)
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

        fields = list(next(iter(user_data.values())).keys())
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

        # Account age distribution — split into four rough bands
        ages = sorted(
            [u.get("account_age_days", 0) for u in users if isinstance(u.get("account_age_days"), (int, float))]
        )
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
        """
        ranked = sorted(
            user_data.values(),
            key=lambda u: (u.get(by) or 0),
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
        old: "Union[Dict[str, dict], str]",
        new: "Union[Dict[str, dict], str]",
    ) -> "Dict[str, List[str]]":
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

        Returns
        =======
        :user_data: UserDataView
            dict-like view keyed by GitHub login with full user profile data.

        Raises
        ======
        ValueError:
            When any requested field or role name is invalid.
        """
        # Validate fields against UserSnapshot before any network calls
        if fields is not None:
            from .users import UserSnapshot
            valid_fields = {f.name for f in dataclasses.fields(UserSnapshot)}
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
        os.makedirs(self.outdir, exist_ok=True)
        if export:
            path = self.export_to_json(user_data)
            print(f"Exported to: {path}")
        if export_csv:
            path = self.export_to_csv(user_data)
            print(f"Exported to: {path}")
        if export_xlsx:
            path = self.export_to_xlsx(user_data)
            if path:
                print(f"Exported to: {path}")

        return UserDataView(user_data)

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
            maximum number of simultaneous aiohttp requests.

        Returns
        =======
        :user_data: dict
            dict keyed by GitHub login with profile-data dicts.
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

        # Filter, strip bots by login suffix, apply limit
        filtered = [
            login for login in usernames
            if login not in exclude_set
            and not (exclude_bots and login.endswith("[bot]"))
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

        sem = asyncio.Semaphore(concurrency)
        failed: List[str] = []
        lock = asyncio.Lock()

        async def _fetch_one(session: aiohttp.ClientSession, login: str) -> None:
            """Fetch one user's profile via aiohttp and store it in ``user_data``."""
            async with sem:
                if verbose:
                    print(f"  Fetching: {login}")

                # Helper: GET a URL and return parsed JSON, or None on non-200
                async def _get_json(url: str, params=None):
                    """GET a URL and return parsed JSON, or None on a non-200 response."""
                    async with session.get(url, headers=headers, params=params) as r:
                        return await r.json() if r.status == 200 else None

                base_url = f"https://api.github.com/users/{login}"
                try:
                    # Fetch base profile, orgs, and latest public event concurrently
                    raw, orgs_data, events_data = await asyncio.gather(
                        _get_json(base_url),
                        _get_json(f"{base_url}/orgs", {"per_page": 100}),
                        _get_json(f"{base_url}/events/public", {"per_page": 1}),
                    )
                    if raw is None:
                        raise ValueError("HTTP error fetching base profile")
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
                created_str = raw.get("created_at", "") or ""
                updated_str = raw.get("updated_at", "") or ""
                account_age_days = 0
                repos_per_year = 0.0
                if created_str:
                    try:
                        created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                        account_age_days = (datetime.now(timezone.utc) - created_dt).days
                        repos_per_year = round(
                            raw.get("public_repos", 0) / max(account_age_days / 365, 1), 2
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
                    "hireable": raw.get("hireable"),
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

                if record.get("login"):
                    async with lock:
                        user_data[record["login"]] = record
                        if save_each_iteration:
                            with open(save_path, "w", encoding="utf-8") as f:
                                json.dump(user_data, f, indent=2, ensure_ascii=False, default=str)

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            try:
                await asyncio.gather(*[_fetch_one(session, login) for login in filtered])
            except Exception as e:
                print(f"  [ERROR] Unexpected error during async fetch: {e}")

        if failed:
            print(f"  Skipped {len(failed)} user(s): {failed}")

        return user_data

    async def get_users_async(
        self,
        export: bool = False,
        export_csv: bool = False,
        save_each_iteration: bool = False,
        limit: Optional[int] = None,
        roles: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        exclude_bots: bool = False,
        resume: bool = False,
        verbose: bool = True,
        fields: Optional[List[str]] = None,
        concurrency: int = 10,
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
        :save_each_iteration: bool (default=False)
            when True, persist after every fetch.
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
            maximum number of simultaneous aiohttp connections.

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
            from .users import UserSnapshot
            valid_fields = {f.name for f in dataclasses.fields(UserSnapshot)}
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
        os.makedirs(self.outdir, exist_ok=True)
        if export:
            path = self.export_to_json(user_data)
            print(f"Exported to: {path}")
        if export_csv:
            path = self.export_to_csv(user_data)
            print(f"Exported to: {path}")

        return UserDataView(user_data)

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
        if cls._valid_fields is None:
            from .users import UserSnapshot
            cls._valid_fields = frozenset(
                f.name for f in dataclasses.fields(UserSnapshot)
            ) | frozenset(["roles"])
        return cls._valid_fields

    @classmethod
    def _clear_valid_fields_cache(cls) -> None:
        """Reset the cached valid-fields set (useful in tests that patch UserSnapshot)."""
        cls._valid_fields = None

    def __getattr__(self, name: str):
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
        self.owner = owner
        self.repo = repo
        self.token = token
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

    def __repr__(self) -> str:
        return (
            f"RepoPeople(owner={self.owner!r}, repo={self.repo!r}, "
            f"outdir={self.outdir!r}, valid_roles={len(self.VALID_ROLES)})"
        )

    # ------------------------------------------------------------------
    # Step 1 - collect usernames from every repo role
    # ------------------------------------------------------------------

    # All valid role keys that can be passed to the roles parameter
    VALID_ROLES: Set[str] = {
        "contributors", "maintainers", "stargazers", "watchers",
        "issue_authors", "pr_authors", "fork_owners", "commit_authors", "dependents",
    }

    def collect_all_usernames(
        self,
        roles: Optional[List[str]] = None,
    ) -> Dict[str, List[str]]:
        """
        Fetch usernames from each repo role and return them grouped by role.

        Returns a dict with keys: contributors, maintainers, stargazers,
        watchers, issue_authors, pr_authors, fork_owners, commit_authors,
        dependents. Each value is a list of GitHub login strings.

        If roles is provided, only the specified roles are collected.
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
        }
        # Only fetch the requested roles (lazy — avoids unnecessary API calls)
        active_roles = roles if roles is not None else list(role_fetchers)

        results: Dict[str, List[str]] = {}

        def _fetch_role(role: str) -> tuple:
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
        Fetch full GitHub profile details for each username via the GitHub API.

        Returns a dict keyed by login containing all available user fields
        (profile info, counters, orgs, computed metrics, etc.).
        Users that cannot be fetched are skipped with a warning.

        If save_each_iteration is True, user_details.json is updated after every
        successful fetch so progress is preserved if the process is interrupted.
        If limit is set, only the first N usernames are fetched.
        If exclude is provided, those logins are skipped.
        If exclude_bots is True, logins ending in '[bot]' are skipped.
        If resume is True, any logins already present in user_details.json are skipped.
        If verbose is False, per-user fetch messages are suppressed.
        If include_social_accounts is True, an extra REST call fetches each user's
        linked social accounts (LinkedIn, Mastodon, YouTube, npm, etc.).
        workers controls the number of concurrent fetches (default 1 = sequential).
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

        total = len(filtered)
        completed = 0
        failed: List[str] = []
        lock = threading.Lock()

        def _fetch_one(login: str) -> dict:
            if verbose:
                print(f"  Fetching: {login}")
            info = GitHubUserInfo(self.gh, username=login)
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
                            # Incrementally persist progress after each successful fetch
                            if save_each_iteration:
                                with open(save_path, "w", encoding="utf-8") as f:
                                    json.dump(user_data, f, indent=2, ensure_ascii=False, default=str)
                except Exception as e:
                    print(f"  [WARNING] Could not fetch data for {login}: {e}")
                    with lock:
                        failed.append(login)

                completed += 1
                # Print rate-limit status every 50 users and at the end
                if completed % 50 == 0 or completed == total:
                    try:
                        rl = self.gh.get_rate_limit()
                        reset_in = max(0, int((rl.core.reset.timestamp() - time.time()) / 60))
                        print(
                            f"  [Progress: {completed}/{total} | "
                            f"Rate limit: {rl.core.remaining}/{rl.core.limit} remaining, "
                            f"resets in {reset_in}m]"
                        )
                    except Exception:
                        pass

        # Print summary of any users that could not be fetched
        if failed:
            print(f"  Skipped {len(failed)} user(s): {failed}")

        return user_data

    # ------------------------------------------------------------------
    # Step 3 - export to file
    # ------------------------------------------------------------------

    def export_to_json(
        self,
        user_data: Dict[str, dict],
        filename: Optional[str] = None,
    ) -> str:
        """Write user data dict to a JSON file in outdir. Returns the output path."""
        filename = filename or f"{self.file_prefix}user_details.json"
        os.makedirs(self.outdir, exist_ok=True)
        path = os.path.join(self.outdir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(user_data, f, indent=2, ensure_ascii=False, default=str)
        return path

    def export_to_csv(
        self,
        user_data: Dict[str, dict],
        filename: Optional[str] = None,
    ) -> str:
        """
        Write flattened user data to a CSV file in outdir.

        List/tuple fields are serialised as semicolon-separated strings.
        Returns the output path, or an empty string if user_data is empty.
        """
        if not user_data:
            return ""
        filename = filename or f"{self.file_prefix}user_details.csv"
        os.makedirs(self.outdir, exist_ok=True)
        path = os.path.join(self.outdir, filename)
        # Derive column names from the first record
        fields = list(next(iter(user_data.values())).keys())
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

    def export_to_markdown(
        self,
        user_data: Dict[str, dict],
        filename: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> str:
        """
        Write user data as a Markdown table to a file in outdir.

        Defaults to a concise set of columns; pass fields to override.
        Returns the output path, or an empty string if user_data is empty.
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
        Print a Markdown table of user data to stdout.

        Produces the same table format as :meth:`export_to_markdown` but
        writes to stdout instead of a file. Useful for quick inspection in a
        terminal or notebook. Does nothing when user_data is empty.
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
        Print and return a summary breakdown of the fetched user data.

        Covers: total users, bot vs human split, top locations, top companies,
        and account age distribution (by quartile).
        Pass top_n to control how many top locations/companies are shown.
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
        Return the top N users ranked by a numeric profile field.

        Common values for 'by': followers, public_repos, account_age_days,
        following, public_gists, total_public_stars_sampled.
        Users missing the field are ranked last.
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
        Compare user populations between this repo and another ``RepoPeople`` instance.

        Returns a dict with three keys:

        - ``"only_in_self"``  — logins present in this repo but not the other.
        - ``"only_in_other"`` — logins present in the other repo but not this one.
        - ``"in_both"``       — logins that appear in both repos.

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

    def get_users(
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
        include_social_accounts: bool = False,
        workers: int = 1,
    ) -> Dict[str, dict]:
        """
        Full pipeline: collect all repo usernames -> fetch user details -> export.

        Steps:
            1. Collect usernames from every repo role (contributors, stargazers, ...).
            2. Deduplicate across all roles.
            3. Fetch the full GitHub profile for each unique user.
            4. Optionally export to user_details.json / user_details.csv inside outdir.

        Parameters:
            export            -- save results to user_details.json when True.
            export_csv        -- save results to user_details.csv when True.
            save_each_iteration -- write user_details.json after every successful fetch.
            limit             -- stop after fetching this many user profiles.
            roles             -- only collect users from these role categories
                                 (e.g. ["contributors", "stargazers"]).
            exclude           -- list of logins to skip entirely.
            exclude_bots      -- skip logins ending in '[bot]' and profiles with is_bot=True.
            resume            -- load existing user_details.json and skip already-fetched users.
            verbose           -- print a line for each user being fetched.
            fields            -- if set, only these attributes are kept per user in the output
                                 (e.g. ["login", "type", "updated_at"]).
            include_social_accounts -- fetch each user's linked social accounts
                                 (LinkedIn, Mastodon, YouTube, npm, …). Costs one extra
                                 API call per user.
            workers           -- number of concurrent fetch threads (default 1 = sequential).

        Returns a dict keyed by GitHub login with full user profile data.
        Each record always includes a "roles" key listing the role(s) the user
        appeared under, regardless of the fields parameter.
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
        Async version of get_user_details using aiohttp.

        Fetches raw user profiles directly from the GitHub REST API
        (GET /users/{login}) using an asyncio.Semaphore to cap simultaneous
        connections. Supports the same filtering params as the sync path.

        Parameters:
            usernames         -- list of GitHub logins to fetch.
            save_each_iteration -- persist user_details.json after each fetch.
            limit             -- cap the number of profiles fetched.
            exclude           -- logins to skip.
            exclude_bots      -- skip logins ending in '[bot]'.
            resume            -- skip logins already in user_details.json.
            verbose           -- print a line per fetched user.
            concurrency       -- max simultaneous aiohttp requests (default 10).

        Returns a dict keyed by login with profile data dicts.
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
            async with sem:
                if verbose:
                    print(f"  Fetching: {login}")

                # Helper: GET a URL and return parsed JSON, or None on non-200
                async def _get_json(url: str, params=None):
                    async with session.get(url, headers=headers, params=params) as r:
                        return await r.json() if r.status == 200 else None

                base_url = f"https://api.github.com/users/{login}"
                try:
                    # Fetch base profile, orgs, latest public event, and owned repos concurrently
                    raw, orgs_data, events_data, repos_data = await asyncio.gather(
                        _get_json(base_url),
                        _get_json(f"{base_url}/orgs", {"per_page": 100}),
                        _get_json(f"{base_url}/events/public", {"per_page": 1}),
                        _get_json(f"{base_url}/repos", {"per_page": 50, "type": "owner"}),
                    )
                    if raw is None:
                        raise ValueError("HTTP error fetching base profile")
                except Exception as e:
                    print(f"  [WARNING] Could not fetch data for {login}: {e}")
                    failed.append(login)
                    return

                # Skip bot accounts flagged by profile type
                if exclude_bots and raw.get("type") == "Bot":
                    return

                # --- Derived string fields (no extra calls needed) ---
                email = raw.get("email") or ""
                email_domain = email.split("@", 1)[1].lower() if "@" in email else ""
                blog = raw.get("blog") or ""
                blog_host = (urlparse(blog).hostname or "").lower() if blog else ""
                company = raw.get("company") or ""
                company_normalized = company.strip().lstrip("@")
                location = raw.get("location") or ""
                location_normalized = location.strip().lower()

                # --- Orgs ---
                orgs_list = orgs_data if isinstance(orgs_data, list) else []
                public_orgs = [o.get("login", "") for o in orgs_list if o.get("login")]

                # --- Last public event (for recently_active, matching sync path) ---
                events_list = events_data if isinstance(events_data, list) else []
                last_public_event_at = events_list[0].get("created_at", "") if events_list else ""

                # --- Repos: top languages + star/fork sums (matches sync default include_langs=True) ---
                repos_list = repos_data if isinstance(repos_data, list) else []
                lang_counts: Dict[str, int] = {}
                total_stars = 0
                total_forks = 0
                for r in repos_list:
                    lang = r.get("language")
                    if lang:
                        lang_counts[lang] = lang_counts.get(lang, 0) + 1
                    total_stars += r.get("stargazers_count", 0)
                    total_forks += r.get("forks_count", 0)
                top_languages = sorted(lang_counts.items(), key=lambda x: x[1], reverse=True)[:3]

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
                    "is_bot": raw.get("type") == "Bot",
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
                    "top_languages": top_languages,
                    "total_public_stars_sampled": total_stars,
                    "total_public_forks_sampled": total_forks,
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

        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*[_fetch_one(session, login) for login in filtered])

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
    ) -> Dict[str, dict]:
        """
        Async version of get_users.

        Collects usernames synchronously (same as get_users), then fetches
        all profiles concurrently via aiohttp. Accepts the same parameters as
        get_users except workers is replaced by concurrency.

        Parameters:
            export            -- save results to user_details.json.
            export_csv        -- save results to user_details.csv.
            save_each_iteration -- persist after every fetch.
            limit             -- cap the number of profiles fetched.
            roles             -- restrict which role categories are collected.
            exclude           -- logins to skip entirely.
            exclude_bots      -- skip bot accounts.
            resume            -- skip logins already in user_details.json.
            verbose           -- print per-user progress.
            fields            -- restrict which fields appear in the output dict.
            concurrency       -- max simultaneous aiohttp connections (default 10).

        Returns a dict keyed by GitHub login with profile data, including a
        'roles' key on every record.
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

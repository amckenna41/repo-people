# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.5.0] - 2026-04-28

### Added

#### `RepoPeople.compare()` — cross-repo user diff
- New method `compare(other, user_data_self, user_data_other)` returns a dict with three keys:
  - `"only_in_self"` — logins present in this repo but not the other.
  - `"only_in_other"` — logins present in the other repo but not this one.
  - `"in_both"` — logins that appear in both repos.
- Enables competitive-intelligence workflows comparing community overlap between two repos.

#### `RepoPeople.print_markdown()` — stdout Markdown table
- New method `print_markdown(user_data, fields=None)` prints the same Markdown table format as `export_to_markdown` directly to stdout, without writing any file.
- Accepts the same optional `fields` parameter to restrict which columns are shown.
- Does nothing silently when `user_data` is empty.

#### Role distribution in `summarise()`
- `summarise()` now returns and prints a `"role_distribution"` key counting how many users appeared under each role (e.g. `{"contributors": 12, "stargazers": 340, ...}`).
- Printed under a new "Role distribution" section in the formatted summary output.

#### `UserDataView._clear_valid_fields_cache()` — explicit cache invalidation
- New classmethod `_clear_valid_fields_cache()` resets the cached `frozenset` of valid field names.
- Allows test code (or any caller that patches `UserSnapshot`) to force a recomputation on next access, preventing stale-cache bugs.

### Changed

#### `collect_all_usernames` — parallel role fetching
- All role fetchers (contributors, stargazers, watchers, etc.) are now fetched **concurrently** using `ThreadPoolExecutor` instead of sequentially.
- Result order matches the requested `roles` list for deterministic output.
- Speeds up collection significantly for repos with many roles to fetch.

#### `export_pr_authors` — switched to `/pulls` endpoint
- `export_pr_authors` previously fetched from `/repos/{owner}/{repo}/issues` and filtered items by the presence of a `pull_request` key.
- Now uses the dedicated `/repos/{owner}/{repo}/pulls` endpoint with `state=all`, which is more accurate, explicit, and avoids fetching issue data unnecessarily.

### Fixed

#### Thread safety for `failed` list in `get_user_details`
- `failed.append(login)` in the `ThreadPoolExecutor` loop was previously called without holding the `lock`, creating a data race under `workers > 1`.
- Now guarded by `with lock:` consistent with all other shared-state writes.

#### `write_csv` dirname guard in `utils.py`
- `os.makedirs(os.path.dirname(path), ...)` would raise `FileNotFoundError` when `path` had no directory component (e.g. `"file.csv"`), because `os.path.dirname` returns an empty string in that case.
- Now only calls `os.makedirs` when the dirname is non-empty.

#### `API_BASE_URL` and `BASE` moved to top of `export.py`
- Both constants were defined mid-file, after the functions that use them (relying on Python's name resolution at call time rather than at definition time).
- Moved to the top of the module, immediately after the imports, following standard Python convention.

### Tests
- Updated `test_export_pr_authors_return_data` in `test_export.py` to use `/pulls`-shaped payload (plain PR objects, no `pull_request` filter key needed).
- Added `TestCollectAllUsernamesParallel` (5 tests): all roles returned when no filter, roles filter respected, output order matches input, invalid role raises `ValueError`, result values are lists.
- Added `TestPrintMarkdown` (4 tests): header and row printed, empty data is silent, custom fields respected, pipe characters escaped.
- Added `TestSummariseRoleDistribution` (3 tests): `role_distribution` key present, counts are correct, empty when no roles.
- Added `TestCompare` (6 tests): `only_in_self`, `only_in_other`, `in_both`, all keys present, empty overlap, results sorted.
- Added `test_cache_clear_resets_valid_fields` to `TestUserDataView`.

---

## [0.4.0] - 2026-04-28

### Added

#### `UserDataView` — dot-notation field access on the returned user dict
- `get_users()` and `get_users_async()` now return a **`UserDataView`** instance instead of a plain `dict`.
- `UserDataView` is a `dict` subclass — all existing dict operations (`[]`, `.keys()`, `.values()`, iteration, JSON serialisation) are fully backward compatible.
- Any valid profile field name may be accessed via **dot notation** to retrieve that field across every collected user:
  ```python
  user_data = rp.get_users()
  user_data.email_public
  # {"alice": {"email_public": "alice@example.com"}, "bob": {"email_public": ""}, ...}
  ```
- Accessing a field that a user record does not contain returns `None` for that user.
- Accessing an unrecognised attribute raises `AttributeError` listing all valid field names.
- `UserDataView` is exported from the top-level `repo_people` package.

### Tests
- Added `TestUserDataView` (8 tests): `UserDataView` is a `dict` subclass, dot access returns correct structure for string and numeric fields, missing field returns `None`, `roles` field is accessible, invalid attribute raises `AttributeError`, `get_users()` return type is `UserDataView`, `UserDataView` is importable from top-level package.

---

## [0.3.0] - 2026-04-12

### Added

#### `export_contributors` pagination bypass
- **Removed 100-item hard cap** — `export_contributors` previously called the `/repos/{owner}/{repo}/contributors` REST endpoint, which is capped at 100 results. It now pages through `/commits` and collects unique `author.login` values, the same technique used by `export_commit_authors`. There is no longer any hard limit on the number of contributors returned.

#### Async API (`asyncio` + `aiohttp`)
- **`get_user_details_async(usernames, ..., concurrency=10)`** — new async method on `RepoPeople`. Fetches raw user profiles directly from `GET https://api.github.com/users/{login}` using `aiohttp.ClientSession` with an `asyncio.Semaphore` to cap simultaneous connections. Supports the same filtering params as the sync path (`exclude`, `exclude_bots`, `limit`, `resume`, `save_each_iteration`, `verbose`). Computes the same derived metrics (`account_age_days`, `repos_per_year`, `followers_following_ratio`, `recently_active`).
- **`get_users_async(..., concurrency=10)`** — async variant of `get_users()`. Collects usernames synchronously (same as the sync path) then fetches all profiles concurrently via `aiohttp`. Accepts all the same parameters as `get_users()` except `workers` is replaced by `concurrency`. Returns the same dict structure including the `roles` key on every record.
- **`aiohttp ^3.9`** added as a package dependency.

#### Utilities refactor
- **`export_commit_authors`** refactored to use `paginate()` from `repo_people.utils` instead of an inline `requests` loop with manual `Link` header parsing. Behaviour is unchanged.

### Tests
- Updated `ExportUnitTests` for `export_contributors` to use commit-shaped payloads (`{"author": {"login": ...}}`).
- Added `test_export_contributors_deduplicates_same_author` — same login across multiple commits appears once.
- Added `test_export_contributors_sorted_output` — result list is alphabetically sorted.
- Added `TestGetUserDetailsAsync` (5 tests): return value, `concurrency=` param, `exclude_bots`, failed-fetch summary, `resume`.
- Added `TestGetUsersAsync` (7 tests): return value, `roles` key present, invalid role/field validation, `concurrency=` param, `export=True` writes JSON, bare-string `roles=` coercion.

### Documentation
- **`README.md`** updated to document the `RepoPeople()` constructor parameters, new `workers` and `include_social_accounts` parameters in the `get_users()` table, new "Concurrent fetching" and "Include social accounts" examples, and the complete output fields table (including `social_accounts`, `roles`, and all sampled/computed fields).
- **`FIELDS.md`** added — full reference table of all 48 output fields with descriptions, types, default values, and notes on when each field is populated.

---

## [0.2.0] - 2026-04-12

### Added

#### Core pipeline (`RepoPeople`)
- **Token validation on startup** — `__init__` now calls `get_rate_limit()` immediately after creating the GitHub client. An invalid or expired token raises `ConnectionError` with a descriptive message rather than failing silently on the first API call.
- **`__repr__`** — `RepoPeople` now has a human-readable representation, e.g. `RepoPeople(owner='alice', repo='myrepo', outdir='outputs/alice_myrepo', valid_roles=9)`.
- **`workers` parameter** on `get_user_details` and `get_users` — controls the number of concurrent fetch threads (default `1` = sequential for full backward compatibility). Uses `concurrent.futures.ThreadPoolExecutor` internally.
- **Role validation** in `get_users` — passing an unrecognised role to `roles=` now raises `ValueError` immediately, before any API calls, listing every invalid name and the full set of valid ones. Mirrors the existing `fields=` validation behaviour. A bare string is also accepted and treated as a single-item list.
- **`roles` key in output records** — every user dict returned by `get_users` now always contains a `"roles"` key listing the role(s) the user appeared under (e.g. `["contributors", "stargazers"]`), even when a `fields=` filter is applied.
- **Rate-limit progress display** — `get_user_details` prints a progress line every 50 users and at the final user showing the current rate-limit headroom (remaining/limit and minutes until reset).
- **Failed-fetch summary** — at the end of `get_user_details`, a single `Skipped N user(s): [...]` line is printed listing all logins that could not be fetched.

#### Utilities module
- **`repo_people.utils`** — new shared utilities module. Helpers previously duplicated inside `export.py` have been consolidated here:
  - `_headers(token, extra)` — builds standard GitHub API request headers with optional overrides.
  - `_sleep_if_ratelimited(resp)` — handles `403` rate-limit responses; sleeps up to a configurable maximum and returns `"skip"` if the wait would be too long.
  - `paginate(url, token, params, accept)` — generic cursor-based paginator for the GitHub REST API.
  - `write_csv(path, header, rows)` — writes a CSV file, creating parent directories automatically.

### Changed
- `export.py` now imports the four shared helpers from `repo_people.utils` instead of defining them inline, eliminating the duplication.
- `pyproject.toml` development-status classifier updated from `5 - Production/Stable` to `4 - Beta`.

### Tests
- Added 7 new unit tests:
  - `TestRepoPeopleInit.test_repr_contains_owner_and_repo` — verifies `__repr__` output.
  - `TestRepoPeopleInit.test_invalid_token_raises_connection_error` — confirms `ConnectionError` on bad token.
  - `TestGetUserDetails.test_workers_param_accepted` — `workers=2` completes without error.
  - `TestGetUserDetails.test_failed_fetch_prints_summary` — failed logins trigger a `Skipped` summary line.
  - `TestGetUsers.test_invalid_role_raises_before_fetch` — unknown role raises `ValueError`.
  - `TestGetUsers.test_roles_always_in_output` — `"roles"` key is present in every record.
  - `TestGetUsers.test_workers_param_accepted` — `workers=2` is accepted by `get_users`.
  - `TestGetUsers.test_string_role_coerced` — `roles="contributors"` (bare string) is accepted.
  - `TestGetUsers.test_roles_content_reflects_membership` — roles list matches actual group membership.

---

## [0.1.0] - 2026-04-10

### Added

#### Core pipeline (`RepoPeople`)
- `RepoPeople` class with a two-step pipeline: collect usernames → fetch profiles.
- `collect_all_usernames(roles=None)` — gathers GitHub usernames across up to nine role categories: `contributors`, `maintainers`, `stargazers`, `watchers`, `issue_authors`, `pr_authors`, `fork_owners`, `commit_authors`, `dependents`.
- `get_user_details(usernames, ...)` — fetches full GitHub profiles for a list of usernames via the GitHub API, returning a dict keyed by login.
- `get_users(...)` — single-call pipeline entry point combining collection, fetching and optional export.
- `VALID_ROLES` class constant exposing the set of accepted role strings.

#### Parameters & filters
- `roles` — restrict collection to a subset of the nine role categories.
- `limit` — cap the number of user profiles fetched.
- `exclude` — skip a list of specific logins.
- `exclude_bots` — automatically skip bot accounts (login suffix `[bot]` or `type == "Bot"`).
- `resume` — continue an interrupted run by skipping logins already present in the output file.
- `save_each_iteration` — persist `user_details.json` after every successful fetch for incremental progress.
- `verbose` — toggle per-user progress messages.
- `fields` — restrict which fields appear in the returned dict and exports; validated against `UserSnapshot` before any API calls, raises `ValueError` for unrecognised names.
- `include_social_accounts` — opt-in flag to fetch each user's linked social accounts (LinkedIn, Mastodon, YouTube, npm, etc.) via an extra REST call per user.
- `skip_codeowners` / `skip_collaborators` — control which sources are used when collecting `maintainers`.

#### Export
- `export_to_json(user_data, filename)` — write results to a JSON file.
- `export_to_csv(user_data, filename)` — write results to a flattened CSV file.
- `export_to_markdown(user_data, filename, fields)` — write results as a Markdown table with an optional field subset.
- All output files are written under `outputs/{owner}_{repo}/` by default (configurable via `outdir`).

#### Analysis helpers
- `summarise(user_data, top_n)` — returns aggregate statistics (total users, top locations, companies, languages, etc.).
- `top_users(user_data, n, by)` — returns the top *n* users ranked by any numeric field.

#### `UserSnapshot` dataclass (30+ fields)
- Core identity: `login`, `id`, `node_id`, `type`, `name`, `company`, `location`, `email_public`, `email_domain`, `blog`, `blog_host`, `twitter`, `bio`, `avatar_url`, `html_url`, `hireable`, `site_admin`.
- Timestamps: `created_at`, `updated_at`.
- Counters: `followers`, `following`, `public_repos`, `public_gists`.
- Organisations: `public_orgs`, `orgs_public_count`.
- Flags: `has_public_email`, `has_blog`, `has_twitter`, `is_bot`.
- Normalised: `company_normalized`, `location_normalized`.
- Computed metrics: `account_age_days`, `followers_following_ratio`, `repos_per_year`, `recently_active`, `last_public_event_at`.
- Optional aggregates: `top_languages`, `total_public_stars_sampled`, `total_public_forks_sampled`, `ssh_keys_count`, `gpg_keys_count`, `starred_repos_sampled`.
- Social: `social_accounts` (provider → URL mapping).
- Repo-specific: `is_collaborator`, `permission_on_repo`.

#### Export module (`repo_people.export`)
Nine role-specific collector functions, each returning a list of GitHub login strings:
`export_contributors`, `export_maintainers`, `export_stargazers`, `export_watchers`, `export_issue_authors`, `export_pr_authors`, `export_fork_owners`, `export_commit_authors`, `export_dependents`.

#### `GitHubUserInfo` class
- Lazy, cached wrapper around a PyGithub `NamedUser` object.
- `snapshot()` method assembles all fields into a `UserSnapshot` dataclass.
- `to_dict()` / `to_csv_row()` / `to_json()` convenience serialisers.
- `social_accounts()` — fetches linked social accounts via `GET /users/{login}/social_accounts`.

#### Documentation
- Sphinx documentation covering installation, usage guide, full API reference, and contributing guide.
- `docs/conf.py` configured with `autodoc`, `napoleon`, `viewcode`, `intersphinx`, and the Alabaster theme.

#### Tests
- Full unit test suite (153 tests) covering `RepoPeople`, `UserSnapshot`, `GitHubUserInfo`, and all nine export functions.
- All GitHub API calls mocked; integration tests provided but skipped by default (require `GITHUB_TOKEN`).

[0.1.0]: https://github.com/amckenna41/repo-people/releases/tag/v0.1.0

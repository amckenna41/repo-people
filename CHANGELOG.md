# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.1] - 2026-08-31

### Added

- **Pre-flight cost estimate (`RepoPeople.dry_run()` / `--dry-run`)** — collects usernames only, then reports per-role counts, the number of users a real run would fetch (after `--exclude`/`--exclude-bots`/`--limit`) and the estimated request budget for the profile fetch (3 REST calls per user, 4 with `--include-social-accounts`) against the current rate-limit window. Over budget, it suggests the `--limit` that fits. Returns the estimate as a dict for library callers.

### Fixed

- **A single unfetchable user cost ~20 API calls instead of 1** — `GitHubUserInfo._user()` cached only successful fetches, so for a deleted or suspended login each of the ~20 properties a snapshot reads re-issued the same failing `get_user()` request (PyGithub's `get_user()` is not lazy — it calls `.complete()`). The failure is now cached too, and a bad login costs one request and one `[DEBUG]` line.
- **The async user-fetch path treated every 403 as a rate limit** — the bug `utils._is_ratelimit_response()` was added to fix for `paginate()` in 1.0.1 was still present in `get_user_details_async()`, where a permission failure (SAML-protected org, missing scope, blocked account) was slept on and retried five times. 403s are now classified by the same rules (exhausted `X-RateLimit-Remaining`, `Retry-After`, or a rate-limit message in the body) and anything else fails immediately.
- **GraphQL fast-path failure discarded already-collected roles** — `collect_simple_roles_graphql()` returned `None` on any failed page, throwing away the roles that had already finished paging and re-walking all of them over REST. It now returns the completed roles, so only the unfinished ones fall back.
- **Async path's User-Agent** — `get_user_details_async()` hardcoded a fourth value instead of `utils.USER_AGENT`, the inconsistency 1.1.0 was meant to close.

---

## [1.1.0] - 2026-07-26

### Security

- **`social_accounts()` always ran unauthenticated** — the token was read from `Requester._Requester__authorizationHeader`, which in PyGithub 2.x is only a class-level *type annotation* and is never assigned. The lookup therefore always returned `None`, so every `include_social_accounts=True` request went out anonymous, hit the 60-requests/hour limit, and returned `{}` with the failure swallowed by a bare `except`. The token is now resolved once in `GitHubUserInfo.__init__` (via the new `utils.extract_token()`, which reads `requester.auth.token`), and non-200 responses are reported instead of silently discarded.
- **`outputs/` was not git-ignored** — the default output directory holds other people's personal data (public emails, locations, employers, bios) scraped from the GitHub API. A routine `git add .` would commit a PII dataset. `outputs/` and the `*_user_details.*` patterns are now ignored.
- **`paginate()` followed the `Link` header to any host with the token attached** — a spoofed or compromised response could point `rel="next"` off-host and receive the `Authorization` header. Pagination is now refused if it leaves the scheme/host of the initial URL (which keeps GitHub Enterprise working). `export_dependents` applies the same check to its scraped pagination links.
- **`jakejarvis/wait-action@master` removed** — a mutable third-party ref executing in the same job as `secrets.PYPI_TEST`. Replaced with `run: sleep 30s`, matching what `deploy_pypi.yml` already did.
- **Deploy workflows no longer reachable from fork pull requests** — `deploy_test_pypi.yml` had no branch filter on its `workflow_run` trigger, so any successful "Building and Testing" run (including one triggered by a fork PR) fired a token-bearing deploy job. Both deploy workflows now require `github.event.workflow_run.event != 'pull_request'`, which also closes the gap where a fork PR from a branch named `main` satisfied a `branches: [main]` filter.
- **Least-privilege `permissions:`** — `build_test.yml` and `deploy_test_pypi.yml` had no `permissions` block and inherited the repository default. All three workflows are now `contents: read`; `packages: write` was dropped from `deploy_pypi.yml`, which never used it.
- **Bandit is now a blocking check** — it ran with `continue-on-error: true`, so the security gate could never fail a build. It now fails on medium-or-higher findings (`--severity-level medium`), with the report still uploaded via `if: always()`. `safety check` (deprecated) now prefers `safety scan`.
- **SQL identifier validation** — `export_to_sqlite()` validates the table name *and* every column name against `[A-Za-z_][A-Za-z0-9_]*`. Column names come from caller-supplied record keys, so treating them as trusted would have been an injection hole.
- **Cache entries are no longer world-readable** — the on-disk ETag cache was created at whatever the ambient umask allowed. Cached response bodies can include private-repository membership (contributor, collaborator and stargazer lists), so on a shared machine any local user could read them. The cache directory is now created `0700` and each entry written `0600`. Tokens were never cached and still are not.
- **Library no longer mutates global warning filters** — `warnings.filterwarnings("ignore", category=ResourceWarning)` ran at import time, silently disabling `ResourceWarning` for the entire importing application and hiding real unclosed-socket bugs (including the async path's own). Removed; the targeted `github.Requester` log-level suppression is kept.

### Fixed

- **`fields=["roles"]` raised `ValueError`** — `get_users()`/`get_users_async()` validated `fields` against `UserSnapshot` alone, which has no `roles` field, while `UserDataView.__getattr__` explicitly allowed `"roles"` and every record carries that key. Both now use the new `RepoPeople.valid_fields()` as a single source of truth.
- **`export_to_xlsx` silently dropped fields** — columns were derived from the first record only, the exact bug `export_to_csv` fixed in 1.0.1 and documented in a comment. Both now share `_union_fields()`.
- **Duplicated commit walk** — `export_contributors` and `export_commit_authors` were byte-identical `/commits` walks registered as separate role fetchers and run concurrently, so a default all-roles run paginated the entire commit history twice. Now memoised per repository via `_walk_commit_authors()`; `clear_commit_author_cache()` resets it.
- **`repo_obj` spent an API call on every instantiation** — it was fetched eagerly in `__init__` but read nowhere in the codebase, and raised PyGithub's `UnknownObjectException` rather than the documented `ValueError`/`ConnectionError` for an inaccessible repo. Now a lazy property that raises `ConnectionError`.
- **`Union` was used but never imported** — `diff_snapshots`' string annotations referenced `Union` with no import, so `typing.get_type_hints()` (used by Sphinx autodoc) raised `NameError`. Now imported and the annotations are real types.
- **Async path silently lost users to rate limits** — `_get_json` mapped every non-200 to `None`, making a `403` rate limit indistinguishable from a `404`. It now retries 403/429 with bounded back-off honouring `X-RateLimit-Reset`/`Retry-After`, and reports a specific reason on failure.
- **Async `save_each_iteration` was O(n²)** — it rewrote the entire JSON file on *every* fetch while holding the lock. Now batched every 10 records, matching the sync path.
- **Async `concurrency` was uncapped** — now capped at 32 with a `UserWarning`, like `workers`.
- **Sync/async field divergence** — three separate mismatches meant the same user yielded different records depending on which pipeline fetched them:
  - `repos_per_year` used `365` in the async path and `365.25` in the sync path. Both now use 365.25 with the divisor clamped to a one-year minimum. The old sync formula also reported absurd rates for very new accounts (a one-day-old account with 5 repos gave `1826.0`, now `5.0`).
  - `hireable` could be `None` from the async path despite `UserSnapshot` declaring it `bool`; now coerced in both.
  - `created_at`/`updated_at` were emitted as `...Z` by async and `...+00:00` by sync — the same instant, but different strings landing in JSON/CSV/SQLite output. Both now normalise to `+00:00` via the new `_iso_utc()`.

  Verified against the live API: the two paths now produce byte-identical records apart from the documented opt-in aggregates.
- **`save_each_iteration` writes are now atomic** — progress was rewritten in place, so a kill mid-write could truncate the very file the feature exists to protect. Now written to a temp file and `os.replace()`d.
- **`exclude_bots` wasted an API call per `-bot` account** — the pre-fetch screen only matched `[bot]`, so `foo-bot` accounts were fetched and then discarded by the post-fetch check. It now uses the shared `_is_bot()` helper, as its docstring always claimed.
- **`export_dependents` could return more than `limit`** — it broke out of the loop once the cap was reached but never truncated the accumulated list.
- **`top_users(by=...)` silently accepted invalid fields** — a typo ranked every user as `0` and returned an arbitrary order. Invalid names now raise `ValueError`, and non-numeric values can no longer raise `TypeError` mid-sort.
- **`export_issue_authors` counted PR authors as issue authors** — REST's `/issues` endpoint returns pull requests alongside issues. They are now filtered out, which both matches the function's documented purpose and makes the REST and GraphQL paths agree. PR authors remain available as the separate `pr_authors` role.
- **Dead lock removed** — `get_user_details` held a `threading.Lock` around `user_data`/`failed`, but every mutation happens in the main thread's `as_completed` loop; the workers only fetch and return.
- **No-op sort removed** — `summarise()` sorted the account-age list before only ever counting it into bands.
- **One failing role discarded the entire collection** — `collect_all_usernames()` called `future.result()` unguarded, so a 5xx on `/stargazers` or a scraping failure in `dependents` propagated out and threw away every other role's work, including an already completed commit-history walk. Failures are now isolated per role: a warning is printed and that role returns an empty list, leaving the rest of the collection intact.
- **Every 403 was treated as a rate limit** — GitHub uses 403 both for an exhausted quota and for an ordinary authorisation failure, so a SAML-protected organisation or a token missing a scope was slept on and retried five times, burning roughly 50 seconds per URL before surfacing the same error anyway. The new `_is_ratelimit_response()` retries only a genuine rate limit — identified by an exhausted `X-RateLimit-Remaining`, a `Retry-After` header, or a rate-limit message in the body (secondary limits that send no header). A permission 403 now raises immediately.
- **`GitHubUserInfo.is_bot` restated the bot rule instead of calling `_is_bot()`** — the duplicate had already drifted from the shared helper once, which is what left the async pre-fetch screen matching only `[bot]`. Both now delegate to the one implementation.
- **Concurrent runs could corrupt each other's progress file** — `_atomic_write_json()` and the cache writer both used a fixed `.tmp` suffix, so two processes writing the same path raced on the temp file — precisely the interruption scenario `save_each_iteration` exists to survive. Temp names now carry the pid.
- **Commit-walk memo was keyed on token *presence*, not the token** — two tokens with different repository visibility shared one cache entry within a process, so the second caller could receive the first's results. The key now holds the token itself.
- **`export_to_sqlite()` raised against a table with no `login` primary key** — a table created by hand or by an older schema has no conflict target, so `ON CONFLICT("login")` failed at execute time. The primary-key flag is now read from `PRAGMA table_info` and the rows being replaced are deleted first when it is absent, giving the same upsert without the constraint.
- **Test isolation only applied under pytest** — the ETag-cache redirect and the per-test reset of the process-wide commit-author memo lived in `tests/conftest.py`, which is a pytest-only mechanism. Run with `python -m unittest discover -s tests -t .` (or a single file's `unittest.main()` footer) neither fired: one test's mocked payload was served to the next and seven tests in `ExportUnitTests` failed on leaked memo state, while the pytest run CI uses stayed green. Both now live in `tests/__init__.py`, which every runner imports. The suite passes under pytest and `unittest`, whole or per file.
- **A 304 with no cache entry to replay crashed** — `raise_for_status()` does not fire on 3xx, so a cache entry evicted between the read and the response fell through to `resp.json()` on an empty body. It now warns and stops pagination.

### Added

- **GraphQL fast paths** (`use_graphql=True` by default, REST fallback automatic):
  - `export_pr_reviewers` fetches 100 pull requests with their reviews nested per call instead of one REST call *per pull request* — roughly 500 calls down to 5 on a 500-PR repository.
  - `collect_simple_roles_graphql()` fetches `stargazers`, `watchers`, `fork_owners`, `issue_authors` and `pr_authors` in a **single** query, so a repository with under 100 of each costs one call instead of five paginated REST walks.
- **On-disk ETag cache** (`use_cache=True` by default) — `paginate()` stores each page's `ETag` and replays it as `If-None-Match`. GitHub's `304 Not Modified` responses **do not count against the rate limit**, making repeat collections of the same repository dramatically cheaper. Location honours `REPO_PEOPLE_CACHE_DIR`, then `XDG_CACHE_HOME`, then `~/.cache/repo-people`. Cleared via `utils.clear_cache()` or `repo-people --clear-cache`. Stores public responses only; the cache key excludes the token.
- **`export_to_sqlite()`** — one row per user keyed on `login`, using the standard-library `sqlite3` (no new dependency). Lists/dicts stored as JSON text, booleans as `0`/`1`. Re-exporting upserts by `login`, so repeated runs accumulate into one queryable table. A better fit than CSV for the longitudinal and comparative research this package targets.
- **`location_country`** — best-effort ISO 3166-1 alpha-2 code derived from the free-text `location` field, via the new `utils.normalize_country()`. `summarise()` gained a `top_countries` breakdown, which aggregates `"SF"`, `"San Francisco"` and `"san francisco, ca"` as one country instead of three locations. A heuristic lookup over country names, major cities and US/Canadian subdivision codes — **not** a geocoder; `""` means *unknown*.
- **CLI exit code `2` on partial failure** — `main()` previously always exited `0` even when profiles failed to fetch, so CI could not tell an incomplete dataset from a complete one. Failures are also exposed programmatically as `rp.last_failed`.
- **CLI flags for existing library features** that had no command-line equivalent: `--resume`, `--save-each-iteration`, `--include-social-accounts`, `--async`, `--concurrency`, `--exclude`, `--export-md`, `--export-sqlite`, `--summarise`, `--progress`, `--no-cache`, `--clear-cache`, `--no-graphql`. The prominently documented resume feature was previously unreachable from the CLI.
- **`GITHUB_TOKEN` fallback in the constructor** — the README has always claimed `RepoPeople(...)` falls back to the environment variable, but only the CLI did. Library callers now get the same behaviour.
- **Async parity** — `get_users_async()` now supports `export_xlsx`, `export_markdown`, `export_sqlite` and `include_social_accounts`, matching `get_users()`. Both share a single `_run_exports()` so the two cannot drift apart again.
- **`tqdm` progress bars** (`progress=True` with `verbose=False`) — the `progress` extra was declared in `pyproject.toml` but never imported anywhere. Degrades to a plain iterator when tqdm is absent.
- **`RepoPeople.valid_fields()`** — the single authoritative allow-list for `fields=`.
- **Tests** — the suite is now 435 tests, covering every fix above: the token-plumbing regression (asserting the `Authorization` header is actually sent), cross-host pagination refusal, ETag 304 replay (and the unreplayable-304 path), the xlsx column union, sync/async field agreement, SQLite round-trip, upsert and upsert-without-a-primary-key, identifier-injection rejection, the memoised commit walk, role-failure isolation, rate-limit versus permission 403 classification, the async `-bot` pre-fetch screen, and CLI exit codes. Each regression test was checked against the pre-fix code to confirm it actually fails there rather than passing vacuously.

### Changed

- **Minimum Python is now 3.10.** `pyproject.toml` declared `^3.9`, but the classifiers listed 3.10–3.12 and CI has only ever tested 3.10 and 3.11 — nothing verified the 3.9 claim. No 3.10-only syntax is in use, so this narrows a supported range that was never exercised rather than dropping working support.
- **`User-Agent`** is now a single `utils.USER_AGENT` constant. Previously three different values were sent, including a stale `gh-census/0.1` and `dep-scraper/1.0`.
- **Rate-limit progress line is no longer suppressed by `verbose=False`** — it is a rate-limit health readout, not per-user noise, and it is exactly what you still want when the per-user output is off.
- **Docs** — corrected "9 role categories" to 10 across `README.md`, `docs/index.rst` and `docs/usage.rst`; documented the previously undocumented `pr_reviewers` role; corrected the `GITHUB_TOKEN` fallback claim; fixed a stray backtick in the README table of contents; removed a stale commented-out duplicate Authentication section; documented every new feature and field. `docs/.DS_Store` untracked. Sphinx now builds with zero warnings.

### Note

Version **1.0.2** was released without a corresponding changelog entry; this entry covers only the changes made in 1.1.0.

---

## [1.0.1] - 2026-07-14

### Fixed

- **`paginate()` infinite loop on a non-rate-limit `403`** — a `403` with no `X-RateLimit-Reset` / `Retry-After` header (e.g. a genuinely forbidden resource) previously retried forever with a 10 s back-off. Retries are now capped at 5, after which the error surfaces via `raise_for_status()`.
- **Sync/async output divergence** — the async pipeline (`get_user_details_async`) computed `top_languages`, `total_public_stars_sampled`, and `total_public_forks_sampled` while the sync pipeline left them `None`, so the two paths produced different records for the same user. These aggregates are expensive and off by default (see `snapshot()` defaults), so the async path now also leaves them `None`, matching sync. As a result async makes **3** requests per user instead of 4 (the owned-repos fetch is dropped).
- **`export_dependents` wasted sleep on failure** — on a non-200 page the function doubled a back-off timer and slept before an unconditional `break`, i.e. it slept then gave up without ever retrying. It now stops immediately on the first non-200 response. (Supersedes the "exponential back-off" behaviour listed under 1.0.0, which never actually retried.)
- **`export_to_csv` dropped columns** — the CSV header was derived from the first record only, so any key unique to a later record (e.g. after `resume` merges an older file with a different field set) was silently omitted. The header is now the union of keys across all records.

### Added

- **Request timeouts** — added a 30 s timeout to `paginate()`, `fetch_codeowners()`, and the async `aiohttp.ClientSession`. Previously a hung/black-holed connection on these paths could block a run indefinitely (only `social_accounts()` and the dependents scraper had timeouts).
- **Thread-safe concurrent fetching** — `get_user_details(workers>1)` now gives each worker thread its own `Github` client instead of sharing one (PyGithub wraps a non-thread-safe `requests.Session`). The default single-worker path is unchanged and its live rate-limit readout is preserved.
- **No-token warning** — `RepoPeople(...)` now emits a `UserWarning` at construction when no token is provided (and `GITHUB_TOKEN` is unset), noting the 60-requests/hour unauthenticated limit, instead of silently crawling into rate limits.
- **Tests** — `test_persistent_403_is_retried_a_bounded_number_of_times` (paginate retry cap) and `test_header_is_union_of_all_record_keys` (CSV column union).

### Changed

- **`docs/usage.rst`** — corrected the `summarise()` example output to the keys the method actually returns (`total`, `humans`, `bots`, `top_locations`, `top_companies`, `account_age_distribution`, `role_distribution`); removed the stale `top_languages` key from that example.
- **`README.md`** — documented the new no-token `UserWarning` in the Authentication section.

---

## [1.0.0] - 2026-05-14

### Added

- **`utils.validate_owner_repo()`** — validates `owner` and `repo` at construction time, rejecting characters outside `[A-Za-z0-9_.-]`. Prevents path/URL injection.
- **`utils._is_bot()`** — shared helper used by both sync and async pipelines for consistent bot detection (type == "Bot", `[bot]` suffix, `-bot` suffix).
- **`export_to_json(lines=True)`** — JSONL / JSON Lines streaming export option. Writes one JSON object per line into a `.jsonl` file.
- **`workers` cap** — `get_user_details()` now caps `workers` at 32 with a `UserWarning` if exceeded.
- **`pyproject.toml` optional extras** — `aiohttp` moved to `[async]` extra; `tqdm` added as `[progress]` extra. Install with `pip install repo-people[async]`.
- **`__version__`** exposed in `repo_people/__init__.py`.
- **`TestExportDependents`** — 8 new unit tests for `export_dependents` (previously had zero coverage).
- **`TestUtilsHelpers`** — 7 new unit tests for `validate_owner_repo` and `_is_bot`.
- Additional `TestRepoPeopleInit` tests: input validation, token privacy, workers cap warning.
- Additional `TestExportToJson` tests: JSONL mode.

### Changed

- **`export_*` functions** — all export functions now always return a `list` of logins. The `return_data` parameter is kept for backwards compatibility but is ignored (was previously a dual return type anti-pattern: `int` vs `list`).
- **`export_maintainers` deduplication** — dedup key changed from `(login_or_team, source)` to just `login_or_team`, so the same person appearing in both CODEOWNERS and collaborators is correctly counted once.
- **`export_dependents` limit semantics** — `limit=0` now correctly returns an empty list immediately. Previously `0` was falsy and the check was skipped entirely.
- **`export_dependents` backoff** — non-200 responses now trigger exponential back-off (doubles per failed page, capped at 60 s) instead of a fixed 1 s sleep per page.
- **`_sleep_if_ratelimited`** — a `wait_s == 0` (no `Retry-After` header) now uses a 10 s fixed back-off instead of silently returning `False` and causing the caller to skip the retry.
- **Async `is_bot`** — unified with sync path via `_is_bot()` helper; previously only checked `type == "Bot"`.
- **Async `company_normalized` / `location_normalized`** — now match sync path: strip `@` prefix, lowercase. Previously applied `.title()` causing divergent output.
- **Async `failed.append`** — now wrapped in `asyncio.Lock` to prevent data race.
- **`save_each_iteration`** — writes in batches of 10 users instead of after every single fetch (O(n²) I/O improvement). A final flush is performed at the end of the function.
- **Rate-limit progress** — reads from `gh.rate_limiting` / `gh.rate_limiting_resettime` (in-memory cache populated by the last API response) instead of calling `get_rate_limit()` every 50 users.
- **`snapshot()` defaults** — `include_langs` and `include_star_fork_sums` default to `False` instead of `True`. These options iterate over repositories (expensive). Pass `include_langs=True` to opt in.
- **Token stored as `_token`** — stored privately; exposed via `.token` read-only property to reduce accidental logging/repr exposure.
- **`social_accounts()`** — now uses `requests.get` directly instead of the private `_Github__requester` internal PyGithub API, making it stable across PyGithub versions.
- **`FIELDS.md`** — `recently_active` description corrected: uses `last_public_event_at`, not `updated_at`.
- **`README.md`** — Codecov badge `branch/master` → `branch/main`; `outdir` default corrected to `"outputs"`; `limit` documents alphabetical ordering; `recently_active` clarified; optional install instructions added.
- **CI (`build_test.yml`)** — `paths-ignore` globs fixed (`**/.md` → `**/*.md`); test runner switched from `unittest discover` to `pytest`.
- **`pyproject.toml`** — removed non-standard `"License :: Free For Educational Use"` classifier; version bumped to `1.0.0`.
- **`requirements.txt`** — removed dev/build deps (`pytest`, `setuptools`, `wheel`, `python-dotenv`).

### Fixed

- `export_dependents`: `limit=0` now correctly returns `[]` (was a falsy-check bug).
- Async pipeline data race on `failed` list (missing `asyncio.Lock`).
- `social_accounts()` was calling `_Github__requester.requestJsonAndCheck` (name-mangled private API).
- CI `paths-ignore` globs would never match any file (missing `*` before `.md`/`.yml`).

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

# repo-people

[![PyPI version](https://badge.fury.io/py/repo-people.svg)](https://badge.fury.io/py/repo-people)
[![Platforms](https://img.shields.io/badge/platforms-linux%2C%20macOS%2C%20Windows-green)](https://pypi.org/project/repo-people/)
[![PythonV](https://img.shields.io/pypi/pyversions/repo-people?logo=2)](https://pypi.org/project/repo-people/)
[![Documentation Status](https://readthedocs.org/projects/repo-people/badge/?version=latest)](https://repo-people.readthedocs.io/en/latest/?badge=latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)
[![Issues](https://img.shields.io/github/issues/amckenna41/repo-people)](https://github.com/amckenna41/repo-people/issues)
[![codecov](https://codecov.io/gh/amckenna41/repo-people/branch/main/graph/badge.svg?token=4PQDVGKGYN)](https://codecov.io/gh/amckenna41/repo-people)

<p align="center">
  <img src="https://github.com/amckenna41/repo-people/blob/main/images/logo.png" alt="repo-people logo" width="300"/>
</p>

**repo-people** is a Python package that collects and exports the full GitHub profile for every person associated with a repository — contributors, maintainers, stargazers, watchers, issue/PR authors, fork owners, commit authors and dependents.


Table of Contents
=================
  * [Introduction](#introduction)
  * [Background](#background)
  * [Requirements](#requirements)
  * [Installation](#installation)
  * [Documentation](#documentation)
  * [Usage](#usage)
  * [Command Line](#command-line)
  * [Directories](#directories)
  * [Issues](#issues)
  * [License](#license)
  * [Contact](#contact)

---

## Introduction

**repo-people** provides a single-call pipeline to collect every GitHub user associated with a repository across 10 role categories, fetch 40+ profile fields for each person from the GitHub API, and export the results to JSON, CSV, Excel, Markdown or SQLite. It is designed for research, open-source community analysis, and developer intelligence workflows.

Key capabilities:
- Collects users from **10 role categories** in a single call
- Fetches **40+ profile fields** per user (bio, location, company, followers, orgs, languages, …)
- Computes derived metrics: account age, followers/following ratio, repos/year, recently-active flag, country code, bot detection
- **GraphQL fast paths** — all flat-list roles in one query, and PR reviewers ~100× cheaper than the REST N+1 walk. Falls back to REST automatically
- **On-disk ETag cache** — repeat runs send conditional requests, and GitHub's `304 Not Modified` responses do not count against your rate limit
- Incremental fetch with `save_each_iteration` and `resume` — safe to interrupt and restart on large repos
- Flexible filtering: `roles`, `exclude`, `exclude_bots`, `limit`, `fields`
- Concurrent fetching via `workers` — uses `ThreadPoolExecutor` to fetch multiple profiles in parallel
- Async fetching via `get_users_async()` — uses `asyncio` + `aiohttp`, with rate-limit back-off and bounded concurrency
- Opt-in social accounts via `include_social_accounts` — fetches linked LinkedIn, Mastodon, npm, and other accounts
- Export to **JSON**, **JSONL**, **CSV**, **Excel**, **Markdown** and **SQLite**
- Analysis helpers: `summarise()`, `top_users()`, `compare()` and `diff_snapshots()`
- Token resolved from the `token` argument or the `GITHUB_TOKEN` environment variable, and validated on startup — invalid or expired tokens raise `ConnectionError` immediately
- `owner` and `repo` validated on construction — invalid characters raise `ValueError` immediately
- Rate-limit progress printed every 50 users with remaining request count and reset time
- Role failures are isolated — one role failing returns an empty list for that role instead of discarding the whole collection
- CLI exits `2` when some profiles could not be fetched, so CI can detect an incomplete dataset

---

## Background

Understanding who contributes to, uses, and maintains an open-source project is valuable for community health analysis, academic research, and competitive intelligence. GitHub exposes this information across many endpoints (contributors, stargazers, watchers, forks, issues, pull requests, CODEOWNERS, commit history), but collecting and joining it requires many paginated API calls.

**repo-people** automates that collection, deduplicates users across all roles, enriches each record with the full GitHub profile, and computes additional signals (account age, activity recency, bot detection) in a single pipeline call.

---

## Requirements

- **[Python](https://www.python.org/)** ^3.10
- **[PyGithub](https://pygithub.readthedocs.io/en/latest/)** ^2.0.0 — GitHub API client
- **[requests](https://requests.readthedocs.io/en/latest/)** ^2.31.0 — HTTP requests for REST endpoints
- **[beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/)** ^4.12.0 — HTML scraping for dependents
- **[aiohttp](https://docs.aiohttp.org/en/stable/)** ^3.9 — *optional* (`[async]` extra), async HTTP client for `get_users_async()`
- **[openpyxl](https://openpyxl.readthedocs.io/)** ^3.1 — *optional* (`[excel]` extra), for `export_to_xlsx()`
- **[tqdm](https://tqdm.github.io/)** ^4.0 — *optional* (`[progress]` extra), for progress bars

SQLite export uses the standard library, so it needs no extra dependency.

A GitHub personal access token is strongly recommended. Unauthenticated requests are limited to 60/hour; authenticated requests allow 5,000/hour.

---

## Installation

Install the latest version of `repo-people` via [PyPi][PyPi] using pip:

```bash
pip3 install repo-people --upgrade
```

To enable the **async** pipeline (`get_users_async`) install with the `async` extra:
```bash
pip3 install "repo-people[async]"
```

Other optional extras — Excel export, progress bars, or everything at once:
```bash
pip3 install "repo-people[excel]"
pip3 install "repo-people[progress]"
pip3 install "repo-people[async,excel,progress]"
```

Installation from source:
```bash
git clone -b main https://github.com/amckenna41/repo-people.git
cd repo-people
pip3 install .
```


---

## Documentation

- [Read the Docs](https://repo-people.readthedocs.io/en/latest/) — full package documentation
- [FIELDS.md](FIELDS.md) — full reference table of every output field with descriptions
- [CHANGELOG.md](CHANGELOG.md) — version history and release notes
- [Deep Wiki][DeepWiki] — structured wiki of the repo, generated by DeepWiki

---

## Usage

### Quick Start

### How to get a GitHub Personal Access Token

1. Sign in to [github.com](https://github.com) and go to **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**.
2. Click **Generate new token (classic)**.
3. Give the token a descriptive name and set an expiration date.
4. Select the following scopes:
   - `repo` — read access to repository metadata, contributors, and collaborators
   - `read:user` — read user profile data
   - `read:org` — read organisation membership (needed for `public_orgs`)
5. Click **Generate token** and copy it immediately — it won't be shown again.
6. Store it securely (e.g. in an environment variable or a secrets manager) and pass it via the `token` parameter:

```python
import os
rp = RepoPeople("owner", "repo", token=os.environ["GITHUB_TOKEN"])
```

> **Tip:** Unauthenticated requests are limited to 60/hour. Authenticated requests allow 5,000/hour, making a token essential for any non-trivial repo.


```python
from repo_people import RepoPeople

rp = RepoPeople("owner", "repo", token="ghp_...")
user_data = rp.get_users(export=True)
# Returns a dict keyed by username, with 40+ profile fields per user
```

### Authentication

```python
import os
rp = RepoPeople("owner", "repo", token=os.environ["GITHUB_TOKEN"])

# Or rely on the environment — token=None reads GITHUB_TOKEN automatically
rp = RepoPeople("owner", "repo")
```

The token is resolved from the `token` argument first, then from the `GITHUB_TOKEN` environment variable. It is validated immediately on construction — an invalid or expired token raises `ConnectionError` before any collection begins. If neither source provides a token, a `UserWarning` is emitted up front noting the 60-requests/hour unauthenticated limit.

### `RepoPeople()` Constructor

```python
RepoPeople(owner, repo, token=None, outdir=None, skip_codeowners=False,
           skip_collaborators=False, use_cache=True, use_graphql=True)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `owner` | `str` | — | GitHub username or organisation that owns the repo. Must contain only `[A-Za-z0-9_.-]` characters; raises `ValueError` on construction otherwise. |
| `repo` | `str` | — | Repository name. Same character restrictions as `owner`. |
| `token` | `str \| None` | `None` | Personal access token. Falls back to the `GITHUB_TOKEN` environment variable. Validated immediately on init; raises `ConnectionError` for invalid tokens. |
| `outdir` | `str \| None` | `"outputs"` | Directory where all output files are written. |
| `skip_codeowners` | `bool` | `False` | Skip CODEOWNERS file when collecting maintainers. |
| `skip_collaborators` | `bool` | `False` | Skip repo collaborators when collecting maintainers. |
| `use_cache` | `bool` | `True` | Use the on-disk ETag cache, so unchanged pages come back as `304` responses that don't count against the rate limit. |
| `use_graphql` | `bool` | `True` | Use the GraphQL fast paths for role collection when a token is available. Falls back to REST automatically. |

### `get_users()` Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `export` | `bool` | `False` | Write results to a JSON file. |
| `export_csv` | `bool` | `False` | Write results to a CSV file. |
| `export_xlsx` | `bool` | `False` | Write results to an Excel file. Requires the `excel` extra. |
| `export_markdown` | `bool` | `False` | Write results to a Markdown table. |
| `export_sqlite` | `bool` | `False` | Write results to a SQLite database (upserts by `login`). |
| `save_each_iteration` | `bool` | `False` | Save after every 10 user fetches (batched to reduce I/O). Writes are atomic. |
| `limit` | `int \| None` | `None` | Cap the number of profiles to fetch.  Usernames are sorted alphabetically before the cap is applied, so results are deterministic. |
| `roles` | `list[str] \| None` | `None` (all 10) | Restrict which roles to collect. |
| `exclude` | `list[str] \| None` | `None` | Usernames to skip. |
| `exclude_bots` | `bool` | `False` | Skip bot accounts automatically (`[bot]`/`-bot` suffixes and `type=Bot`). |
| `resume` | `bool` | `False` | Skip users already in the output file. |
| `verbose` | `bool` | `True` | Print progress to stdout. |
| `fields` | `list[str] \| str \| None` | `None` (all) | Restrict which fields appear in output. Accepts any name in `RepoPeople.valid_fields()`, including `roles`. Invalid names raise `ValueError` before any fetch. |
| `include_social_accounts` | `bool` | `False` | Fetch each user's linked social accounts (LinkedIn, Mastodon, npm, …). Costs one extra API call per user. |
| `workers` | `int` | `1` | Number of concurrent fetch threads (max 32). Increase for faster collection on large repos. |
| `progress` | `bool` | `False` | Show a progress bar instead of per-user lines (needs `verbose=False` and the `progress` extra). |

Valid `roles` values: `contributors`, `maintainers`, `stargazers`, `watchers`, `issue_authors`, `pr_authors`, `pr_reviewers`, `fork_owners`, `commit_authors`, `dependents`.

### Examples

#### Filter by role

```python
# Only gather contributors and stargazers
user_data = rp.get_users(roles=["contributors", "stargazers"])
```

#### Limit, exclude, and skip bots

```python
user_data = rp.get_users(
    limit=100,
    exclude=["dependabot", "github-actions[bot]"],
    exclude_bots=True,
)
```

#### Export to JSON Lines (JSONL)

```python
# Write one JSON object per line (streaming-friendly)
rp.export_to_json(user_data, lines=True)
# or with a custom filename
rp.export_to_json(user_data, filename="users.jsonl", lines=True)
```

#### Export to JSON and CSV

```python
user_data = rp.get_users(export=True, export_csv=True)
```

#### Export to Markdown table

```python
rp.export_to_markdown(user_data, fields=["login", "name", "location", "followers"])
```

#### Resume an interrupted run

```python
# First run
rp.get_users(save_each_iteration=True, export=True)

# Resume after interruption
rp.get_users(save_each_iteration=True, export=True, resume=True)
```

#### Concurrent fetching

```python
# Speed up large repos by fetching profiles in parallel
user_data = rp.get_users(workers=4)
```

#### Async fetching

```python
import asyncio

user_data = asyncio.run(rp.get_users_async(concurrency=10))
```

`get_users_async()` accepts the same parameters as `get_users()` — including every
export format — except that `workers` is replaced by `concurrency` (capped at 32).
Rate-limit responses are retried with the same bounded back-off as the sync path.

#### Export to SQLite

```python
rp.get_users(export_sqlite=True)
```

Writes `outputs/<owner>_<repo>_user_details.db` with one row per user, keyed on
`login`. Uses the standard-library `sqlite3` module, so it adds no dependency.
List and dict fields are stored as JSON text, and booleans as `0`/`1` so SQL
comparisons work. Re-exporting **upserts** by `login`, so repeated runs accumulate
into one queryable table rather than overwriting:

```sql
SELECT location_country, COUNT(*) FROM users
WHERE recently_active = 1
GROUP BY location_country ORDER BY 2 DESC;
```

#### Caching (fewer API calls on repeat runs)

Every REST page's `ETag` is cached on disk and replayed as an `If-None-Match`
header on later runs. GitHub answers an unchanged page with `304 Not Modified`,
**which does not count against your rate limit** — so re-collecting the same
repository is both faster and nearly free.

```python
rp = RepoPeople("owner", "repo", token=..., use_cache=False)   # opt out
```

```bash
repo-people owner repo --no-cache      # skip the cache for this run
repo-people owner repo --clear-cache   # wipe the cache and exit
```

The cache lives in `$XDG_CACHE_HOME/repo-people` (`~/.cache/repo-people` by
default); override it with `REPO_PEOPLE_CACHE_DIR`. It holds only public API
responses — no tokens.

#### GraphQL fast paths

With a token, role collection uses GraphQL where it is cheaper, and falls back to
REST automatically if GraphQL is unavailable, unauthorised or errors:

- **`pr_reviewers`** — REST needs one extra call *per pull request*; GraphQL fetches
  100 PRs with their reviews nested per call. On a 500-PR repo that is ~500 calls
  down to ~5.
- **Flat-list roles** (`stargazers`, `watchers`, `fork_owners`, `issue_authors`,
  `pr_authors`) are fetched in a *single* query rather than one paginated REST walk
  each, so a repo with under 100 of each costs one call instead of five.

```python
rp = RepoPeople("owner", "repo", token=..., use_graphql=False)   # force REST
```

#### Include social accounts

```python
user_data = rp.get_users(include_social_accounts=True)
# Each record gains a 'social_accounts' dict, e.g. {'linkedin': 'https://linkedin.com/in/...'}
```

#### Dot-notation field access

`get_users()` returns a `UserDataView` — a plain `dict` subclass that additionally supports dot notation to extract a single field across every user at once:

```python
user_data = rp.get_users()

# Extract one field for all users
emails    = user_data.email_public
# {"alice": {"email_public": "alice@example.com"}, "bob": {"email_public": ""}, ...}

locations = user_data.location
followers = user_data.followers
roles     = user_data.roles
```

All standard `dict` operations still work unchanged. Accessing an unrecognised field name raises `AttributeError` listing the valid field names.

#### Analysis helpers

```python
stats = rp.summarise(user_data, top_n=5)
# {'total': 134, 'top_locations': [('san francisco', 18), ...],
#  'top_countries': [('US', 61), ('DE', 14), ...], ...}

leaders = rp.top_users(user_data, n=10, by="followers")
# by= must name a real field; a typo raises ValueError rather than
# silently ranking everyone as 0

# Compare two repositories' populations
diff = rp_a.compare(rp_b, data_a, data_b)   # only_in_self / only_in_other / in_both

# Track churn between two snapshots (dicts or paths to exported JSON)
churn = RepoPeople.diff_snapshots("snapshot_jan.json", "snapshot_feb.json")
print(churn["joined"], churn["left"])
```

`summarise()` reports `top_countries` alongside `top_locations`, which aggregates
the free-text location field — so `"SF"`, `"San Francisco"` and
`"san francisco, ca"` count as one country instead of three separate locations.
See the `location_country` note in [FIELDS.md](FIELDS.md) for its limits.

---

## Command Line

```bash
repo-people <owner> <repo> [options]
```

```bash
# Collect everything and write JSON + CSV
repo-people torvalds linux --export-json --export-csv

# Just contributors and stargazers, capped, with a summary
repo-people psf cpython --roles contributors stargazers --limit 50 --summarise

# Async pipeline, SQLite output, progress bar instead of per-user lines
repo-people amckenna41 iso3166-2 --async --concurrency 20 --export-sqlite \
  --no-verbose --progress

# Interrupt-safe long run, then resume it
repo-people torvalds linux --save-each-iteration --export-json
repo-people torvalds linux --save-each-iteration --export-json --resume
```

The token comes from `--token` or the `GITHUB_TOKEN` environment variable.
Run `repo-people --help` for the full flag list.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Every requested profile was collected. |
| `1` | A usage, validation or connection error prevented the run. |
| `2` | The run completed, but some profiles could not be fetched. |

Exit code `2` is what makes the CLI usable in CI — a partially collected dataset
no longer looks like success. The affected logins are listed on stderr and are
available programmatically as `rp.last_failed`.

### Output Fields

Each user entry contains 40+ fields. See [FIELDS.md](FIELDS.md) for the full reference. A summary by category:

| Category | Fields |
|---|---|
| Identity | `login`, `name`, `company`, `location`, `email_public`, `blog`, `twitter`, `bio` |
| Timestamps | `created_at`, `updated_at` |
| Counters | `followers`, `following`, `public_repos`, `public_gists` |
| Flags | `has_public_email`, `has_blog`, `has_twitter`, `is_bot`, `hireable` |
| Computed | `account_age_days`, `followers_following_ratio`, `repos_per_year`, `recently_active` (based on `last_public_event_at`, not `updated_at`), `last_public_event_at`, `location_country` |
| Organisations | `public_orgs`, `orgs_public_count` |
| Sampled | `top_languages`, `total_public_stars_sampled`, `total_public_forks_sampled`, `ssh_keys_count`, `gpg_keys_count`, `starred_repos_sampled` |
| Social | `social_accounts` (opt-in via `include_social_accounts`) |
| Repo-specific | `is_collaborator`, `permission_on_repo` |
| Metadata | `roles` (populated by `get_users()`) |

---

## Directories

```
repo-people/
├── repo_people/          # Package source
│   ├── __init__.py
│   ├── repo_people.py    # RepoPeople class — main pipeline
│   ├── cli.py            # Command-line interface
│   ├── export.py         # Role-specific username collectors + GraphQL fast paths
│   ├── users.py          # GitHubUserInfo wrapper and UserSnapshot dataclass
│   └── utils.py          # paginate(), ETag cache, graphql(), normalize_country()
├── tests/                # Unit and integration tests
│   ├── test_repo_people.py
│   ├── test_export.py
│   ├── test_users.py
│   └── test_cli.py
├── docs/                 # Sphinx documentation source
├── outputs/              # Default output directory (created at runtime)
├── FIELDS.md             # Full output field reference
├── CHANGELOG.md          # Version history
├── pyproject.toml        # Package metadata and dependencies
└── README.md
```


## Issues
Any issues, errors or bugs can be raised via the [Issues](https://github.com/amckenna41/repo-people/issues) tab in the repository.

## Contact
If you have any questions or comments, please contact amckenna41@qub.ac.uk or raise an issue on the [Issues][Issues] tab. <br><br>

## License
Distributed under the MIT License. See [`LICENSE`][license] for more details. 



[<img src="https://img.shields.io/github/stars/amckenna41/repo-people?color=green&label=star%20it%20on%20GitHub" width="132" height="20" alt="Star it on GitHub">](https://github.com/amckenna41/repo-people)


<a href="https://www.buymeacoffee.com/amckenna41" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="41" width="174"></a>

[Back to top](#TOP)

[PyPi]: https://pypi.org/project/repo-people
[Issues]: https://github.com/amckenna41/repo-people/issues
[license]: https://github.com/amckenna41/repo-people/blob/master/LICENSE
[DeepWiki]: https://deepwiki.com/amckenna41/repo-people
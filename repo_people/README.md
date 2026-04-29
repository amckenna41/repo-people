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
# Returns a dict keyed by username, with 30+ profile fields per user
```

### Authentication

```python
import os
rp = RepoPeople("owner", "repo", token=os.environ["GITHUB_TOKEN"])
```

The token is validated immediately on construction — an invalid or expired token raises `ConnectionError` before any collection begins.

### `RepoPeople()` Constructor

```python
RepoPeople(owner, repo, token=None, outdir=None, skip_codeowners=False, skip_collaborators=False)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `owner` | `str` | — | GitHub username or organisation that owns the repo. |
| `repo` | `str` | — | Repository name. |
| `token` | `str \| None` | `None` | Personal access token. Strongly recommended — validated immediately on init; raises `ConnectionError` for invalid tokens. |
| `outdir` | `str \| None` | `"{owner}_{repo}"` | Leaf directory inside `outputs/`. All output files are written under `outputs/{outdir}/`. |
| `skip_codeowners` | `bool` | `False` | Skip CODEOWNERS file when collecting maintainers. |
| `skip_collaborators` | `bool` | `False` | Skip repo collaborators when collecting maintainers. |

### `get_users()` Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `export` | `bool` | `False` | Write results to a JSON file. |
| `export_csv` | `bool` | `False` | Write results to a CSV file. |
| `save_each_iteration` | `bool` | `False` | Save after every single user fetch. |
| `limit` | `int \| None` | `None` | Cap the number of profiles to fetch. |
| `roles` | `list[str] \| None` | `None` (all 9) | Restrict which roles to collect. |
| `exclude` | `list[str] \| None` | `None` | Usernames to skip. |
| `exclude_bots` | `bool` | `False` | Skip bot accounts automatically. |
| `resume` | `bool` | `False` | Skip users already in the output file. |
| `verbose` | `bool` | `True` | Print progress to stdout. |
| `fields` | `list[str] \| str \| None` | `None` (all) | Restrict which fields appear in output. Invalid names raise `ValueError` before any fetch. |
| `include_social_accounts` | `bool` | `False` | Fetch each user's linked social accounts (LinkedIn, Mastodon, npm, …). Costs one extra API call per user. |
| `workers` | `int` | `1` | Number of concurrent fetch threads. Increase for faster collection on large repos. |

Valid `roles` values: `contributors`, `maintainers`, `stargazers`, `watchers`, `issue_authors`, `pr_authors`, `fork_owners`, `commit_authors`, `dependents`.

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
# {'total': 134, 'top_locations': [('San Francisco', 18), ...], ...}

leaders = rp.top_users(user_data, n=10, by="followers")
```

### Output Fields

Each user entry contains 30+ fields. See [FIELDS.md](FIELDS.md) for the full reference. A summary by category:

| Category | Fields |
|---|---|
| Identity | `login`, `name`, `company`, `location`, `email_public`, `blog`, `twitter`, `bio` |
| Timestamps | `created_at`, `updated_at` |
| Counters | `followers`, `following`, `public_repos`, `public_gists` |
| Flags | `has_public_email`, `has_blog`, `has_twitter`, `is_bot`, `hireable` |
| Computed | `account_age_days`, `followers_following_ratio`, `repos_per_year`, `recently_active`, `last_public_event_at` |
| Organisations | `public_orgs`, `orgs_public_count` |
| Sampled | `top_languages`, `total_public_stars_sampled`, `total_public_forks_sampled`, `ssh_keys_count`, `gpg_keys_count`, `starred_repos_sampled` |
| Social | `social_accounts` (opt-in via `include_social_accounts`) |
| Repo-specific | `is_collaborator`, `permission_on_repo` |
| Metadata | `roles` (populated by `get_users()`) |

---
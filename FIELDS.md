# Output Fields Reference

All GitHub profile fields available for export:

| Field | Description |
|---|---|
| `login` | GitHub username (login handle). |
| `id` | GitHub numeric user ID. |
| `node_id` | GitHub GraphQL node ID. |
| `type` | Account type — `"User"`, `"Organization"` or `"Bot"`. |
| `name` | Display name as set on the GitHub profile. |
| `company` | Company field as entered on the GitHub profile (raw, may include `@` prefix). |
| `company_normalized` | Company name with leading `@` stripped and whitespace normalised. |
| `location` | Location field as entered on the GitHub profile (raw string). |
| `location_normalized` | Location string lowercased and trimmed for consistent comparison. |
| `location_country` | Best-effort ISO 3166-1 alpha-2 country code derived from `location` (e.g. `"US"`, `"GB"`). Empty string when the location is blank or could not be recognised — treat `""` as *unknown*, not as "no country". A heuristic lookup over country names, major cities and US/Canadian subdivision codes, **not** a geocoder. |
| `email_public` | Publicly visible email address, or empty string if not set. |
| `email_domain` | Domain portion of `email_public` (e.g. `gmail.com`), or empty string. |
| `blog` | Blog or website URL as entered on the GitHub profile. |
| `blog_host` | Hostname extracted from `blog` (e.g. `dev.to`), or empty string. |
| `twitter` | Twitter/X username as entered on the GitHub profile (without `@`). |
| `bio` | Profile bio text. |
| `avatar_url` | URL of the user's GitHub avatar image. |
| `html_url` | URL of the user's GitHub profile page. |
| `hireable` | `True` if the user has marked themselves as available for hire. GitHub returns `null` when unset; this is coerced to `False` in both the sync and async paths. |
| `site_admin` | `True` if the user is a GitHub site administrator. |
| `created_at` | ISO 8601 timestamp of when the GitHub account was created. |
| `updated_at` | ISO 8601 timestamp of the most recent profile update. |
| `followers` | Number of GitHub followers. |
| `following` | Number of accounts the user is following. |
| `public_repos` | Number of public repositories owned by the user. |
| `public_gists` | Number of public gists owned by the user. |
| `public_orgs` | List of organisation login strings the user publicly belongs to. |
| `orgs_public_count` | Count of organisations in `public_orgs`. |
| `is_bot` | `True` if the account is detected as a bot: account type `"Bot"`, or a login ending in `[bot]` or `-bot`. |
| `has_public_email` | `True` if `email_public` is non-empty. |
| `has_blog` | `True` if `blog` is non-empty. |
| `has_twitter` | `True` if `twitter` is non-empty. |
| `last_public_event_at` | ISO 8601 timestamp of the user's most recent public GitHub event, if available. |
| `account_age_days` | Number of days since the account was created (computed from `created_at`). |
| `followers_following_ratio` | `followers / following`, or `followers` when `following` is zero. Rounded to 2 decimal places. |
| `repos_per_year` | `public_repos / account_age_years`, where a year is 365.25 days and the divisor is clamped to a minimum of 1 year (so a week-old account with 5 repos reports `5.0`, not `260.0`). Rounded to 2 decimal places. Identical in the sync and async paths. |
| `recently_active` | `True` if `last_public_event_at` is within the last 90 days. |
| `top_languages` | List of up to three `(language, repo_count)` tuples sampled from the user's owned repos, most-used first. `None` if not computed. |
| `total_public_stars_sampled` | Total stars received across a sample of the user's public repos. `None` if not computed. |
| `total_public_forks_sampled` | Total forks received across a sample of the user's public repos. `None` if not computed. |
| `ssh_keys_count` | Number of public SSH keys on the account. `None` if not fetched. |
| `gpg_keys_count` | Number of GPG keys on the account. `None` if not fetched. |
| `starred_repos_sampled` | Count of repos starred by the user (sampled). `None` if not fetched. |
| `social_accounts` | Dict mapping provider name to URL (e.g. `{"linkedin": "https://linkedin.com/in/..."}`) populated when `include_social_accounts=True`. `None` otherwise. |
| `is_collaborator` | `True`/`False` indicating whether the user is a collaborator on the target repo. `None` if not checked or insufficient permissions. |
| `permission_on_repo` | Permission level on the target repo: `"admin"`, `"maintain"`, `"write"`, `"triage"`, or `"read"`. `None` if not checked. |
| `roles` | List of role categories the user appeared under (e.g. `["contributors", "stargazers"]`). Always present in `get_users()` output. |

## Notes

- **`fields=` accepts every name in this table, including `roles`.** The allow-list
  is `RepoPeople.valid_fields()`.
- Fields marked *`None` if not computed* are opt-in because each costs extra API
  calls; see `snapshot()` in `repo_people/users.py` for the toggles.
- The async pipeline (`get_users_async`) leaves the sampled/aggregate fields as
  `None` and populates `social_accounts` only when `include_social_accounts=True`.
  Every other field is computed identically to the sync path.

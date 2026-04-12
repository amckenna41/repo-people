# Output Fields Reference

All GitHub profile fields available for export:

| Field | Description |
|---|---|
| `login` | GitHub username (login handle). |
| `id` | GitHub numeric user ID. |
| `node_id` | GitHub GraphQL node ID. |
| `type` | Account type — `"User"` or `"Organization"`. |
| `name` | Display name as set on the GitHub profile. |
| `company` | Company field as entered on the GitHub profile (raw, may include `@` prefix). |
| `company_normalized` | Company name with leading `@` stripped and whitespace normalised. |
| `location` | Location field as entered on the GitHub profile (raw string). |
| `location_normalized` | Location string lowercased and trimmed for consistent comparison. |
| `email_public` | Publicly visible email address, or empty string if not set. |
| `email_domain` | Domain portion of `email_public` (e.g. `gmail.com`), or empty string. |
| `blog` | Blog or website URL as entered on the GitHub profile. |
| `blog_host` | Hostname extracted from `blog` (e.g. `dev.to`), or empty string. |
| `twitter` | Twitter/X username as entered on the GitHub profile (without `@`). |
| `bio` | Profile bio text. |
| `avatar_url` | URL of the user's GitHub avatar image. |
| `html_url` | URL of the user's GitHub profile page. |
| `hireable` | `True` if the user has marked themselves as available for hire. |
| `site_admin` | `True` if the user is a GitHub site administrator. |
| `created_at` | ISO 8601 timestamp of when the GitHub account was created. |
| `updated_at` | ISO 8601 timestamp of the most recent profile update. |
| `followers` | Number of GitHub followers. |
| `following` | Number of accounts the user is following. |
| `public_repos` | Number of public repositories owned by the user. |
| `public_gists` | Number of public gists owned by the user. |
| `public_orgs` | List of organisation login strings the user publicly belongs to. |
| `orgs_public_count` | Count of organisations in `public_orgs`. |
| `is_bot` | `True` if the account is detected as a bot (type `"Bot"` or login ending in `[bot]`). |
| `has_public_email` | `True` if `email_public` is non-empty. |
| `has_blog` | `True` if `blog` is non-empty. |
| `has_twitter` | `True` if `twitter` is non-empty. |
| `last_public_event_at` | ISO 8601 timestamp of the user's most recent public GitHub event, if available. |
| `account_age_days` | Number of days since the account was created (computed from `created_at`). |
| `followers_following_ratio` | `followers / following`, or `followers` when `following` is zero. Rounded to 2 decimal places. |
| `repos_per_year` | `public_repos / account_age_years`. Rounded to 2 decimal places. |
| `recently_active` | `True` if `updated_at` is within the last 90 days. |
| `top_languages` | List of `(language, byte_count)` tuples sampled from the user's public repos, ordered by frequency. `None` if not computed. |
| `total_public_stars_sampled` | Total stars received across a sample of the user's public repos. `None` if not computed. |
| `total_public_forks_sampled` | Total forks received across a sample of the user's public repos. `None` if not computed. |
| `ssh_keys_count` | Number of public SSH keys on the account. `None` if not fetched. |
| `gpg_keys_count` | Number of GPG keys on the account. `None` if not fetched. |
| `starred_repos_sampled` | Count of repos starred by the user (sampled). `None` if not fetched. |
| `social_accounts` | Dict mapping provider name to URL (e.g. `{"linkedin": "https://linkedin.com/in/..."}`) populated when `include_social_accounts=True`. `None` otherwise. |
| `is_collaborator` | `True`/`False` indicating whether the user is a collaborator on the target repo. `None` if not checked or insufficient permissions. |
| `permission_on_repo` | Permission level on the target repo: `"admin"`, `"maintain"`, `"write"`, `"triage"`, or `"read"`. `None` if not checked. |
| `roles` | List of role categories the user appeared under (e.g. `["contributors", "stargazers"]`). Always present in `get_users()` output. |

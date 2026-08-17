API Reference
=============

RepoPeople
----------

.. autoclass:: repo_people.RepoPeople
   :members:
   :undoc-members: False
   :show-inheritance:

.. rubric:: Valid roles

.. code-block:: python

   RepoPeople.VALID_ROLES == {
       "contributors",
       "maintainers",
       "stargazers",
       "watchers",
       "issue_authors",
       "pr_authors",
       "pr_reviewers",
       "fork_owners",
       "commit_authors",
       "dependents",
   }

.. rubric:: Representation

``repr(rp)`` returns a concise summary of the instance:

.. code-block:: python

   repr(rp)
   # "RepoPeople(owner='alice', repo='myrepo', outdir='outputs', valid_roles=10)"

The token is deliberately omitted from ``repr()``.

.. rubric:: ``roles`` key in output

:meth:`~repo_people.RepoPeople.get_users` always adds a ``"roles"`` key to
every user record, regardless of any ``fields=`` filter. It lists the
role(s) that user appeared under:

.. code-block:: python

   user_data = rp.get_users()
   user_data["octocat"]["roles"]  # e.g. ['contributors', 'stargazers']

.. note::

   ``"roles"`` is **not** a :class:`~repo_people.users.UserSnapshot` field —
   it is injected by ``get_users`` after profile fetching, so it does not appear
   in the snapshot field table below. It *is* accepted by ``fields=``: the
   allow-list is :meth:`~repo_people.RepoPeople.valid_fields`, which is the union
   of the snapshot fields and ``"roles"``.


UserSnapshot
------------

.. autoclass:: repo_people.users.UserSnapshot
   :members:
   :undoc-members: True

The following table lists every field returned in a ``UserSnapshot`` (and in
every ``dict`` entry of the ``user_data`` mapping produced by
:meth:`~repo_people.RepoPeople.get_users` /
:meth:`~repo_people.RepoPeople.get_user_details`).

.. list-table:: UserSnapshot fields
   :header-rows: 1
   :widths: 25 15 60

   * - Field
     - Type
     - Description
   * - ``login``
     - ``str``
     - GitHub username.
   * - ``id``
     - ``int | None``
     - Numeric GitHub user ID.
   * - ``node_id``
     - ``str``
     - Global node ID (GraphQL).
   * - ``type``
     - ``str``
     - Account type — ``"User"``, ``"Organization"`` or ``"Bot"``.
   * - ``name``
     - ``str``
     - Display name on their profile.
   * - ``company``
     - ``str``
     - Raw company string from their profile.
   * - ``company_normalized``
     - ``str``
     - Company with leading ``@`` stripped and whitespace trimmed.
   * - ``location``
     - ``str``
     - Raw location string from their profile.
   * - ``location_normalized``
     - ``str``
     - Location trimmed and lowercased for consistent grouping.
   * - ``location_country``
     - ``str``
     - Best-effort ISO 3166-1 alpha-2 country code from ``location``. Empty
       string means *unknown*, not "no country".
   * - ``email_public``
     - ``str``
     - Public e-mail address (empty string if not set).
   * - ``email_domain``
     - ``str``
     - Domain part of ``email_public``, e.g. ``"gmail.com"``.
   * - ``has_public_email``
     - ``bool``
     - ``True`` when ``email_public`` is non-empty.
   * - ``blog``
     - ``str``
     - Blog / website URL from their profile.
   * - ``blog_host``
     - ``str``
     - Hostname extracted from ``blog``.
   * - ``has_blog``
     - ``bool``
     - ``True`` when ``blog`` is non-empty.
   * - ``twitter``
     - ``str``
     - Twitter / X username from their profile.
   * - ``has_twitter``
     - ``bool``
     - ``True`` when ``twitter`` is non-empty.
   * - ``bio``
     - ``str``
     - Profile bio text.
   * - ``avatar_url``
     - ``str``
     - URL of their profile avatar image.
   * - ``html_url``
     - ``str``
     - URL of their GitHub profile page.
   * - ``hireable``
     - ``bool``
     - Whether they have marked themselves as hireable.
   * - ``site_admin``
     - ``bool``
     - Whether they are a GitHub staff/site admin.
   * - ``created_at``
     - ``str``
     - ISO-8601 timestamp of account creation.
   * - ``updated_at``
     - ``str``
     - ISO-8601 timestamp of last profile update.
   * - ``followers``
     - ``int``
     - Number of GitHub followers.
   * - ``following``
     - ``int``
     - Number of accounts they follow.
   * - ``followers_following_ratio``
     - ``float``
     - ``followers / following``, or ``followers`` when ``following`` is 0.
   * - ``public_repos``
     - ``int``
     - Number of public repositories.
   * - ``public_gists``
     - ``int``
     - Number of public gists.
   * - ``public_orgs``
     - ``list[str]``
     - Logins of their public organisations.
   * - ``orgs_public_count``
     - ``int``
     - Length of ``public_orgs``.
   * - ``is_bot``
     - ``bool``
     - ``True`` when the account is detected as a bot.
   * - ``last_public_event_at``
     - ``str``
     - ISO-8601 timestamp of their most recent public event.
   * - ``account_age_days``
     - ``int``
     - Days since account creation.
   * - ``repos_per_year``
     - ``float``
     - ``public_repos`` per year (365.25 days), with the divisor clamped to a
       minimum of one year. Identical in the sync and async paths.
   * - ``recently_active``
     - ``bool``
     - ``True`` when ``last_public_event_at`` is within the last 90 days.
   * - ``top_languages``
     - ``list[tuple[str, int]] | None``
     - Up to three (language, repo-count) pairs from their owned repos.
   * - ``total_public_stars_sampled``
     - ``int | None``
     - Sum of stargazer counts across a sample of their public repos.
   * - ``total_public_forks_sampled``
     - ``int | None``
     - Sum of fork counts across a sample of their public repos.
   * - ``ssh_keys_count``
     - ``int | None``
     - Number of public SSH keys on their account.
   * - ``gpg_keys_count``
     - ``int | None``
     - Number of GPG keys on their account.
   * - ``starred_repos_sampled``
     - ``int | None``
     - Number of repos they have starred (sampled).
   * - ``social_accounts``
     - ``dict[str, str] | None``
     - Provider-to-URL map of linked social accounts. Populated only when
       ``include_social_accounts=True``.
   * - ``is_collaborator``
     - ``bool | None``
     - Whether they have collaborator access on the queried repository.
   * - ``permission_on_repo``
     - ``str | None``
     - Their permission level on the queried repo (e.g. ``"push"``).

----

GitHubUserInfo
--------------

.. autoclass:: repo_people.users.GitHubUserInfo
   :members:
   :undoc-members: False
   :show-inheritance:

----

Export Module
-------------

.. automodule:: repo_people.export
   :members:
   :undoc-members: False

Each function returns a list of strings (usernames) for *one* specific role.
They share a common signature shape:

.. code-block:: python

   export_<role>(
       owner: str,
       repo: str,
       token: str | None,
       outdir: str,
       return_data: bool = True,
       export_csv: bool = False,
       use_cache: bool = True,
   ) -> list[str]

``export_dependents`` takes no ``token`` (it scrapes the HTML dependents page)
and additionally accepts ``limit`` and ``sleep``. ``export_maintainers`` takes
``skip_codeowners`` and ``skip_collaborators``. ``export_pr_reviewers`` also
accepts ``use_graphql``.

.. note::

   ``return_data`` is retained for backwards compatibility and is ignored — the
   list is always returned.

.. list-table:: Export functions
   :header-rows: 1
   :widths: 30 70

   * - Function
     - Returns
   * - ``export_contributors``
     - Usernames of repository contributors.
   * - ``export_maintainers``
     - CODEOWNERS + collaborator usernames.
   * - ``export_stargazers``
     - Usernames who have starred the repository.
   * - ``export_watchers``
     - Usernames watching the repository.
   * - ``export_issue_authors``
     - Usernames who have opened issues.
   * - ``export_pr_authors``
     - Usernames who have opened pull requests.
   * - ``export_fork_owners``
     - Usernames who have forked the repository.
   * - ``export_pr_reviewers``
     - Usernames who have reviewed a pull request. Uses GraphQL when a token is
       available (one call per 100 PRs instead of one call per PR), falling back
       to REST otherwise.
   * - ``export_commit_authors``
     - Usernames extracted from commit history. An alias of
       ``export_contributors``; the commit walk is memoised per repository so
       requesting both roles costs one pass.
   * - ``export_dependents``
     - Usernames of repositories that depend on this one (HTML scrape).

----

Utils Module
------------

.. automodule:: repo_people.utils
   :members: validate_owner_repo, normalize_country, paginate, graphql, clear_cache, write_csv, extract_token
   :undoc-members: False

.. rubric:: HTTP response cache

:func:`~repo_people.utils.paginate` stores each page's ``ETag`` on disk and
replays it as an ``If-None-Match`` header on subsequent runs. GitHub answers an
unchanged page with ``304 Not Modified``, which does **not** count against the
rate limit.

The cache directory is ``$REPO_PEOPLE_CACHE_DIR`` if set, otherwise
``$XDG_CACHE_HOME/repo-people`` (defaulting to ``~/.cache/repo-people``). Tokens
are never stored, and the cache key deliberately excludes the token so entries
are reusable across them. Cached *bodies* can still include private-repository
membership, so the directory is created ``0700`` and entries are written
``0600`` rather than inheriting the ambient umask. Clear it with
:func:`~repo_people.utils.clear_cache` or ``repo-people --clear-cache``.

.. rubric:: Rate limits versus authorisation failures

GitHub returns 403 both when a rate limit is exhausted and when a request is
simply not permitted. :func:`~repo_people.utils.paginate` retries only the
former, identified by an exhausted ``X-RateLimit-Remaining``, a ``Retry-After``
header, or a rate-limit message in the body. A permission 403 — a SAML-protected
organisation, or a token missing a scope — raises immediately instead of being
slept on and retried.

.. rubric:: Country normalisation

:func:`~repo_people.utils.normalize_country` is a heuristic lookup over country
names, common abbreviations, major cities and US/Canadian subdivision codes — it
is **not** a geocoder. Unrecognised input returns ``""``, which callers must read
as *unknown*.

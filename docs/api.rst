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
       "fork_owners",
       "commit_authors",
       "dependents",
   }

.. rubric:: Representation

``repr(rp)`` returns a concise summary of the instance:

.. code-block:: python

   repr(rp)
   # "RepoPeople(owner='alice', repo='myrepo', outdir='outputs/alice_myrepo', valid_roles=9)"

.. rubric:: ``roles`` key in output

:meth:`~repo_people.RepoPeople.get_users` always adds a ``"roles"`` key to
every user record, regardless of any ``fields=`` filter. It lists the
role(s) that user appeared under:

.. code-block:: python

   user_data = rp.get_users()
   user_data["octocat"]["roles"]  # e.g. ['contributors', 'stargazers']

.. note::

   ``"roles"`` is **not** a :class:`~repo_people.users.UserSnapshot` field —
   it is injected by ``get_users`` after profile fetching. It will therefore
   not appear in the snapshot field table below.


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
     - Account type — ``"User"`` or ``"Bot"``.
   * - ``name``
     - ``str``
     - Display name on their profile.
   * - ``company``
     - ``str``
     - Raw company string from their profile.
   * - ``company_normalized``
     - ``str``
     - Company with leading ``@`` stripped and lowercased.
   * - ``location``
     - ``str``
     - Raw location string from their profile.
   * - ``location_normalized``
     - ``str``
     - Location stripped of trailing country codes.
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
     - ``followers / following`` (0 when ``following`` is 0).
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
     - ``public_repos / (account_age_days / 365)``.
   * - ``recently_active``
     - ``bool``
     - ``True`` when ``last_public_event_at`` is within the last 90 days.
   * - ``top_languages``
     - ``list[tuple[str, int]] | None``
     - Sampled (language, byte-count) pairs from their public repos.
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
All nine functions share the same signature:

.. code-block:: python

   export_<role>(gh: Github, owner: str, repo: str) -> list[str]

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
   * - ``export_commit_authors``
     - Usernames extracted from commit history.
   * - ``export_dependents``
     - Usernames of repositories that depend on this one.

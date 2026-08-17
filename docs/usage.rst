Usage
=====

Installation
------------

Install from PyPI:

.. code-block:: console

   pip install repo-people

Or with Poetry:

.. code-block:: console

   poetry add repo-people

Quick Start
-----------

The simplest end-to-end call collects users from all ten role categories,
fetches their full GitHub profiles, and returns a dictionary keyed by username:

.. code-block:: python

   from repo_people import RepoPeople

   rp = RepoPeople("octocat", "Hello-World", token="ghp_...")
   user_data = rp.get_users()
   # {'octocat': {'login': 'octocat', 'followers': 9001, ...}, ...}

Authentication
--------------

A GitHub personal-access token is strongly recommended. Without one, the GitHub
API rate-limit is only 60 requests per hour. With a token it rises to 5 000
requests per hour.

.. code-block:: python

   rp = RepoPeople("owner", "repo", token="ghp_YOUR_TOKEN_HERE")

Alternatively, export the token as an environment variable and pass it in:

.. code-block:: python

   import os
   from repo_people import RepoPeople

   rp = RepoPeople("owner", "repo", token=os.environ["GITHUB_TOKEN"])

Tip — store your token in a ``.env`` file and load it with ``python-dotenv``:

.. code-block:: python

   from dotenv import load_dotenv
   load_dotenv()
   rp = RepoPeople("owner", "repo", token=os.environ["GITHUB_TOKEN"])

Token Validation
----------------

The token is validated immediately when ``RepoPeople`` is instantiated. If the
token is invalid or expired, a ``ConnectionError`` is raised right away with a
descriptive message rather than failing silently on the first API call:

.. code-block:: python

   try:
       rp = RepoPeople("owner", "repo", token="invalid_token")
   except ConnectionError as e:
       print(e)  # GitHub connection failed — verify your token. (...)

Input Validation
----------------

The ``owner`` and ``repo`` parameters are validated at construction time.
Both must contain only ``[A-Za-z0-9_.-]`` characters. Any other characters
raise a ``ValueError`` immediately:

.. code-block:: python

   try:
       rp = RepoPeople("owner with spaces", "repo")
   except ValueError as e:
       print(e)  # Invalid owner: 'owner with spaces'. Must match [A-Za-z0-9_.-]+

Choosing an Output Directory
-----------------------------

By default, exported files are written to the current working directory. Use
``outdir`` to specify a different location:

.. code-block:: python

   rp = RepoPeople("owner", "repo", token="...", outdir="/path/to/output")

Filtering by Role
-----------------

The ``roles`` parameter accepts a list of one or more of the ten valid roles.
All ten roles are collected when ``roles`` is not specified:

.. code-block:: python

   # Only contributors and stargazers
   user_data = rp.get_users(roles=["contributors", "stargazers"])

Available roles:

* ``contributors``
* ``maintainers``  (CODEOWNERS + collaborators)
* ``stargazers``
* ``watchers``
* ``issue_authors``  (issues only — pull requests belong to ``pr_authors``)
* ``pr_authors``
* ``pr_reviewers``  (anyone who reviewed a pull request)
* ``fork_owners``
* ``commit_authors``  (an alias of ``contributors``; the commit walk is shared,
  so requesting both costs one pass, not two)
* ``dependents``

.. code-block:: python

   # Inspect the full set at runtime
   print(RepoPeople.VALID_ROLES)

Role names are validated **before any API calls** are made. Passing an
unrecognised name raises ``ValueError`` immediately, listing every invalid name
and the full set of valid ones:

.. code-block:: python

   rp.get_users(roles=["typo_role"])
   # ValueError: Invalid role(s): ['typo_role'].
   # Valid roles are: ['commit_authors', 'contributors', ...]

A bare string is also accepted and treated as a single-item list:

.. code-block:: python

   user_data = rp.get_users(roles="contributors")

Skipping CODEOWNERS or Collaborators
--------------------------------------

When collecting ``maintainers`` the package looks up both the ``CODEOWNERS``
file and the repository's collaborator list. Either source can be disabled:

.. code-block:: python

   rp = RepoPeople("owner", "repo", token="...",
                   skip_codeowners=True,
                   skip_collaborators=True)

Limiting the Number of Results
-------------------------------

``limit`` caps the total number of user profiles fetched. Useful for quickly
testing on large repositories:

.. code-block:: python

   user_data = rp.get_users(limit=50)

Excluding Users
---------------

Pass a list of usernames to ``exclude`` to skip specific accounts:

.. code-block:: python

   user_data = rp.get_users(exclude=["dependabot", "github-actions[bot]"])

To automatically skip all bot accounts (those whose GitHub ``type`` field is
``"Bot"`` or whose login matches common bot patterns):

.. code-block:: python

   user_data = rp.get_users(exclude_bots=True)

Incremental Fetching (Resume Support)
--------------------------------------

For large repositories the fetch can take a long time. Use
``save_each_iteration=True`` to persist progress in batches of 10 user
profiles. If the process is interrupted, restart with ``resume=True`` to
pick up from where you left off:

.. code-block:: python

   # First run — saves after every user
   user_data = rp.get_users(save_each_iteration=True, export=True)

   # Restart after interruption — skips users already in the output file
   user_data = rp.get_users(save_each_iteration=True, export=True, resume=True)

Filtering the Output Fields
-----------------------------

By default all 40+ fields are included for every user. Pass a list of field
names to ``fields`` to limit what appears in exports and the returned dict:

.. code-block:: python

   user_data = rp.get_users(
       fields=["login", "name", "location", "followers", "public_repos"]
   )

A bare string is also accepted and is treated as a single-item list:

.. code-block:: python

   user_data = rp.get_users(fields="login")

Field names are validated against :meth:`~repo_people.RepoPeople.valid_fields`
(the fields of :class:`~repo_people.users.UserSnapshot` plus ``"roles"``)
**before any API calls are made**. Passing an unrecognised name raises a
``ValueError`` immediately, listing every invalid name and the full set of
valid ones:

.. code-block:: python

   rp.get_users(fields=["login", "typo_field"])
   # ValueError: Invalid field(s): ['typo_field'].
   # Valid fields are: ['account_age_days', 'avatar_url', 'bio', ...]

See the :doc:`api` page for the complete field list.

Roles in Output Records
-----------------------

Every user dict returned by ``get_users`` always has a ``"roles"`` key listing
the role(s) the user appeared under, regardless of any ``fields=`` filter:

.. code-block:: python

   user_data = rp.get_users(roles=["contributors", "stargazers"], fields=["login"])
   print(user_data["octocat"])
   # {'login': 'octocat', 'roles': ['contributors', 'stargazers']}

Exporting Results
-----------------

Export to JSON
~~~~~~~~~~~~~~

Pass ``export=True`` to write a JSON file automatically. The file is saved to
``outdir`` (or the current directory) as ``user_details.json``:

.. code-block:: python

   user_data = rp.get_users(export=True)

To export manually after the fact:

.. code-block:: python

   rp.export_to_json(user_data, filename="my_output.json")

Export to CSV
~~~~~~~~~~~~~

Pass ``export_csv=True`` to write a CSV file:

.. code-block:: python

   user_data = rp.get_users(export_csv=True)

Or manually:

.. code-block:: python

   rp.export_to_csv(user_data, filename="my_output.csv")

Export to Markdown
~~~~~~~~~~~~~~~~~~

Generate a Markdown table with (optionally) a subset of fields:

.. code-block:: python

   rp.export_to_markdown(
       user_data,
       filename="users.md",
       fields=["login", "name", "location", "followers"]
   )

Both ``export=True`` and ``export_csv=True`` can be combined:

.. code-block:: python

   user_data = rp.get_users(export=True, export_csv=True)

Analysis Helpers
----------------

summarise
~~~~~~~~~

Returns aggregate statistics for the collected user data:

.. code-block:: python

   stats = rp.summarise(user_data, top_n=5)
   # {
   #   'total': 134,
   #   'humans': 120,
   #   'bots': 14,
   #   'top_locations': [('san francisco', 18), ...],
   #   'top_companies': [('GitHub', 9), ...],
   #   'top_countries': [('US', 61), ('DE', 14), ...],
   #   'account_age_distribution': {'< 1 year': 5, '1–5 years': 40, ...},
   #   'role_distribution': {'contributors': 30, 'stargazers': 110, ...},
   # }

top_users
~~~~~~~~~

Returns the top *n* users ranked by a given field:

.. code-block:: python

   # Top 10 by follower count
   leaders = rp.top_users(user_data, n=10, by="followers")
   for u in leaders:
       print(u["login"], u["followers"])

   # Top 5 by number of public repos
   prolific = rp.top_users(user_data, n=5, by="public_repos")

``by`` must name a real field. A typo raises ``ValueError`` rather than silently
treating every user as ``0`` and returning an arbitrary order.

Using the Lower-Level API
--------------------------

The two-step pipeline is available directly if you need more control:

.. code-block:: python

   from repo_people import RepoPeople

   rp = RepoPeople("owner", "repo", token="...")

   # Step 1 — collect all usernames grouped by role
   all_usernames = rp.collect_all_usernames(roles=["contributors", "stargazers"])
   # {'contributors': ['alice', 'bob'], 'stargazers': ['carol', ...], ...}

   # Flatten to a unique set
   unique = list({u for users in all_usernames.values() for u in users})

   # Step 2 — fetch full profiles
   user_data = rp.get_user_details(
       unique,
       limit=100,
       exclude_bots=True,
       verbose=True,
   )

.. note::

   Role failures are isolated. If one role cannot be collected — a 5xx on
   ``/stargazers``, a scraping failure in ``dependents`` — a warning is printed
   and that role comes back as an empty list, while every other role's result
   survives. Check for empty lists if you need to distinguish "this repo has no
   stargazers" from "collecting them failed".

Rate-Limit Tips
---------------

* Always use a token — it gives you 5 000 requests/hour vs 60 unauthenticated.
* Use ``limit`` during development to avoid exhausting the rate limit on large repos.
* Use ``exclude_bots=True`` to skip bot accounts that do not need enrichment.
* Use ``save_each_iteration=True`` on very large repos so partial progress is
  persisted (every 10 profiles) if the rate limit is hit mid-run.
* ``resume=True`` allows you to continue after hitting a rate limit without
  re-fetching profiles already collected.
* Leave the on-disk ETag cache enabled (the default). Unchanged pages come back
  as ``304 Not Modified``, which GitHub does **not** charge against your limit.
* Keep ``use_graphql=True`` (the default) — collecting ``pr_reviewers`` over REST
  costs one extra call per pull request.
* A progress line is printed automatically every 50 users and at the end of
  the fetch, showing the current rate-limit headroom::

     [Progress: 50/134 | Rate limit: 4820/5000 remaining, resets in 42m]

* Any users that fail to fetch are collected and a summary is printed at the
  end rather than stopping the whole run::

     Skipped 2 user(s): ['ghost', 'deleted-account']

Concurrent Fetching
-------------------

The ``workers`` parameter controls how many profiles are fetched in parallel
(default ``1`` = sequential). Increasing it reduces wall-clock time on repos
with many users:

.. code-block:: python

   # Fetch up to 8 profiles simultaneously
   user_data = rp.get_users(workers=8)

Or pass it directly to the lower-level method:

.. code-block:: python

   user_data = rp.get_user_details(logins, workers=4)

.. note::

   Concurrent requests still count against your rate limit. ``workers``
   reduces wall-clock time by overlapping requests, not by increasing the
   total request budget.

   The maximum value for ``workers`` is **32**. If a higher value is passed,
   it is silently capped to 32 and a :class:`UserWarning` is emitted.

Async Fetching
--------------

For very high concurrency use the async pipeline. This requires the optional
``aiohttp`` dependency:

.. code-block:: console

   pip install "repo-people[async]"

Then:

.. code-block:: python

   import asyncio

   user_data = asyncio.run(rp.get_users_async(concurrency=10))

``get_users_async`` accepts the same arguments as
:meth:`~repo_people.RepoPeople.get_users` — including every export format and
``include_social_accounts`` — except that ``workers`` is replaced by
``concurrency``.

.. note::

   ``concurrency`` is capped at **32**, like ``workers``. Rate-limited responses
   (HTTP 403/429) are retried with bounded back-off, so a rate limit slows the
   run down rather than silently dropping users.

Exporting as JSON Lines (JSONL)
-------------------------------

Pass ``lines=True`` to :meth:`~repo_people.RepoPeople.export_to_json` to write
one JSON object per line (JSONL / JSON Lines format). This is useful for
streaming large outputs:

.. code-block:: python

   path = rp.export_to_json(user_data, lines=True)
   # Writes <outdir>/<prefix>user_details.jsonl

You can also specify a custom filename:

.. code-block:: python

   path = rp.export_to_json(user_data, filename="users.jsonl", lines=True)

Export to Excel
---------------

Requires the optional ``openpyxl`` dependency (``pip install "repo-people[excel]"``):

.. code-block:: python

   user_data = rp.get_users(export_xlsx=True)
   # or manually
   rp.export_to_xlsx(user_data, filename="users.xlsx")

Columns are the union of keys across every record, so a resumed run that merges
an older file does not silently lose fields.

Export to SQLite
----------------

SQLite export uses the standard-library ``sqlite3`` module, so it needs no extra
dependency:

.. code-block:: python

   user_data = rp.get_users(export_sqlite=True)
   # or manually, with a custom table name
   rp.export_to_sqlite(user_data, filename="people.db", table="users")

One row per user, keyed on ``login``. List and dict fields are stored as JSON
text; booleans are stored as ``0``/``1`` so SQL comparisons behave. Re-exporting
**upserts** by ``login``, so repeated runs accumulate into one queryable table:

.. code-block:: sql

   SELECT location_country, COUNT(*) AS people
   FROM users
   WHERE recently_active = 1
   GROUP BY location_country
   ORDER BY people DESC;

This is usually a better fit than CSV for longitudinal or comparative research,
since snapshots can be joined and filtered in SQL instead of reloading files.

Response Caching
----------------

Every REST page's ``ETag`` is cached on disk and replayed as an ``If-None-Match``
header on later runs. GitHub answers unchanged pages with ``304 Not Modified``,
which does **not** count against your rate limit — so re-collecting the same
repository is dramatically cheaper.

Caching is on by default. To disable it:

.. code-block:: python

   rp = RepoPeople("owner", "repo", token="...", use_cache=False)

The cache lives in ``$XDG_CACHE_HOME/repo-people`` (``~/.cache/repo-people`` by
default). Override the location with the ``REPO_PEOPLE_CACHE_DIR`` environment
variable, and clear it with:

.. code-block:: python

   from repo_people.utils import clear_cache
   clear_cache()

Only public API responses are cached — tokens are never written to disk, and the
cache key does not include the token, so entries are shared across tokens.

GraphQL Fast Paths
------------------

When a token is available, role collection uses GraphQL where it is cheaper, and
falls back to REST automatically if GraphQL is unavailable, unauthorised, or
returns an error:

* ``pr_reviewers`` — REST requires one extra call *per pull request*. GraphQL
  fetches 100 pull requests with their reviews nested per call, turning ~500 calls
  into ~5 on a 500-PR repository.
* The flat-list roles (``stargazers``, ``watchers``, ``fork_owners``,
  ``issue_authors``, ``pr_authors``) are fetched in a **single** query instead of
  one paginated REST walk each, so a repository with fewer than 100 of each costs
  one call rather than five.

.. code-block:: python

   rp = RepoPeople("owner", "repo", token="...", use_graphql=False)  # force REST

.. note::

   GraphQL rejects anonymous requests, so unauthenticated runs always use REST.

.. note::

   REST's ``/issues`` endpoint returns pull requests alongside issues, while
   GraphQL's ``issues`` connection does not. ``export_issue_authors`` filters
   pull requests out of the REST results so both paths agree that an "issue
   author" opened an issue, not a pull request. PR authors are available as the
   separate ``pr_authors`` role.

Progress Bars
-------------

With the optional ``tqdm`` dependency (``pip install "repo-people[progress]"``),
pass ``progress=True`` together with ``verbose=False`` to replace the per-user
output with a single progress bar:

.. code-block:: python

   user_data = rp.get_users(verbose=False, progress=True)

If ``tqdm`` is not installed the flag is ignored, so this never becomes a hard
dependency.

Comparing Repositories and Snapshots
------------------------------------

:meth:`~repo_people.RepoPeople.compare` diffs the people behind two repositories:

.. code-block:: python

   rp_a = RepoPeople("owner", "repo-a", token="...")
   rp_b = RepoPeople("owner", "repo-b", token="...")
   diff = rp_a.compare(rp_b, rp_a.get_users(), rp_b.get_users())
   print(diff["in_both"], diff["only_in_self"], diff["only_in_other"])

:meth:`~repo_people.RepoPeople.diff_snapshots` tracks churn over time. It accepts
either dicts or paths to previously exported JSON files:

.. code-block:: python

   churn = RepoPeople.diff_snapshots("snapshot_jan.json", "snapshot_feb.json")
   print(churn["joined"])     # appeared since the earlier snapshot
   print(churn["left"])       # present before, absent now
   print(churn["unchanged"])  # in both

Command-Line Interface
----------------------

.. code-block:: console

   repo-people <owner> <repo> [options]

.. code-block:: console

   # Collect everything, write JSON and CSV
   repo-people torvalds linux --export-json --export-csv

   # Restrict roles, cap the fetch, print a summary
   repo-people psf cpython --roles contributors stargazers --limit 50 --summarise

   # Async pipeline with SQLite output and a progress bar
   repo-people amckenna41 iso3166-2 --async --concurrency 20 \
       --export-sqlite --no-verbose --progress

   # Interrupt-safe long run, then resume it
   repo-people torvalds linux --save-each-iteration --export-json
   repo-people torvalds linux --save-each-iteration --export-json --resume

The token is read from ``--token`` or the ``GITHUB_TOKEN`` environment variable.
Run ``repo-people --help`` for the complete flag list.

Exit codes
~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 10 90

   * - Code
     - Meaning
   * - ``0``
     - Every requested profile was collected.
   * - ``1``
     - A usage, validation or connection error prevented the run.
   * - ``2``
     - The run completed, but some profiles could not be fetched.

Exit code ``2`` is what makes the CLI safe to use in CI: a partially collected
dataset no longer looks like success. The affected logins are printed to stderr
and are available programmatically as ``rp.last_failed``.

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

The simplest end-to-end call collects users from all nine role categories,
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

Choosing an Output Directory
-----------------------------

By default, exported files are written to the current working directory. Use
``outdir`` to specify a different location:

.. code-block:: python

   rp = RepoPeople("owner", "repo", token="...", outdir="/path/to/output")

Filtering by Role
-----------------

The ``roles`` parameter accepts a list of one or more of the nine valid roles.
All nine roles are collected when ``roles`` is not specified:

.. code-block:: python

   # Only contributors and stargazers
   user_data = rp.get_users(roles=["contributors", "stargazers"])

Available roles:

* ``contributors``
* ``maintainers``  (CODEOWNERS + collaborators)
* ``stargazers``
* ``watchers``
* ``issue_authors``
* ``pr_authors``
* ``fork_owners``
* ``commit_authors``
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
``save_each_iteration=True`` to write the result file after every single user
profile is fetched. If the process is interrupted, restart with
``resume=True`` to pick up from where you left off:

.. code-block:: python

   # First run — saves after every user
   user_data = rp.get_users(save_each_iteration=True, export=True)

   # Restart after interruption — skips users already in the output file
   user_data = rp.get_users(save_each_iteration=True, export=True, resume=True)

Filtering the Output Fields
-----------------------------

By default all 30+ fields are included for every user. Pass a list of field
names to ``fields`` to limit what appears in exports and the returned dict:

.. code-block:: python

   user_data = rp.get_users(
       fields=["login", "name", "location", "followers", "public_repos"]
   )

A bare string is also accepted and is treated as a single-item list:

.. code-block:: python

   user_data = rp.get_users(fields="login")

Field names are validated against :class:`~repo_people.users.UserSnapshot`
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
   #   'total_users': 134,
   #   'users_with_email': 42,
   #   'users_with_blog': 61,
   #   'top_locations': [('San Francisco', 18), ...],
   #   'top_companies': [('GitHub', 9), ...],
   #   'top_languages': [('Python', 54), ...],
   #   ...
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

Rate-Limit Tips
---------------

* Always use a token — it gives you 5 000 requests/hour vs 60 unauthenticated.
* Use ``limit`` during development to avoid exhausting the rate limit on large repos.
* Use ``exclude_bots=True`` to skip bot accounts that do not need enrichment.
* Use ``save_each_iteration=True`` on very large repos so partial progress is
  persisted if the rate limit is hit mid-run.
* ``resume=True`` allows you to continue after hitting a rate limit without
  re-fetching profiles already collected.* A progress line is printed automatically every 50 users and at the end of
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
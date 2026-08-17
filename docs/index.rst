repo-people
===========

**repo-people** is a Python package that collects and exports the full GitHub profile data
for every person associated with a repository — contributors, maintainers, stargazers,
watchers, issue/PR authors, fork owners, commit authors and dependents.

.. code-block:: python

   from repo_people import RepoPeople

   rp = RepoPeople("owner", "repo", token="ghp_...")
   user_data = rp.get_users(export=True)

Key features
------------

* Collects users from **10 role categories** in a single call.
* Fetches **40+ profile fields** per user (bio, location, company, followers, orgs, …).
* Computes derived metrics: account age, followers/following ratio, repos/year,
  recently active flag, country code, bot detection.
* **GraphQL fast paths** for role collection, with automatic REST fallback — PR
  reviewers cost roughly 100x fewer API calls than the REST N+1 walk.
* **On-disk ETag cache**: repeat runs issue conditional requests, and GitHub's
  ``304 Not Modified`` responses do not count against the rate limit.
* Incremental fetch with ``save_each_iteration=True`` and ``resume=True`` — safe to
  interrupt and restart on large repositories.
* Flexible filtering: ``roles``, ``exclude``, ``exclude_bots``, ``limit``, ``fields``.
* Export to **JSON**, **JSONL**, **CSV**, **Excel**, **Markdown** and **SQLite**.
* Analysis helpers: :meth:`~repo_people.RepoPeople.summarise`,
  :meth:`~repo_people.RepoPeople.top_users`,
  :meth:`~repo_people.RepoPeople.compare` and
  :meth:`~repo_people.RepoPeople.diff_snapshots`.

Installation
------------

.. code-block:: console

   pip install repo-people

Optional extras enable the async pipeline, Excel export and progress bars:

.. code-block:: console

   pip install "repo-people[async,excel,progress]"

.. toctree::
   :maxdepth: 2
   :caption: Contents

   usage
   api
   contributing

Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

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

* Collects users from **9 role categories** in a single call.
* Fetches **30+ profile fields** per user (bio, location, company, followers, orgs, …).
* Computes derived metrics: account age, followers/following ratio, repos/year,
  recently active flag, bot detection.
* Incremental fetch with ``save_each_iteration=True`` and ``resume=True`` — safe to
  interrupt and restart on large repositories.
* Flexible filtering: ``roles``, ``exclude``, ``exclude_bots``, ``limit``, ``fields``.
* Export to **JSON**, **CSV** and **Markdown** table.
* Analysis helpers: :meth:`~repo_people.RepoPeople.summarise` and
  :meth:`~repo_people.RepoPeople.top_users`.

Installation
------------

.. code-block:: console

   pip install repo-people

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

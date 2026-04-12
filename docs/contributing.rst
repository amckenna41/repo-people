Contributing
============

Thank you for your interest in contributing to **repo-people**!

Development Setup
-----------------

1. Clone the repository:

   .. code-block:: console

      git clone https://github.com/amckenna41/repo-people.git
      cd repo-people

2. Install dependencies with Poetry:

   .. code-block:: console

      poetry install

   Or with pip in a virtual environment:

   .. code-block:: console

      python -m venv .venv
      source .venv/bin/activate   # Windows: .venv\Scripts\activate
      pip install -r requirements.txt

3. Set your GitHub token (required for integration tests):

   .. code-block:: console

      export GITHUB_TOKEN="ghp_YOUR_TOKEN_HERE"

Running Tests
-------------

Run the full test suite (unit tests only — no network calls):

.. code-block:: console

   pytest tests/ -v

Run a specific test file:

.. code-block:: console

   pytest tests/test_repo_people.py -v

Run with coverage:

.. code-block:: console

   pytest tests/ --cov=repo_people --cov-report=term-missing

Integration Tests
~~~~~~~~~~~~~~~~~

Integration tests make real GitHub API calls and are skipped by default unless a
``GITHUB_TOKEN`` environment variable is set. They are decorated with
``@pytest.mark.skipif`` to guard against missing credentials:

.. code-block:: console

   GITHUB_TOKEN="ghp_..." pytest tests/ -v

Test Structure
--------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - File
     - Contents
   * - ``tests/test_repo_people.py``
     - Unit tests for :class:`~repo_people.RepoPeople` — constructor,
       ``collect_all_usernames``, ``get_user_details``, export, analysis helpers,
       and the ``get_users`` pipeline. All GitHub API calls are mocked.
   * - ``tests/test_users.py``
     - Unit tests for :class:`~repo_people.users.UserSnapshot` and
       :class:`~repo_people.users.GitHubUserInfo` — field defaults, property
       accessors, ``to_dict``, ``to_csv_row``, ``csv_headers``, bot detection.
   * - ``tests/test_export.py``
     - Unit tests (mocked) and integration tests (live API) for the nine
       ``export_*`` functions in :mod:`repo_people.export`.

Mocking Pattern
~~~~~~~~~~~~~~~

All unit tests patch the PyGithub client with ``unittest.mock.patch`` or
``unittest.mock.MagicMock``. The standard pattern used throughout the test
suite is:

.. code-block:: python

   from unittest.mock import patch, MagicMock

   @patch("repo_people.repo_people.Github")
   def test_something(self, mock_github):
       mock_gh = MagicMock()
       mock_github.return_value = mock_gh
       rp = RepoPeople("owner", "repo", token="test_token")
       # ... set up mock_gh.get_repo.return_value etc.

Code Style
----------

* Follow **PEP 8**. Use ``flake8`` or ``ruff`` to check your changes.
* Add a concise single-line comment for each logical block added.
* Keep functions focused — prefer small, composable helpers over large monoliths.
* New public methods require docstrings (Google style).

Submitting a Pull Request
--------------------------

1. Fork the repository and create a feature branch from ``main``:

   .. code-block:: console

      git checkout -b feature/my-improvement

2. Implement your changes and add tests for any new behaviour.
3. Ensure the full test suite passes:

   .. code-block:: console

      pytest tests/ -v

4. Open a pull request against ``main`` and describe what was changed and why.

Reporting Issues
----------------

Open an issue on `GitHub <https://github.com/amckenna41/repo-people/issues>`_
and include:

* A minimal reproducible example.
* The repository you were running against (owner/repo).
* The Python and ``repo-people`` versions you are using.
* The full traceback if applicable.

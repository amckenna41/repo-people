"""
Test-suite setup that has to work under *both* pytest and plain ``unittest``.

``conftest.py`` is a pytest-only mechanism. Isolation that lives only there
silently stops protecting the suite the moment anyone runs
``python -m unittest discover -s tests`` or executes a single test file via
``unittest.main()`` — which every test module here supports at the bottom. That
gap let the process-wide commit-author memo leak between tests and produced
seven order-dependent failures under ``unittest`` while pytest stayed green.

This module is imported by both runners (``tests`` is a package, so test
modules import as ``tests.test_*``), which makes it the right home for anything
the whole suite depends on.
"""

import atexit
import os
import shutil
import tempfile
import unittest

from repo_people import export

# ---------------------------------------------------------------------------
# On-disk ETag cache isolation (process-wide)
# ---------------------------------------------------------------------------
# Point the cache at a throwaway directory before any test runs, so the suite
# never reads or writes the developer's real ~/.cache/repo-people. A cached page
# there could otherwise satisfy a request a test expected to issue, and the run
# would leave junk behind. _cache_dir() reads this variable at call time, so
# setting it at import is enough regardless of module import order.
_CACHE_DIR = tempfile.mkdtemp(prefix="repo-people-test-cache-")
os.environ["REPO_PEOPLE_CACHE_DIR"] = _CACHE_DIR
atexit.register(shutil.rmtree, _CACHE_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Per-test cleanup of process-wide state
# ---------------------------------------------------------------------------
# export_contributors and export_commit_authors share a memoised commit walk so
# that requesting both roles costs one pass instead of two. The memo is keyed by
# repository and every test uses the same ("o", "r") pair, so without clearing it
# between tests one test's mocked payload gets served to the next.
#
# Hooking TestCase.run rather than adding setUp to each class: there are 70+ test
# classes across six files, every test is a TestCase method, and a base class
# only protects the classes someone remembers to inherit from. This covers all of
# them, including ones added later, under either runner.
if not getattr(unittest.TestCase, "_repo_people_memo_hook", False):
    _original_run = unittest.TestCase.run

    def _run_with_clean_memo(self, result=None):
        """Run a test with the commit-author memo cleared before and after."""
        export.clear_commit_author_cache()
        try:
            return _original_run(self, result)
        finally:
            export.clear_commit_author_cache()

    unittest.TestCase.run = _run_with_clean_memo
    unittest.TestCase._repo_people_memo_hook = True

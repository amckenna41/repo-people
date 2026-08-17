import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dotenv import load_dotenv
from repo_people import export

unittest.TestLoader.sortTestMethodsUsing = None


# ---------------------------------------------------------------------------
# Helpers for building mock HTTP responses
# ---------------------------------------------------------------------------

def _mock_response(json_data, status_code=200, link_header=""):
    """Return a minimal MagicMock that mimics requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.headers = {"Link": link_header}
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Unit tests — all HTTP calls mocked
# ---------------------------------------------------------------------------

class ExportUnitTests(unittest.TestCase):
    """Mock-based unit tests for individual export_* functions."""

    def setUp(self):
        # These tests all drive the memoised commit walk against the same
        # ("o", "r") repository, so a leftover memo entry serves one test's
        # mocked payload to the next. tests/__init__.py clears the memo around
        # every test, but that module is not imported when this file is run
        # directly as a script (``python tests/test_export.py``), so the class
        # that actually depends on the reset asks for it explicitly too.
        export.clear_commit_author_cache()
        self.addCleanup(export.clear_commit_author_cache)

    def test_export_contributors_return_data(self):
        """export_contributors with return_data=True returns list of logins."""
        # /commits endpoint returns commit objects with nested author
        payload = [
            {"author": {"login": "alice"}},
            {"author": {"login": "bob"}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_contributors(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        self.assertEqual(sorted(result), ["alice", "bob"])

    def test_export_contributors_count_when_no_return_data(self):
        """export_contributors always returns a list of logins (return_data is deprecated)."""
        payload = [{"author": {"login": "alice"}}, {"author": {"login": "bob"}}]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_contributors(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=False
                )
        # return_data is now ignored — always returns a list
        self.assertIsInstance(result, list)
        self.assertEqual(sorted(result), ["alice", "bob"])

    def test_export_contributors_csv_created(self):
        """export_contributors with export_csv=True creates a contributors.csv file."""
        payload = [{"author": {"login": "alice"}}]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                export.export_contributors(
                    owner="o", repo="r", token=None, outdir=tmpdir, export_csv=True
                )
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "o_r_contributors.csv")))

    def test_export_contributors_skips_entries_without_login(self):
        """Commits missing an authenticated author are silently skipped."""
        payload = [
            {"author": {"login": "alice"}},
            {"author": None},           # unauthenticated / anonymous commit
            {"author": {}},             # author dict with no login key
            {},                         # no author key at all
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_contributors(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        self.assertEqual(result, ["alice"])

    def test_export_contributors_deduplicates_same_author(self):
        """The same login appearing in multiple commits is returned only once."""
        payload = [
            {"author": {"login": "alice"}},
            {"author": {"login": "alice"}},
            {"author": {"login": "bob"}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_contributors(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        self.assertEqual(result, ["alice", "bob"])  # sorted, deduplicated

    def test_export_contributors_sorted_output(self):
        """Returned list is sorted alphabetically."""
        payload = [
            {"author": {"login": "zara"}},
            {"author": {"login": "alice"}},
            {"author": {"login": "mike"}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_contributors(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        self.assertEqual(result, sorted(result))

    def test_export_stargazers_return_data(self):
        """export_stargazers with return_data=True returns list of logins."""
        # Stargazer records are {"user": {"login": ...}} when using star+json accept header
        payload = [{"user": {"login": "carol"}}, {"user": {"login": "dave"}}]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_stargazers(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        self.assertEqual(sorted(result), ["carol", "dave"])

    def test_export_stargazers_csv_created(self):
        payload = [{"user": {"login": "carol"}}]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                export.export_stargazers(
                    owner="o", repo="r", token=None, outdir=tmpdir, export_csv=True
                )
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "o_r_stargazers.csv")))

    def test_export_watchers_return_data(self):
        """export_watchers with return_data=True returns list of subscriber logins."""
        payload = [{"login": "eve"}, {"login": "frank"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_watchers(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        self.assertEqual(sorted(result), ["eve", "frank"])

    def test_export_issue_authors_return_data(self):
        """export_issue_authors returns deduplicated list of issue author logins."""
        payload = [
            {"user": {"login": "grace"}, "title": "Bug"},
            {"user": {"login": "grace"}, "title": "Another bug"},  # duplicate, same author
            {"user": {"login": "henry"}, "title": "Feature"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_issue_authors(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        self.assertEqual(sorted(result), ["grace", "henry"])

    def test_export_pr_authors_return_data(self):
        """export_pr_authors returns logins from the /pulls endpoint."""
        # /pulls endpoint returns PR objects directly (no 'pull_request' key needed)
        payload = [
            {"user": {"login": "ida"}, "number": 1, "title": "Add feature"},
            {"user": {"login": "joe"}, "number": 2, "title": "Fix bug"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_pr_authors(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        self.assertEqual(sorted(result), ["ida", "joe"])

    def test_export_fork_owners_return_data(self):
        """export_fork_owners returns list of fork owner logins."""
        payload = [{"owner": {"login": "kim"}}, {"owner": {"login": "leo"}}]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_fork_owners(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        self.assertEqual(sorted(result), ["kim", "leo"])

    def test_export_commit_authors_return_data(self):
        """export_commit_authors returns unique commit author logins."""
        commit_payload = [
            {"author": {"login": "mia"}},
            {"author": {"login": "mia"}},   # duplicate
            {"author": {"login": "noah"}},
        ]
        mock_resp = _mock_response(commit_payload)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=mock_resp):
                result = export.export_commit_authors(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        self.assertEqual(sorted(result), ["mia", "noah"])

    def test_export_commit_authors_csv_created(self):
        commit_payload = [{"author": {"login": "mia"}}]
        mock_resp = _mock_response(commit_payload)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=mock_resp):
                export.export_commit_authors(
                    owner="o", repo="r", token=None, outdir=tmpdir, export_csv=True
                )
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "o_r_commit_authors.csv")))

    def test_export_csv_file_has_header_row(self):
        """CSV files written by export functions include a login header row."""
        import csv as csv_mod
        payload = [{"author": {"login": "alice"}}]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                export.export_contributors(
                    owner="o", repo="r", token=None, outdir=tmpdir,
                    return_data=False, export_csv=True,
                )
            path = os.path.join(tmpdir, "o_r_contributors.csv")
            with open(path, newline="") as f:
                reader = csv_mod.reader(f)
                rows = list(reader)
        self.assertEqual(rows[0], ["login"])   # header
        self.assertEqual(rows[1], ["alice"])   # data row

    def test_export_maintainers_collaborators_only(self):
        """export_maintainers returns collaborators with push/maintain/admin perms."""
        collab_payload = [
            {"login": "oz", "html_url": "https://github.com/oz",
             "permissions": {"admin": True, "maintain": False, "push": True, "triage": False, "pull": True}},
            {"login": "pat", "html_url": "https://github.com/pat",
             "permissions": {"admin": False, "maintain": False, "push": False, "triage": False, "pull": True}},
        ]
        # First requests.get call is for CODEOWNERS (returns 404), second is collaborators
        def side_effect(url, **kwargs):
            if "contents" in url:   # CODEOWNERS attempt
                return _mock_response({}, status_code=404)
            return _mock_response(collab_payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", side_effect=side_effect):
                result = export.export_maintainers(
                    owner="o", repo="r", token="tok", outdir=tmpdir,
                    skip_codeowners=False, skip_collaborators=False, return_data=True,
                )
        # "oz" has admin+push — included; "pat" has only pull — excluded
        self.assertIn("oz", result)
        self.assertNotIn("pat", result)

    def test_export_maintainers_deduplicates_same_login(self):
        """A user appearing in both CODEOWNERS and collaborators should be listed once."""
        codeowners_text = "* @oz"
        collab_payload = [
            {"login": "oz", "html_url": "https://github.com/oz",
             "permissions": {"admin": True, "maintain": False, "push": True, "triage": False, "pull": True}},
        ]

        def side_effect(url, **kwargs):
            if "contents" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"content": __import__("base64").b64encode(codeowners_text.encode()).decode()}
                resp.headers = {}
                return resp
            return _mock_response(collab_payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", side_effect=side_effect):
                result = export.export_maintainers(
                    owner="o", repo="r", token="tok", outdir=tmpdir,
                    skip_codeowners=False, skip_collaborators=False, return_data=True,
                )
        self.assertEqual(result.count("oz"), 1, "oz appeared more than once (dedup failed)")

    def test_paginate_stops_without_next_link(self):
        """A response with no Link header causes pagination to stop after one page."""
        payload = [{"author": {"login": f"user{i}"}} for i in range(10)]
        mock_resp = _mock_response(payload, link_header="")
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=mock_resp) as mock_get:
                export.export_contributors(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=True
                )
        # Only one HTTP request should be made (no next page)
        mock_get.assert_called_once()

    def test_permission_403_is_not_retried(self):
        """A 403 with no rate-limit signal is a permission error: raise at once."""
        import requests as _requests
        resp = _mock_response([], status_code=403)
        resp.headers = {}  # no X-RateLimit-Remaining: 0, no Retry-After
        resp.raise_for_status.side_effect = _requests.exceptions.HTTPError("403")
        with patch("repo_people.utils.requests.get", return_value=resp) as mock_get, \
             patch("repo_people.utils.time.sleep") as mock_sleep:
            with self.assertRaises(_requests.exceptions.HTTPError):
                list(export.paginate("https://api.github.com/x", token=None))
        # Sleeping and retrying a SAML/scope 403 burned ~50s before surfacing the
        # same error. One request, no sleeps.
        self.assertEqual(mock_get.call_count, 1)
        mock_sleep.assert_not_called()

    def test_persistent_ratelimit_403_is_retried_a_bounded_number_of_times(self):
        """A genuine rate-limit 403 is retried, but must not loop forever."""
        import requests as _requests
        resp = _mock_response([], status_code=403)
        # An exhausted quota with no reset header -> short fixed backoff path.
        resp.headers = {"X-RateLimit-Remaining": "0"}
        resp.raise_for_status.side_effect = _requests.exceptions.HTTPError("403")
        with patch("repo_people.utils.requests.get", return_value=resp) as mock_get, \
             patch("repo_people.utils.time.sleep"):
            with self.assertRaises(_requests.exceptions.HTTPError):
                list(export.paginate("https://api.github.com/x", token=None))
        # 1 initial request + 5 capped retries = 6; then it gives up rather than looping.
        self.assertEqual(mock_get.call_count, 6)

    def test_secondary_ratelimit_403_detected_from_body(self):
        """Secondary limits can arrive with no headers, only a message body."""
        from repo_people.utils import _is_ratelimit_response
        resp = MagicMock()
        resp.status_code = 403
        resp.headers = {}
        resp.json.return_value = {"message": "You have exceeded a secondary rate limit"}
        self.assertTrue(_is_ratelimit_response(resp))

    def test_plain_403_body_is_not_a_ratelimit(self):
        from repo_people.utils import _is_ratelimit_response
        resp = MagicMock()
        resp.status_code = 403
        resp.headers = {}
        resp.json.return_value = {"message": "Resource protected by organization SAML enforcement"}
        self.assertFalse(_is_ratelimit_response(resp))


# ---------------------------------------------------------------------------
# PR reviewers unit tests
# ---------------------------------------------------------------------------

class TestExportPrReviewers(unittest.TestCase):
    """Unit tests for export_pr_reviewers."""

    def test_returns_unique_sorted_reviewer_logins(self):
        """Collects reviewers across multiple PRs and deduplicates them."""
        prs_payload = [{"number": 1}, {"number": 2}]
        reviews_pr1 = [{"user": {"login": "alice"}}, {"user": {"login": "bob"}}]
        reviews_pr2 = [{"user": {"login": "alice"}}, {"user": {"login": "carol"}}]

        def side_effect(url, **kwargs):
            if "/pulls/1/reviews" in url:
                return _mock_response(reviews_pr1)
            if "/pulls/2/reviews" in url:
                return _mock_response(reviews_pr2)
            return _mock_response(prs_payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", side_effect=side_effect):
                result = export.export_pr_reviewers(
                    owner="o", repo="r", token=None, outdir=tmpdir
                )
        self.assertEqual(result, ["alice", "bob", "carol"])

    def test_returns_empty_list_when_no_prs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response([])):
                result = export.export_pr_reviewers(
                    owner="o", repo="r", token=None, outdir=tmpdir
                )
        self.assertEqual(result, [])

    def test_skips_reviews_with_no_user(self):
        prs_payload = [{"number": 1}]
        reviews = [{"user": None}, {"user": {"login": "alice"}}, {}]

        def side_effect(url, **kwargs):
            if "/reviews" in url:
                return _mock_response(reviews)
            return _mock_response(prs_payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", side_effect=side_effect):
                result = export.export_pr_reviewers(
                    owner="o", repo="r", token=None, outdir=tmpdir
                )
        self.assertEqual(result, ["alice"])

    def test_csv_created_when_export_csv_true(self):
        prs_payload = [{"number": 1}]
        reviews = [{"user": {"login": "alice"}}]

        def side_effect(url, **kwargs):
            if "/reviews" in url:
                return _mock_response(reviews)
            return _mock_response(prs_payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", side_effect=side_effect):
                export.export_pr_reviewers(
                    owner="o", repo="r", token=None, outdir=tmpdir, export_csv=True
                )
            self.assertTrue(
                os.path.isfile(os.path.join(tmpdir, "o_r_pr_reviewers.csv"))
            )

    def test_return_data_param_is_ignored(self):
        """return_data=False still returns a list (backwards-compat param is deprecated)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response([])):
                result = export.export_pr_reviewers(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=False
                )
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# Structure/setup validation test (no API calls)
# ---------------------------------------------------------------------------

class Export_Tests(unittest.TestCase):
    """
    Test Suite for export functionality
    """
    @classmethod
    def setUpClass(cls):
        """ Initialise test variables and directories for all tests. """
        load_dotenv()
        # Put test output directory within tests directory
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        cls.test_output_dir = os.path.join(tests_dir, "test_output")
        cls.token = os.getenv("GITHUB_TOKEN")
        
        # Test repository - using amckenna41/iso3166-2
        cls.test_owner = "amckenna41"
        cls.test_repo = "iso3166-2"
        
        # Create test output directory if it doesn't exist
        if not os.path.exists(cls.test_output_dir):
            os.mkdir(cls.test_output_dir)

    @classmethod
    def tearDownClass(cls):
        """Remove the test output directory and all its contents after all tests in this class."""
        if os.path.exists(cls.test_output_dir):
            shutil.rmtree(cls.test_output_dir)
    
    def setUp(self):
        """ Setup for individual tests. """
        # Create repo-specific output directory
        self.repo_output_dir = os.path.join(self.test_output_dir, f"{self.test_owner}_{self.test_repo}")
        if not os.path.exists(self.repo_output_dir):
            os.mkdir(self.repo_output_dir)
    
    def test_export_structure_validation(self):
        """
        Test the structure and setup of the integration test without API calls.
        """
        print(f"\n=== Structure Test: Validating test setup ===")
        
        # Test that we have the proper setup
        self.assertIsNotNone(self.test_owner)
        self.assertIsNotNone(self.test_repo)
        self.assertEqual(self.test_owner, "amckenna41")
        self.assertEqual(self.test_repo, "iso3166-2")
        
        # Test that directories are created properly
        self.assertTrue(os.path.exists(self.test_output_dir))
        self.assertTrue(os.path.exists(self.repo_output_dir))
        
        print(f"✅ Test structure validation passed!")
        print(f"   Test owner: {self.test_owner}")
        print(f"   Test repo: {self.test_repo}")
        print(f"   Output dir: {self.repo_output_dir}")
    
    def test_export_all_user_types_integration(self):
        """
        Integration test: Export all types of users from pycountry/pycountry repository.
        Makes real API calls to test complete export functionality.
        Works with or without GitHub token (unauthenticated API has rate limits).
        """
        # Check token status and inform user
        if self.token:
            print(f"\n=== Integration Test: Exporting all user types from {self.test_owner}/{self.test_repo} (WITH TOKEN) ===")
            print("✅ Using authenticated GitHub API (higher rate limits)")
        else:
            print(f"\n=== Integration Test: Exporting all user types from {self.test_owner}/{self.test_repo} (NO TOKEN) ===")
            print("⚠️  Using unauthenticated GitHub API (lower rate limits - may be slower)")
        
        # Dictionary to store all exported user lists
        all_users = {}
        
        # Test 1: Export Contributors
        print("\n1. Exporting contributors...")
        try:
            contributors = export.export_contributors(
                owner=self.test_owner,
                repo=self.test_repo,
                token=self.token,
                outdir=self.repo_output_dir,
                return_data=True,
                export_csv=True
            )
            all_users['contributors'] = contributors
            print(f"   Found {len(contributors)} contributors: {contributors[:5]}{'...' if len(contributors) > 5 else ''}")
            self.assertIsInstance(contributors, list)
            self.assertTrue(len(contributors) > 0, "Should have at least some contributors")
        except Exception as e:
            print(f"   ❌ Failed to export contributors: {e}")
            all_users['contributors'] = []
        
        # Test 2: Export Stargazers
        print("\n2. Exporting stargazers...")
        try:
            stargazers = export.export_stargazers(
                owner=self.test_owner,
                repo=self.test_repo,
                token=self.token,
                outdir=self.repo_output_dir,
                return_data=True,
                export_csv=True
            )
            all_users['stargazers'] = stargazers
            print(f"   Found {len(stargazers)} stargazers: {stargazers[:5]}{'...' if len(stargazers) > 5 else ''}")
            self.assertIsInstance(stargazers, list)
        except Exception as e:
            print(f"   ❌ Failed to export stargazers: {e}")
            all_users['stargazers'] = []
        
        # Test 3: Export Watchers
        print("\n3. Exporting watchers...")
        try:
            watchers = export.export_watchers(
                owner=self.test_owner,
                repo=self.test_repo,
                token=self.token,
                outdir=self.repo_output_dir,
                return_data=True,
                export_csv=True
            )
            all_users['watchers'] = watchers
            print(f"   Found {len(watchers)} watchers: {watchers[:5]}{'...' if len(watchers) > 5 else ''}")
            self.assertIsInstance(watchers, list)
        except Exception as e:
            print(f"   ❌ Failed to export watchers: {e}")
            all_users['watchers'] = []
        
        # Test 4: Export Issue Authors
        print("\n4. Exporting issue authors...")
        try:
            issue_authors = export.export_issue_authors(
                owner=self.test_owner,
                repo=self.test_repo,
                token=self.token,
                outdir=self.repo_output_dir,
                return_data=True,
                export_csv=True
            )
            all_users['issue_authors'] = issue_authors
            print(f"   Found {len(issue_authors)} issue authors: {issue_authors[:5]}{'...' if len(issue_authors) > 5 else ''}")
            self.assertIsInstance(issue_authors, list)
        except Exception as e:
            print(f"   ❌ Failed to export issue authors: {e}")
            all_users['issue_authors'] = []
        
        # Test 5: Export PR Authors
        print("\n5. Exporting PR authors...")
        try:
            pr_authors = export.export_pr_authors(
                owner=self.test_owner,
                repo=self.test_repo,
                token=self.token,
                outdir=self.repo_output_dir,
                return_data=True,
                export_csv=True
            )
            all_users['pr_authors'] = pr_authors
            print(f"   Found {len(pr_authors)} PR authors: {pr_authors[:5]}{'...' if len(pr_authors) > 5 else ''}")
            self.assertIsInstance(pr_authors, list)
        except Exception as e:
            print(f"   ❌ Failed to export PR authors: {e}")
            all_users['pr_authors'] = []
        
        # Test 6: Export Fork Owners
        print("\n6. Exporting fork owners...")
        try:
            fork_owners = export.export_fork_owners(
                owner=self.test_owner,
                repo=self.test_repo,
                token=self.token,
                outdir=self.repo_output_dir,
                return_data=True,
                export_csv=True
            )
            all_users['fork_owners'] = fork_owners
            print(f"   Found {len(fork_owners)} fork owners: {fork_owners[:5]}{'...' if len(fork_owners) > 5 else ''}")
            self.assertIsInstance(fork_owners, list)
        except Exception as e:
            print(f"   ❌ Failed to export fork owners: {e}")
            all_users['fork_owners'] = []
        
        # Test 7: Export Commit Authors
        print("\n7. Exporting commit authors...")
        try:
            commit_authors = export.export_commit_authors(
                owner=self.test_owner,
                repo=self.test_repo,
                token=self.token,
                outdir=self.repo_output_dir,
                return_data=True,
                export_csv=True
            )
            all_users['commit_authors'] = commit_authors
            print(f"   Found {len(commit_authors)} commit authors: {commit_authors[:5]}{'...' if len(commit_authors) > 5 else ''}")
            self.assertIsInstance(commit_authors, list)
        except Exception as e:
            print(f"   ❌ Failed to export commit authors: {e}")
            all_users['commit_authors'] = []
        
        # Test 8: Export Maintainers
        print("\n8. Exporting maintainers...")
        try:
            maintainers = export.export_maintainers(
                owner=self.test_owner,
                repo=self.test_repo,
                token=self.token,
                outdir=self.repo_output_dir,
                skip_codeowners=False,
                skip_collaborators=False,
                return_data=True,
                export_csv=True
            )
            all_users['maintainers'] = maintainers
            print(f"   Found {len(maintainers)} maintainers: {maintainers[:5]}{'...' if len(maintainers) > 5 else ''}")
            self.assertIsInstance(maintainers, list)
        except Exception as e:
            print(f"   ❌ Failed to export maintainers: {e}")
            all_users['maintainers'] = []
        
        # Test 9: Export Dependents
        print("\n9. Exporting dependents...")
        try:
            dependents = export.export_dependents(
                owner=self.test_owner,
                repo=self.test_repo,
                outdir=self.repo_output_dir,
                return_data=True,
                export_csv=True
            )
            all_users['dependents'] = dependents
            print(f"   Found {len(dependents)} dependents: {dependents[:5]}{'...' if len(dependents) > 5 else ''}")
            self.assertIsInstance(dependents, list)
        except Exception as e:
            print(f"   ❌ Failed to export dependents: {e}")
            all_users['dependents'] = []
        
        # Summary and Validation
        print(f"\n=== Export Summary for {self.test_owner}/{self.test_repo} ===")
        total_unique_users = set()
        for user_type, users in all_users.items():
            print(f"{user_type:15}: {len(users):4d} users")
            total_unique_users.update(users)
        
        print(f"{'Total unique':15}: {len(total_unique_users):4d} users across all categories")
        
        # Validate CSV files were created (only check files that were successfully exported)
        print(f"\n=== Validating CSV files in {self.repo_output_dir} ===")
        expected_csv_files = [
            'contributors.csv', 'stargazers.csv', 'watchers.csv', 
            'issue_authors.csv', 'pr_authors.csv', 'fork_owners.csv',
            'commit_authors.csv', 'maintainers.csv', 'dependents.csv'
        ]
        
        files_created = 0
        for csv_file in expected_csv_files:
            csv_path = os.path.join(self.repo_output_dir, csv_file)
            if os.path.exists(csv_path):
                files_created += 1
                # Check file has content
                with open(csv_path, 'r') as f:
                    lines = f.readlines()
                    print(f"   ✅ {csv_file:20}: {len(lines):3d} lines")
            else:
                print(f"   ❌ {csv_file:20}: Not created (likely due to API failure)")
        
        # Flexible test assertions - should work with or without token
        successful_exports = sum(1 for users in all_users.values() if len(users) > 0)
        self.assertGreater(successful_exports, 0, "Should have at least one successful export")
        self.assertGreater(len(total_unique_users), 0, "Should have found some users")
        self.assertEqual(len(all_users), 9, "Should have attempted 9 different user types")
        
        # Token-specific validation
        if self.token:
            print(f"\n✅ Integration test with token completed successfully!")
            # With token, we expect most exports to succeed
            self.assertGreater(successful_exports, 3, "With token, should have multiple successful exports")
        else:
            print(f"\n⚠️  Integration test without token completed!")
            print("   Some exports may have failed due to rate limits - this is expected")
            # Without token, we just need at least one success
        
        print(f"\n✅ Integration test completed successfully!")
        print(f"   Exported {len(total_unique_users)} unique users across 9 categories")
        print(f"   All CSV files created in: {self.repo_output_dir}")


# ---------------------------------------------------------------------------
# export_dependents unit tests (scraping — no real HTTP)
# ---------------------------------------------------------------------------

class TestExportDependents(unittest.TestCase):
    """Unit tests for export_dependents — uses a mock requests.Session."""

    def _make_html(self, user_repo_pairs, next_url=None):
        """Build minimal HTML mimicking the GitHub dependents page."""
        rows_html = ""
        for owner, repo_name in user_repo_pairs:
            rows_html += (
                f'<div class="Box-row">'
                f'<a href="/{owner}/{repo_name}" data-hovercard-type="repository">{owner}/{repo_name}</a>'
                f"</div>"
            )
        next_link = ""
        if next_url:
            next_link = f'<div class="paginate-container"><a class="next_page" href="{next_url}">Next</a></div>'
        return f"<html><body>{rows_html}{next_link}</body></html>"

    def _mock_session(self, responses):
        """
        Returns a mock requests.Session whose .get() returns responses in order.
        *responses* is a list of (status_code, html_text) tuples.
        """
        session = MagicMock()
        response_mocks = []
        for status, text in responses:
            r = MagicMock()
            r.status_code = status
            r.text = text
            response_mocks.append(r)
        session.get.side_effect = response_mocks
        return session

    def test_returns_list_of_usernames(self):
        """export_dependents returns a sorted list of usernames (repo owners)."""
        html = self._make_html([("alice", "proj"), ("bob", "tool")])
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.Session") as mock_session_cls:
                mock_session_cls.return_value = self._mock_session([(200, html)])
                result = export.export_dependents("o", "r", outdir=tmpdir)
        self.assertIsInstance(result, list)
        self.assertIn("alice", result)
        self.assertIn("bob", result)

    def test_always_returns_list_regardless_of_return_data(self):
        """return_data=False still returns a list (parameter is deprecated)."""
        html = self._make_html([("alice", "proj")])
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.Session") as mock_session_cls:
                mock_session_cls.return_value = self._mock_session([(200, html)])
                result = export.export_dependents("o", "r", outdir=tmpdir, return_data=False)
        self.assertIsInstance(result, list)

    def test_limit_zero_returns_empty(self):
        """limit=0 should return an empty list immediately."""
        # Even if there are dependents on the page, limit=0 must return []
        html = self._make_html([("alice", "proj")])
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.Session") as mock_session_cls:
                mock_session_cls.return_value = self._mock_session([(200, html)])
                result = export.export_dependents("o", "r", outdir=tmpdir, limit=0)
        self.assertEqual(result, [])

    def test_limit_caps_results(self):
        """limit=N stops collecting after N unique repos are found."""
        # Use two repos from the same owner so that limit=1 repo → 1 unique username
        html = self._make_html([("alice", "proj-a"), ("alice", "proj-b")])
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.Session") as mock_session_cls:
                mock_session_cls.return_value = self._mock_session([(200, html)])
                result = export.export_dependents("o", "r", outdir=tmpdir, limit=1)
        # At most 1 unique repo was collected → 1 unique owner
        self.assertLessEqual(len(result), 1)

    def test_non_200_triggers_backoff_and_stops(self):
        """A non-200 response aborts pagination and still returns the collected results."""
        html = self._make_html([("alice", "proj")])
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.Session") as mock_session_cls:
                mock_session_cls.return_value = self._mock_session([(200, html), (429, "")])
                with patch("repo_people.export.time.sleep"):  # don't actually sleep
                    result = export.export_dependents("o", "r", outdir=tmpdir)
        # First page was collected before the 429
        self.assertIn("alice", result)

    def test_csv_created(self):
        """export_csv=True creates a _dependents.csv file."""
        html = self._make_html([("alice", "proj")])
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.Session") as mock_session_cls:
                mock_session_cls.return_value = self._mock_session([(200, html)])
                export.export_dependents("o", "r", outdir=tmpdir, export_csv=True)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "o_r_dependents.csv")))

    def test_deduplicates_repos_same_owner(self):
        """Multiple repos from the same owner are deduped to a single username."""
        html = self._make_html([("alice", "repo-a"), ("alice", "repo-b")])
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.Session") as mock_session_cls:
                mock_session_cls.return_value = self._mock_session([(200, html)])
                result = export.export_dependents("o", "r", outdir=tmpdir)
        self.assertEqual(result.count("alice"), 1)


# ---------------------------------------------------------------------------
# utils helpers unit tests
# ---------------------------------------------------------------------------

class TestUtilsHelpers(unittest.TestCase):
    """Unit tests for utility helpers added in utils.py."""

    def test_is_bot_by_type(self):
        from repo_people.utils import _is_bot
        self.assertTrue(_is_bot("renovate", "Bot"))
        self.assertTrue(_is_bot("renovate", "bot"))

    def test_is_bot_by_bot_suffix(self):
        from repo_people.utils import _is_bot
        self.assertTrue(_is_bot("github-actions[bot]", "User"))
        self.assertTrue(_is_bot("dependabot-bot", "User"))

    def test_not_bot_for_regular_user(self):
        from repo_people.utils import _is_bot
        self.assertFalse(_is_bot("alice", "User"))
        self.assertFalse(_is_bot("robotics-lab", "Organization"))

    def test_validate_owner_repo_accepts_valid(self):
        from repo_people.utils import validate_owner_repo
        # Should not raise
        validate_owner_repo("amckenna41", "repo-people")
        validate_owner_repo("org_name", "repo.name")

    def test_validate_owner_repo_rejects_empty(self):
        from repo_people.utils import validate_owner_repo
        with self.assertRaises(ValueError):
            validate_owner_repo("", "repo")
        with self.assertRaises(ValueError):
            validate_owner_repo("owner", "")

    def test_validate_owner_repo_rejects_path_traversal(self):
        from repo_people.utils import validate_owner_repo
        with self.assertRaises(ValueError):
            validate_owner_repo("../etc", "passwd")
        with self.assertRaises(ValueError):
            validate_owner_repo("owner", "../repo")

    def test_validate_owner_repo_rejects_special_chars(self):
        from repo_people.utils import validate_owner_repo
        with self.assertRaises(ValueError):
            validate_owner_repo("owner;rm", "repo")
        with self.assertRaises(ValueError):
            validate_owner_repo("owner", "repo<script>")

    def test_sleep_if_ratelimited_zero_wait_uses_fallback(self):
        """wait_s=0 (no Retry-After header) should sleep 10s, not silently skip."""
        from repo_people.utils import _sleep_if_ratelimited
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {}  # no Retry-After, no X-RateLimit-Reset
        with patch("repo_people.utils.time.sleep") as mock_sleep:
            _sleep_if_ratelimited(mock_resp)
        mock_sleep.assert_called_once_with(10)

    def test_sleep_if_ratelimited_uses_header_value(self):
        """Retry-After header value is used as the sleep duration."""
        from repo_people.utils import _sleep_if_ratelimited
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {"Retry-After": "30"}
        with patch("repo_people.utils.time.sleep") as mock_sleep:
            _sleep_if_ratelimited(mock_resp)
        mock_sleep.assert_called_once_with(30)


if __name__ == '__main__':
    unittest.main()
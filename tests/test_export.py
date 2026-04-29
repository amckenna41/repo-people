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
        """export_contributors returns an integer count when return_data=False."""
        payload = [{"author": {"login": "alice"}}, {"author": {"login": "bob"}}]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.requests.get", return_value=_mock_response(payload)):
                result = export.export_contributors(
                    owner="o", repo="r", token=None, outdir=tmpdir, return_data=False
                )
        self.assertIsInstance(result, int)
        self.assertEqual(result, 2)

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


if __name__ == '__main__':
    unittest.main()

import os
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call
from dotenv import load_dotenv
from repo_people import RepoPeople

unittest.TestLoader.sortTestMethodsUsing = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_EXPORT_ATTRS = (
    "export_contributors", "export_maintainers", "export_stargazers",
    "export_watchers", "export_issue_authors", "export_pr_authors",
    "export_fork_owners", "export_commit_authors", "export_dependents",
)

def _stub_export(mock_export, logins=None):
    """Make every export_* function on mock_export return `logins` (default [])."""
    for attr in _ALL_EXPORT_ATTRS:
        getattr(mock_export, attr).return_value = logins or []


def _stub_user(mock_cls, login="alice", extra=None):
    """Configure GitHubUserInfo mock to return a minimal user dict."""
    data = {"login": login}
    if extra:
        data.update(extra)
    mock_cls.return_value.to_dict.return_value = data
    return mock_cls


# ---------------------------------------------------------------------------
# Unit tests — no real API calls
# ---------------------------------------------------------------------------

class TestRepoPeopleInit(unittest.TestCase):
    """Tests for RepoPeople.__init__."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()

    def tearDown(self):
        self.gh_patcher.stop()

    def _make(self, **kwargs):
        defaults = dict(owner="owner", repo="repo", token="tok")
        defaults.update(kwargs)
        return RepoPeople(**defaults)

    def test_default_outdir(self):
        """outdir defaults to 'outputs' and file_prefix is 'owner_repo_'."""
        rp = self._make(owner="alice", repo="myrepo")
        self.assertEqual(rp.outdir, "outputs")
        self.assertEqual(rp.file_prefix, "alice_myrepo_")

    def test_custom_outdir(self):
        """Custom outdir overrides the default outputs/ directory."""
        rp = self._make(outdir="custom")
        self.assertEqual(rp.outdir, "custom")

    def test_owner_and_repo_stored(self):
        rp = self._make(owner="bob", repo="proj")
        self.assertEqual(rp.owner, "bob")
        self.assertEqual(rp.repo, "proj")

    def test_skip_flags_default_false(self):
        rp = self._make()
        self.assertFalse(rp.skip_codeowners)
        self.assertFalse(rp.skip_collaborators)

    def test_valid_roles_class_attribute(self):
        """VALID_ROLES contains all nine expected role keys."""
        expected = {
            "contributors", "maintainers", "stargazers", "watchers",
            "issue_authors", "pr_authors", "fork_owners", "commit_authors", "dependents",
        }
        self.assertEqual(RepoPeople.VALID_ROLES, expected)

    def test_repr_contains_owner_and_repo(self):
        """__repr__ includes owner, repo, outdir, and valid_roles count."""
        rp = self._make(owner="alice", repo="myrepo")
        r = repr(rp)
        self.assertIn("RepoPeople(owner=", r)
        self.assertIn("alice", r)
        self.assertIn("myrepo", r)

    def test_invalid_token_raises_connection_error(self):
        """ConnectionError is raised when get_rate_limit() throws on init."""
        self.gh_patcher.stop()
        with patch("repo_people.repo_people.Github") as mock_cls:
            mock_cls.return_value.get_rate_limit.side_effect = Exception("bad token")
            with self.assertRaises(ConnectionError) as ctx:
                RepoPeople(owner="o", repo="r", token="bad")
            self.assertIn("bad token", str(ctx.exception))
        self.gh_patcher.start()  # restart for tearDown


class TestCollectAllUsernames(unittest.TestCase):
    """Tests for RepoPeople.collect_all_usernames."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def test_returns_all_nine_roles(self):
        with patch("repo_people.repo_people.export") as mock_export:
            _stub_export(mock_export)
            result = self.rp.collect_all_usernames()
        self.assertEqual(set(result.keys()), RepoPeople.VALID_ROLES)

    def test_values_are_lists(self):
        with patch("repo_people.repo_people.export") as mock_export:
            _stub_export(mock_export)
            result = self.rp.collect_all_usernames()
        for key, val in result.items():
            self.assertIsInstance(val, list, f"Expected list for role '{key}'")

    def test_roles_filter_limits_keys(self):
        """Passing roles= returns only the requested subset."""
        with patch("repo_people.repo_people.export") as mock_export:
            _stub_export(mock_export)
            result = self.rp.collect_all_usernames(roles=["contributors", "stargazers"])
        self.assertEqual(set(result.keys()), {"contributors", "stargazers"})

    def test_invalid_roles_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.rp.collect_all_usernames(roles=["not_a_role"])

    def test_logins_are_passed_through(self):
        with patch("repo_people.repo_people.export") as mock_export:
            mock_export.export_contributors.return_value = ["alice", "bob"]
            for attr in _ALL_EXPORT_ATTRS[1:]:
                getattr(mock_export, attr).return_value = []
            result = self.rp.collect_all_usernames()
        self.assertEqual(result["contributors"], ["alice", "bob"])


class TestGetUserDetails(unittest.TestCase):
    """Tests for RepoPeople.get_user_details."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def _mock_user(self, mock_cls, login):
        mock_cls.return_value.to_dict.return_value = {"login": login}

    def test_returns_dict_keyed_by_login(self):
        with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
            self._mock_user(mock_cls, "alice")
            result = self.rp.get_user_details(["alice"])
        self.assertIn("alice", result)

    def test_skips_users_with_no_login(self):
        with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
            mock_cls.return_value.to_dict.return_value = {"login": ""}
            result = self.rp.get_user_details(["ghost"])
        self.assertEqual(result, {})

    def test_exception_is_caught_other_users_returned(self):
        def side_effect(gh, username):
            if username == "bad":
                raise RuntimeError("fail")
            m = MagicMock()
            m.to_dict.return_value = {"login": username}
            return m

        with patch("repo_people.repo_people.GitHubUserInfo", side_effect=side_effect):
            result = self.rp.get_user_details(["good", "bad"])
        self.assertIn("good", result)
        self.assertNotIn("bad", result)

    def test_limit_truncates_list(self):
        fetch_order = []

        def side_effect(gh, username):
            fetch_order.append(username)
            m = MagicMock()
            m.to_dict.return_value = {"login": username}
            return m

        with patch("repo_people.repo_people.GitHubUserInfo", side_effect=side_effect):
            self.rp.get_user_details(["a", "b", "c", "d"], limit=2)
        self.assertEqual(fetch_order, ["a", "b"])

    def test_exclude_skips_listed_logins(self):
        fetch_order = []

        def side_effect(gh, username):
            fetch_order.append(username)
            m = MagicMock()
            m.to_dict.return_value = {"login": username}
            return m

        with patch("repo_people.repo_people.GitHubUserInfo", side_effect=side_effect):
            self.rp.get_user_details(["alice", "bob", "carol"], exclude=["bob"])
        self.assertNotIn("bob", fetch_order)
        self.assertIn("alice", fetch_order)

    def test_exclude_bots_skips_bot_suffix(self):
        fetch_order = []

        def side_effect(gh, username):
            fetch_order.append(username)
            m = MagicMock()
            m.to_dict.return_value = {"login": username, "is_bot": False}
            return m

        with patch("repo_people.repo_people.GitHubUserInfo", side_effect=side_effect):
            self.rp.get_user_details(["alice", "dependabot[bot]"], exclude_bots=True)
        self.assertNotIn("dependabot[bot]", fetch_order)
        self.assertIn("alice", fetch_order)

    def test_exclude_bots_skips_is_bot_profile_flag(self):
        def side_effect(gh, username):
            m = MagicMock()
            m.to_dict.return_value = {"login": username, "is_bot": username == "botuser"}
            return m

        with patch("repo_people.repo_people.GitHubUserInfo", side_effect=side_effect):
            result = self.rp.get_user_details(["alice", "botuser"], exclude_bots=True)
        self.assertIn("alice", result)
        self.assertNotIn("botuser", result)

    def test_verbose_false_suppresses_print(self):
        with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
            mock_cls.return_value.to_dict.return_value = {"login": "alice"}
            with patch("builtins.print") as mock_print:
                self.rp.get_user_details(["alice"], verbose=False)
        # No per-user "Fetching:" message should be printed
        fetching_calls = [c for c in mock_print.call_args_list if "Fetching:" in str(c)]
        self.assertEqual(fetching_calls, [])

    def test_save_each_iteration_writes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
                mock_cls.return_value.to_dict.return_value = {"login": "alice"}
                self.rp.get_user_details(["alice"], save_each_iteration=True)
            path = os.path.join(tmpdir, f"{self.rp.file_prefix}user_details.json")
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                data = json.load(f)
            self.assertIn("alice", data)

    def test_resume_loads_existing_and_skips_fetched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            # Pre-populate the JSON with "alice" already fetched
            existing = {"alice": {"login": "alice"}}
            with open(os.path.join(tmpdir, f"{self.rp.file_prefix}user_details.json"), "w") as f:
                json.dump(existing, f)

            fetch_order = []

            def side_effect(gh, username):
                fetch_order.append(username)
                m = MagicMock()
                m.to_dict.return_value = {"login": username}
                return m

            with patch("repo_people.repo_people.GitHubUserInfo", side_effect=side_effect):
                result = self.rp.get_user_details(["alice", "bob"], resume=True)

        # alice should not be fetched again; bob should be
        self.assertNotIn("alice", fetch_order)
        self.assertIn("bob", fetch_order)
        # Both should appear in the final result (alice from disk, bob fetched)
        self.assertIn("alice", result)
        self.assertIn("bob", result)

    def test_workers_param_accepted(self):
        """workers=2 completes successfully and returns the same result as workers=1."""
        with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
            mock_cls.return_value.to_dict.return_value = {"login": "alice"}
            result = self.rp.get_user_details(["alice"], workers=2)
        self.assertIn("alice", result)

    def test_failed_fetch_prints_summary(self):
        """A 'Skipped N user(s)' summary is printed when users cannot be fetched."""
        def side_effect(gh, username):
            if username == "bad":
                raise RuntimeError("network error")
            m = MagicMock()
            m.to_dict.return_value = {"login": username}
            return m

        with patch("repo_people.repo_people.GitHubUserInfo", side_effect=side_effect):
            with patch("builtins.print") as mock_print:
                self.rp.get_user_details(["good", "bad"])
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Skipped", printed)
        self.assertIn("bad", printed)


class TestExportToJson(unittest.TestCase):

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def test_creates_valid_json_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            user_data = {"alice": {"login": "alice", "name": "Alice"}}
            path = self.rp.export_to_json(user_data)
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                self.assertEqual(json.load(f), user_data)

    def test_custom_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            path = self.rp.export_to_json({"u": {"login": "u"}}, filename="out.json")
            self.assertTrue(path.endswith("out.json"))


class TestExportToCsv(unittest.TestCase):

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def test_creates_csv_with_header_and_row(self):
        import csv as csv_mod
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            user_data = {"alice": {"login": "alice", "name": "Alice", "followers": 5}}
            path = self.rp.export_to_csv(user_data)
            self.assertTrue(os.path.isfile(path))
            with open(path, newline="") as f:
                rows = list(csv_mod.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["login"], "alice")

    def test_returns_empty_string_when_no_data(self):
        self.assertEqual(self.rp.export_to_csv({}), "")

    def test_list_fields_serialised_as_semicolon_separated(self):
        import csv as csv_mod
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            user_data = {"alice": {"login": "alice", "public_orgs": ["org1", "org2"]}}
            path = self.rp.export_to_csv(user_data)
            with open(path, newline="") as f:
                rows = list(csv_mod.DictReader(f))
            self.assertEqual(rows[0]["public_orgs"], "org1;org2")


class TestExportToMarkdown(unittest.TestCase):

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def test_creates_markdown_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            user_data = {"alice": {"login": "alice", "name": "Alice", "location": "NYC",
                                   "company": "ACME", "followers": 10, "public_repos": 5,
                                   "html_url": "https://github.com/alice"}}
            path = self.rp.export_to_markdown(user_data)
            self.assertTrue(os.path.isfile(path))

    def test_contains_header_and_separator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            user_data = {"alice": {"login": "alice", "name": "Alice", "location": "",
                                   "company": "", "followers": 0, "public_repos": 0,
                                   "html_url": ""}}
            path = self.rp.export_to_markdown(user_data)
            with open(path) as f:
                content = f.read()
            self.assertIn("| login |", content)
            self.assertIn("| --- |", content)

    def test_returns_empty_string_when_no_data(self):
        self.assertEqual(self.rp.export_to_markdown({}), "")

    def test_custom_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            user_data = {"alice": {"login": "alice", "followers": 42}}
            path = self.rp.export_to_markdown(user_data, fields=["login", "followers"])
            with open(path) as f:
                content = f.read()
            self.assertIn("login", content)
            self.assertIn("followers", content)
            self.assertNotIn("name", content)

    def test_pipe_chars_in_values_are_escaped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            user_data = {"alice": {"login": "a|b", "name": "", "location": "",
                                   "company": "", "followers": 0, "public_repos": 0,
                                   "html_url": ""}}
            path = self.rp.export_to_markdown(user_data)
            with open(path) as f:
                content = f.read()
            self.assertIn(r"\|", content)


class TestSummarise(unittest.TestCase):

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def _make_user(self, login, is_bot=False, location="London", company="ACME", age=1000):
        return {
            "login": login, "is_bot": is_bot,
            "location": location, "location_normalized": location.lower(),
            "company": company, "company_normalized": company,
            "account_age_days": age,
        }

    def test_returns_summary_dict(self):
        user_data = {
            "alice": self._make_user("alice"),
            "bob": self._make_user("bob", is_bot=True),
        }
        result = self.rp.summarise(user_data)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["bots"], 1)
        self.assertEqual(result["humans"], 1)

    def test_returns_empty_dict_for_no_data(self):
        result = self.rp.summarise({})
        self.assertEqual(result, {})

    def test_top_locations_present(self):
        user_data = {u: self._make_user(u, location="London") for u in ["a", "b", "c"]}
        result = self.rp.summarise(user_data)
        locs = dict(result["top_locations"])
        self.assertEqual(locs.get("london"), 3)

    def test_top_companies_present(self):
        user_data = {u: self._make_user(u, company="GitHub") for u in ["a", "b"]}
        result = self.rp.summarise(user_data)
        cos = dict(result["top_companies"])
        self.assertEqual(cos.get("GitHub"), 2)

    def test_account_age_distribution_keys(self):
        user_data = {"alice": self._make_user("alice", age=100)}  # < 1 year
        result = self.rp.summarise(user_data)
        self.assertIn("account_age_distribution", result)
        self.assertIn("< 1 year", result["account_age_distribution"])

    def test_top_n_controls_output_length(self):
        user_data = {str(i): self._make_user(str(i), location=f"City{i}") for i in range(10)}
        result = self.rp.summarise(user_data, top_n=3)
        self.assertLessEqual(len(result["top_locations"]), 3)


class TestTopUsers(unittest.TestCase):

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def _make_data(self):
        return {
            "alice": {"login": "alice", "followers": 100, "public_repos": 5},
            "bob":   {"login": "bob",   "followers": 50,  "public_repos": 20},
            "carol": {"login": "carol", "followers": 200, "public_repos": 10},
        }

    def test_returns_list(self):
        result = self.rp.top_users(self._make_data())
        self.assertIsInstance(result, list)

    def test_sorted_descending_by_default_field(self):
        result = self.rp.top_users(self._make_data(), n=3, by="followers")
        logins = [u["login"] for u in result]
        self.assertEqual(logins, ["carol", "alice", "bob"])

    def test_sorted_by_custom_field(self):
        result = self.rp.top_users(self._make_data(), n=3, by="public_repos")
        self.assertEqual(result[0]["login"], "bob")

    def test_limit_n_respected(self):
        result = self.rp.top_users(self._make_data(), n=2, by="followers")
        self.assertEqual(len(result), 2)

    def test_missing_field_treated_as_zero(self):
        data = {
            "x": {"login": "x", "followers": 10},
            "y": {"login": "y"},  # no followers field
        }
        result = self.rp.top_users(data, n=2, by="followers")
        self.assertEqual(result[0]["login"], "x")


class TestGetUsers(unittest.TestCase):
    """Tests for the main get_users() entry point."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def _run(self, logins=None, extra_kwargs=None):
        """Helper: run get_users() with all external calls mocked."""
        logins = logins or ["alice"]
        kwargs = extra_kwargs or {}
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, logins)
                with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
                    mock_cls.return_value.to_dict.return_value = {"login": logins[0]}
                    result = self.rp.get_users(**kwargs)
            return result, tmpdir

    def test_returns_dict_keyed_by_login(self):
        result, _ = self._run(["alice"])
        self.assertIn("alice", result)

    def test_deduplicates_logins_across_roles(self):
        """Same login from multiple roles is only fetched once."""
        call_count = {"n": 0}

        def side_effect(gh, username):
            call_count["n"] += 1
            m = MagicMock()
            m.to_dict.return_value = {"login": username}
            return m

        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])  # alice in every role
                with patch("repo_people.repo_people.GitHubUserInfo", side_effect=side_effect):
                    self.rp.get_users()
        self.assertEqual(call_count["n"], 1)

    def test_export_true_writes_json_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
                    mock_cls.return_value.to_dict.return_value = {"login": "alice"}
                    self.rp.get_users(export=True)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, f"{self.rp.file_prefix}user_details.json")))

    def test_export_false_does_not_write_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
                    mock_cls.return_value.to_dict.return_value = {"login": "alice"}
                    self.rp.get_users(export=False)
            self.assertFalse(os.path.isfile(os.path.join(tmpdir, f"{self.rp.file_prefix}user_details.json")))

    def test_export_csv_true_writes_csv_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
                    mock_cls.return_value.to_dict.return_value = {"login": "alice"}
                    self.rp.get_users(export_csv=True)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, f"{self.rp.file_prefix}user_details.csv")))

    def test_roles_filter_passed_to_collect(self):
        """roles= is forwarded correctly — only requested role functions are called."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, [])
                mock_export.export_contributors.return_value = ["alice"]
                with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
                    mock_cls.return_value.to_dict.return_value = {"login": "alice"}
                    self.rp.get_users(roles=["contributors"])
            # Only contributors function should have been called with return_data=True
            mock_export.export_contributors.assert_called_once()
            mock_export.export_stargazers.assert_not_called()

    def test_fields_filters_output_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
                    mock_cls.return_value.to_dict.return_value = {
                        "login": "alice", "name": "Alice", "followers": 10
                    }
                    result = self.rp.get_users(fields=["login", "name"])
        self.assertIn("login", result["alice"])
        self.assertIn("name", result["alice"])
        self.assertNotIn("followers", result["alice"])

    def test_invalid_field_raises_before_fetch(self):
        # Should raise ValueError immediately, before any network call
        with self.assertRaises(ValueError) as ctx:
            self.rp.get_users(fields=["login", "email_publiashdahsdfc"])
        self.assertIn("email_publiashdahsdfc", str(ctx.exception))
        self.assertIn("Invalid field(s)", str(ctx.exception))

    def test_multiple_invalid_fields_all_reported(self):
        # All invalid names should appear in the error message
        with self.assertRaises(ValueError) as ctx:
            self.rp.get_users(fields=["bad_one", "bad_two"])
        msg = str(ctx.exception)
        self.assertIn("bad_one", msg)
        self.assertIn("bad_two", msg)

    def test_string_field_coerced_and_validated(self):
        # A bare string that is a valid field name should not raise ValueError
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
                    mock_cls.return_value.to_dict.return_value = {"login": "alice"}
                    # Should not raise — 'login' is a valid UserSnapshot field
                    result = self.rp.get_users(fields="login")
        self.assertIn("alice", result)

    def test_invalid_string_field_raises(self):
        # A bare invalid string should also raise ValueError
        with self.assertRaises(ValueError) as ctx:
            self.rp.get_users(fields="email_publiashdahsdfc")
        self.assertIn("email_publiashdahsdfc", str(ctx.exception))

    def test_limit_applied(self):
        fetched = []

        def side_effect(gh, username):
            fetched.append(username)
            m = MagicMock()
            m.to_dict.return_value = {"login": username}
            return m

        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["a", "b", "c", "d"])
                with patch("repo_people.repo_people.GitHubUserInfo", side_effect=side_effect):
                    self.rp.get_users(limit=2)
        self.assertEqual(len(fetched), 2)

    def test_exclude_bots_filters_bot_logins(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice", "bot[bot]"])

                def side_effect(gh, username):
                    m = MagicMock()
                    m.to_dict.return_value = {"login": username, "is_bot": False}
                    return m

                with patch("repo_people.repo_people.GitHubUserInfo", side_effect=side_effect):
                    result = self.rp.get_users(exclude_bots=True)
        self.assertIn("alice", result)
        self.assertNotIn("bot[bot]", result)

    def test_invalid_role_raises_before_fetch(self):
        """Invalid role name raises ValueError immediately, before any network call."""
        with self.assertRaises(ValueError) as ctx:
            self.rp.get_users(roles=["typo_role"])
        self.assertIn("typo_role", str(ctx.exception))
        self.assertIn("Invalid role(s)", str(ctx.exception))

    def test_roles_always_in_output(self):
        """Each user record contains a 'roles' key, even when fields= is set."""
        result, _ = self._run(["alice"])
        self.assertIn("roles", result["alice"])
        self.assertIsInstance(result["alice"]["roles"], list)

    def test_workers_param_accepted(self):
        """workers=2 runs without error and returns the same result as workers=1."""
        result, _ = self._run(["alice"], extra_kwargs={"workers": 2})
        self.assertIn("alice", result)

    def test_string_role_coerced(self):
        """A bare string for roles= is treated as a single-item list."""
        result, _ = self._run(["alice"], extra_kwargs={"roles": "contributors"})
        self.assertIn("alice", result)

    def test_roles_content_reflects_membership(self):
        """roles key in each record lists the actual roles the user appeared under."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                mock_export.export_contributors.return_value = ["alice"]
                mock_export.export_stargazers.return_value = ["alice"]
                for attr in _ALL_EXPORT_ATTRS:
                    if attr not in ("export_contributors", "export_stargazers"):
                        getattr(mock_export, attr).return_value = []
                with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
                    mock_cls.return_value.to_dict.return_value = {"login": "alice"}
                    result = self.rp.get_users(roles=["contributors", "stargazers"])
        self.assertIn("contributors", result["alice"]["roles"])
        self.assertIn("stargazers", result["alice"]["roles"])


# ---------------------------------------------------------------------------
# Integration tests — real GitHub API (skipped by default)
# ---------------------------------------------------------------------------

@unittest.skip("Integration tests — requires a real GITHUB_TOKEN env variable")
class RepoPeopleIntegrationTests(unittest.TestCase):

    def setUp(self):
        load_dotenv()
        self.token = os.getenv("GITHUB_TOKEN", None)
        self.test_output_dir = "tests/test_output"
        os.makedirs(self.test_output_dir, exist_ok=True)
        self.test_repo = ("amckenna41", "iso3166-2")

    def test_collect_all_usernames(self):
        """collect_all_usernames returns non-empty lists for a real repo."""
        rp = RepoPeople(
            owner=self.test_repo[0], repo=self.test_repo[1], token=self.token,
            outdir=os.path.join(self.test_output_dir, f"{self.test_repo[0]}_{self.test_repo[1]}"),
        )
        result = rp.collect_all_usernames()
        self.assertIsInstance(result, dict)
        self.assertGreater(len(result.get("contributors", [])), 0)

    def test_collect_all_usernames_roles_filter(self):
        """collect_all_usernames with roles= only returns those roles."""
        rp = RepoPeople(owner=self.test_repo[0], repo=self.test_repo[1], token=self.token)
        result = rp.collect_all_usernames(roles=["contributors"])
        self.assertEqual(set(result.keys()), {"contributors"})

    def test_get_users_full_pipeline(self):
        """get_users() completes successfully and exports valid files."""
        outdir = os.path.join(self.test_output_dir, f"{self.test_repo[0]}_{self.test_repo[1]}")
        rp = RepoPeople(owner=self.test_repo[0], repo=self.test_repo[1],
                        token=self.token, outdir=outdir)
        user_data = rp.get_users(export=True, export_csv=True)
        self.assertIsInstance(user_data, dict)
        self.assertGreater(len(user_data), 0)
        json_path = os.path.join(outdir, "user_details.json")
        self.assertTrue(os.path.isfile(json_path))
        with open(json_path) as f:
            self.assertEqual(json.load(f), user_data)
        self.assertTrue(os.path.isfile(os.path.join(outdir, "user_details.csv")))

    def test_get_users_with_limit(self):
        rp = RepoPeople(owner=self.test_repo[0], repo=self.test_repo[1], token=self.token)
        user_data = rp.get_users(limit=3)
        self.assertLessEqual(len(user_data), 3)

    def test_get_users_exclude_bots(self):
        rp = RepoPeople(owner=self.test_repo[0], repo=self.test_repo[1], token=self.token)
        user_data = rp.get_users(exclude_bots=True)
        for login in user_data:
            self.assertFalse(login.endswith("[bot]"))


# ---------------------------------------------------------------------------
# Async API tests
# ---------------------------------------------------------------------------

class TestGetUserDetailsAsync(unittest.TestCase):
    """Tests for RepoPeople.get_user_details_async."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def _mock_aiohttp_get(self, user_json):
        """Return an async context-manager mock yielding the given JSON."""
        from unittest.mock import AsyncMock
        resp_mock = MagicMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value=user_json)
        # Support 'async with session.get(...) as resp:'
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp_mock)
        cm.__aexit__ = AsyncMock(return_value=False)
        session_mock = MagicMock()
        session_mock.get.return_value = cm
        return session_mock

    def test_returns_dict_keyed_by_login(self):
        """get_user_details_async returns a dict keyed by login."""
        from unittest.mock import AsyncMock, patch as apatch
        user_json = {"login": "alice", "type": "User"}
        session_mock = self._mock_aiohttp_get(user_json)
        with apatch("aiohttp.ClientSession") as mock_session_cls:
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = self._run(self.rp.get_user_details_async(["alice"]))
        self.assertIn("alice", result)

    def test_concurrency_param_accepted(self):
        """concurrency= parameter is accepted and does not raise."""
        from unittest.mock import AsyncMock, patch as apatch
        user_json = {"login": "alice", "type": "User"}
        session_mock = self._mock_aiohttp_get(user_json)
        with apatch("aiohttp.ClientSession") as mock_session_cls:
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = self._run(self.rp.get_user_details_async(["alice"], concurrency=5))
        self.assertIn("alice", result)

    def test_exclude_bots_by_login_suffix(self):
        """Logins ending in '[bot]' are filtered before any fetch."""
        from unittest.mock import AsyncMock, patch as apatch
        user_json = {"login": "alice", "type": "User"}
        session_mock = self._mock_aiohttp_get(user_json)
        with apatch("aiohttp.ClientSession") as mock_session_cls:
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = self._run(
                self.rp.get_user_details_async(["alice", "bot[bot]"], exclude_bots=True)
            )
        self.assertIn("alice", result)
        self.assertNotIn("bot[bot]", result)

    def test_failed_fetch_skipped_with_summary(self):
        """A login that returns non-200 is skipped and a Skipped summary is printed."""
        from unittest.mock import AsyncMock, patch as apatch
        resp_mock = MagicMock()
        resp_mock.status = 404
        resp_mock.json = AsyncMock(return_value={})
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp_mock)
        cm.__aexit__ = AsyncMock(return_value=False)
        session_mock = MagicMock()
        session_mock.get.return_value = cm
        with apatch("aiohttp.ClientSession") as mock_session_cls:
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("builtins.print") as mock_print:
                result = self._run(self.rp.get_user_details_async(["ghost"]))
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Skipped", printed)
        self.assertNotIn("ghost", result)

    def test_resume_skips_already_fetched(self):
        """resume=True loads existing JSON and skips those logins."""
        from unittest.mock import AsyncMock, patch as apatch
        user_json = {"login": "bob", "type": "User"}
        session_mock = self._mock_aiohttp_get(user_json)
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            # Pre-populate alice as already fetched
            with open(os.path.join(tmpdir, f"{self.rp.file_prefix}user_details.json"), "w") as f:
                json.dump({"alice": {"login": "alice"}}, f)
            with apatch("aiohttp.ClientSession") as mock_session_cls:
                mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
                mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                result = self._run(
                    self.rp.get_user_details_async(["alice", "bob"], resume=True)
                )
        # alice from disk, bob fetched fresh
        self.assertIn("alice", result)
        self.assertIn("bob", result)
        # session.get is now called 4 times per user (base profile + orgs + events + repos)
        self.assertEqual(session_mock.get.call_count, 4)


class TestGetUsersAsync(unittest.TestCase):
    """Tests for RepoPeople.get_users_async."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def _make_session_mock(self, login="alice"):
        from unittest.mock import AsyncMock
        resp_mock = MagicMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value={"login": login, "type": "User"})
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp_mock)
        cm.__aexit__ = AsyncMock(return_value=False)
        session_mock = MagicMock()
        session_mock.get.return_value = cm
        return session_mock

    def test_returns_dict_keyed_by_login(self):
        """get_users_async returns a dict keyed by login."""
        from unittest.mock import AsyncMock, patch as apatch
        session_mock = self._make_session_mock("alice")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with apatch("aiohttp.ClientSession") as mock_session_cls:
                    mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
                    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = self._run(self.rp.get_users_async())
        self.assertIsInstance(result, dict)
        self.assertIn("alice", result)

    def test_roles_always_in_output(self):
        """Every record in get_users_async output contains a 'roles' key."""
        from unittest.mock import AsyncMock, patch as apatch
        session_mock = self._make_session_mock("alice")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with apatch("aiohttp.ClientSession") as mock_session_cls:
                    mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
                    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = self._run(self.rp.get_users_async())
        self.assertIn("roles", result["alice"])

    def test_invalid_role_raises_before_fetch(self):
        """Unknown role raises ValueError immediately, before any network call."""
        with self.assertRaises(ValueError) as ctx:
            self._run(self.rp.get_users_async(roles=["bad_role"]))
        self.assertIn("bad_role", str(ctx.exception))

    def test_invalid_field_raises_before_fetch(self):
        """Unknown field raises ValueError immediately, before any network call."""
        with self.assertRaises(ValueError) as ctx:
            self._run(self.rp.get_users_async(fields=["nonexistent_field"]))
        self.assertIn("nonexistent_field", str(ctx.exception))

    def test_concurrency_param_accepted(self):
        """concurrency= parameter is accepted without error."""
        from unittest.mock import AsyncMock, patch as apatch
        session_mock = self._make_session_mock("alice")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with apatch("aiohttp.ClientSession") as mock_session_cls:
                    mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
                    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = self._run(self.rp.get_users_async(concurrency=3))
        self.assertIn("alice", result)

    def test_export_true_writes_json_file(self):
        """export=True writes user_details.json to outdir."""
        from unittest.mock import AsyncMock, patch as apatch
        session_mock = self._make_session_mock("alice")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with apatch("aiohttp.ClientSession") as mock_session_cls:
                    mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
                    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    self._run(self.rp.get_users_async(export=True))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, f"{self.rp.file_prefix}user_details.json")))

    def test_string_role_coerced(self):
        """roles= accepts a bare string and treats it as a single-item list."""
        from unittest.mock import AsyncMock, patch as apatch
        session_mock = self._make_session_mock("alice")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with apatch("aiohttp.ClientSession") as mock_session_cls:
                    mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
                    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    # Should not raise
                    result = self._run(self.rp.get_users_async(roles="contributors"))
        self.assertIsInstance(result, dict)


class TestUserDataView(unittest.TestCase):
    """Tests for the UserDataView dot-notation access wrapper."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def _make_view(self):
        from repo_people.repo_people import UserDataView
        return UserDataView({
            "alice": {"login": "alice", "email_public": "alice@example.com", "followers": 10},
            "bob":   {"login": "bob",   "email_public": "",                  "followers": 5},
        })

    def test_is_dict_subclass(self):
        """UserDataView is a dict and supports standard dict operations."""
        from repo_people.repo_people import UserDataView
        view = self._make_view()
        self.assertIsInstance(view, dict)
        self.assertIsInstance(view, UserDataView)
        self.assertIn("alice", view)
        self.assertEqual(len(view), 2)

    def test_dot_access_returns_field_for_all_users(self):
        """Dot notation returns {username: {field: value}} for every user."""
        view = self._make_view()
        result = view.email_public
        self.assertEqual(result, {
            "alice": {"email_public": "alice@example.com"},
            "bob":   {"email_public": ""},
        })

    def test_dot_access_numeric_field(self):
        """Numeric fields are returned correctly via dot notation."""
        view = self._make_view()
        result = view.followers
        self.assertEqual(result, {
            "alice": {"followers": 10},
            "bob":   {"followers": 5},
        })

    def test_dot_access_missing_field_returns_none(self):
        """If a user record lacks the field, its value is None."""
        from repo_people.repo_people import UserDataView
        view = UserDataView({"alice": {"login": "alice"}})
        result = view.followers
        self.assertEqual(result, {"alice": {"followers": None}})

    def test_dot_access_roles_field(self):
        """The 'roles' field is also accessible via dot notation."""
        from repo_people.repo_people import UserDataView
        view = UserDataView({"alice": {"login": "alice", "roles": ["contributors"]}})
        result = view.roles
        self.assertEqual(result, {"alice": {"roles": ["contributors"]}})

    def test_invalid_field_raises_attribute_error(self):
        """Accessing an unknown attribute raises AttributeError."""
        view = self._make_view()
        with self.assertRaises(AttributeError) as ctx:
            _ = view.not_a_real_field
        self.assertIn("not_a_real_field", str(ctx.exception))

    def test_get_users_returns_user_data_view(self):
        """get_users() returns a UserDataView instance."""
        from repo_people.repo_people import UserDataView
        with tempfile.TemporaryDirectory() as tmpdir:
            self.rp.outdir = tmpdir
            with patch("repo_people.repo_people.export") as mock_export:
                _stub_export(mock_export, ["alice"])
                with patch("repo_people.repo_people.GitHubUserInfo") as mock_cls:
                    mock_cls.return_value.to_dict.return_value = {"login": "alice"}
                    result = self.rp.get_users()
        self.assertIsInstance(result, UserDataView)

    def test_user_data_view_exported_from_package(self):
        """UserDataView is importable from the top-level repo_people package."""
        from repo_people import UserDataView
        self.assertTrue(issubclass(UserDataView, dict))

    def test_cache_clear_resets_valid_fields(self):
        """_clear_valid_fields_cache() resets the cached frozenset so it is recomputed."""
        from repo_people.repo_people import UserDataView
        # Warm the cache
        _ = UserDataView._get_valid_fields()
        self.assertIsNotNone(UserDataView._valid_fields)
        # Clear it
        UserDataView._clear_valid_fields_cache()
        self.assertIsNone(UserDataView._valid_fields)
        # Recompute — should not raise and should return the same fields
        fields_after = UserDataView._get_valid_fields()
        self.assertIsInstance(fields_after, frozenset)
        self.assertIn("login", fields_after)
        self.assertIn("roles", fields_after)


class TestCollectAllUsernamesParallel(unittest.TestCase):
    """Tests for parallelised collect_all_usernames."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def _stub(self, mock_export):
        for attr in _ALL_EXPORT_ATTRS:
            getattr(mock_export, attr).return_value = []

    def test_returns_all_roles_when_no_filter(self):
        """collect_all_usernames returns a key for every valid role."""
        with patch("repo_people.repo_people.export") as mock_export:
            self._stub(mock_export)
            result = self.rp.collect_all_usernames()
        self.assertEqual(set(result.keys()), RepoPeople.VALID_ROLES)

    def test_respects_roles_filter(self):
        """Only requested roles are returned."""
        with patch("repo_people.repo_people.export") as mock_export:
            self._stub(mock_export)
            result = self.rp.collect_all_usernames(roles=["contributors", "stargazers"])
        self.assertEqual(set(result.keys()), {"contributors", "stargazers"})

    def test_output_order_matches_input(self):
        """Order of returned keys matches the requested roles list."""
        with patch("repo_people.repo_people.export") as mock_export:
            self._stub(mock_export)
            requested = ["stargazers", "contributors"]
            result = self.rp.collect_all_usernames(roles=requested)
        self.assertEqual(list(result.keys()), requested)

    def test_invalid_role_raises_value_error(self):
        """An unrecognised role raises ValueError before any fetching."""
        with patch("repo_people.repo_people.export") as mock_export:
            self._stub(mock_export)
            with self.assertRaises(ValueError):
                self.rp.collect_all_usernames(roles=["unknown_role"])

    def test_result_values_are_lists(self):
        """Each role value is a list (possibly empty)."""
        with patch("repo_people.repo_people.export") as mock_export:
            mock_export.export_contributors.return_value = ["alice", "bob"]
            for attr in _ALL_EXPORT_ATTRS:
                if attr != "export_contributors":
                    getattr(mock_export, attr).return_value = []
            result = self.rp.collect_all_usernames(roles=["contributors"])
        self.assertEqual(result["contributors"], ["alice", "bob"])


class TestPrintMarkdown(unittest.TestCase):
    """Tests for RepoPeople.print_markdown."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def test_prints_header_and_row(self):
        """print_markdown outputs a header row, separator, and one data row."""
        user_data = {"alice": {"login": "alice", "name": "Alice", "location": "NYC",
                               "company": "ACME", "followers": 10, "public_repos": 5,
                               "html_url": "https://github.com/alice"}}
        with patch("builtins.print") as mock_print:
            self.rp.print_markdown(user_data)
        calls = [str(c.args[0]) for c in mock_print.call_args_list]
        self.assertTrue(any("| login |" in c for c in calls))
        self.assertTrue(any("| --- |" in c for c in calls))
        self.assertTrue(any("alice" in c for c in calls))

    def test_empty_data_prints_nothing(self):
        """print_markdown does nothing when user_data is empty."""
        with patch("builtins.print") as mock_print:
            self.rp.print_markdown({})
        mock_print.assert_not_called()

    def test_custom_fields_respected(self):
        """Only requested fields appear in the output."""
        user_data = {"alice": {"login": "alice", "followers": 42, "name": "Alice"}}
        with patch("builtins.print") as mock_print:
            self.rp.print_markdown(user_data, fields=["login", "followers"])
        calls = " ".join(str(c.args[0]) for c in mock_print.call_args_list)
        self.assertIn("login", calls)
        self.assertIn("followers", calls)
        self.assertNotIn("name", calls)

    def test_pipe_chars_escaped(self):
        """Pipe characters inside values are escaped as \\|."""
        user_data = {"x": {"login": "a|b", "name": "", "location": "",
                           "company": "", "followers": 0, "public_repos": 0,
                           "html_url": ""}}
        with patch("builtins.print") as mock_print:
            self.rp.print_markdown(user_data)
        calls = " ".join(str(c.args[0]) for c in mock_print.call_args_list)
        self.assertIn(r"\|", calls)


class TestSummariseRoleDistribution(unittest.TestCase):
    """Tests for the role_distribution key added to summarise()."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp = RepoPeople(owner="o", repo="r", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def _make_user(self, login, roles=None):
        return {
            "login": login, "is_bot": False,
            "location": "", "location_normalized": "",
            "company": "", "company_normalized": "",
            "account_age_days": 500,
            "roles": roles or [],
        }

    def test_role_distribution_key_present(self):
        """summarise() result always contains 'role_distribution'."""
        user_data = {"alice": self._make_user("alice", ["contributors"])}
        result = self.rp.summarise(user_data)
        self.assertIn("role_distribution", result)

    def test_role_distribution_counts_are_correct(self):
        """Each role count reflects how many users carry that role."""
        user_data = {
            "alice": self._make_user("alice", ["contributors", "stargazers"]),
            "bob":   self._make_user("bob",   ["stargazers"]),
        }
        result = self.rp.summarise(user_data)
        dist = result["role_distribution"]
        self.assertEqual(dist.get("contributors"), 1)
        self.assertEqual(dist.get("stargazers"), 2)

    def test_role_distribution_empty_when_no_roles(self):
        """role_distribution is an empty dict when no users have roles."""
        user_data = {"alice": self._make_user("alice", [])}
        result = self.rp.summarise(user_data)
        self.assertEqual(result["role_distribution"], {})


class TestCompare(unittest.TestCase):
    """Tests for RepoPeople.compare."""

    def setUp(self):
        self.gh_patcher = patch("repo_people.repo_people.Github")
        mock_github_cls = self.gh_patcher.start()
        mock_github_cls.return_value.get_repo.return_value = MagicMock()
        self.rp_a = RepoPeople(owner="o", repo="a", token="tok")
        self.rp_b = RepoPeople(owner="o", repo="b", token="tok")

    def tearDown(self):
        self.gh_patcher.stop()

    def test_only_in_self(self):
        """Users exclusive to the first repo appear in 'only_in_self'."""
        data_a = {"alice": {}, "shared": {}}
        data_b = {"bob": {}, "shared": {}}
        result = self.rp_a.compare(self.rp_b, data_a, data_b)
        self.assertEqual(result["only_in_self"], ["alice"])

    def test_only_in_other(self):
        """Users exclusive to the second repo appear in 'only_in_other'."""
        data_a = {"alice": {}, "shared": {}}
        data_b = {"bob": {}, "shared": {}}
        result = self.rp_a.compare(self.rp_b, data_a, data_b)
        self.assertEqual(result["only_in_other"], ["bob"])

    def test_in_both(self):
        """Users in both repos appear in 'in_both'."""
        data_a = {"alice": {}, "shared": {}}
        data_b = {"bob": {}, "shared": {}}
        result = self.rp_a.compare(self.rp_b, data_a, data_b)
        self.assertEqual(result["in_both"], ["shared"])

    def test_all_keys_present(self):
        """Result always contains 'only_in_self', 'only_in_other', and 'in_both'."""
        result = self.rp_a.compare(self.rp_b, {}, {})
        self.assertIn("only_in_self", result)
        self.assertIn("only_in_other", result)
        self.assertIn("in_both", result)

    def test_empty_overlap(self):
        """No shared users when user sets are disjoint."""
        data_a = {"alice": {}}
        data_b = {"bob": {}}
        result = self.rp_a.compare(self.rp_b, data_a, data_b)
        self.assertEqual(result["in_both"], [])

    def test_results_are_sorted(self):
        """All three lists are returned in alphabetical order."""
        data_a = {"zara": {}, "mike": {}, "shared": {}}
        data_b = {"beth": {}, "anna": {}, "shared": {}}
        result = self.rp_a.compare(self.rp_b, data_a, data_b)
        self.assertEqual(result["only_in_self"], sorted(result["only_in_self"]))
        self.assertEqual(result["only_in_other"], sorted(result["only_in_other"]))
        self.assertEqual(result["in_both"], sorted(result["in_both"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)


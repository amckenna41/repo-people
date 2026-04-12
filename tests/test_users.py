import json
import unittest
import unittest.mock
from dataclasses import fields as dc_fields
from datetime import datetime, timezone
from unittest.mock import MagicMock

from repo_people.users import GitHubUserInfo, UserSnapshot

unittest.TestLoader.sortTestMethodsUsing = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_mock_user(
    login="alice",
    name="Alice Example",
    company="@ACME",
    location="London",
    email="alice@example.com",
    blog="https://alice.dev",
    bio="Developer",
    twitter_username="alice_dev",
    avatar_url="https://avatars.githubusercontent.com/u/1",
    html_url="https://github.com/alice",
    hireable=True,
    site_admin=False,
    user_type="User",
    followers=100,
    following=20,
    public_repos=30,
    public_gists=5,
    node_id="MDQ6VXNlcjE=",
    user_id=1,
    created_at=None,
    updated_at=None,
):
    """Return a MagicMock NamedUser with sensible defaults."""
    m = MagicMock()
    m.login = login
    m.name = name
    m.company = company
    m.location = location
    m.email = email
    m.blog = blog
    m.bio = bio
    m.twitter_username = twitter_username
    m.avatar_url = avatar_url
    m.html_url = html_url
    m.hireable = hireable
    m.site_admin = site_admin
    m.type = user_type
    m.followers = followers
    m.following = following
    m.public_repos = public_repos
    m.public_gists = public_gists
    m.node_id = node_id
    m.id = user_id
    m.created_at = created_at or datetime(2020, 1, 1, tzinfo=timezone.utc)
    m.updated_at = updated_at or datetime(2024, 6, 1, tzinfo=timezone.utc)
    m.get_orgs.return_value = []
    m.get_public_events.return_value = iter([])
    m.get_repos.return_value = []
    return m


def _make_user_info(login="alice", mock_user=None, **kwargs):
    """Return a GitHubUserInfo backed by a fully mocked GitHub client."""
    mock_gh = MagicMock()
    mu = mock_user or _make_mock_user(login=login, **kwargs)
    mock_gh.get_user.return_value = mu
    info = GitHubUserInfo(gh=mock_gh, username=login)
    return info, mu, mock_gh


# ---------------------------------------------------------------------------
# UserSnapshot dataclass tests
# ---------------------------------------------------------------------------

class TestUserSnapshot(unittest.TestCase):

    def test_can_be_instantiated(self):
        """UserSnapshot can be created with all required fields."""
        snap = UserSnapshot(
            login="alice", id=1, node_id="x", type="User", name="Alice",
            company="ACME", location="London", email_public="a@b.com",
            email_domain="b.com", blog="", blog_host="", twitter="", bio="",
            avatar_url="", html_url="", hireable=False, site_admin=False,
            created_at="2020-01-01", updated_at="2024-01-01",
            followers=10, following=5, public_repos=3, public_gists=0,
            public_orgs=[], orgs_public_count=0, is_bot=False,
            last_public_event_at="",
        )
        self.assertEqual(snap.login, "alice")

    def test_optional_fields_have_correct_defaults(self):
        """Optional UserSnapshot fields default to documented values."""
        snap = UserSnapshot(
            login="x", id=None, node_id="", type="User", name="", company="",
            location="", email_public="", email_domain="", blog="", blog_host="",
            twitter="", bio="", avatar_url="", html_url="", hireable=False,
            site_admin=False, created_at="", updated_at="", followers=0,
            following=0, public_repos=0, public_gists=0, public_orgs=[],
            orgs_public_count=0, is_bot=False, last_public_event_at="",
        )
        self.assertFalse(snap.has_public_email)
        self.assertFalse(snap.has_blog)
        self.assertFalse(snap.has_twitter)
        self.assertEqual(snap.company_normalized, "")
        self.assertEqual(snap.account_age_days, 0)
        self.assertIsNone(snap.top_languages)
        self.assertIsNone(snap.is_collaborator)

    def test_field_count_at_least_30(self):
        """UserSnapshot has at least 30 fields — guards against accidental removals."""
        self.assertGreaterEqual(len(dc_fields(UserSnapshot)), 30)


# ---------------------------------------------------------------------------
# GitHubUserInfo.__init__ tests
# ---------------------------------------------------------------------------

class TestGitHubUserInfoInit(unittest.TestCase):

    def test_raises_if_neither_username_nor_user_obj(self):
        with self.assertRaises(ValueError):
            GitHubUserInfo(gh=MagicMock())

    def test_init_with_username(self):
        info = GitHubUserInfo(gh=MagicMock(), username="alice")
        self.assertEqual(info._username, "alice")

    def test_init_with_user_obj(self):
        mu = _make_mock_user(login="bob")
        info = GitHubUserInfo(gh=MagicMock(), user_obj=mu)
        self.assertEqual(info._username, "bob")

    def test_creates_client_from_token(self):
        import repo_people.users as users_mod
        with unittest.mock.patch.object(users_mod, "Github") as mock_cls:
            mock_cls.return_value = MagicMock()
            GitHubUserInfo(username="alice", token="tok")
        mock_cls.assert_called_once()

    def test_creates_unauthenticated_client_when_no_token(self):
        import repo_people.users as users_mod
        with unittest.mock.patch.object(users_mod, "Github") as mock_cls:
            mock_cls.return_value = MagicMock()
            GitHubUserInfo(username="alice")
        mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# Basic property tests
# ---------------------------------------------------------------------------

class TestProperties(unittest.TestCase):

    def setUp(self):
        self.info, self.mu, self.mock_gh = _make_user_info()

    def test_login(self):
        self.assertEqual(self.info.login, "alice")

    def test_name(self):
        self.assertEqual(self.info.name, "Alice Example")

    def test_company_raw(self):
        # Raw value keeps @ prefix; normalization is a separate method
        self.assertEqual(self.info.company, "@ACME")

    def test_location(self):
        self.assertEqual(self.info.location, "London")

    def test_email_public(self):
        self.assertEqual(self.info.email_public, "alice@example.com")

    def test_email_domain_extracted(self):
        self.assertEqual(self.info.email_domain, "example.com")

    def test_email_domain_empty_when_no_email(self):
        info, _, _ = _make_user_info(login="b", email="")
        self.assertEqual(info.email_domain, "")

    def test_blog(self):
        self.assertEqual(self.info.blog, "https://alice.dev")

    def test_blog_host_extracted(self):
        self.assertEqual(self.info.blog_host, "alice.dev")

    def test_blog_host_empty_when_no_blog(self):
        info, _, _ = _make_user_info(login="b", blog="")
        self.assertEqual(info.blog_host, "")

    def test_twitter(self):
        self.assertEqual(self.info.twitter, "alice_dev")

    def test_bio(self):
        self.assertEqual(self.info.bio, "Developer")

    def test_followers(self):
        self.assertEqual(self.info.followers, 100)

    def test_following(self):
        self.assertEqual(self.info.following, 20)

    def test_public_repos(self):
        self.assertEqual(self.info.public_repos, 30)

    def test_public_gists(self):
        self.assertEqual(self.info.public_gists, 5)

    def test_hireable(self):
        self.assertTrue(self.info.hireable)

    def test_site_admin(self):
        self.assertFalse(self.info.site_admin)

    def test_type(self):
        self.assertEqual(self.info.type, "User")

    def test_avatar_url(self):
        self.assertIn("githubusercontent", self.info.avatar_url)

    def test_html_url(self):
        self.assertIn("github.com/alice", self.info.html_url)

    def test_created_at_is_iso_string(self):
        val = self.info.created_at
        self.assertIsInstance(val, str)
        datetime.fromisoformat(val)  # should not raise

    def test_updated_at_is_iso_string(self):
        val = self.info.updated_at
        self.assertIsInstance(val, str)
        datetime.fromisoformat(val)

    def test_values_are_cached(self):
        """Second access to a property uses the cache, not a second API call."""
        _ = self.info.login
        _ = self.info.login
        self.mock_gh.get_user.assert_called_once()

    def test_public_orgs_empty(self):
        self.assertEqual(self.info.public_orgs, [])

    def test_public_orgs_returns_logins(self):
        mock_org = MagicMock()
        mock_org.login = "my-org"
        mu = _make_mock_user(login="x")
        mu.get_orgs.return_value = [mock_org]
        info, _, _ = _make_user_info(login="x", mock_user=mu)
        self.assertEqual(info.public_orgs, ["my-org"])

    def test_orgs_public_count_matches(self):
        self.assertEqual(self.info.orgs_public_count, len(self.info.public_orgs))

    def test_last_public_event_empty_when_no_events(self):
        self.assertEqual(self.info.last_public_event_at, "")

    def test_last_public_event_returns_date(self):
        mock_event = MagicMock()
        mock_event.created_at = datetime(2024, 5, 1, tzinfo=timezone.utc)
        mu = _make_mock_user(login="y")
        mu.get_public_events.return_value = iter([mock_event])
        info, _, _ = _make_user_info(login="y", mock_user=mu)
        self.assertIn("2024-05-01", info.last_public_event_at)


# ---------------------------------------------------------------------------
# is_bot detection tests
# ---------------------------------------------------------------------------

class TestIsBot(unittest.TestCase):

    def _info(self, login, user_type="User"):
        info, mu, _ = _make_user_info(login=login)
        mu.login = login
        mu.type = user_type
        info._cache.clear()
        return info

    def test_regular_user_not_bot(self):
        self.assertFalse(self._info("alice").is_bot)

    def test_bracket_bot_suffix(self):
        self.assertTrue(self._info("dependabot[bot]").is_bot)

    def test_dash_bot_suffix(self):
        self.assertTrue(self._info("renovate-bot").is_bot)

    def test_type_bot(self):
        self.assertTrue(self._info("github-actions", user_type="Bot").is_bot)

    def test_bot_type_case_insensitive(self):
        self.assertTrue(self._info("x", user_type="BOT").is_bot)


# ---------------------------------------------------------------------------
# Computed helper tests
# ---------------------------------------------------------------------------

class TestComputedHelpers(unittest.TestCase):

    def setUp(self):
        self.info, self.mu, _ = _make_user_info()

    def test_normalized_company_strips_at(self):
        self.mu.company = "@GitHub"
        self.info._cache.clear()
        self.assertEqual(self.info._normalized_company(), "GitHub")

    def test_normalized_company_no_at(self):
        self.mu.company = "Microsoft"
        self.info._cache.clear()
        self.assertEqual(self.info._normalized_company(), "Microsoft")

    def test_normalized_company_none(self):
        self.mu.company = None
        self.info._cache.clear()
        self.assertEqual(self.info._normalized_company(), "")

    def test_normalized_location_lowercased(self):
        self.mu.location = "San Francisco"
        self.info._cache.clear()
        self.assertEqual(self.info._normalized_location(), "san francisco")

    def test_normalized_location_strips_whitespace(self):
        self.mu.location = "  NYC  "
        self.info._cache.clear()
        self.assertEqual(self.info._normalized_location(), "nyc")

    def test_days_since_returns_positive(self):
        result = self.info._days_since("2020-01-01T00:00:00+00:00")
        self.assertGreater(result, 365)

    def test_days_since_empty_string_returns_zero(self):
        self.assertEqual(self.info._days_since(""), 0)

    def test_followers_following_ratio(self):
        self.mu.followers = 100
        self.mu.following = 10
        self.info._cache.clear()
        self.assertAlmostEqual(self.info._followers_following_ratio(), 10.0)

    def test_followers_following_ratio_zero_following(self):
        """When following is 0, ratio equals followers count."""
        self.mu.followers = 42
        self.mu.following = 0
        self.info._cache.clear()
        self.assertAlmostEqual(self.info._followers_following_ratio(), 42.0)

    def test_repos_per_year_positive(self):
        self.mu.public_repos = 30
        self.mu.created_at = datetime(2019, 1, 1, tzinfo=timezone.utc)
        self.info._cache.clear()
        self.assertGreater(self.info._repos_per_year(), 0)

    def test_recently_active_true_for_recent_event(self):
        self.info._cache["last_public_event_at"] = datetime.now(timezone.utc).isoformat()
        self.assertTrue(self.info._recently_active(days=90))

    def test_recently_active_false_for_old_event(self):
        self.info._cache["last_public_event_at"] = "2015-01-01T00:00:00+00:00"
        self.assertFalse(self.info._recently_active(days=90))

    def test_recently_active_false_when_no_event(self):
        info, _, _ = _make_user_info(login="fresh")
        self.assertFalse(info._recently_active(days=90))


# ---------------------------------------------------------------------------
# snapshot() tests
# ---------------------------------------------------------------------------

class TestSnapshot(unittest.TestCase):

    def setUp(self):
        self.info, _, _ = _make_user_info()

    def _snap(self):
        return self.info.snapshot(include_langs=False, include_star_fork_sums=False)

    def test_returns_user_snapshot(self):
        self.assertIsInstance(self._snap(), UserSnapshot)

    def test_login_matches(self):
        self.assertEqual(self._snap().login, "alice")

    def test_name_matches(self):
        self.assertEqual(self._snap().name, "Alice Example")

    def test_has_public_email_true(self):
        self.assertTrue(self._snap().has_public_email)

    def test_has_public_email_false_when_no_email(self):
        info, _, _ = _make_user_info(login="nomail", email="")
        snap = info.snapshot(include_langs=False, include_star_fork_sums=False)
        self.assertFalse(snap.has_public_email)

    def test_has_blog_true(self):
        self.assertTrue(self._snap().has_blog)

    def test_company_normalized_strips_at(self):
        self.assertEqual(self._snap().company_normalized, "ACME")

    def test_location_normalized(self):
        self.assertEqual(self._snap().location_normalized, "london")

    def test_account_age_days_positive(self):
        self.assertGreater(self._snap().account_age_days, 0)

    def test_is_bot_false_for_regular_user(self):
        self.assertFalse(self._snap().is_bot)

    def test_top_languages_list_when_requested(self):
        snap = self.info.snapshot(include_langs=True, include_star_fork_sums=False)
        self.assertIsInstance(snap.top_languages, list)

    def test_top_languages_none_when_not_requested(self):
        self.assertIsNone(self._snap().top_languages)

    def test_orgs_count_matches_list_length(self):
        snap = self._snap()
        self.assertEqual(snap.orgs_public_count, len(snap.public_orgs))


# ---------------------------------------------------------------------------
# to_dict() tests
# ---------------------------------------------------------------------------

class TestToDict(unittest.TestCase):

    def setUp(self):
        self.info, _, _ = _make_user_info()

    def _dict(self):
        return self.info.to_dict(include_langs=False, include_star_fork_sums=False)

    def test_returns_dict(self):
        self.assertIsInstance(self._dict(), dict)

    def test_login_present_and_correct(self):
        self.assertEqual(self._dict()["login"], "alice")

    def test_all_snapshot_fields_present(self):
        result = self._dict()
        for f in dc_fields(UserSnapshot):
            self.assertIn(f.name, result, f"Missing key: {f.name}")

    def test_output_is_json_serialisable(self):
        json.dumps(self._dict(), default=str)  # should not raise


# ---------------------------------------------------------------------------
# to_csv_row() and csv_headers() tests
# ---------------------------------------------------------------------------

class TestToCsvRow(unittest.TestCase):

    def setUp(self):
        self.info, _, _ = _make_user_info()

    def _row(self):
        return self.info.to_csv_row(include_langs=False, include_star_fork_sums=False)

    def test_returns_list(self):
        self.assertIsInstance(self._row(), list)

    def test_all_elements_are_strings(self):
        for i, val in enumerate(self._row()):
            self.assertIsInstance(val, str, f"Element {i} is not a string: {val!r}")

    def test_length_matches_csv_headers(self):
        self.assertEqual(len(self._row()), len(GitHubUserInfo.csv_headers()))

    def test_login_is_first_element(self):
        self.assertEqual(self._row()[0], "alice")


class TestCsvHeaders(unittest.TestCase):

    def test_returns_list_of_strings(self):
        h = GitHubUserInfo.csv_headers()
        self.assertIsInstance(h, list)
        for item in h:
            self.assertIsInstance(item, str)

    def test_login_in_headers(self):
        self.assertIn("login", GitHubUserInfo.csv_headers())

    def test_no_duplicate_headers(self):
        h = GitHubUserInfo.csv_headers()
        self.assertEqual(len(h), len(set(h)))


# ---------------------------------------------------------------------------
# to_json() tests
# ---------------------------------------------------------------------------

class TestToJson(unittest.TestCase):

    def setUp(self):
        self.info, _, _ = _make_user_info()

    def _parsed(self):
        s = self.info.to_json(include_langs=False, include_star_fork_sums=False)
        return json.loads(s)

    def test_returns_string(self):
        result = self.info.to_json(include_langs=False, include_star_fork_sums=False)
        self.assertIsInstance(result, str)

    def test_valid_json(self):
        self.assertIsInstance(self._parsed(), dict)

    def test_contains_login(self):
        self.assertEqual(self._parsed()["login"], "alice")


# ---------------------------------------------------------------------------
# Integration test — real GitHub API (skipped by default)
# ---------------------------------------------------------------------------

@unittest.skip("Integration test — requires GITHUB_TOKEN env variable")
class TestGitHubUserInfoIntegration(unittest.TestCase):

    def test_real_user_snapshot(self):
        import os
        from dotenv import load_dotenv
        from github import Github, Auth
        load_dotenv()
        token = os.getenv("GITHUB_TOKEN")
        gh = Github(auth=Auth.Token(token)) if token else Github()
        info = GitHubUserInfo(gh=gh, username="torvalds")
        snap = info.snapshot(include_langs=True, include_star_fork_sums=True)
        self.assertEqual(snap.login, "torvalds")
        self.assertGreater(snap.followers, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

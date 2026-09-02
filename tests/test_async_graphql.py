"""
Tests for the async pipeline fixes and the GraphQL fast paths added in 1.1.0.
"""

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from repo_people import RepoPeople, export
from repo_people.users import GitHubUserInfo


# ---------------------------------------------------------------------------
# Helpers for faking aiohttp
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal async context manager mimicking an aiohttp response."""

    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class _FakeSession:
    """
    Fake aiohttp.ClientSession that serves responses by URL suffix.

    `routes` maps a substring of the URL to either a _FakeResponse or a list of
    them (consumed in order, to simulate a retry succeeding on a later attempt).
    """

    def __init__(self, routes, default=None):
        self.routes = routes
        self.default = default or _FakeResponse([], 200)
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        for needle, response in self.routes.items():
            if needle in url:
                if isinstance(response, list):
                    return response.pop(0) if response else self.default
                return response
        return self.default

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


_PROFILE = {
    "login": "alice",
    "id": 1,
    "node_id": "n1",
    "type": "User",
    "name": "Alice",
    "company": "@acme",
    "location": "Berlin, Germany",
    "email": "alice@example.com",
    "blog": "https://alice.dev",
    "twitter_username": "alice",
    "bio": "hi",
    "avatar_url": "https://a",
    "html_url": "https://h",
    "hireable": None,
    "site_admin": False,
    "created_at": "2015-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z",
    "followers": 10,
    "following": 5,
    "public_repos": 20,
    "public_gists": 1,
}


def _make_rp(**kwargs):
    """Build a RepoPeople with the GitHub client patched out."""
    patcher = patch("repo_people.repo_people.Github")
    patcher.start()
    defaults = dict(owner="o", repo="r", token="tok")
    defaults.update(kwargs)
    rp = RepoPeople(**defaults)
    return rp, patcher


class TestAsyncRateLimitHandling(unittest.TestCase):
    """The async path retries rate limits instead of silently dropping users."""

    def setUp(self):
        self.rp, self.patcher = _make_rp()
        self.tmpdir = tempfile.mkdtemp()
        self.rp.outdir = self.tmpdir

    def tearDown(self):
        import shutil
        self.patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rate_limited_then_success_is_retried(self):
        """A 403 with Retry-After is retried and the user is collected."""
        session = _FakeSession({
            "/users/alice/orgs": _FakeResponse([], 200),
            "/users/alice/events/public": _FakeResponse([], 200),
            "/users/alice": [
                _FakeResponse(None, 403, {"Retry-After": "0"}),
                _FakeResponse(_PROFILE, 200),
            ],
        })
        with patch("aiohttp.ClientSession", return_value=session):
            with patch("asyncio.sleep", new=self._noop_sleep):
                result = asyncio.run(
                    self.rp.get_user_details_async(["alice"], verbose=False)
                )
        self.assertIn("alice", result)
        self.assertEqual(self.rp.last_failed, [])

    @staticmethod
    async def _noop_sleep(_seconds):
        return None

    def test_persistent_rate_limit_reports_reason(self):
        """A user lost to a rate limit is reported as rate limited, not 'not found'."""
        session = _FakeSession({
            "/users/alice/orgs": _FakeResponse([], 200),
            "/users/alice/events/public": _FakeResponse([], 200),
            # Remaining: 0 is what marks a primary rate limit; a 403 without it
            # is a permission error and now fails fast instead.
            "/users/alice": _FakeResponse(
                None, 403,
                {"X-RateLimit-Reset": "99999999999", "X-RateLimit-Remaining": "0"},
            ),
        })
        with patch("aiohttp.ClientSession", return_value=session):
            with patch("builtins.print") as mock_print:
                result = asyncio.run(
                    self.rp.get_user_details_async(["alice"], verbose=False)
                )
        self.assertEqual(result, {})
        self.assertEqual(self.rp.last_failed, ["alice"])
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("rate limited", printed)

    def test_404_is_distinguished_from_rate_limit(self):
        """A genuinely missing user reports 404, not a rate limit."""
        session = _FakeSession({
            "/users/ghost/orgs": _FakeResponse([], 200),
            "/users/ghost/events/public": _FakeResponse([], 200),
            "/users/ghost": _FakeResponse(None, 404),
        })
        with patch("aiohttp.ClientSession", return_value=session):
            with patch("builtins.print") as mock_print:
                asyncio.run(self.rp.get_user_details_async(["ghost"], verbose=False))
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("404", printed)

    def test_permission_403_fails_fast(self):
        """A 403 that is not a rate limit must not be slept on and retried."""
        forbidden = _FakeResponse({"message": "Forbidden"}, 403)
        session = _FakeSession({
            "/users/alice/orgs": _FakeResponse([], 200),
            "/users/alice/events/public": _FakeResponse([], 200),
            "/users/alice": forbidden,
        })
        with patch("aiohttp.ClientSession", return_value=session):
            with patch("asyncio.sleep", new=self._noop_sleep) as _:
                with patch("builtins.print") as mock_print:
                    result = asyncio.run(
                        self.rp.get_user_details_async(["alice"], verbose=False)
                    )
        self.assertEqual(result, {})
        self.assertEqual(self.rp.last_failed, ["alice"])
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("forbidden", printed)
        self.assertNotIn("rate limit", printed)
        # One attempt per URL, not five retries of the profile call.
        self.assertEqual(session.requests.count("https://api.github.com/users/alice"), 1)

    def test_concurrency_capped_with_warning(self):
        session = _FakeSession({})
        with patch("aiohttp.ClientSession", return_value=session):
            with self.assertWarns(UserWarning):
                asyncio.run(
                    self.rp.get_user_details_async([], verbose=False, concurrency=500)
                )


class TestAsyncSyncParity(unittest.TestCase):
    """The async and sync paths must produce the same values for shared fields."""

    def setUp(self):
        self.rp, self.patcher = _make_rp()
        self.tmpdir = tempfile.mkdtemp()
        self.rp.outdir = self.tmpdir

    def tearDown(self):
        import shutil
        self.patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_async(self, **kwargs):
        session = _FakeSession({
            "/users/alice/orgs": _FakeResponse([{"login": "acme"}], 200),
            "/users/alice/events/public": _FakeResponse(
                [{"created_at": "2024-06-01T00:00:00Z"}], 200
            ),
            "/users/alice/social_accounts": _FakeResponse(
                [{"provider": "LinkedIn", "url": "https://li/alice"}], 200
            ),
            "/users/alice": _FakeResponse(_PROFILE, 200),
        })
        with patch("aiohttp.ClientSession", return_value=session):
            return asyncio.run(
                self.rp.get_user_details_async(["alice"], verbose=False, **kwargs)
            )

    def test_hireable_is_bool_not_none(self):
        """GitHub sends null for unset; UserSnapshot declares bool."""
        record = self._run_async()["alice"]
        self.assertIsInstance(record["hireable"], bool)
        self.assertFalse(record["hireable"])

    def test_location_country_populated(self):
        record = self._run_async()["alice"]
        self.assertEqual(record["location_country"], "DE")

    def test_repos_per_year_matches_sync_formula(self):
        """Both paths use 365.25 days/year with a 1-year floor."""
        record = self._run_async()["alice"]

        info = GitHubUserInfo(gh=MagicMock(), username="alice")
        info._cache.update({
            "created_at": _PROFILE["created_at"].replace("Z", "+00:00"),
            "public_repos": _PROFILE["public_repos"],
        })
        self.assertAlmostEqual(record["repos_per_year"], info._repos_per_year(), places=2)

    def test_async_record_keys_match_snapshot_fields(self):
        """The async record carries exactly the UserSnapshot field set."""
        import dataclasses
        from repo_people.users import UserSnapshot
        record = self._run_async()["alice"]
        expected = {f.name for f in dataclasses.fields(UserSnapshot)}
        self.assertEqual(set(record), expected)

    def test_social_accounts_opt_in(self):
        """include_social_accounts now works on the async path too."""
        record = self._run_async(include_social_accounts=True)["alice"]
        self.assertEqual(record["social_accounts"], {"linkedin": "https://li/alice"})

    def test_social_accounts_absent_by_default(self):
        record = self._run_async()["alice"]
        self.assertIsNone(record["social_accounts"])


class TestAsyncBatchedSaves(unittest.TestCase):
    """save_each_iteration must batch, not rewrite the file per user."""

    def setUp(self):
        self.rp, self.patcher = _make_rp()
        self.tmpdir = tempfile.mkdtemp()
        self.rp.outdir = self.tmpdir

    def tearDown(self):
        import shutil
        self.patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_writes_are_batched_and_final_flush_happens(self):
        """3 users => fewer than 3 writes, but the file still ends up complete."""
        logins = ["u1", "u2", "u3"]
        routes = {}
        for login in logins:
            profile = dict(_PROFILE, login=login)
            routes[f"/users/{login}/orgs"] = _FakeResponse([], 200)
            routes[f"/users/{login}/events/public"] = _FakeResponse([], 200)
            routes[f"/users/{login}"] = _FakeResponse(profile, 200)
        session = _FakeSession(routes)

        real_write = RepoPeople._atomic_write_json
        calls = {"n": 0}

        def counting_write(path, payload):
            calls["n"] += 1
            real_write(path, payload)

        with patch("aiohttp.ClientSession", return_value=session):
            with patch.object(RepoPeople, "_atomic_write_json", staticmethod(counting_write)):
                result = asyncio.run(
                    self.rp.get_user_details_async(
                        logins, verbose=False, save_each_iteration=True, concurrency=1
                    )
                )

        self.assertEqual(len(result), 3)
        # Old behaviour: one full-file write per user (3). Batched: just the
        # final flush, since 3 < the batch size of 10.
        self.assertLess(calls["n"], 3)
        save_path = os.path.join(self.tmpdir, "o_r_user_details.json")
        with open(save_path, encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(set(on_disk), set(logins))

    def test_atomic_write_leaves_no_temp_file(self):
        RepoPeople._atomic_write_json(
            os.path.join(self.tmpdir, "out.json"), {"a": 1}
        )
        self.assertEqual(sorted(os.listdir(self.tmpdir)), ["out.json"])


class TestAsyncExportParity(unittest.TestCase):
    """get_users_async supports the same export formats as get_users."""

    def setUp(self):
        self.rp, self.patcher = _make_rp()
        self.tmpdir = tempfile.mkdtemp()
        self.rp.outdir = self.tmpdir

    def tearDown(self):
        import shutil
        self.patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_all_export_formats_accepted(self):
        session = _FakeSession({
            "/users/alice/orgs": _FakeResponse([], 200),
            "/users/alice/events/public": _FakeResponse([], 200),
            "/users/alice": _FakeResponse(_PROFILE, 200),
        })
        with patch("repo_people.repo_people.export") as mock_export:
            for attr in (
                "export_contributors", "export_maintainers", "export_stargazers",
                "export_watchers", "export_issue_authors", "export_pr_authors",
                "export_pr_reviewers", "export_fork_owners",
                "export_commit_authors", "export_dependents",
            ):
                getattr(mock_export, attr).return_value = []
            mock_export.export_contributors.return_value = ["alice"]
            mock_export._SIMPLE_ROLE_CONNECTIONS = {}
            with patch("aiohttp.ClientSession", return_value=session):
                asyncio.run(self.rp.get_users_async(
                    roles=["contributors"],
                    export=True, export_csv=True,
                    export_markdown=True, export_sqlite=True,
                    verbose=False,
                ))
        written = set(os.listdir(self.tmpdir))
        for suffix in ("json", "csv", "md", "db"):
            self.assertIn(f"o_r_user_details.{suffix}", written)


# ---------------------------------------------------------------------------
# GraphQL fast paths
# ---------------------------------------------------------------------------

class TestGraphqlQueryBuilder(unittest.TestCase):
    """The generated GraphQL document must be well formed."""

    def test_aliases_and_cursor_variables(self):
        q = export._build_simple_roles_query(["stargazers", "pr_authors"])
        self.assertIn("$stargazers_cursor: String", q)
        self.assertIn("$pr_authors_cursor: String", q)
        self.assertIn("stargazers: stargazers(first: 100, after: $stargazers_cursor)", q)
        # Existing arguments must be preserved alongside the injected ones
        self.assertIn("pr_authors: pullRequests(first: 100, after: $pr_authors_cursor, states: [OPEN, CLOSED, MERGED])", q)
        self.assertEqual(q.count("pageInfo"), 2)

    def test_balanced_braces(self):
        for roles in (["stargazers"], list(export._SIMPLE_ROLE_CONNECTIONS)):
            q = export._build_simple_roles_query(roles)
            self.assertEqual(q.count("{"), q.count("}"), roles)

    def test_dig_tolerates_nulls(self):
        """GraphQL returns author: null for deleted accounts."""
        self.assertEqual(export._dig({"author": {"login": "a"}}, ("author", "login")), "a")
        self.assertIsNone(export._dig({"author": None}, ("author", "login")))
        self.assertIsNone(export._dig({}, ("author", "login")))
        self.assertIsNone(export._dig({"author": {"login": ""}}, ("author", "login")))


class TestCollectSimpleRolesGraphql(unittest.TestCase):
    """collect_simple_roles_graphql batches roles into one query."""

    def test_single_page_single_call(self):
        data = {
            "repository": {
                "stargazers": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"login": "alice"}, {"login": "bob"}],
                },
                "fork_owners": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"owner": {"login": "carol"}}],
                },
            }
        }
        with patch("repo_people.export.graphql", return_value=data) as mock_gql:
            result = export.collect_simple_roles_graphql(
                "o", "r", "tok", ["stargazers", "fork_owners"]
            )
        self.assertEqual(result, {"stargazers": ["alice", "bob"], "fork_owners": ["carol"]})
        self.assertEqual(mock_gql.call_count, 1)

    def test_pagination_continues_only_for_unfinished_roles(self):
        page1 = {
            "repository": {
                "stargazers": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    "nodes": [{"login": "alice"}],
                },
                "watchers": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"login": "wendy"}],
                },
            }
        }
        page2 = {
            "repository": {
                "stargazers": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"login": "bob"}],
                },
            }
        }
        with patch("repo_people.export.graphql", side_effect=[page1, page2]) as mock_gql:
            result = export.collect_simple_roles_graphql(
                "o", "r", "tok", ["stargazers", "watchers"]
            )
        self.assertEqual(result["stargazers"], ["alice", "bob"])
        self.assertEqual(result["watchers"], ["wendy"])
        self.assertEqual(mock_gql.call_count, 2)
        # The second query must only contain the still-paging role.
        second_query = mock_gql.call_args_list[1][0][0]
        self.assertIn("stargazers", second_query)
        self.assertNotIn("watchers", second_query)

    def test_returns_none_without_token(self):
        """No token => None, so the caller falls back to REST."""
        self.assertIsNone(export.collect_simple_roles_graphql("o", "r", "", ["stargazers"]))

    def test_returns_none_on_graphql_failure(self):
        with patch("repo_people.export.graphql", return_value=None):
            self.assertIsNone(
                export.collect_simple_roles_graphql("o", "r", "tok", ["stargazers"])
            )

    def test_returns_none_when_repository_missing(self):
        """An invisible repo is 'no data', not 'no stargazers'."""
        with patch("repo_people.export.graphql", return_value={"repository": None}):
            self.assertIsNone(
                export.collect_simple_roles_graphql("o", "r", "tok", ["stargazers"])
            )

    def test_unsupported_roles_ignored(self):
        self.assertIsNone(
            export.collect_simple_roles_graphql("o", "r", "tok", ["dependents"])
        )

    def test_partial_results_survive_a_mid_pagination_failure(self):
        """A finished role is kept; only the unfinished one falls back to REST."""
        page1 = {
            "repository": {
                "stargazers": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    "nodes": [{"login": "alice"}],
                },
                "watchers": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"login": "wendy"}],
                },
            }
        }
        with patch("repo_people.export.graphql", side_effect=[page1, None]):
            result = export.collect_simple_roles_graphql(
                "o", "r", "tok", ["stargazers", "watchers"]
            )
        self.assertEqual(result, {"watchers": ["wendy"]})


class TestPrReviewersGraphql(unittest.TestCase):
    """export_pr_reviewers uses GraphQL when possible, REST otherwise."""

    def test_graphql_path_avoids_per_pr_rest_calls(self):
        data = {
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"reviews": {"nodes": [{"author": {"login": "rev1"}}]}},
                        {"reviews": {"nodes": [{"author": {"login": "rev2"}},
                                                {"author": None}]}},
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.graphql", return_value=data):
                with patch("repo_people.utils.requests.get") as mock_rest:
                    result = export.export_pr_reviewers("o", "r", "tok", tmpdir)
        self.assertEqual(result, ["rev1", "rev2"])
        mock_rest.assert_not_called()

    def test_falls_back_to_rest_when_graphql_fails(self):
        prs = MagicMock()
        prs.status_code = 200
        prs.json.return_value = [{"number": 1}]
        prs.headers = {}
        prs.raise_for_status.return_value = None

        reviews = MagicMock()
        reviews.status_code = 200
        reviews.json.return_value = [{"user": {"login": "rest_reviewer"}}]
        reviews.headers = {}
        reviews.raise_for_status.return_value = None

        def route(url, **kwargs):
            return reviews if "/reviews" in url else prs

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.graphql", return_value=None):
                with patch("repo_people.utils.requests.get", side_effect=route):
                    result = export.export_pr_reviewers(
                        "o", "r", "tok", tmpdir, use_cache=False
                    )
        self.assertEqual(result, ["rest_reviewer"])

    def test_no_token_uses_rest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.graphql") as mock_gql:
                with patch("repo_people.utils.requests.get") as mock_rest:
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json.return_value = []
                    resp.headers = {}
                    mock_rest.return_value = resp
                    export.export_pr_reviewers("o", "r", None, tmpdir, use_cache=False)
        mock_gql.assert_not_called()

    def test_use_graphql_false_forces_rest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("repo_people.export.graphql") as mock_gql:
                with patch("repo_people.utils.requests.get") as mock_rest:
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json.return_value = []
                    resp.headers = {}
                    mock_rest.return_value = resp
                    export.export_pr_reviewers(
                        "o", "r", "tok", tmpdir, use_cache=False, use_graphql=False
                    )
        mock_gql.assert_not_called()


class TestGraphqlHelper(unittest.TestCase):
    """utils.graphql() error handling."""

    def test_requires_token(self):
        from repo_people.utils import graphql
        self.assertIsNone(graphql("query {}", {}, ""))

    def test_returns_none_on_401(self):
        from repo_people.utils import graphql
        resp = MagicMock()
        resp.status_code = 401
        resp.headers = {}
        with patch("repo_people.utils.requests.post", return_value=resp):
            self.assertIsNone(graphql("query {}", {}, "tok"))

    def test_returns_none_on_errors_without_data(self):
        from repo_people.utils import graphql
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = {"errors": [{"message": "Bad credentials"}], "data": None}
        with patch("repo_people.utils.requests.post", return_value=resp):
            with patch("builtins.print") as mock_print:
                self.assertIsNone(graphql("query {}", {}, "tok"))
        self.assertIn("Bad credentials", " ".join(str(c) for c in mock_print.call_args_list))

    def test_returns_data_on_success(self):
        from repo_people.utils import graphql
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = {"data": {"repository": {"x": 1}}}
        with patch("repo_people.utils.requests.post", return_value=resp):
            self.assertEqual(graphql("query {}", {}, "tok"), {"repository": {"x": 1}})

    def test_network_error_returns_none(self):
        import requests
        from repo_people.utils import graphql
        with patch("repo_people.utils.requests.post",
                   side_effect=requests.exceptions.ConnectionError("boom")):
            with patch("builtins.print"):
                self.assertIsNone(graphql("query {}", {}, "tok"))


class TestCollectAllUsernamesGraphqlIntegration(unittest.TestCase):
    """collect_all_usernames prefers GraphQL and falls back cleanly."""

    def setUp(self):
        self.rp, self.patcher = _make_rp()

    def tearDown(self):
        self.patcher.stop()

    def test_graphql_results_skip_rest_fetchers(self):
        collected = {"stargazers": ["alice"], "watchers": ["bob"]}
        with patch("repo_people.export.collect_simple_roles_graphql", return_value=collected):
            with patch("repo_people.export.export_stargazers") as mock_star:
                with patch("repo_people.export.export_watchers") as mock_watch:
                    result = self.rp.collect_all_usernames(roles=["stargazers", "watchers"])
        self.assertEqual(result, collected)
        mock_star.assert_not_called()
        mock_watch.assert_not_called()

    def test_rest_used_when_graphql_returns_none(self):
        with patch("repo_people.export.collect_simple_roles_graphql", return_value=None):
            with patch("repo_people.export.export_stargazers", return_value=["alice"]) as mock_star:
                result = self.rp.collect_all_usernames(roles=["stargazers"])
        self.assertEqual(result, {"stargazers": ["alice"]})
        mock_star.assert_called_once()

    def test_non_graphql_roles_still_use_rest(self):
        """dependents has no GraphQL equivalent and must still be fetched."""
        with patch("repo_people.export.collect_simple_roles_graphql",
                   return_value={"stargazers": ["alice"]}):
            with patch("repo_people.export.export_dependents", return_value=["dep"]) as mock_dep:
                result = self.rp.collect_all_usernames(roles=["stargazers", "dependents"])
        self.assertEqual(result, {"stargazers": ["alice"], "dependents": ["dep"]})
        mock_dep.assert_called_once()

    def test_no_graphql_when_unauthenticated(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with patch.dict(os.environ, {}, clear=True):
                rp, patcher = _make_rp(token=None)
        try:
            with patch("repo_people.export.collect_simple_roles_graphql") as mock_gql:
                with patch("repo_people.export.export_stargazers", return_value=[]):
                    rp.collect_all_usernames(roles=["stargazers"])
            mock_gql.assert_not_called()
        finally:
            patcher.stop()

    def test_order_is_deterministic(self):
        """Results follow the requested role order regardless of completion order."""
        with patch("repo_people.export.collect_simple_roles_graphql",
                   return_value={"watchers": [], "stargazers": []}):
            with patch("repo_people.export.export_dependents", return_value=[]):
                result = self.rp.collect_all_usernames(
                    roles=["dependents", "watchers", "stargazers"]
                )
        self.assertEqual(list(result), ["dependents", "watchers", "stargazers"])


if __name__ == "__main__":
    unittest.main()


class TestTimestampParity(unittest.TestCase):
    """Both pipelines must emit the same ISO-8601 spelling for timestamps."""

    def test_iso_utc_normalises_z_suffix(self):
        from repo_people.repo_people import _iso_utc
        self.assertEqual(_iso_utc("2015-01-01T00:00:00Z"), "2015-01-01T00:00:00+00:00")

    def test_iso_utc_passes_through_offset_form(self):
        from repo_people.repo_people import _iso_utc
        self.assertEqual(
            _iso_utc("2015-01-01T00:00:00+00:00"), "2015-01-01T00:00:00+00:00"
        )

    def test_iso_utc_handles_empty_and_garbage(self):
        from repo_people.repo_people import _iso_utc
        self.assertEqual(_iso_utc(""), "")
        self.assertEqual(_iso_utc("not a date"), "not a date")

    def test_async_created_at_matches_sync_spelling(self):
        """The async record must not emit a bare 'Z' where sync emits '+00:00'."""
        rp, patcher = _make_rp()
        tmpdir = tempfile.mkdtemp()
        rp.outdir = tmpdir
        try:
            session = _FakeSession({
                "/users/alice/orgs": _FakeResponse([], 200),
                "/users/alice/events/public": _FakeResponse([], 200),
                "/users/alice": _FakeResponse(_PROFILE, 200),
            })
            with patch("aiohttp.ClientSession", return_value=session):
                record = asyncio.run(
                    rp.get_user_details_async(["alice"], verbose=False)
                )["alice"]
        finally:
            import shutil
            patcher.stop()
            shutil.rmtree(tmpdir, ignore_errors=True)
        self.assertEqual(record["created_at"], "2015-01-01T00:00:00+00:00")
        self.assertFalse(record["created_at"].endswith("Z"))

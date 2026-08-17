"""
Tests for the CLI additions in 1.1.0: exit codes and the new flags.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from repo_people.cli import EXIT_PARTIAL_FAILURE, _build_parser, main


def _parse(argv):
    return _build_parser().parse_args(argv)


class TestNewFlagsParse(unittest.TestCase):
    """Every newly exposed flag is accepted and defaults sensibly."""

    def test_defaults(self):
        args = _parse(["o", "r"])
        self.assertFalse(args.resume)
        self.assertFalse(args.save_each_iteration)
        self.assertFalse(args.include_social_accounts)
        self.assertFalse(args.use_async)
        self.assertFalse(args.export_md)
        self.assertFalse(args.export_sqlite)
        self.assertFalse(args.summarise)
        self.assertFalse(args.progress)
        self.assertFalse(args.no_cache)
        self.assertFalse(args.clear_cache)
        self.assertFalse(args.no_graphql)
        self.assertEqual(args.concurrency, 10)
        self.assertEqual(args.workers, 1)
        self.assertIsNone(args.exclude)

    def test_all_flags_together(self):
        args = _parse([
            "o", "r",
            "--resume", "--save-each-iteration", "--include-social-accounts",
            "--async", "--concurrency", "20",
            "--export-md", "--export-sqlite", "--summarise", "--progress",
            "--no-cache", "--no-graphql",
            "--exclude", "dependabot", "renovate",
        ])
        self.assertTrue(args.resume)
        self.assertTrue(args.save_each_iteration)
        self.assertTrue(args.include_social_accounts)
        self.assertTrue(args.use_async)
        self.assertEqual(args.concurrency, 20)
        self.assertTrue(args.export_md)
        self.assertTrue(args.export_sqlite)
        self.assertTrue(args.summarise)
        self.assertTrue(args.progress)
        self.assertTrue(args.no_cache)
        self.assertTrue(args.no_graphql)
        self.assertEqual(args.exclude, ["dependabot", "renovate"])


class TestExitCodes(unittest.TestCase):
    """main() returns 0 on success and EXIT_PARTIAL_FAILURE on partial failure."""

    def _run(self, argv, last_failed=None, user_data=None):
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            rp = mock_cls.return_value
            rp.get_users.return_value = user_data if user_data is not None else {"a": {}}
            rp.last_failed = last_failed if last_failed is not None else []
            code = main(argv)
        return code, rp

    def test_zero_on_clean_run(self):
        code, _ = self._run(["o", "r"])
        self.assertEqual(code, 0)

    def test_two_on_partial_failure(self):
        """A run that lost users must not look like success to CI."""
        code, _ = self._run(["o", "r"], last_failed=["ghost", "deleted"])
        self.assertEqual(code, EXIT_PARTIAL_FAILURE)
        self.assertEqual(EXIT_PARTIAL_FAILURE, 2)

    def test_failed_logins_reported_on_stderr(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self._run(["o", "r"], last_failed=["ghost"])
        self.assertIn("ghost", buf.getvalue())

    def test_many_failures_are_truncated(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self._run(["o", "r"], last_failed=[f"u{i}" for i in range(25)])
        err = buf.getvalue()
        self.assertIn("25 user(s)", err)
        self.assertIn("…", err)

    def test_zero_when_last_failed_attribute_missing(self):
        """A stubbed RepoPeople without last_failed must not crash the CLI."""
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            rp = mock_cls.return_value
            rp.get_users.return_value = {"a": {}}
            del rp.last_failed
            code = main(["o", "r"])
        self.assertEqual(code, 0)

    def test_construction_error_exits_1(self):
        with patch("repo_people.cli.RepoPeople", side_effect=ValueError("bad owner")):
            with self.assertRaises(SystemExit) as ctx:
                main(["bad owner", "r"])
        self.assertEqual(ctx.exception.code, 1)

    def test_connection_error_exits_1(self):
        with patch("repo_people.cli.RepoPeople", side_effect=ConnectionError("bad token")):
            with self.assertRaises(SystemExit) as ctx:
                main(["o", "r"])
        self.assertEqual(ctx.exception.code, 1)

    def test_invalid_field_exits_1(self):
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            mock_cls.return_value.get_users.side_effect = ValueError("Invalid field(s)")
            with self.assertRaises(SystemExit) as ctx:
                main(["o", "r", "--fields", "nope"])
        self.assertEqual(ctx.exception.code, 1)

    def test_missing_openpyxl_exits_1_with_message(self):
        """--export-xlsx without openpyxl gives a clean error, not a traceback."""
        import io
        import contextlib
        buf = io.StringIO()
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            mock_cls.return_value.get_users.side_effect = ImportError(
                "openpyxl is required for Excel export."
            )
            with contextlib.redirect_stderr(buf):
                with self.assertRaises(SystemExit) as ctx:
                    main(["o", "r", "--export-xlsx"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("openpyxl", buf.getvalue())


class TestFlagsReachTheLibrary(unittest.TestCase):
    """Parsed flags are actually forwarded to RepoPeople."""

    def test_cache_and_graphql_flags_forwarded_to_constructor(self):
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            mock_cls.return_value.get_users.return_value = {}
            mock_cls.return_value.last_failed = []
            main(["o", "r", "--no-cache", "--no-graphql"])
        kwargs = mock_cls.call_args[1]
        self.assertFalse(kwargs["use_cache"])
        self.assertFalse(kwargs["use_graphql"])

    def test_defaults_enable_cache_and_graphql(self):
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            mock_cls.return_value.get_users.return_value = {}
            mock_cls.return_value.last_failed = []
            main(["o", "r"])
        kwargs = mock_cls.call_args[1]
        self.assertTrue(kwargs["use_cache"])
        self.assertTrue(kwargs["use_graphql"])

    def test_run_control_flags_forwarded_to_get_users(self):
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            rp = mock_cls.return_value
            rp.get_users.return_value = {}
            rp.last_failed = []
            main([
                "o", "r", "--resume", "--save-each-iteration",
                "--include-social-accounts", "--export-md", "--export-sqlite",
                "--exclude", "bot1", "--progress", "--no-verbose",
            ])
        kwargs = rp.get_users.call_args[1]
        self.assertTrue(kwargs["resume"])
        self.assertTrue(kwargs["save_each_iteration"])
        self.assertTrue(kwargs["include_social_accounts"])
        self.assertTrue(kwargs["export_markdown"])
        self.assertTrue(kwargs["export_sqlite"])
        self.assertEqual(kwargs["exclude"], ["bot1"])
        self.assertTrue(kwargs["progress"])
        self.assertFalse(kwargs["verbose"])

    def test_summarise_invoked(self):
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            rp = mock_cls.return_value
            rp.get_users.return_value = {"a": {}}
            rp.last_failed = []
            main(["o", "r", "--summarise"])
        rp.summarise.assert_called_once()

    def test_summarise_not_invoked_by_default(self):
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            rp = mock_cls.return_value
            rp.get_users.return_value = {"a": {}}
            rp.last_failed = []
            main(["o", "r"])
        rp.summarise.assert_not_called()


class TestAsyncCliPath(unittest.TestCase):
    """--async routes to get_users_async with concurrency, not workers."""

    def test_async_calls_get_users_async(self):
        async def _fake_async(**kwargs):
            return {"a": {}}

        with patch("repo_people.cli.RepoPeople") as mock_cls:
            rp = mock_cls.return_value
            rp.get_users_async.side_effect = _fake_async
            rp.last_failed = []
            code = main(["o", "r", "--async", "--concurrency", "7"])
        self.assertEqual(code, 0)
        rp.get_users.assert_not_called()
        self.assertEqual(rp.get_users_async.call_args[1]["concurrency"], 7)

    def test_async_without_aiohttp_exits_1(self):
        """A clear message beats an ImportError traceback."""
        import builtins
        import io
        import contextlib
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "aiohttp":
                raise ImportError("No module named 'aiohttp'")
            return real_import(name, *args, **kwargs)

        buf = io.StringIO()
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            mock_cls.return_value.last_failed = []
            with patch.object(builtins, "__import__", side_effect=fake_import):
                with contextlib.redirect_stderr(buf):
                    with self.assertRaises(SystemExit) as ctx:
                        main(["o", "r", "--async"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("aiohttp", buf.getvalue())
        # Checked before the client is built: constructing RepoPeople spends an
        # API call verifying the token, and a missing extra should cost nothing.
        mock_cls.assert_not_called()

    def test_workers_not_passed_to_async(self):
        async def _fake_async(**kwargs):
            self.assertNotIn("workers", kwargs)
            self.assertNotIn("progress", kwargs)
            return {}

        with patch("repo_people.cli.RepoPeople") as mock_cls:
            rp = mock_cls.return_value
            rp.get_users_async.side_effect = _fake_async
            rp.last_failed = []
            main(["o", "r", "--async"])


class TestClearCache(unittest.TestCase):
    """--clear-cache short-circuits before any collection."""

    def test_clears_and_exits_zero(self):
        with patch("repo_people.cli.clear_cache", return_value=3) as mock_clear:
            with patch("repo_people.cli.RepoPeople") as mock_cls:
                code = main(["o", "r", "--clear-cache"])
        self.assertEqual(code, 0)
        mock_clear.assert_called_once()
        mock_cls.assert_not_called()

    def test_singular_plural_wording(self):
        for count, expected in ((1, "1 cache entry"), (0, "0 cache entries")):
            with patch("repo_people.cli.clear_cache", return_value=count):
                with patch("repo_people.cli.RepoPeople"):
                    with patch("builtins.print") as mock_print:
                        main(["o", "r", "--clear-cache"])
            printed = " ".join(str(c) for c in mock_print.call_args_list)
            self.assertIn(expected, printed)


class TestTokenResolution(unittest.TestCase):
    """The CLI still resolves the token from --token then GITHUB_TOKEN."""

    def test_explicit_token_wins(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "env_tok"}):
            with patch("repo_people.cli.RepoPeople") as mock_cls:
                mock_cls.return_value.get_users.return_value = {}
                mock_cls.return_value.last_failed = []
                main(["o", "r", "--token", "cli_tok"])
        self.assertEqual(mock_cls.call_args[1]["token"], "cli_tok")

    def test_env_token_used(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "env_tok"}):
            with patch("repo_people.cli.RepoPeople") as mock_cls:
                mock_cls.return_value.get_users.return_value = {}
                mock_cls.return_value.last_failed = []
                main(["o", "r"])
        self.assertEqual(mock_cls.call_args[1]["token"], "env_tok")


if __name__ == "__main__":
    unittest.main()

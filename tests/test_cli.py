import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from repo_people.cli import _build_parser, main

unittest.TestLoader.sortTestMethodsUsing = None


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------

class TestCliParser(unittest.TestCase):
    """Tests for the CLI argument parser."""

    def _parse(self, args):
        return _build_parser().parse_args(args)

    def test_positional_args(self):
        args = self._parse(["alice", "myrepo"])
        self.assertEqual(args.owner, "alice")
        self.assertEqual(args.repo, "myrepo")

    def test_token_flag(self):
        args = self._parse(["o", "r", "--token", "ghp_abc"])
        self.assertEqual(args.token, "ghp_abc")

    def test_token_defaults_to_none(self):
        args = self._parse(["o", "r"])
        self.assertIsNone(args.token)

    def test_outdir_default(self):
        args = self._parse(["o", "r"])
        self.assertEqual(args.outdir, "outputs")

    def test_outdir_custom(self):
        args = self._parse(["o", "r", "--outdir", "my_dir"])
        self.assertEqual(args.outdir, "my_dir")

    def test_export_flags_default_false(self):
        args = self._parse(["o", "r"])
        self.assertFalse(args.export_json)
        self.assertFalse(args.export_csv)
        self.assertFalse(args.export_xlsx)

    def test_export_flags_set(self):
        args = self._parse(["o", "r", "--export-json", "--export-csv", "--export-xlsx"])
        self.assertTrue(args.export_json)
        self.assertTrue(args.export_csv)
        self.assertTrue(args.export_xlsx)

    def test_roles_flag(self):
        args = self._parse(["o", "r", "--roles", "contributors", "stargazers"])
        self.assertEqual(args.roles, ["contributors", "stargazers"])

    def test_roles_defaults_to_none(self):
        args = self._parse(["o", "r"])
        self.assertIsNone(args.roles)

    def test_limit_flag(self):
        args = self._parse(["o", "r", "--limit", "10"])
        self.assertEqual(args.limit, 10)

    def test_limit_defaults_to_none(self):
        args = self._parse(["o", "r"])
        self.assertIsNone(args.limit)

    def test_workers_flag(self):
        args = self._parse(["o", "r", "--workers", "4"])
        self.assertEqual(args.workers, 4)

    def test_workers_defaults_to_one(self):
        args = self._parse(["o", "r"])
        self.assertEqual(args.workers, 1)

    def test_exclude_bots_flag(self):
        args = self._parse(["o", "r", "--exclude-bots"])
        self.assertTrue(args.exclude_bots)

    def test_exclude_bots_default_false(self):
        args = self._parse(["o", "r"])
        self.assertFalse(args.exclude_bots)

    def test_no_verbose_flag(self):
        args = self._parse(["o", "r", "--no-verbose"])
        self.assertTrue(args.no_verbose)

    def test_no_verbose_default_false(self):
        args = self._parse(["o", "r"])
        self.assertFalse(args.no_verbose)

    def test_fields_flag(self):
        args = self._parse(["o", "r", "--fields", "login", "name"])
        self.assertEqual(args.fields, ["login", "name"])

    def test_skip_codeowners_flag(self):
        args = self._parse(["o", "r", "--skip-codeowners"])
        self.assertTrue(args.skip_codeowners)

    def test_skip_collaborators_flag(self):
        args = self._parse(["o", "r", "--skip-collaborators"])
        self.assertTrue(args.skip_collaborators)

    def test_skip_flags_default_false(self):
        args = self._parse(["o", "r"])
        self.assertFalse(args.skip_codeowners)
        self.assertFalse(args.skip_collaborators)


# ---------------------------------------------------------------------------
# main() functional tests
# ---------------------------------------------------------------------------

class TestCliMain(unittest.TestCase):
    """Functional tests for main() — all external calls mocked."""

    def _mock_rp(self, user_data=None):
        mock_rp = MagicMock()
        mock_rp.get_users.return_value = user_data or {"alice": {"login": "alice"}}
        mock_rp.export_to_xlsx.return_value = "/tmp/out.xlsx"
        return mock_rp

    def test_main_invokes_get_users(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo"])
        mock_rp.get_users.assert_called_once()

    def test_main_passes_export_json(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo", "--export-json"])
        self.assertTrue(mock_rp.get_users.call_args[1]["export"])

    def test_main_passes_export_csv(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo", "--export-csv"])
        self.assertTrue(mock_rp.get_users.call_args[1]["export_csv"])

    def test_main_passes_export_xlsx(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo", "--export-xlsx"])
        self.assertTrue(mock_rp.get_users.call_args[1]["export_xlsx"])

    def test_main_passes_roles(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo", "--roles", "contributors"])
        self.assertEqual(mock_rp.get_users.call_args[1]["roles"], ["contributors"])

    def test_main_passes_limit(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo", "--limit", "5"])
        self.assertEqual(mock_rp.get_users.call_args[1]["limit"], 5)

    def test_main_passes_workers(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo", "--workers", "4"])
        self.assertEqual(mock_rp.get_users.call_args[1]["workers"], 4)

    def test_main_passes_exclude_bots(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo", "--exclude-bots"])
        self.assertTrue(mock_rp.get_users.call_args[1]["exclude_bots"])

    def test_main_no_verbose_sets_verbose_false(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo", "--no-verbose"])
        self.assertFalse(mock_rp.get_users.call_args[1]["verbose"])

    def test_main_verbose_true_by_default(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo"])
        self.assertTrue(mock_rp.get_users.call_args[1]["verbose"])

    def test_main_passes_fields(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            main(["owner", "repo", "--fields", "login", "name"])
        self.assertEqual(mock_rp.get_users.call_args[1]["fields"], ["login", "name"])

    def test_main_exits_on_connection_error(self):
        with patch("repo_people.cli.RepoPeople", side_effect=ConnectionError("bad token")):
            with self.assertRaises(SystemExit) as ctx:
                main(["owner", "repo"])
        self.assertEqual(ctx.exception.code, 1)

    def test_main_exits_on_value_error_from_init(self):
        with patch("repo_people.cli.RepoPeople", side_effect=ValueError("bad owner")):
            with self.assertRaises(SystemExit) as ctx:
                main(["owner", "repo"])
        self.assertEqual(ctx.exception.code, 1)

    def test_main_exits_on_invalid_role(self):
        mock_rp = self._mock_rp()
        mock_rp.get_users.side_effect = ValueError("Invalid role(s)")
        with patch("repo_people.cli.RepoPeople", return_value=mock_rp):
            with self.assertRaises(SystemExit) as ctx:
                main(["owner", "repo"])
        self.assertEqual(ctx.exception.code, 1)

    def test_token_falls_back_to_env_var(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            mock_cls.return_value = mock_rp
            with patch.dict("os.environ", {"GITHUB_TOKEN": "env_token"}):
                main(["owner", "repo"])
        self.assertEqual(mock_cls.call_args[1]["token"], "env_token")

    def test_explicit_token_takes_priority_over_env_var(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            mock_cls.return_value = mock_rp
            with patch.dict("os.environ", {"GITHUB_TOKEN": "env_token"}):
                main(["owner", "repo", "--token", "explicit_token"])
        self.assertEqual(mock_cls.call_args[1]["token"], "explicit_token")

    def test_skip_codeowners_forwarded(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            mock_cls.return_value = mock_rp
            main(["owner", "repo", "--skip-codeowners"])
        self.assertTrue(mock_cls.call_args[1]["skip_codeowners"])

    def test_skip_collaborators_forwarded(self):
        mock_rp = self._mock_rp()
        with patch("repo_people.cli.RepoPeople") as mock_cls:
            mock_cls.return_value = mock_rp
            main(["owner", "repo", "--skip-collaborators"])
        self.assertTrue(mock_cls.call_args[1]["skip_collaborators"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

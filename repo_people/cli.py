"""
Command-line interface for repo-people.

Usage::

    repo-people <owner> <repo> [options]

Examples::

    repo-people torvalds linux --token ghp_... --export-json --export-csv
    repo-people psf cpython --roles contributors stargazers --limit 50
    repo-people amckenna41 iso3166-2 --export-xlsx --exclude-bots
"""

import argparse
import os
import sys

from repo_people import RepoPeople


def _build_parser() -> argparse.ArgumentParser:
    """
    Build and configure the command-line argument parser for repo-people,
    registering the positional owner/repo arguments and every optional flag
    (token, output directory, roles, export formats, limits, workers, etc.).

    Returns
    =======
    :parser: argparse.ArgumentParser
        the fully configured argument parser for the repo-people CLI.
    """
    parser = argparse.ArgumentParser(
        prog="repo-people",
        description=(
            "Collect and export full GitHub user profile data for everyone "
            "associated with a repository."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("owner", help="GitHub repository owner (user or organisation).")
    parser.add_argument("repo", help="GitHub repository name.")
    parser.add_argument(
        "--token",
        default=None,
        metavar="TOKEN",
        help="GitHub personal access token. Falls back to the GITHUB_TOKEN env var if not supplied.",
    )
    parser.add_argument(
        "--outdir",
        default="outputs",
        metavar="DIR",
        help="Output directory (default: outputs).",
    )
    parser.add_argument(
        "--roles",
        nargs="+",
        default=None,
        metavar="ROLE",
        help=(
            "Roles to collect users from (default: all roles). "
            "Valid values: contributors, maintainers, stargazers, watchers, "
            "issue_authors, pr_authors, pr_reviewers, fork_owners, commit_authors, dependents."
        ),
    )
    parser.add_argument(
        "--export-json",
        action="store_true",
        help="Export results to a JSON file inside --outdir.",
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Export results to a CSV file inside --outdir.",
    )
    parser.add_argument(
        "--export-xlsx",
        action="store_true",
        help="Export results to an Excel (.xlsx) file inside --outdir. Requires openpyxl.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of user profiles to fetch.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Number of concurrent fetch threads (default: 1).",
    )
    parser.add_argument(
        "--exclude-bots",
        action="store_true",
        help="Skip bot accounts (logins ending in [bot] or -bot, and accounts with type=Bot).",
    )
    parser.add_argument(
        "--fields",
        nargs="+",
        default=None,
        metavar="FIELD",
        help="Output only these profile fields (e.g. --fields login name followers).",
    )
    parser.add_argument(
        "--no-verbose",
        action="store_true",
        help="Suppress per-user fetch messages.",
    )
    parser.add_argument(
        "--skip-codeowners",
        action="store_true",
        help="Skip the CODEOWNERS file when collecting maintainers.",
    )
    parser.add_argument(
        "--skip-collaborators",
        action="store_true",
        help="Skip the collaborators API when collecting maintainers.",
    )
    return parser


def main(argv=None) -> None:
    """
    Entry point for the repo-people command-line interface. Parses the CLI
    arguments, resolves the GitHub token (falling back to the ``GITHUB_TOKEN``
    environment variable), instantiates :class:`RepoPeople`, runs the user
    collection pipeline and prints a summary of how many users were collected.

    Parameters
    ==========
    :argv: list/None (default=None)
        argument list to parse. If None, ``sys.argv`` is used (the normal case
        when invoked from the shell); an explicit list is mainly useful in tests.

    Returns
    =======
    None

    Raises
    ======
    SystemExit:
        With exit code 1 when constructing the client or collecting users fails
        with a ValueError or ConnectionError.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Fall back to GITHUB_TOKEN env var if --token not supplied
    token = args.token or os.environ.get("GITHUB_TOKEN")

    try:
        rp = RepoPeople(
            owner=args.owner,
            repo=args.repo,
            token=token,
            outdir=args.outdir,
            skip_codeowners=args.skip_codeowners,
            skip_collaborators=args.skip_collaborators,
        )
    except (ValueError, ConnectionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        user_data = rp.get_users(
            export=args.export_json,
            export_csv=args.export_csv,
            export_xlsx=args.export_xlsx,
            limit=args.limit,
            roles=args.roles,
            exclude_bots=args.exclude_bots,
            verbose=not args.no_verbose,
            fields=args.fields,
            workers=args.workers,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDone. Collected {len(user_data)} users.")


if __name__ == "__main__":
    main()

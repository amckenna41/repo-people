"""
Command-line interface for repo-people.

Usage::

    repo-people <owner> <repo> [options]

Examples::

    repo-people torvalds linux --token ghp_... --export-json --export-csv
    repo-people psf cpython --roles contributors stargazers --limit 50 --summarise
    repo-people amckenna41 iso3166-2 --async --concurrency 20 --export-sqlite

Exit codes: ``0`` all profiles collected, ``1`` usage/validation/connection
error, ``2`` the run finished but some profiles could not be fetched.
"""

import argparse
import asyncio
import os
import sys

from repo_people import RepoPeople
from repo_people.utils import clear_cache

# A run that lost users must not look like success to CI.
EXIT_PARTIAL_FAILURE = 2

# How many failed logins to name on stderr before truncating.
_MAX_REPORTED_FAILURES = 20


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

    # --- export formats ---
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
        "--export-md",
        action="store_true",
        help="Export results to a Markdown table inside --outdir.",
    )
    parser.add_argument(
        "--export-sqlite",
        action="store_true",
        help="Export results to a SQLite database inside --outdir (upserts by login).",
    )

    # --- filtering ---
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of user profiles to fetch.",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        default=None,
        metavar="LOGIN",
        help="Logins to skip entirely (e.g. --exclude dependabot renovate).",
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

    # --- fetch behaviour ---
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Number of concurrent fetch threads for the sync path (default: 1, max 32).",
    )
    parser.add_argument(
        "--async",
        dest="use_async",
        action="store_true",
        help="Use the asyncio/aiohttp pipeline instead of threads. Requires aiohttp.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        metavar="N",
        help="Maximum simultaneous requests when --async is used (default: 10, max 32).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Load an existing user_details.json and skip logins already in it.",
    )
    parser.add_argument(
        "--save-each-iteration",
        action="store_true",
        help="Persist progress to user_details.json during the run, so it survives interruption.",
    )
    parser.add_argument(
        "--include-social-accounts",
        action="store_true",
        help="Fetch each user's linked social accounts. Costs one extra API call per user.",
    )

    # --- caching / transport ---
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable the on-disk ETag cache (conditional requests).",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete every on-disk cache entry and exit without collecting.",
    )
    parser.add_argument(
        "--no-graphql",
        action="store_true",
        help="Disable the GraphQL fast paths and use REST only.",
    )

    # --- output ---
    parser.add_argument(
        "--summarise",
        action="store_true",
        help="Print a summary breakdown (bots, locations, companies, countries, roles) after collecting.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Show a tqdm progress bar instead of per-user lines (use with --no-verbose).",
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


def _report_failures(failed) -> None:
    """
    Print the logins that could not be fetched to stderr, truncating the list so
    a run that lost hundreds of users does not bury the rest of the output.

    Parameters
    ==========
    :failed: list
        the logins recorded in ``RepoPeople.last_failed``.

    Returns
    =======
    None
    """
    if not failed:
        return
    shown = ", ".join(failed[:_MAX_REPORTED_FAILURES])
    if len(failed) > _MAX_REPORTED_FAILURES:
        shown += ", …"
    print(
        f"Warning: could not fetch {len(failed)} user(s): {shown}",
        file=sys.stderr,
    )


def main(argv=None) -> int:
    """
    Entry point for the repo-people command-line interface. Parses the CLI
    arguments, resolves the GitHub token (falling back to the ``GITHUB_TOKEN``
    environment variable), instantiates :class:`RepoPeople`, runs the sync or
    async collection pipeline and reports how many users were collected.

    Parameters
    ==========
    :argv: list/None (default=None)
        argument list to parse. If None, ``sys.argv`` is used (the normal case
        when invoked from the shell); an explicit list is mainly useful in tests.

    Returns
    =======
    :exit_code: int
        ``0`` when every requested profile was collected, or
        :data:`EXIT_PARTIAL_FAILURE` (``2``) when some could not be fetched.

    Raises
    ======
    SystemExit:
        With exit code 1 when the run cannot proceed — an invalid owner/repo,
        a failed connection or token check, an invalid field/role name, or a
        missing optional dependency for a requested export format.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --clear-cache is a maintenance action: do it and stop, before spending any
    # API call on constructing a client.
    if args.clear_cache:
        removed = clear_cache()
        noun = "entry" if removed == 1 else "entries"
        print(f"Cleared {removed} cache {noun}.")
        return 0

    # Check the optional async dependency before constructing the client, which
    # spends an API call verifying the token. A missing extra should cost nothing.
    if args.use_async:
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            print(
                "Error: --async requires aiohttp. Install it with: "
                'pip install "repo-people[async]"',
                file=sys.stderr,
            )
            sys.exit(1)

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
            use_cache=not args.no_cache,
            use_graphql=not args.no_graphql,
        )
    except (ValueError, ConnectionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Options shared by both pipelines. workers/progress are sync-only and
    # concurrency is async-only, so they are added per-path below.
    common = dict(
        export=args.export_json,
        export_csv=args.export_csv,
        export_xlsx=args.export_xlsx,
        export_markdown=args.export_md,
        export_sqlite=args.export_sqlite,
        save_each_iteration=args.save_each_iteration,
        limit=args.limit,
        roles=args.roles,
        exclude=args.exclude,
        exclude_bots=args.exclude_bots,
        resume=args.resume,
        verbose=not args.no_verbose,
        fields=args.fields,
        include_social_accounts=args.include_social_accounts,
    )

    try:
        if args.use_async:
            user_data = asyncio.run(
                rp.get_users_async(concurrency=args.concurrency, **common)
            )
        else:
            user_data = rp.get_users(
                workers=args.workers, progress=args.progress, **common
            )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ImportError as exc:
        # e.g. --export-xlsx without openpyxl installed.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.summarise:
        rp.summarise(user_data)

    print(f"\nDone. Collected {len(user_data)} users.")

    # A partially collected dataset must not look like success to CI.
    failed = getattr(rp, "last_failed", None) or []
    _report_failures(failed)
    return EXIT_PARTIAL_FAILURE if failed else 0


if __name__ == "__main__":
    sys.exit(main())

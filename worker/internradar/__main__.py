"""Command line entry point.

    py -m internradar fetch                     # list jobs for every enabled company
    py -m internradar fetch --company Groww     # one company
    py -m internradar fetch --company Intel --details --show 3

Phase 2 adds `run` (with --dry-run and --alert-on-baseline).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from internradar.config import ConfigError, contact_email, load_companies, load_env
from internradar.fetch import fetch_all, fill_details
from internradar.http import PoliteClient


def _fetch(args: argparse.Namespace) -> int:
    companies = load_companies()
    if args.company:
        wanted = args.company.lower()
        companies = [c for c in companies if c.name.lower() == wanted]
        if not companies:
            print(f"No company named {args.company!r} in companies.yaml", file=sys.stderr)
            return 2
        companies[0].enabled = True  # --company runs it even if disabled

    start = time.monotonic()
    with PoliteClient(contact_email()) as http:
        results = fetch_all(companies, http)
        for r in results:
            if not r.ok:
                print(f"\n✗ {r.company.name}: {r.error}")
                continue
            took = f"{r.duration_s:.1f}s"
            print(f"\n✓ {r.company.name} ({r.company.ats}) — {len(r.jobs)} jobs in {took}")
            for job in r.jobs[: args.show]:
                if args.details:
                    job = fill_details(r.company, job, http)
                if args.json:
                    print(json.dumps(job.model_dump(mode="json"), indent=2, ensure_ascii=False))
                else:
                    posted = job.posted_at.date().isoformat() if job.posted_at else "?"
                    print(f"  • {job.title} | {job.location or '-'} | posted {posted}")
                    print(f"    {job.url}")
                    if args.details:
                        print(f"    description: {len(job.description_text)} chars")
        requests = http.request_count

    failed = [r for r in results if not r.ok]
    print(
        f"\nSummary: {len(results)} companies checked, {len(failed)} failed, "
        f"{sum(len(r.jobs) for r in results)} jobs, {requests} requests, "
        f"{time.monotonic() - start:.1f}s"
    )
    for r in failed:
        print(f"  failed: {r.company.name} — {r.error}")
    return 1 if failed and len(failed) == len(results) else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_env()

    parser = argparse.ArgumentParser(prog="internradar")
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="fetch and print jobs (no database, no alerts)")
    f.add_argument("--company", help="only this company (name from companies.yaml)")
    f.add_argument("--show", type=int, default=5, help="jobs to print per company")
    f.add_argument("--details", action="store_true", help="also fetch descriptions/dates")
    f.add_argument("--json", action="store_true", help="print full normalised Job objects")
    args = parser.parse_args(argv)

    try:
        return _fetch(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

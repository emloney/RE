"""Command line entry point.

py -m internradar fetch                     # list jobs for every enabled company
py -m internradar fetch --company Groww     # one company
py -m internradar fetch --company Intel --details --show 3

py -m internradar run                       # the real checker: detect new jobs, save state
py -m internradar run --dry-run             # read the database but write nothing
py -m internradar run --company Groww       # one company
py -m internradar run --alert-on-baseline   # testing only: treat first-fetch jobs as new
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from internradar.config import ConfigError, contact_email, load_companies, load_env
from internradar.fetch import fetch_all, fill_details
from internradar.http import PoliteClient
from internradar.run import UnknownCompany, format_summary, run_once
from internradar.store import MemoryStore, ReadOnlyStore, Store
from internradar.supabase_store import StoreError, SupabaseStore


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


def _make_store(dry_run: bool) -> Store:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        store: Store = SupabaseStore(url, key)
        return ReadOnlyStore(store) if dry_run else store
    if dry_run:
        print(
            "No SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY set: using a temporary in-memory "
            "store, so every company is a first-time baseline (add --alert-on-baseline "
            "to see them as new).\n"
        )
        return MemoryStore()
    raise ConfigError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env (or use --dry-run)")


def _run(args: argparse.Namespace) -> int:
    store = _make_store(args.dry_run)
    with PoliteClient(contact_email()) as http:
        result = run_once(
            store,
            load_companies(),
            http,
            only=args.company,
            alert_on_baseline=args.alert_on_baseline,
            trigger=args.trigger,
        )

    verb = "Would alert" if args.dry_run else "New"
    for found in result.new_jobs:
        job = found.new.job
        print(f"🆕 {verb}: {job.company} — {job.title} | {job.location or '-'}\n   {job.url}")
    print()
    print(format_summary(result.summary))
    return 1 if result.summary.status == "failed" else 0


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

    r = sub.add_parser("run", help="check for new jobs and save state")
    r.add_argument("--dry-run", action="store_true", help="read the database, write nothing")
    r.add_argument("--company", help="only this company (runs it even if disabled)")
    r.add_argument(
        "--alert-on-baseline",
        action="store_true",
        help="TESTING ONLY: treat a company's first-fetch jobs as new",
    )
    r.add_argument("--trigger", default="manual", help="recorded in the runs table")
    args = parser.parse_args(argv)

    try:
        return _fetch(args) if args.command == "fetch" else _run(args)
    except (ConfigError, UnknownCompany, StoreError) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

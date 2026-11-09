"""CLI: `uv run python -m app.modules.migration import --source DIR [--prefix v1_] [--apply] [--report out.json]`.

Dry-run is the default; nothing is written without --apply. The legacy APP_SECRET (only needed to carry MFA enrolments
over) is read from the environment variable named by --legacy-secret-env, never from argv.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from sqlalchemy import select

from ...core.services import SYSTEM
from ...db import session_factory
from ..platform.models import Store
from .importer import run_import


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.modules.migration")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="dry-run (default) or apply a legacy export")
    imp.add_argument("--source", required=True, help="directory of <collection>.json / .ndjson files")
    imp.add_argument("--prefix", default="", help="legacy collection prefix, e.g. v1_")
    imp.add_argument("--store-slug", help="target store (default: the only/first store)")
    imp.add_argument("--apply", action="store_true", help="write changes (otherwise a rolled-back dry run)")
    imp.add_argument("--report", help="write the full JSON report here")
    imp.add_argument("--legacy-secret-env", default="VOLTORA_LEGACY_APP_SECRET",
                     help="env var holding the legacy APP_SECRET (to carry MFA secrets over)")
    args = ap.parse_args(argv)

    with session_factory()() as db:
        q = select(Store).order_by(Store.created_at)
        store = db.execute(q.where(Store.slug == args.store_slug) if args.store_slug else q.limit(1)).scalars().first()
        if store is None:
            print("No store found. Complete installation first (the importer needs a target store).", file=sys.stderr)
            return 2
        report = run_import(db, store, args.source, apply=args.apply, prefix=args.prefix, actor=SYSTEM,
                            legacy_app_secret=os.environ.get(args.legacy_secret_env) or None)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
    print(f"mode: {report['mode']}   store: {report['store']}   source: {report['source']}")
    print(f"{'collection':<16}{'read':>6}{'created':>9}{'linked':>8}{'existing':>9}{'invalid':>9}{'warn':>6}")
    for name, s in report["collections"].items():
        print(f"{name:<16}{s['read']:>6}{s['created']:>9}{s['linked']:>8}{s['skipped_existing']:>9}{s['invalid']:>9}{s['warnings']:>6}"
              + (f"  [{s['note']}]" if s.get("note") else ""))
    for k, v in report["skipped_targets"].items():
        print(f"SKIPPED {k}: {v}")
    for coll, why in report["not_migrated"].items():
        print(f"not migrated: {coll} ({why})")
    for coll, fields in report["unmapped_fields"].items():
        print(f"unmapped fields in {coll}: {', '.join(sorted(fields))}")
    if report["unmapped_settings"]:
        print("unmapped settings:", ", ".join(sorted(report["unmapped_settings"])))
    for name, r in report["reconciliation"].items():
        print(f"reconcile {name}: {'OK' if r.get('ok') else 'MISMATCH'} {json.dumps({k: v for k, v in r.items() if k not in ('ok', 'note')})}")
    inv = [i for i in report["issues"] if i["severity"] == "invalid"]
    for i in inv[:25]:
        print(f"INVALID {i['collection']}/{i['legacy_id']}: {i['reason']}")
    if len(inv) > 25:
        print(f"... {len(inv) - 25} more invalid records (see --report)")
    return 0 if report["totals"]["invalid"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

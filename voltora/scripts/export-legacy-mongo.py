#!/usr/bin/env python3
"""Export legacy VOLTORA MongoDB collections to NDJSON (one file per collection) for `app.modules.migration`.

Needs only pymongo (the legacy app's venv has it). mongoexport is not required.
The output contains password hashes and personal data: it is written 0700/0600 and must be deleted after the import.

  scripts/export-legacy-mongo.sh --uri mongodb://127.0.0.1:27017 --db voltora --prefix v1_ --out /secure/legacy-export
"""
import argparse
import os
import sys
from pathlib import Path

try:
    from bson import json_util
    from pymongo import MongoClient
except ImportError:
    sys.exit("pymongo is required (use the legacy backend venv: LEGACY_PYTHON=/path/to/legacy/.venv/bin/python)")

SKIP_SUFFIXES = ("_blobs", "_variants_blobs")
SKIP = {"sessions", "login_attempts", "otps", "rate_limits", "events", "previews", "email_queue", "notifications",
        "font_blobs", "visitor_sessions", "alert_log", "offer_events", "migrations"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default=os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017"))
    ap.add_argument("--db", default=os.environ.get("DB_NAME"), required=not os.environ.get("DB_NAME"))
    ap.add_argument("--prefix", default=os.environ.get("REBUILD_COLLECTION_PREFIX", ""))
    ap.add_argument("--out", required=True)
    ap.add_argument("--include-ephemeral", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out, 0o700)
    db = MongoClient(a.uri, serverSelectionTimeoutMS=5000)[a.db]
    total = 0
    for full in sorted(db.list_collection_names()):
        name = full[len(a.prefix):] if a.prefix and full.startswith(a.prefix) else full
        if a.prefix and not full.startswith(a.prefix):
            continue
        if not a.include_ephemeral and (name in SKIP or name.endswith(SKIP_SUFFIXES)):
            continue
        path = out / f"{name}.ndjson"
        n = 0
        with path.open("w", encoding="utf-8") as f:
            for doc in db[full].find({}):
                f.write(json_util.dumps(doc, json_options=json_util.RELAXED_JSON_OPTIONS) + "\n")
                n += 1
        os.chmod(path, 0o600)
        total += n
        print(f"{name}: {n}")
    print(f"exported {total} documents to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

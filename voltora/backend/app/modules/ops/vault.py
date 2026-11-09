"""Vault key rotation: re-seal every sealed column with a new Fernet key, all-or-nothing.

CLI (stop the API first, take a backup first):
  OLD_KEY=... NEW_KEY=... uv run python -m app.modules.ops.vault rotate --old-key-env OLD_KEY --new-key-env NEW_KEY [--apply]
  uv run python -m app.modules.ops.vault verify          # every sealed value decrypts with the configured VOLTORA_VAULT_KEY

Sealed columns are discovered from the schema (any column named `sealed` or `*_sealed`), so modules added later are covered.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ...db import Base


def sealed_columns() -> list[tuple]:
    out = []
    for t in Base.metadata.sorted_tables:
        for c in t.columns:
            if c.name == "sealed" or c.name.endswith("_sealed"):
                out.append((t, c))
    return out


def rotate(db: Session, old_key: str, new_key: str, apply: bool) -> dict:
    """Decrypt with `old_key`, encrypt with `new_key`. Any value that does not decrypt aborts the whole run untouched."""
    old, new = Fernet(old_key.encode()), Fernet(new_key.encode())
    report: dict = {"mode": "apply" if apply else "dry_run", "columns": {}, "failures": []}
    changes: list[tuple] = []
    for table, col in sealed_columns():
        pks = list(table.primary_key.columns)
        n = 0
        for row in db.execute(select(*pks, col).where(col.is_not(None))).all():
            *pk_vals, token = row
            try:
                plain = old.decrypt(token.encode())
            except InvalidToken:
                report["failures"].append({"table": table.name, "column": col.name, "pk": [str(v) for v in pk_vals]})
                continue
            changes.append((table, col, pks, pk_vals, new.encrypt(plain).decode()))
            n += 1
        report["columns"][f"{table.name}.{col.name}"] = n
    if report["failures"]:
        report["ok"] = False
        report["error"] = "Some values do not decrypt with the old key; nothing was changed."
        db.rollback()
        return report
    if apply:
        for table, col, pks, pk_vals, token in changes:
            db.execute(update(table).where(*[p == v for p, v in zip(pks, pk_vals, strict=True)]).values({col.name: token}))
        db.commit()
        # Re-read with the new key: prove the result before reporting success.
        bad = 0
        for _table, col in sealed_columns():
            for (token,) in db.execute(select(col).where(col.is_not(None))).all():
                try:
                    new.decrypt(token.encode())
                except InvalidToken:
                    bad += 1
        report["verified_with_new_key"] = bad == 0
        report["ok"] = bad == 0
    else:
        db.rollback()
        report["ok"] = True
    report["rotated"] = len(changes)
    return report


def verify(db: Session, key: str) -> dict:
    f = Fernet(key.encode())
    total, bad = 0, []
    for table, col in sealed_columns():
        for row in db.execute(select(*table.primary_key.columns, col).where(col.is_not(None))).all():
            total += 1
            try:
                f.decrypt(row[-1].encode())
            except InvalidToken:
                bad.append(f"{table.name}.{col.name}:{row[0]}")
    return {"ok": not bad, "checked": total, "undecryptable": bad}


def main(argv: list[str] | None = None) -> int:
    from ...config import get_settings
    from ...db import session_factory
    from ...main import discover_modules

    discover_modules()
    ap = argparse.ArgumentParser(prog="python -m app.modules.ops.vault")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rotate")
    r.add_argument("--old-key-env", required=True)
    r.add_argument("--new-key-env", required=True)
    r.add_argument("--apply", action="store_true")
    sub.add_parser("verify")
    a = ap.parse_args(argv)
    with session_factory()() as db:
        if a.cmd == "verify":
            key = get_settings().vault_key
            if not key:
                print("VOLTORA_VAULT_KEY is not set", file=sys.stderr)
                return 2
            rep = verify(db, key)
        else:
            old, new = os.environ.get(a.old_key_env, ""), os.environ.get(a.new_key_env, "")
            if not old or not new or old == new:
                print("Both key variables must be set and different", file=sys.stderr)
                return 2
            rep = rotate(db, old, new, a.apply)
    print(json.dumps(rep, indent=2))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

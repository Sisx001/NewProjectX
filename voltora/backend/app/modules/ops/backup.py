"""Backups: pg_dump (custom format) + media tarball in one `.vbak` container, optional AES-256-GCM, sha256 manifest,
restore drills into a disposable database, retention.

CLI: `uv run python -m app.modules.ops.backup create|verify|list|restore`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from ...config import CORE_VERSION, get_settings
from ...core.services import SYSTEM, Actor, audit, verify_audit_chain
from ...db import utcnow
from ..platform.models import BackupRecord, SecretRecord, Store
from .dbtools import parse_url, pg_binary, pg_env, scratch_database

MAGIC = b"VBK1"
CHUNK = 1024 * 1024
KEY_TABLES = ("stores", "users", "audit_events", "setting_values", "secrets", "products", "variants",
              "customers", "orders", "stock_levels", "payments", "backups")


class BackupError(Exception):
    pass


def backup_dir() -> Path:
    d = get_settings().data_dir / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def media_dir() -> Path:
    return get_settings().data_dir / "media"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- AES-256-GCM container
def _key(salt: bytes) -> bytes:
    s = get_settings()
    ikm = (s.vault_key or s.secret_key).encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"voltora-backup-v1").derive(ikm)


def encrypt_file(src: Path, dst: Path) -> None:
    """Chunked AEAD: each chunk's nonce = random prefix || counter, AAD marks the final chunk (truncation is detected)."""
    salt, prefix = os.urandom(16), os.urandom(8)
    aes = AESGCM(_key(salt))
    with src.open("rb") as fi, dst.open("wb") as fo:
        fo.write(MAGIC + salt + prefix)
        counter = 0
        block = fi.read(CHUNK)
        while True:
            nxt = fi.read(CHUNK)
            final = not nxt
            ct = aes.encrypt(prefix + struct.pack(">I", counter), block, b"\x01" if final else b"\x00")
            fo.write(struct.pack(">I", len(ct)) + ct)
            if final:
                break
            block, counter = nxt, counter + 1


def decrypt_file(src: Path, dst: Path) -> None:
    with src.open("rb") as fi, dst.open("wb") as fo:
        head = fi.read(4 + 16 + 8)
        if len(head) != 28 or head[:4] != MAGIC:
            raise BackupError("Not a VOLTORA encrypted backup")
        aes = AESGCM(_key(head[4:20]))
        prefix = head[20:]
        counter = 0
        while True:
            raw = fi.read(4)
            if not raw:
                raise BackupError("Encrypted backup is truncated")
            n = struct.unpack(">I", raw)[0]
            ct = fi.read(n)
            if len(ct) != n:
                raise BackupError("Encrypted backup is truncated")
            final = not fi.peek(1)
            try:
                fo.write(aes.decrypt(prefix + struct.pack(">I", counter), ct, b"\x01" if final else b"\x00"))
            except InvalidTag as exc:
                raise BackupError("Decryption failed: wrong vault key or the file was modified") from exc
            if final:
                return
            counter += 1


# ---------------------------------------------------------------- creation
def _row_counts(url: str) -> dict[str, int]:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            names = inspect(c).get_table_names()
            return {n: c.execute(text(f'SELECT count(*) FROM "{n}"')).scalar_one() for n in sorted(names)}
    finally:
        eng.dispose()


def _alembic_revision(url: str) -> str | None:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            if "alembic_version" not in inspect(c).get_table_names():
                return None
            return c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        eng.dispose()


def _run(cmd: list[str], env: dict[str, str]) -> None:
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        raise BackupError(f"{Path(cmd[0]).name} failed: {p.stderr.strip()[-600:]}")


def start_record(db: Session, actor_id: uuid.UUID | None, encrypt: bool) -> BackupRecord:
    rec = BackupRecord(kind="full", status="running", encrypted=encrypt, created_by=actor_id)
    db.add(rec)
    db.commit()
    return rec


def run_backup(record_id: uuid.UUID, store_id: uuid.UUID | None, actor: Actor = SYSTEM, session_factory=None) -> None:
    """Executes a started record. Never raises: failures are stored on the record (visible in the admin UI)."""
    from ...db import session_factory as default_sf

    sf = session_factory or default_sf()
    t0 = time.monotonic()
    with sf() as db:
        rec = db.get(BackupRecord, record_id)
        assert rec is not None
        final: Path | None = None
        try:
            s = get_settings()
            pg_dump = pg_binary("pg_dump")
            if not pg_dump:
                raise BackupError("pg_dump was not found on this host (install postgresql-client-16)")
            stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
            name = f"voltora-{stamp}-{str(rec.id)[:8]}.vbak"
            with tempfile.TemporaryDirectory(dir=backup_dir(), prefix=".work-") as tmp:
                tmpd = Path(tmp)
                before = _row_counts(s.database_url)
                dump = tmpd / "database.dump"
                _run([pg_dump, "-Fc", "--no-owner", "--no-acl", "-f", str(dump), parse_url(s.database_url).database],
                     pg_env(s.database_url))
                after = _row_counts(s.database_url)
                files = [("database.dump", dump)]
                md = media_dir()
                if md.exists() and any(md.rglob("*")):
                    media_tar = tmpd / "media.tar"
                    with tarfile.open(media_tar, "w") as tf:
                        tf.add(md, arcname="media")
                    files.append(("media.tar", media_tar))
                manifest = {
                    "format": 1, "app_version": CORE_VERSION, "created_at": utcnow().isoformat(),
                    "alembic_revision": _alembic_revision(s.database_url), "database": parse_url(s.database_url).database,
                    "files": [{"name": n, "sha256": sha256_file(p), "bytes": p.stat().st_size} for n, p in files],
                    "row_counts_before": before, "row_counts_after": after,
                }
                (tmpd / "manifest.json").write_text(json.dumps(manifest, indent=2))
                container = tmpd / "container.tar"
                with tarfile.open(container, "w") as tf:
                    tf.add(tmpd / "manifest.json", arcname="manifest.json")
                    for n, p in files:
                        tf.add(p, arcname=n)
                final = backup_dir() / (name + (".enc" if rec.encrypted else ""))
                if rec.encrypted:
                    encrypt_file(container, final)
                else:
                    shutil.move(str(container), final)
            final.chmod(0o600)
            rec.path, rec.size_bytes, rec.sha256 = str(final), final.stat().st_size, sha256_file(final)
            rec.status, rec.error, rec.finished_at = "succeeded", None, utcnow()
            rec.restore_report = {"manifest": {k: v for k, v in manifest.items() if not k.startswith("row_counts")},
                                  "duration_seconds": round(time.monotonic() - t0, 2)}
            audit(db, store_id, actor, "backup.created", "backup", rec.id,
                  {"size_bytes": rec.size_bytes, "encrypted": rec.encrypted, "sha256": rec.sha256})
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
            if final is not None:
                final.unlink(missing_ok=True)
            rec.status, rec.error, rec.finished_at = "failed", str(exc)[:1000], utcnow()
            audit(db, store_id, actor, "backup.failed", "backup", rec.id, {"error": rec.error})
        db.commit()


def create_backup(db: Session, store_id: uuid.UUID | None, actor: Actor = SYSTEM, encrypt: bool = False) -> BackupRecord:
    rec = start_record(db, actor.id, encrypt)
    run_backup(rec.id, store_id, actor, session_factory=lambda: Session(db.get_bind(), expire_on_commit=False))
    db.refresh(rec)
    return rec


# ---------------------------------------------------------------- verification
def _check(report: dict, name: str, ok: bool, detail: str = "") -> bool:
    report["steps"].append({"name": name, "ok": ok, "detail": detail})
    if not ok:
        report["ok"] = False
    return ok


def open_container(rec: BackupRecord, workdir: Path, report: dict | None = None) -> Path | None:
    """sha256 check, optional decrypt, manifest hash check. Returns directory with extracted members."""
    report = report if report is not None else {"ok": True, "steps": []}
    path = Path(rec.path or "")
    if not path.is_file():
        _check(report, "file_present", False, "backup file is missing")
        return None
    if not _check(report, "sha256", sha256_file(path) == rec.sha256,
                  "matches the recorded checksum" if sha256_file(path) == rec.sha256 else "checksum mismatch: file was modified"):
        return None
    tar_path = path
    if rec.encrypted:
        tar_path = workdir / "container.tar"
        try:
            decrypt_file(path, tar_path)
            _check(report, "decrypt", True, "AES-256-GCM authentication passed")
        except BackupError as exc:
            _check(report, "decrypt", False, str(exc))
            return None
    out = workdir / "x"
    out.mkdir()
    try:
        with tarfile.open(tar_path) as tf:
            tf.extractall(out, filter="data")
        manifest = json.loads((out / "manifest.json").read_text())
    except Exception as exc:  # noqa: BLE001
        _check(report, "container", False, f"unreadable archive: {exc}")
        return None
    bad = [f["name"] for f in manifest["files"] if sha256_file(out / f["name"]) != f["sha256"]]
    _check(report, "manifest", not bad, "all member hashes match" if not bad else f"hash mismatch: {', '.join(bad)}")
    return out if not bad else None


def restore_dump_to(dump: Path, target_url: str) -> None:
    pg_restore = pg_binary("pg_restore")
    if not pg_restore:
        raise BackupError("pg_restore was not found on this host (install postgresql-client-16)")
    _run([pg_restore, "--no-owner", "--no-acl", "--exit-on-error", "-d", parse_url(target_url).database, str(dump)],
         pg_env(target_url))


def verify_restore(db: Session, record_id: uuid.UUID, store_id: uuid.UUID | None, actor: Actor = SYSTEM) -> dict:
    """Restore drill: restore into a disposable database, check integrity, record the report, drop the scratch DB."""
    rec = db.get(BackupRecord, record_id)
    if rec is None or rec.status != "succeeded":
        raise BackupError("Only succeeded backups can be verified")
    t0 = time.monotonic()
    report: dict = {"ok": True, "steps": [], "started_at": utcnow().isoformat()}
    rec.restore_report = {**(rec.restore_report or {}), "drill": {"status": "running"}}
    db.commit()
    try:
        with tempfile.TemporaryDirectory(dir=backup_dir(), prefix=".drill-") as tmp:
            out = open_container(rec, Path(tmp), report)
            if out is not None:
                manifest = json.loads((out / "manifest.json").read_text())
                with scratch_database(get_settings().database_url, "voltora_drill") as url:
                    restore_dump_to(out / "database.dump", url)
                    _check(report, "restore", True, "pg_restore completed into a scratch database")
                    counts = _row_counts(url)
                    before, after = manifest["row_counts_before"], manifest["row_counts_after"]
                    missing = [t for t in before if t not in counts]
                    _check(report, "tables_present", not missing, f"{len(counts)} tables" if not missing else f"missing: {missing}")
                    drift = []
                    for t, n in counts.items():
                        lo, hi = sorted((before.get(t, n), after.get(t, n)))
                        if not lo <= n <= hi:
                            drift.append(f"{t}: restored {n}, expected {lo}..{hi}")
                    _check(report, "row_counts", not drift, "restored counts match the backup window" if not drift else "; ".join(drift))
                    report["row_counts"] = {t: counts[t] for t in KEY_TABLES if t in counts}
                    report["total_rows"] = sum(counts.values())
                    eng = create_engine(url)
                    try:
                        with Session(eng) as sdb:
                            chains = {}
                            sids = [None, *sdb.execute(select(Store.id)).scalars()] if "stores" in counts else [None]
                            for sid in sids:
                                chains[str(sid)] = verify_audit_chain(sdb, sid)
                            broken = {k: v for k, v in chains.items() if not v["ok"]}
                            checked = sum(v["checked"] for v in chains.values())
                            _check(report, "audit_chain", not broken, f"{checked} events verified" if not broken else f"broken: {broken}")
                            ok_s, bad_s = 0, 0
                            from ...core.security import unseal
                            for sealed in sdb.execute(select(SecretRecord.sealed)).scalars():
                                try:
                                    unseal(sealed)
                                    ok_s += 1
                                except ValueError:
                                    bad_s += 1
                            _check(report, "secrets_decryptable", bad_s == 0,
                                   f"{ok_s} sealed secrets decrypt with the current vault key" if not bad_s
                                   else f"{bad_s} secrets cannot be decrypted with the current vault key")
                    finally:
                        eng.dispose()
                    if "media.tar" in {f["name"] for f in manifest["files"]}:
                        with tarfile.open(out / "media.tar") as tf:
                            _check(report, "media", True, f"{len(tf.getnames())} media entries readable")
    except Exception as exc:  # noqa: BLE001
        _check(report, "drill", False, str(exc)[:500])
    report["duration_seconds"] = round(time.monotonic() - t0, 2)
    rec = db.get(BackupRecord, record_id)
    assert rec is not None
    prior = dict(rec.restore_report or {})
    prior["drill"] = report
    rec.restore_report = prior
    rec.restore_verified_at = utcnow() if report["ok"] else None
    audit(db, store_id, actor, "backup.verified" if report["ok"] else "backup.verify_failed", "backup", rec.id,
          {"ok": report["ok"], "steps": [s["name"] for s in report["steps"] if not s["ok"]]})
    db.commit()
    return report


# ---------------------------------------------------------------- retention
def prune(db: Session, store_id: uuid.UUID | None, keep_count: int, keep_days: int) -> list[str]:
    """A backup survives if either rule keeps it (newest N, or younger than D days). The newest verified one is kept."""
    ok = db.execute(select(BackupRecord).where(BackupRecord.status == "succeeded")
                    .order_by(BackupRecord.created_at.desc())).scalars().all()
    cutoff = utcnow() - dt.timedelta(days=keep_days)
    protected = next((r.id for r in ok if r.restore_verified_at), None)
    removed = []
    for i, r in enumerate(ok):
        if i < keep_count or r.created_at >= cutoff or r.id == protected:
            continue
        delete_record(db, store_id, r, SYSTEM, reason="retention")
        removed.append(str(r.id))
    stale = utcnow() - dt.timedelta(days=7)
    for r in db.execute(select(BackupRecord).where(BackupRecord.status == "failed", BackupRecord.created_at < stale)).scalars():
        db.delete(r)
    db.commit()
    return removed


def delete_record(db: Session, store_id: uuid.UUID | None, rec: BackupRecord, actor: Actor, reason: str = "manual") -> None:
    if rec.path:
        p = Path(rec.path)
        if backup_dir().resolve() in p.resolve().parents:
            p.unlink(missing_ok=True)
    audit(db, store_id, actor, "backup.deleted", "backup", rec.id, {"reason": reason})
    db.delete(rec)


def serialize(r: BackupRecord) -> dict:
    drill = (r.restore_report or {}).get("drill")
    return {"id": str(r.id), "kind": r.kind, "status": r.status, "size_bytes": r.size_bytes, "sha256": r.sha256,
            "encrypted": r.encrypted, "file": Path(r.path).name if r.path else None, "error": r.error,
            "created_at": r.created_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "restore_verified_at": r.restore_verified_at.isoformat() if r.restore_verified_at else None,
            "drill": drill, "manifest": (r.restore_report or {}).get("manifest")}


# ---------------------------------------------------------------- CLI
def _default_store(db: Session) -> uuid.UUID | None:
    return db.execute(select(Store.id).order_by(Store.created_at).limit(1)).scalar()


def main(argv: list[str] | None = None) -> int:
    from ...db import session_factory

    ap = argparse.ArgumentParser(prog="python -m app.modules.ops.backup")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="create a backup now")
    c.add_argument("--encrypt", action="store_true", help="AES-256-GCM with a key derived from the vault key")
    v = sub.add_parser("verify", help="restore drill into a scratch database")
    v.add_argument("id", nargs="?", help="backup id (default: newest succeeded)")
    sub.add_parser("list")
    r = sub.add_parser("restore", help="restore a backup into an EMPTY database you created (never the live one)")
    r.add_argument("id")
    r.add_argument("--to", required=True, help="target database URL (postgresql://user:pw@host:port/dbname)")
    r.add_argument("--media-dir", help="also extract media into this directory")
    args = ap.parse_args(argv)
    print_ = lambda o: print(json.dumps(o, indent=2, default=str))  # noqa: E731
    with session_factory()() as db:
        sid = _default_store(db)
        if args.cmd == "create":
            rec = create_backup(db, sid, SYSTEM, encrypt=args.encrypt)
            print_(serialize(rec))
            return 0 if rec.status == "succeeded" else 1
        if args.cmd == "list":
            print_([serialize(x) for x in db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc())).scalars()])
            return 0
        if args.cmd == "verify":
            q = select(BackupRecord).where(BackupRecord.status == "succeeded").order_by(BackupRecord.created_at.desc())
            rec = db.get(BackupRecord, uuid.UUID(args.id)) if args.id else db.execute(q.limit(1)).scalar()
            if rec is None:
                print("no backup to verify", file=sys.stderr)
                return 2
            rep = verify_restore(db, rec.id, sid)
            print_(rep)
            return 0 if rep["ok"] else 1
        if args.cmd == "restore":
            rec = db.get(BackupRecord, uuid.UUID(args.id))
            live = parse_url(get_settings().database_url)
            tgt = parse_url(args.to)
            if rec is None:
                print("unknown backup", file=sys.stderr)
                return 2
            if (tgt.host, tgt.port, tgt.database) == (live.host, live.port, live.database):
                print("Refusing to restore over the live database. Restore into a new database, then switch "
                      "VOLTORA_DATABASE_URL (see docs/OPERATIONS.md).", file=sys.stderr)
                return 2
            with tempfile.TemporaryDirectory(dir=backup_dir(), prefix=".restore-") as tmp:
                rep: dict = {"ok": True, "steps": []}
                out = open_container(rec, Path(tmp), rep)
                if out is None:
                    print_(rep)
                    return 1
                restore_dump_to(out / "database.dump", args.to)
                if args.media_dir and (out / "media.tar").exists():
                    with tarfile.open(out / "media.tar") as tf:
                        tf.extractall(args.media_dir, filter="data")
            print("restored", rec.id, "into", tgt.database)
            return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

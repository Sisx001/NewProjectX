from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.security import seal
from app.core.services import audit, get_setting, set_settings, verify_audit_chain
from app.modules.ops import backup
from app.modules.ops.dbtools import pg_binary
from app.modules.platform.models import AuditEvent, BackupRecord, SecretRecord
from tests.conftest import staff_api

pytestmark = pytest.mark.skipif(not pg_binary("pg_dump"), reason="pg_dump/pg_restore not installed")


def _seed(db, store):
    db.add(SecretRecord(store_id=store.id, ref="test.secret", sealed=seal("hunter2"), hint="••••"))
    audit(db, store.id, backup.SYSTEM, "test.event", "thing", "1", {"a": 1})
    db.commit()


def _make(owner_api, encrypt=False):
    r = owner_api.post("/api/system/backups", json={"encrypt": encrypt})
    assert r.status_code == 202, r.text
    items = owner_api.get("/api/system/backups").json()["items"]
    rec = next(i for i in items if i["id"] == r.json()["id"])
    assert rec["status"] == "succeeded", rec
    return rec


@pytest.mark.parametrize("encrypt", [False, True])
def test_backup_then_restore_drill_passes(owner_api, db, store, encrypt):
    _seed(db, store)
    rec = _make(owner_api, encrypt)
    assert rec["encrypted"] is encrypt and rec["size_bytes"] > 0 and len(rec["sha256"]) == 64
    assert rec["file"].endswith(".enc") == encrypt
    r = owner_api.post(f"/api/system/backups/{rec['id']}/verify-restore")
    assert r.status_code == 202
    got = next(i for i in owner_api.get("/api/system/backups").json()["items"] if i["id"] == rec["id"])
    drill = got["drill"]
    assert drill["ok"] is True, drill
    steps = {s["name"]: s for s in drill["steps"]}
    assert {"sha256", "manifest", "restore", "tables_present", "row_counts", "audit_chain", "secrets_decryptable"} <= set(steps)
    assert all(s["ok"] for s in drill["steps"])
    assert drill["row_counts"]["stores"] == 1 and drill["row_counts"]["secrets"] >= 1
    assert got["restore_verified_at"]
    # scratch database was dropped
    from sqlalchemy import text

    from app.db import get_engine
    with get_engine().connect() as c:
        assert c.execute(text("select count(*) from pg_database where datname like 'voltora_drill_%'")).scalar() == 0
    actions = {e.action for e in db.execute(select(AuditEvent)).scalars()}
    assert {"backup.created", "backup.verified"} <= actions
    assert verify_audit_chain(db, store.id)["ok"]


def test_tampered_file_fails_checksum(owner_api, db, store):
    _seed(db, store)
    rec = _make(owner_api)
    path = Path(db.get(BackupRecord, rec["id"]).path)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    owner_api.post(f"/api/system/backups/{rec['id']}/verify-restore")
    got = next(i for i in owner_api.get("/api/system/backups").json()["items"] if i["id"] == rec["id"])
    assert got["drill"]["ok"] is False and got["restore_verified_at"] is None
    sha = next(s for s in got["drill"]["steps"] if s["name"] == "sha256")
    assert sha["ok"] is False and "mismatch" in sha["detail"]


def test_encrypted_tamper_with_fixed_checksum_still_fails_authentication(owner_api, db, store):
    _seed(db, store)
    rec = _make(owner_api, encrypt=True)
    row = db.get(BackupRecord, rec["id"])
    path = Path(row.path)
    data = bytearray(path.read_bytes())
    data[100] ^= 0x01
    path.write_bytes(bytes(data))
    row.sha256 = backup.sha256_file(path)  # attacker also rewrites the recorded checksum
    db.commit()
    owner_api.post(f"/api/system/backups/{rec['id']}/verify-restore")
    got = next(i for i in owner_api.get("/api/system/backups").json()["items"] if i["id"] == rec["id"])
    dec = next(s for s in got["drill"]["steps"] if s["name"] == "decrypt")
    assert got["drill"]["ok"] is False and dec["ok"] is False


def test_encryption_round_trip_multichunk_and_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "CHUNK", 1000)
    src = tmp_path / "plain"
    src.write_bytes(os.urandom(5500))
    enc, dec = tmp_path / "enc", tmp_path / "dec"
    backup.encrypt_file(src, enc)
    assert enc.read_bytes()[:4] == b"VBK1" and src.read_bytes()[:50] not in enc.read_bytes()
    backup.decrypt_file(enc, dec)
    assert dec.read_bytes() == src.read_bytes()
    raw = enc.read_bytes()
    # dropping the last chunk must not authenticate (final-chunk marker in AAD)
    first_chunks = 28 + 5 * (4 + 1000 + 16)
    (tmp_path / "cut").write_bytes(raw[:first_chunks])
    with pytest.raises(backup.BackupError):
        backup.decrypt_file(tmp_path / "cut", tmp_path / "x")
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    backup.encrypt_file(empty, tmp_path / "e2")
    backup.decrypt_file(tmp_path / "e2", tmp_path / "d2")
    assert (tmp_path / "d2").read_bytes() == b""


def test_wrong_key_cannot_decrypt(tmp_path, monkeypatch):
    from app.config import get_settings

    src = tmp_path / "p"
    src.write_bytes(b"secret data")
    backup.encrypt_file(src, tmp_path / "e")
    monkeypatch.setattr(get_settings(), "secret_key", "another-secret-key-another-secret-key-0000")
    with pytest.raises(backup.BackupError):
        backup.decrypt_file(tmp_path / "e", tmp_path / "d")


def test_permissions_journey_20(owner_api, new_api, make_staff):
    anon = new_api()
    assert anon.get("/api/system/backups").status_code in (401, 403)
    assert anon.post("/api/system/backups", json={}).status_code in (401, 403)
    auditor = staff_api(new_api, make_staff, "marketer")  # no system.read
    assert auditor.get("/api/system/backups").status_code == 403
    dev = staff_api(new_api, make_staff, "developer")  # system.read but not system.manage
    assert dev.get("/api/system/backups").status_code == 200
    assert dev.post("/api/system/backups", json={}).status_code == 403
    rec = _make(owner_api)
    assert dev.post(f"/api/system/backups/{rec['id']}/verify-restore").status_code == 403
    assert dev.get(f"/api/system/backups/{rec['id']}/download").status_code == 403
    assert dev.delete(f"/api/system/backups/{rec['id']}").status_code == 403


def test_download_is_audited_and_matches_checksum(owner_api, db, store):
    rec = _make(owner_api)
    r = owner_api.get(f"/api/system/backups/{rec['id']}/download")
    assert r.status_code == 200
    import hashlib
    assert hashlib.sha256(r.content).hexdigest() == rec["sha256"]
    assert db.execute(select(AuditEvent).where(AuditEvent.action == "backup.downloaded")).scalar() is not None


def test_media_included_and_retention(owner_api, db, store):
    from app.config import get_settings

    media = get_settings().data_dir / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "a.txt").write_text("hello")
    rec = _make(owner_api)
    assert {f["name"] for f in rec["manifest"]["files"]} == {"database.dump", "media.tar"}
    r2, r3 = _make(owner_api), _make(owner_api)
    removed = backup.prune(db, store.id, keep_count=1, keep_days=0)
    assert len(removed) == 2  # oldest two go; the newest stays
    remaining = db.execute(select(BackupRecord.id)).scalars().all()
    assert [str(x) for x in remaining] == [r3["id"]] and r2["id"] in removed
    (media / "a.txt").unlink()


def test_schedule_settings_registered(db, store):
    assert get_setting(db, store.id, "backup.enabled") is False
    set_settings(db, store.id, {"backup.enabled": True, "backup.retention_count": 3}, backup.SYSTEM)
    db.commit()
    from app.modules.ops.service import scheduled_backup
    out = scheduled_backup(db)
    assert out["status"] == "succeeded" and out["drill_ok"] is True
    assert scheduled_backup(db) == {"skipped": "not due"}

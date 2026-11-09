from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.modules.identity.models import User
from app.modules.ops import vault
from app.modules.platform.models import SecretRecord


@pytest.fixture
def keys():
    return Fernet.generate_key().decode(), Fernet.generate_key().decode()


def _seed(db, store, old):
    f = Fernet(old.encode())
    db.add(SecretRecord(store_id=store.id, ref="a.b", sealed=f.encrypt(b"s1").decode(), hint="••"))
    db.add(SecretRecord(store_id=store.id, ref="c.d", sealed=f.encrypt(b"s2").decode(), hint="••"))
    db.add(User(email="u@example.com", kind="staff", mfa_secret_sealed=f.encrypt(b"JBSWY3DP").decode()))
    db.commit()


def test_rotation_reseals_everything_and_verifies(db, store, keys):
    old, new = keys
    _seed(db, store, old)
    assert {f"{t.name}.{c.name}" for t, c in vault.sealed_columns()} >= {"secrets.sealed", "users.mfa_secret_sealed", "users.mfa_pending_sealed"}
    dry = vault.rotate(db, old, new, apply=False)
    assert dry["ok"] and dry["rotated"] == 3
    with pytest.raises(InvalidToken):  # dry run changed nothing
        Fernet(new.encode()).decrypt(db.execute(select(SecretRecord.sealed)).scalars().first().encode())
    rep = vault.rotate(db, old, new, apply=True)
    assert rep["ok"] and rep["verified_with_new_key"] and rep["rotated"] == 3
    assert vault.verify(db, new) == {"ok": True, "checked": 3, "undecryptable": []}
    assert vault.verify(db, old)["ok"] is False


def test_wrong_old_key_changes_nothing(db, store, keys):
    old, new = keys
    _seed(db, store, old)
    rep = vault.rotate(db, Fernet.generate_key().decode(), new, apply=True)
    assert rep["ok"] is False and len(rep["failures"]) == 3
    assert vault.verify(db, old)["ok"] is True

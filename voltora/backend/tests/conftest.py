from __future__ import annotations

import os
import tempfile

os.environ.setdefault("VOLTORA_ENV", "test")
os.environ.setdefault("VOLTORA_DATABASE_URL", "postgresql+psycopg://voltora@127.0.0.1:5433/voltora_test")
os.environ.setdefault("VOLTORA_DATA_DIR", tempfile.mkdtemp(prefix="voltora-test-"))

import pyotp  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.services import limiter  # noqa: E402
from app.db import Base, get_engine, session_factory  # noqa: E402
from app.main import create_app  # noqa: E402

PASSWORD = "correct-horse-42"


@pytest.fixture(scope="session")
def app():
    application = create_app(start_worker=False)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    return application


@pytest.fixture(autouse=True)
def _clean(app):
    engine = get_engine()
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    limiter.reset()
    yield


@pytest.fixture
def db():
    s = session_factory()()
    yield s
    s.close()


class Api:
    """TestClient wrapper that behaves like the web app: sends the CSRF header from the cookie."""

    def __init__(self, app):
        self.c = TestClient(app, base_url="http://localhost:8000")

    def _h(self, headers):
        h = dict(headers or {})
        tok = self.c.cookies.get("vt_csrf")
        if tok:
            h.setdefault("X-CSRF-Token", tok)
        return h

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, headers=None, **kw):
        return self.c.post(url, json=json, headers=self._h(headers), **kw)

    def put(self, url, json=None, headers=None, **kw):
        return self.c.put(url, json=json, headers=self._h(headers), **kw)

    def patch(self, url, json=None, headers=None, **kw):
        return self.c.patch(url, json=json, headers=self._h(headers), **kw)

    def delete(self, url, headers=None, **kw):
        return self.c.delete(url, headers=self._h(headers), **kw)


@pytest.fixture
def api(app):
    return Api(app)


@pytest.fixture
def new_api(app):
    return lambda: Api(app)


@pytest.fixture
def store(app, db):
    from app.modules.platform.models import Store
    from app.seed import seed_foundation

    s = Store(name="Test Store", slug="test-store", currency="BDT", environment="test")
    db.add(s)
    db.flush()
    seed_foundation(db, s, demo=False)
    db.commit()
    return s


@pytest.fixture
def make_staff(db, store):
    from app.modules.identity.api import create_user, ensure_builtin_roles
    from app.modules.identity.models import Membership

    def _make(role_key="owner", email=None, password=PASSWORD):
        roles = ensure_builtin_roles(db, store.id)
        email = email or f"{role_key}@example.com"
        u = create_user(db, email=email, password=password, name=role_key.title(), kind="staff")
        db.add(Membership(user_id=u.id, store_id=store.id, role_id=roles[role_key].id))
        db.commit()
        return u

    return _make


def login(api: Api, email: str, password: str = PASSWORD):
    r = api.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def owner_api(api, make_staff):
    u = make_staff("owner")
    login(api, u.email)
    api.user = u
    return api


def staff_api(new_api, make_staff, role_key: str):
    a = new_api()
    u = make_staff(role_key, email=f"{role_key}-{os.urandom(3).hex()}@example.com")
    login(a, u.email)
    a.user = u
    return a


def totp_now(secret: str) -> str:
    return pyotp.TOTP(secret).now()

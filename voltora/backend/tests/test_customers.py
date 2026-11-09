from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.modules.customers.models import Address, Customer
from app.modules.identity.api import create_user
from app.modules.platform.models import AuditEvent, OutboxEvent, Store
from tests.conftest import PASSWORD, login, staff_api


@pytest.fixture
def seeded(db, store):
    sid = store.id
    user = create_user(db, email="rahim@example.com", password=PASSWORD, name="Rahim Uddin", kind="customer")
    a = Customer(store_id=sid, user_id=user.id, email="rahim@example.com", phone="+8801712345678", name="Rahim Uddin",
                 tags=["vip"], marketing_consent=True, orders_count=3, total_spent_minor=450000)
    b = Customer(store_id=sid, email="karim@example.com", phone="01898765432", name="Karim Hossain", orders_count=0)
    c = Customer(store_id=sid, email="nusrat@example.com", name="Nusrat Jahan", tags=["wholesale"], orders_count=1,
                 total_spent_minor=90000)
    db.add_all([a, b, c])
    db.flush()
    db.add(Address(customer_id=a.id, name="Rahim Uddin", phone="+8801712345678", line1="House 7, Road 3", area="Dhanmondi",
                   upazila="Dhanmondi", district="Dhaka", division="Dhaka", is_default=True))
    db.commit()
    return SimpleNamespace(a=a, b=b, c=c, user=user)



def test_list_search_filters_and_pagination(owner_api, seeded):
    j = owner_api.get("/api/customers").json()
    assert j["total"] == 3 and j["pii_masked"] is False
    names = lambda **kw: sorted(i["name"] for i in owner_api.get("/api/customers", params=kw).json()["items"])  # noqa: E731
    assert names(q="rahim") == ["Rahim Uddin"] and names(q="karim@example.com") == ["Karim Hossain"]
    assert names(q="01898") == ["Karim Hossain"]
    assert names(tag="vip") == ["Rahim Uddin"] and names(consent="yes") == ["Rahim Uddin"]
    assert names(has_orders="no") == ["Karim Hossain"] and len(names(has_orders="yes")) == 2
    assert [i["name"] for i in owner_api.get("/api/customers", params={"sort": "spent"}).json()["items"]][0] == "Rahim Uddin"
    p = owner_api.get("/api/customers", params={"page_size": 2, "page": 2, "sort": "name"}).json()
    assert p["total"] == 3 and [i["name"] for i in p["items"]] == ["Rahim Uddin"]
    assert owner_api.get("/api/customers/tags").json()["items"] == ["vip", "wholesale"]


def test_pii_masking_for_staff_without_pii_permission(owner_api, new_api, make_staff, seeded):
    agent = staff_api(new_api, make_staff, "support_agent")  # customers.read, no customers.pii
    j = agent.get("/api/customers").json()
    assert j["pii_masked"] is True
    row = next(i for i in j["items"] if i["name"] == "Rahim Uddin")
    assert row["email"] != "rahim@example.com" and "•" in row["email"] and row["email"].startswith("r")
    assert row["phone"].endswith("78") and "+88017" not in row["phone"] and row["pii_masked"] is True
    d = agent.get(f"/api/customers/{seeded.a.id}").json()
    assert d["addresses"][0]["line1"] == "•••" and "•" in d["addresses"][0]["phone"] and d["addresses"][0]["district"] == "Dhaka"
    full = owner_api.get(f"/api/customers/{seeded.a.id}").json()
    assert full["email"] == "rahim@example.com" and full["addresses"][0]["line1"] == "House 7, Road 3"
    # no substring enumeration of contact data without pii access, exact lookups still work
    assert agent.get("/api/customers", params={"q": "rahim@exa"}).json()["total"] == 0
    assert agent.get("/api/customers", params={"q": "rahim@example.com"}).json()["total"] == 1
    # global search respects the same rules
    s = agent.get("/api/admin/search", params={"q": "Rahim"}).json()["items"]
    assert any(i["kind"] == "customer" and "rahim@example.com" not in i["sublabel"] for i in s)
    s = owner_api.get("/api/admin/search", params={"q": "Rahim"}).json()["items"]
    assert any(i["kind"] == "customer" and i["path"] == f"/admin/customers/{seeded.a.id}" for i in s)


def test_permissions(owner_api, new_api, make_staff, seeded):
    agent = staff_api(new_api, make_staff, "support_agent")
    cid = seeded.a.id
    assert agent.patch(f"/api/customers/{cid}", json={"note": "x"}).status_code == 403
    assert agent.post(f"/api/customers/{cid}/export").status_code == 403
    assert agent.post(f"/api/customers/{cid}/erase", json={"confirm": True}).status_code == 403
    cat = staff_api(new_api, make_staff, "catalog_staff")
    assert cat.get("/api/customers").status_code == 403
    assert new_api().get("/api/customers").status_code == 401
    assert owner_api.get(f"/api/customers/{uuid.uuid4()}").status_code == 404
    assert owner_api.get("/api/customers/nonsense").status_code == 404
    # global search returns no customers/products for roles without read
    assert [i for i in cat.get("/api/admin/search", params={"q": "Rahim"}).json()["items"] if i["kind"] == "customer"] == []


def test_update_note_tags_consent_audited(owner_api, db, seeded):
    r = owner_api.patch(f"/api/customers/{seeded.b.id}", json={"note": "Prefers evening delivery", "tags": ["vip", "VIP", " late "],
                                                                 "marketing_consent": True})
    assert r.status_code == 200
    j = r.json()
    assert j["note"] == "Prefers evening delivery" and j["tags"] == ["vip", "late"] and j["marketing_consent"] is True
    assert j["consent_updated_at"]
    assert db.execute(select(AuditEvent.seq).where(AuditEvent.action == "customer.updated")).first()


def test_export_and_erase(owner_api, new_api, db, seeded):
    cid = seeded.a.id
    ex = owner_api.post(f"/api/customers/{cid}/export")
    assert ex.status_code == 200
    j = ex.json()
    assert j["customer"]["email"] == "rahim@example.com" and j["addresses"][0]["line1"] == "House 7, Road 3"
    assert "orders" in j
    assert db.execute(select(AuditEvent.seq).where(AuditEvent.action == "customer.exported")).first()
    r = owner_api.post(f"/api/customers/{cid}/erase", json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "confirmation_required"
    r = owner_api.post(f"/api/customers/{cid}/erase", json={"confirm": True})
    assert r.status_code == 200
    out = r.json()
    assert out["erased"] and out["email"] is None and out["name"] == "Erased customer" and out["tags"] == []
    assert out["orders_count"] == 3 and out["total_spent_minor"] == 450000  # financial summary kept
    db.expire_all()
    assert db.execute(select(Address).where(Address.customer_id == cid)).first() is None
    assert owner_api.get(f"/api/customers/{cid}").json()["addresses"] == []
    ev = db.execute(select(AuditEvent).where(AuditEvent.action == "customer.erased")).scalar_one()
    assert "rahim" not in str(ev.data).lower()
    assert db.execute(select(OutboxEvent.type).where(OutboxEvent.type == "customer.erased")).first()
    # the linked shopper account can no longer sign in
    r = new_api().post("/api/auth/login", json={"email": "rahim@example.com", "password": PASSWORD})
    assert r.status_code == 401
    assert owner_api.patch(f"/api/customers/{cid}", json={"note": "x"}).json()["error"]["code"] == "customer_erased"
    assert owner_api.post(f"/api/customers/{cid}/erase", json={"confirm": True}).status_code == 200  # idempotent
    assert owner_api.get("/api/customers", params={"q": "rahim"}).json()["total"] == 0
    assert db.execute(select(Store.id)).first()
    login(owner_api, owner_api.user.email)

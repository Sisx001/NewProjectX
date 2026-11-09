from __future__ import annotations

from tests.conftest import PASSWORD, login, staff_api, totp_now


def test_register_login_me_logout(api, store):
    r = api.post("/api/auth/register", json={"email": "Buyer@Example.com", "password": PASSWORD, "name": "Buyer"})
    assert r.status_code == 200, r.text
    me = api.get("/api/auth/me").json()
    assert me["user"]["email"] == "buyer@example.com" and me["user"]["kind"] == "customer"
    assert me["permissions"] == []
    assert api.post("/api/auth/logout").status_code == 200
    assert api.get("/api/auth/me").json()["user"] is None


def test_weak_password_and_duplicate_email(api, store):
    r = api.post("/api/auth/register", json={"email": "a@example.com", "password": "short", "name": "A"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "weak_password"
    api.post("/api/auth/register", json={"email": "a@example.com", "password": PASSWORD, "name": "A"})
    r = api.post("/api/auth/register", json={"email": "A@example.com", "password": PASSWORD, "name": "A"})
    assert r.status_code == 409


def test_wrong_password_and_lockout(api, make_staff):
    u = make_staff("store_manager")
    for _ in range(8):
        assert api.post("/api/auth/login", json={"email": u.email, "password": "nope-nope-nope"}).status_code == 401
    r = api.post("/api/auth/login", json={"email": u.email, "password": PASSWORD})
    assert r.status_code == 423


def test_csrf_required_for_cookie_sessions(api, make_staff):
    u = make_staff("owner")
    login(api, u.email)
    r = api.c.patch("/api/settings", json={"changes": {"store.tagline": "x"}})  # no CSRF header
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = api.patch("/api/settings", json={"changes": {"store.tagline": "Hello"}})
    assert r.status_code == 200, r.text


def test_cross_origin_blocked(api, make_staff):
    u = make_staff("owner")
    login(api, u.email)
    r = api.patch("/api/settings", json={"changes": {"store.tagline": "x"}}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"


def test_mfa_enrollment_and_challenge(api, new_api, make_staff):
    u = make_staff("owner")
    login(api, u.email)
    setup = api.post("/api/auth/mfa/setup").json()
    assert api.post("/api/auth/mfa/enable", json={"code": "000000"}).status_code == 400
    codes = api.post("/api/auth/mfa/enable", json={"code": totp_now(setup["secret"])}).json()["recovery_codes"]
    assert len(codes) == 8

    other = new_api()
    assert login(other, u.email)["mfa_required"] is True
    # Staff endpoints refuse until the second factor is presented.
    r = other.get("/api/staff/roles")
    assert r.status_code == 403 and r.json()["error"]["code"] == "mfa_required"
    assert other.post("/api/auth/mfa/challenge", json={"code": "123456"}).status_code == 400
    me = other.post("/api/auth/mfa/challenge", json={"code": codes[0]}).json()  # recovery code works once
    assert "*" in me["permissions"]
    assert other.get("/api/staff/roles").status_code == 200

    third = new_api()
    login(third, u.email)
    assert third.post("/api/auth/mfa/challenge", json={"code": codes[0]}).status_code == 400


def test_store_policy_requires_mfa_enrollment(owner_api, new_api, make_staff):
    owner_api.patch("/api/settings", json={"changes": {"security.require_staff_mfa": True}})
    # Owner holds security.manage, so the change succeeded; a manager without MFA is now blocked.
    mgr = staff_api(new_api, make_staff, "store_manager")
    r = mgr.get("/api/orders")
    assert r.status_code in (403, 404)
    r = mgr.get("/api/staff/roles")
    assert r.status_code == 403 and r.json()["error"]["code"] == "mfa_enrollment_required"


def test_rbac_denies_and_no_privilege_escalation(new_api, make_staff, owner_api):
    support = staff_api(new_api, make_staff, "support_agent")
    assert support.get("/api/settings").status_code == 403
    assert support.post("/api/staff/roles", json={"name": "x", "permissions": []}).status_code == 403

    mgr_role_admin = staff_api(new_api, make_staff, "store_admin")
    # store_admin lacks security.manage so cannot mint a role holding it
    r = mgr_role_admin.post("/api/staff/roles", json={"name": "Sec", "permissions": ["security.manage"]})
    assert r.status_code == 403
    r = mgr_role_admin.post("/api/staff/roles", json={"name": "Packers", "permissions": ["orders.read"]})
    assert r.status_code == 200


def test_invite_accept_time_limited_grant_and_revoke(owner_api, new_api, db):
    roles = {r["key"]: r for r in owner_api.get("/api/staff/roles").json()["items"]}
    r = owner_api.post("/api/staff/invite", json={"email": "packer@example.com", "name": "Packer",
                                                  "role_id": roles["fulfillment"]["id"]})
    assert r.status_code == 200, r.text
    token = r.json()["invite_link"].split("token=")[1]
    staff = new_api()
    assert staff.post("/api/auth/invite/accept", json={"token": token, "password": PASSWORD}).status_code == 200
    me = staff.get("/api/auth/me").json()
    assert "shipping.manage" in me["permissions"] and "orders.refund" not in me["permissions"]
    # token is single use
    assert new_api().post("/api/auth/invite/accept", json={"token": token, "password": PASSWORD}).status_code == 400

    uid = me["user"]["id"]
    owner_api.patch(f"/api/staff/members/{uid}", json={"expires_at": "2000-01-01T00:00:00Z"})
    assert staff.get("/api/auth/me").json()["permissions"] == []
    assert owner_api.delete(f"/api/staff/members/{uid}").status_code == 200
    assert staff.get("/api/auth/me").json()["user"] is None  # sessions revoked


def test_last_owner_cannot_be_removed(owner_api):
    members = owner_api.get("/api/staff/members").json()["items"]
    roles = {r["key"]: r for r in owner_api.get("/api/staff/roles").json()["items"]}
    r = owner_api.patch(f"/api/staff/members/{members[0]['id']}", json={"role_id": roles["store_admin"]["id"]})
    assert r.status_code == 409


def test_password_reset_flow(api, new_api, make_staff, db):
    from app.modules.messaging.models import OutboundMessage

    api.post("/api/auth/register", json={"email": "c@example.com", "password": PASSWORD, "name": "C"})
    anon = new_api()
    r = anon.post("/api/auth/password/forgot", json={"email": "c@example.com"})
    assert r.status_code == 200
    assert anon.post("/api/auth/password/forgot", json={"email": "nobody@example.com"}).status_code == 200
    msg = db.query(OutboundMessage).filter_by(to="c@example.com").one()
    token = msg.body.split("token=")[1].split()[0]
    assert anon.post("/api/auth/password/reset", json={"token": token, "password": "new-password-99"}).status_code == 200
    # existing session revoked, new password works
    assert api.get("/api/auth/me").json()["user"] is None
    login(new_api(), "c@example.com", "new-password-99")


def test_api_key_scoped(owner_api, new_api):
    r = owner_api.post("/api/developer/api-keys", json={"name": "ERP", "permissions": ["settings.read"]})
    key = r.json()["key"]
    c = new_api()
    assert c.get("/api/settings", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert c.get("/api/staff/roles", headers={"Authorization": f"Bearer {key}"}).status_code == 403
    kid = r.json()["id"]
    owner_api.delete(f"/api/developer/api-keys/{kid}")
    assert c.get("/api/settings", headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_session_listing_and_revoke(owner_api, new_api):
    other = new_api()
    login(other, owner_api.user.email)
    sessions = owner_api.get("/api/auth/sessions").json()["items"]
    assert len(sessions) == 2
    target = next(s for s in sessions if not s["current"])
    assert owner_api.delete(f"/api/auth/sessions/{target['id']}").status_code == 200
    assert other.get("/api/auth/me").json()["user"] is None

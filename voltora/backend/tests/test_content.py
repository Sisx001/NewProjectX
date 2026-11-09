from __future__ import annotations

import copy

from tests.conftest import staff_api

from app.modules.content.registry import COMPONENTS, ComponentDef, register_component, unregister_component
from app.modules.content.themes import contrast_ratio, starter
from app.modules.content.validation import Limits, sanitize_css, sanitize_html, scope_css, validate_document


def _doc(*children):
    return {"schemaVersion": 1, "root": {"id": "root", "type": "page", "props": {}, "children": list(children)}}


def _heading(id_="h1", text="Hello"):
    return {"id": id_, "type": "heading", "props": {"text": text, "level": "h2"}}


def _page_id(api, slug, kind="page"):
    items = api.get(f"/api/content/pages?kind={kind}").json()["items"]
    return next(i for i in items if i["slug"] == slug)


# ------------------------------------------------------------------------------------------------ pure validation
def test_validation_rejects_unknown_component_and_bad_props():
    _, rep = validate_document(_doc({"id": "x", "type": "marquee-3000", "props": {}}))
    assert any(e["code"] == "unknown_component" for e in rep.errors)
    _, rep = validate_document(_doc({"id": "g", "type": "grid", "props": {"columns": "three"}}))
    assert any(e["code"] == "prop_type" for e in rep.errors)
    _, rep = validate_document(_doc({"id": "g", "type": "grid", "props": {"columns": 99}}))
    assert any(e["code"] == "prop_range" for e in rep.errors)
    _, rep = validate_document(_doc({"id": "h", "type": "heading", "props": {"text": "x", "level": "h9"}}))
    assert any(e["code"] == "prop_choice" for e in rep.errors)
    _, rep = validate_document(_doc({"id": "h", "type": "heading", "props": {"nope": 1}}))
    assert any(e["code"] == "unknown_prop" for e in rep.errors)
    _, rep = validate_document(_doc({"id": "b", "type": "button", "props": {"label": "x", "href": "javascript:alert(1)"}}))
    assert any(e["code"] == "unsafe_link" for e in rep.errors)
    _, rep = validate_document(_doc({"id": "v", "type": "video", "props": {"url": "https://evil.example/x",
                                                                           "title": "t"}}))
    assert any(e["code"] == "video_host" for e in rep.errors)
    _, rep = validate_document(_doc(_heading("a"), _heading("a")))
    assert any(e["code"] == "node_id" for e in rep.errors)
    # children rules
    _, rep = validate_document(_doc({"id": "t", "type": "tabs", "props": {}, "children": [_heading()]}))
    assert any(e["code"] == "children" for e in rep.errors)
    _, rep = validate_document(_doc({"id": "s", "type": "spacer", "props": {}, "children": [_heading()]}))
    assert any(e["code"] == "children" for e in rep.errors)


def test_validation_limits_are_configurable():
    big = _doc(*[_heading(f"h{i}") for i in range(30)])
    _, rep = validate_document(big, Limits(max_nodes=10))
    assert any(e["code"] == "too_many_nodes" for e in rep.errors)
    _, rep = validate_document(big, Limits(max_nodes=100))
    assert not rep.errors
    _, rep = validate_document(_doc(_heading(text="x" * 300)), Limits(max_bytes=200))
    assert rep.errors[0]["code"] == "too_large"


def test_style_visibility_motion_normalised():
    node = {"id": "h", "type": "heading", "props": {"text": "Hi"},
            "style": {"base": {"font-size": "48px", "color": "var(--t-text)"}, "mobile": {"font-size": "32px"},
                      "tablet": {"position": "fixed"}},
            "visibility": {"devices": ["mobile"], "signed_in": "signed_out", "starts_at": "2026-01-01T00:00:00Z"},
            "motion": {"preset": "rise", "duration": 400, "easing": "out"}}
    clean, rep = validate_document(_doc(node))
    assert [e["path"] for e in rep.errors] == ["style.tablet.position"]
    n = clean["root"]["children"][0]
    assert n["style"]["mobile"] == {"font-size": "32px"}
    assert n["visibility"]["devices"] == ["mobile"]
    assert n["motion"]["preset"] == "rise" and n["motion"]["trigger"] == "enter"
    _, rep = validate_document(_doc({**node, "style": {"base": {"background-image": "url(javascript:x)"}}}))
    assert any(e["code"] == "style_value" for e in rep.errors)
    _, rep = validate_document(_doc({**node, "style": None, "motion": {"preset": "spin"}}))
    assert any(e["path"] == "motion.preset" for e in rep.errors)
    _, rep = validate_document(_doc({**node, "style": None, "motion": None,
                                     "visibility": {"starts_at": "2026-02-01", "ends_at": "2026-01-01"}}))
    assert any(e["path"] == "visibility.ends_at" for e in rep.errors)


def test_css_sanitizer_cases():
    css, w = sanitize_css('@import url("https://x/y.css"); & { color: red; }')
    assert "@import" not in css and "color: red" in css and w
    css, _ = sanitize_css("& { background: url(javascript:alert(1)) }")
    assert "javascript" not in css
    css, _ = sanitize_css("& { width: expression(alert(1)); behavior: url(x.htc); }")
    assert "expression" not in css and "behavior" not in css
    css, _ = sanitize_css("& { color: red } </style><script>alert(1)</script>")
    assert "</style" not in css.lower() and "<script" not in css.lower()
    css, _ = sanitize_css("& { color: red } \\3c/style\\3e")
    assert "\\" not in css
    css, w = sanitize_css("& { color: red }" * 5000, limit=1000)
    assert len(css) <= 1000 and any("truncated" in x for x in w)
    scoped = scope_css("& { color: red } .title, h2 { margin: 0 } @media (max-width: 600px) { & { color: blue } }",
                       "n1")
    assert '[data-vn="n1"]{color: red}' in scoped.replace(" {", "{")
    assert '[data-vn="n1"] .title, [data-vn="n1"] h2' in scoped
    assert '@media (max-width: 600px){[data-vn="n1"]' in scoped.replace(" {", "{")


def test_richtext_sanitised():
    out = sanitize_html('<p onclick="x">Hi <a href="javascript:alert(1)">bad</a> <a href="/shop">ok</a>'
                        '<script>alert(1)</script><img src=x onerror=y></p>')
    assert "onclick" not in out and "javascript" not in out and "<script" not in out and "<img" not in out
    assert '<a href="/shop">ok</a>' in out


def test_registry_is_extensible():
    register_component({"type": "acme.stars", "label": "Stars", "category": "content", "description": "x",
                        "props": [{"name": "count", "type": "number", "label": "Count", "min": 0, "max": 5}],
                        "source": "plugin:acme"})
    try:
        clean, rep = validate_document(_doc({"id": "s", "type": "acme.stars", "props": {"count": 3}}))
        assert not rep.errors
        _, rep = validate_document(_doc({"id": "s", "type": "acme.stars", "props": {"count": 9}}))
        assert rep.errors
    finally:
        unregister_component("acme.stars", "plugin:acme")
    assert "acme.stars" not in COMPONENTS
    try:
        register_component(ComponentDef("heading", "Dup", "content", ""))
        raise AssertionError("duplicate registration must fail")
    except ValueError:
        pass


def test_contrast_math():
    assert contrast_ratio("#000000", "#ffffff") == 21.0
    assert contrast_ratio("#777777", "#ffffff") < 4.5


# ------------------------------------------------------------------------------------------------ API
def test_component_catalog_endpoint(owner_api, store):
    r = owner_api.get("/api/content/components")
    assert r.status_code == 200
    body = r.json()
    types = {c["type"] for c in body["components"]}
    assert {"section", "grid", "hero", "product-grid", "category-tiles", "site-header", "faq"} <= types
    hero = next(c for c in body["components"] if c["type"] == "hero")
    assert any(p["name"] == "primary" and p["type"] == "list" and p["fields"] for p in hero["props"])
    assert body["limits"]["max_nodes"] == 5000


def test_seed_is_idempotent_and_publishes_defaults(owner_api, store, db):
    from app.modules.content.service import seed_content

    again = seed_content(db, store, False)
    db.commit()
    assert again == {"themes": [], "pages": []}
    themes = owner_api.get("/api/content/themes").json()["items"]
    assert len(themes) >= 6
    live = [t for t in themes if t["is_live"]]
    assert len(live) == 1 and live[0]["key"] == "editorial-minimal"
    layouts = {t["components"]["header"]["layout"] for t in themes}
    cards = {t["components"]["product-card"]["variant"] for t in themes}
    assert len(layouts) == 6 and len(cards) >= 5
    pages = {(p["kind"], p["slug"]): p for p in owner_api.get("/api/content/pages").json()["items"]}
    assert pages[("page", "home")]["status"] == "published"
    for slug in ("header", "footer", "announcement"):
        assert pages[("global", slug)]["status"] == "published"
    for slug in ("about", "contact", "shipping", "returns", "privacy", "terms"):
        assert pages[("page", slug)]["status"] == "draft" and pages[("page", slug)]["needs_review"]


def test_public_endpoints(api, store):
    theme = api.get("/api/store/theme").json()
    assert theme["id"] == "editorial-minimal" and theme["tokens"]["color"]["bg"]
    assert theme["components"]["product-card"]["variant"] == "editorial"
    assert theme["globals"]["header"]["children"][0]["type"] == "site-header"
    assert {"slug": "home", "title": "Home"} in theme["pages"]
    assert not any(p["slug"] == "about" for p in theme["pages"])
    home = api.get("/api/store/pages/home").json()
    assert home["revision"] == 1
    types = [c["type"] for c in home["document"]["root"]["children"]]
    assert types[0] == "hero"
    # drafts are not public
    assert api.get("/api/store/pages/about").status_code == 404


def test_draft_conflict_publish_and_public(owner_api, api, store):
    home = _page_id(owner_api, "home")
    detail = owner_api.get(f"/api/content/pages/{home['id']}").json()
    doc = copy.deepcopy(detail["document"])
    doc["root"]["children"].insert(0, _heading("fresh", "Fresh heading"))
    v = detail["draft_version"]
    r = owner_api.put(f"/api/content/pages/{home['id']}/draft", json={"base_version": v, "document": doc})
    assert r.status_code == 200, r.text
    assert r.json()["draft_version"] == v + 1 and r.json()["status"] == "changed"
    stale = owner_api.put(f"/api/content/pages/{home['id']}/draft", json={"base_version": v, "document": doc})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "draft_conflict"
    # public still serves revision 1 until publish
    assert api.get("/api/store/pages/home").json()["document"]["root"]["children"][0]["id"] == "hero"
    pub = owner_api.post(f"/api/content/pages/{home['id']}/publish", json={"message": "Add heading"})
    assert pub.status_code == 200, pub.text
    assert pub.json()["revision"]["diff"]["added"] == [{"id": "fresh", "type": "heading"}]
    live = api.get("/api/store/pages/home").json()
    assert live["revision"] == 2 and live["document"]["root"]["children"][0]["id"] == "fresh"


def test_invalid_document_rejected_on_save(owner_api, store):
    home = _page_id(owner_api, "home")
    r = owner_api.put(f"/api/content/pages/{home['id']}/draft",
                      json={"base_version": home["draft_version"], "document": _doc({"id": "z", "type": "nope"})})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_document"
    assert r.json()["error"]["details"]["errors"][0]["code"] == "unknown_component"


def test_rollback_creates_new_revision(owner_api, api, store):
    home = _page_id(owner_api, "home")
    detail = owner_api.get(f"/api/content/pages/{home['id']}").json()
    original_first = detail["document"]["root"]["children"][0]["id"]
    owner_api.put(f"/api/content/pages/{home['id']}/draft",
                  json={"base_version": detail["draft_version"], "document": _doc(_heading("only", "Only"))})
    owner_api.post(f"/api/content/pages/{home['id']}/publish", json={"message": "Replace"})
    assert api.get("/api/store/pages/home").json()["document"]["root"]["children"][0]["id"] == "only"
    r = owner_api.post(f"/api/content/pages/{home['id']}/rollback", json={"number": 1, "message": "Undo"})
    assert r.status_code == 200, r.text
    assert r.json()["revision"]["number"] == 3 and r.json()["revision"]["kind"] == "rollback"
    assert r.json()["revision"]["source_number"] == 1
    live = api.get("/api/store/pages/home").json()
    assert live["revision"] == 3 and live["document"]["root"]["children"][0]["id"] == original_first
    revs = owner_api.get(f"/api/content/pages/{home['id']}/revisions").json()["items"]
    assert [x["number"] for x in revs] == [3, 2, 1] and revs[0]["live"]
    assert r.json()["page"]["status"] == "published"


def test_preview_token_gates_drafts(owner_api, new_api, store):
    about = _page_id(owner_api, "about")
    anon = new_api()
    assert anon.get(f"/api/content/preview/pages/{about['id']}").status_code == 401
    assert anon.get(f"/api/content/preview/pages/{about['id']}?token=forged.abc").status_code == 401
    tok = owner_api.post(f"/api/content/pages/{about['id']}/preview-token").json()
    r = anon.get(f"/api/content/preview/pages/{about['id']}?token={tok['token']}")
    assert r.status_code == 200 and r.json()["preview"] is True
    home = _page_id(owner_api, "home")
    # a token for one page does not open another
    assert anon.get(f"/api/content/preview/pages/{home['id']}?token={tok['token']}").status_code == 401
    assert owner_api.get(f"/api/content/preview/pages/{home['id']}").status_code == 200


def test_publish_blocked_by_lint(owner_api, store):
    r = owner_api.post("/api/content/pages", json={"kind": "page", "slug": "lookbook", "title": "Lookbook",
                                                   "document": _doc({"id": "img", "type": "image", "props": {
                                                       "image": {"url": "/media/a.jpg", "alt": ""}}})})
    assert r.status_code == 200, r.text
    assert any(x["code"] == "missing_alt" for x in r.json()["lint"])
    pub = owner_api.post(f"/api/content/pages/{r.json()['id']}/publish", json={"message": "x"})
    assert pub.status_code == 422 and pub.json()["error"]["code"] == "lint_failed"


def test_permissions_marketer_drafts_but_cannot_publish(new_api, make_staff, store):
    marketer = staff_api(new_api, make_staff, "marketer")
    home = _page_id(marketer, "home")
    detail = marketer.get(f"/api/content/pages/{home['id']}").json()
    r = marketer.put(f"/api/content/pages/{home['id']}/draft",
                     json={"base_version": detail["draft_version"], "title": "Home (draft)"})
    assert r.status_code == 200
    assert marketer.post(f"/api/content/pages/{home['id']}/publish", json={}).status_code == 403
    assert marketer.post(f"/api/content/pages/{home['id']}/rollback", json={"number": 1}).status_code == 403
    themes = marketer.get("/api/content/themes").json()["items"]
    assert marketer.post(f"/api/content/themes/{themes[0]['id']}/activate").status_code == 403
    fulfillment = staff_api(new_api, make_staff, "fulfillment")
    assert fulfillment.get("/api/content/pages").status_code == 403
    assert new_api().get("/api/content/pages").status_code == 401


def test_theme_contrast_gate_and_publish(owner_api, api, store):
    themes = owner_api.get("/api/content/themes").json()["items"]
    tid = next(t["id"] for t in themes if t["key"] == "dark-technology")
    fork = owner_api.post(f"/api/content/themes/{tid}/fork", json={"name": "My dark"}).json()
    assert fork["origin"] == "fork" and fork["forked_from"] == "dark-technology" and fork["status"] == "draft"
    bad = copy.deepcopy(fork["manifest"])
    bad["tokens"]["color"]["text"] = "#3a3a3a"  # dark grey on near-black
    r = owner_api.put(f"/api/content/themes/{fork['id']}/draft",
                      json={"base_version": fork["draft_version"], "manifest": bad})
    assert r.status_code == 200 and any("contrast" in w for w in r.json()["warnings"])
    blocked = owner_api.post(f"/api/content/themes/{fork['id']}/publish", json={"make_live": True})
    assert blocked.status_code == 422 and blocked.json()["error"]["code"] == "contrast_failed"
    good = copy.deepcopy(bad)
    good["tokens"]["color"]["text"] = "#f5f5f5"
    good["components"]["product-card"]["variant"] = "dense"
    r = owner_api.put(f"/api/content/themes/{fork['id']}/draft",
                      json={"base_version": r.json()["draft_version"], "manifest": good})
    pub = owner_api.post(f"/api/content/themes/{fork['id']}/publish", json={"make_live": True, "message": "Go"})
    assert pub.status_code == 200, pub.text
    live = api.get("/api/store/theme").json()
    assert live["name"] == "My dark" and live["components"]["product-card"]["variant"] == "dense"
    assert sum(t["is_live"] for t in owner_api.get("/api/content/themes").json()["items"]) == 1
    # rollback to revision 1 republishes it as revision 2
    owner_api.put(f"/api/content/themes/{fork['id']}/draft",
                  json={"base_version": pub.json()["theme"]["draft_version"],
                        "manifest": {**good, "name": "My dark v2"}})
    owner_api.post(f"/api/content/themes/{fork['id']}/publish", json={})
    rb = owner_api.post(f"/api/content/themes/{fork['id']}/rollback", json={"number": 1})
    assert rb.status_code == 200 and rb.json()["revision"]["number"] == 3
    assert api.get("/api/store/theme").json()["name"] == "My dark"
    # invalid manifest rejected
    r = owner_api.put(f"/api/content/themes/{fork['id']}/draft",
                      json={"base_version": rb.json()["theme"]["draft_version"],
                            "manifest": {**good, "components": {"header": {"layout": "zigzag"}}}})
    assert r.status_code == 422
    assert owner_api.delete(f"/api/content/themes/{fork['id']}").status_code == 409


def test_theme_and_page_import_export_round_trip(owner_api, store):
    themes = owner_api.get("/api/content/themes").json()["items"]
    tid = next(t["id"] for t in themes if t["key"] == "luxury-boutique")
    exported = owner_api.get(f"/api/content/themes/{tid}/export").json()
    assert exported["format"] == "voltora.theme"
    imp = owner_api.post("/api/content/themes/import", json={"data": exported})
    assert imp.status_code == 200, imp.text
    assert imp.json()["key"] != "luxury-boutique" and imp.json()["origin"] == "import"
    expected = {**exported["manifest"], "id": imp.json()["key"]}
    assert imp.json()["manifest"] == expected
    assert owner_api.post("/api/content/themes/import", json={"data": {"format": "x"}}).status_code == 422
    # pages
    home = _page_id(owner_api, "home")
    data = owner_api.get(f"/api/content/pages/{home['id']}/export").json()
    data["slug"] = "home-copy"
    r = owner_api.post("/api/content/pages/import", json={"data": data})
    assert r.status_code == 200, r.text
    assert r.json()["document"] == owner_api.get(f"/api/content/pages/{home['id']}").json()["document"]
    assert owner_api.post("/api/content/pages/import", json={"data": data}).status_code == 409


def test_visibility_schedule_and_tags_pruned_publicly(owner_api, api, store):
    doc = _doc(_heading("always"),
               {**_heading("future"), "visibility": {"starts_at": "2999-01-01T00:00:00Z"}},
               {**_heading("past"), "visibility": {"ends_at": "2000-01-01T00:00:00Z"}},
               {**_heading("vip"), "visibility": {"customer_tags": ["vip"]}},
               {**_heading("mobile"), "visibility": {"devices": ["mobile"]}})
    r = owner_api.post("/api/content/pages", json={"slug": "promo", "title": "Promo", "document": doc})
    owner_api.post(f"/api/content/pages/{r.json()['id']}/publish", json={})
    ids = [c["id"] for c in api.get("/api/store/pages/promo").json()["document"]["root"]["children"]]
    assert ids == ["always", "mobile"]


def test_archive_rules(owner_api, new_api, make_staff, store):
    home = _page_id(owner_api, "home")
    assert owner_api.delete(f"/api/content/pages/{home['id']}").status_code == 422
    marketer = staff_api(new_api, make_staff, "marketer")
    r = owner_api.post("/api/content/pages", json={"slug": "sale", "title": "Sale", "document": _doc(_heading())})
    owner_api.post(f"/api/content/pages/{r.json()['id']}/publish", json={})
    assert marketer.delete(f"/api/content/pages/{r.json()['id']}").status_code == 403
    assert owner_api.delete(f"/api/content/pages/{r.json()['id']}").status_code == 200
    assert new_api().get("/api/store/pages/sale").status_code == 404


def test_starter_themes_pass_contrast_gate():
    from app.modules.content.themes import STARTER_THEMES, publish_blockers, validate_manifest

    for t in STARTER_THEMES:
        clean, errors, _ = validate_manifest(starter(t["id"]))
        assert not errors and not publish_blockers(clean), t["id"]

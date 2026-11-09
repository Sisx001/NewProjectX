"""Starter documents installed by the seed hook.

Copy is neutral and factual: text bound to store settings ({{store.name}}, {{store.tagline}}) or describing what the
platform actually does. Legal/info pages are installed as unpublished templates flagged for owner review.
"""
from __future__ import annotations


def n(id_: str, type_: str, props: dict | None = None, children: list | None = None, **extra) -> dict:
    node = {"id": id_, "type": type_, "props": props or {}}
    if children:
        node["children"] = children
    node.update(extra)
    return node


def doc(*children) -> dict:
    return {"schemaVersion": 1, "root": n("root", "page", {}, list(children))}


def L(label: str, href: str) -> dict:  # noqa: N802
    return {"label": label, "href": href}


HEADER = doc(n("header", "site-header", {
    "layout": "theme",
    "menu": [L("Shop", "/shop"), L("New arrivals", "/shop?sort=newest"), L("About", "/pages/about"),
             L("Contact", "/pages/contact")],
    "show_categories": True, "show_search": True, "show_account": True, "sticky": True}))

FOOTER = doc(n("footer", "site-footer", {
    "layout": "theme", "blurb": "{{store.tagline}}", "show_contact": True,
    "legal": "© {{year}} {{store.name}}",
    "columns": [
        {"title": "Shop", "links": [L("All products", "/shop"), L("New arrivals", "/shop?sort=newest")]},
        {"title": "Help", "links": [L("Track an order", "/track"), L("Shipping", "/pages/shipping"),
                                    L("Returns", "/pages/returns"), L("Contact", "/pages/contact")]},
        {"title": "Company", "links": [L("About", "/pages/about"), L("Privacy", "/pages/privacy"),
                                       L("Terms", "/pages/terms")]},
    ]}))

ANNOUNCEMENT = doc(n("announcement", "announcement", {
    "text": "{{settings.store.announcement}}", "href": "", "dismissible": True}))

HOME = doc(
    n("hero", "hero", {
        "variant": "split", "eyebrow": "", "title": "{{store.name}}", "subtitle": "{{store.tagline}}",
        "primary": [L("Shop all products", "/shop"), L("New arrivals", "/shop?sort=newest")],
        "align": "start", "height": "medium"},
      motion={"preset": "rise", "duration": 560, "delay": 0, "easing": "out", "trigger": "load"}),
    n("cats", "section", {"width": "wide", "spacing": "regular"}, [
        n("cats-tiles", "category-tiles", {"title": "Shop by category", "limit": 6, "style": "tiles",
                                           "show_counts": True, "top_level_only": True},
          motion={"preset": "stagger", "duration": 420, "stagger": 60, "easing": "out", "trigger": "enter"}),
    ]),
    n("new", "section", {"width": "wide", "spacing": "regular", "tone": "alt"}, [
        n("new-grid", "product-grid", {"title": "New arrivals", "sort": "newest", "limit": 8, "columns": 4,
                                       "card": "theme", "view_all": True},
          motion={"preset": "rise", "duration": 480, "easing": "out", "trigger": "enter"}),
    ]),
    n("story", "section", {"width": "content", "spacing": "generous"}, [
        n("story-cols", "columns", {"ratio": "1:1", "gap": "xl", "align": "center", "stack_on": "mobile"}, [
            n("story-head", "heading", {"text": "Browse the full catalogue", "level": "h2", "size": "xl",
                                        "font": "display", "eyebrow": "{{store.name}}"}),
            n("story-body", "stack", {"gap": "md"}, [
                n("story-text", "text", {"html": "<p>Every product page shows live availability. Delivery charges "
                                                 "for your district and the payment methods you can use are "
                                                 "calculated at checkout before you place an order.</p>",
                                         "size": "lg"}),
                n("story-cta", "button", {"label": "Visit the shop", "href": "/shop", "style": "secondary"}),
            ]),
        ], motion={"preset": "fade", "duration": 600, "easing": "out", "trigger": "enter"}),
    ]),
    n("policies", "section", {"width": "wide", "spacing": "compact"}, [
        n("policy-strip", "policy-strip", {"items": [
            {"icon": "truck", "title": "Shipping", "text": "Delivery areas, times and charges",
             "href": "/pages/shipping"},
            {"icon": "refresh", "title": "Returns", "text": "How returns and exchanges work",
             "href": "/pages/returns"},
            {"icon": "phone", "title": "Help", "text": "{{settings.store.support_phone}}",
             "href": "/pages/contact", "requires_setting": "store.support_phone"},
            {"icon": "store", "title": "Order tracking", "text": "Follow your delivery with your order number",
             "href": "/track"},
        ]}),
    ]),
)

REVIEW = ("<p><strong>Template — review required.</strong> VOLTORA installed this page as a starting point. "
          "Replace every bracketed item with your store's real policy and have it checked before publishing. "
          "It is not legal advice.</p>")


def info_page(slug: str, title: str, sections: list[tuple[str, str]]) -> dict:
    kids = [n(f"{slug}-notice", "text", {"html": REVIEW, "size": "sm"},
              visibility={"hidden": False}),
            n(f"{slug}-title", "heading", {"text": title, "level": "h1", "size": "display"})]
    for i, (h, body) in enumerate(sections):
        kids.append(n(f"{slug}-h{i}", "heading", {"text": h, "level": "h2", "size": "md", "font": "body"}))
        kids.append(n(f"{slug}-p{i}", "text", {"html": body}))
    return doc(n(f"{slug}-section", "section", {"width": "narrow", "spacing": "generous"}, kids))


INFO = {
    "about": ("About us", [
        ("Who we are", "<p>{{store.name}} — [describe your business, where you are based and what you sell].</p>"),
        ("How we choose products", "<p>[Explain how you source products. Only state what you can stand behind.]</p>"),
    ]),
    "contact": ("Contact", [
        ("Get in touch", "<p>[Support hours]. Email: [your support email]. Phone: [your support phone].</p>"),
        ("Business address", "<p>[Registered business address]</p>"),
    ]),
    "shipping": ("Shipping & delivery", [
        ("Where we deliver", "<p>[Districts or zones you deliver to and the courier partners you use.]</p>"),
        ("Charges and timing", "<p>[Delivery charges inside and outside Dhaka, typical delivery times, "
                               "cash-on-delivery rules.]</p>"),
    ]),
    "returns": ("Returns & refunds", [
        ("Eligibility", "<p>[Return window, condition requirements, non-returnable items.]</p>"),
        ("Refunds", "<p>[How refunds are issued (original method, bKash/Nagad, bank), and how long they take.]</p>"),
    ]),
    "privacy": ("Privacy policy", [
        ("What we collect", "<p>[Data collected at checkout and in accounts, and why.]</p>"),
        ("Your choices", "<p>[How customers can access, correct or delete their data, and contact details.]</p>"),
    ]),
    "terms": ("Terms of service", [
        ("Orders and pricing", "<p>[Order acceptance, pricing errors, payment terms.]</p>"),
        ("Governing law", "<p>[Applicable law and dispute process.]</p>"),
    ]),
}


def documents() -> list[dict]:
    out = [
        {"kind": "global", "slug": "header", "title": "Header", "document": HEADER, "publish": True},
        {"kind": "global", "slug": "footer", "title": "Footer", "document": FOOTER, "publish": True},
        {"kind": "global", "slug": "announcement", "title": "Announcement bar", "document": ANNOUNCEMENT,
         "publish": True},
        {"kind": "page", "slug": "home", "title": "Home", "document": HOME, "publish": True,
         "seo": {"title": "", "description": ""}},
    ]
    for slug, (title, sections) in INFO.items():
        out.append({"kind": "page", "slug": slug, "title": title, "document": info_page(slug, title, sections),
                    "needs_review": True, "meta": {"template": "legal-info"}})
    return out

"""Typed component registry.

Core components are declared here; plugins (or any module) add more with `register_component`. The registry is the
single contract shared by validation, the Studio inspector (prop schemas are served by the API) and the renderer.
There is no fixed list of section types: a document may use any registered type.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PROP_TYPES = {"text", "richtext", "number", "boolean", "select", "color", "image", "link", "product", "category",
              "list", "date"}

# Style capability groups → allowed CSS properties (kebab case). Values are sanitised in validation.py.
STYLE_GROUPS: dict[str, list[str]] = {
    "spacing": ["padding", "padding-top", "padding-right", "padding-bottom", "padding-left", "margin", "margin-top",
                "margin-bottom", "margin-left", "margin-right", "gap", "row-gap", "column-gap"],
    "size": ["width", "max-width", "min-width", "height", "min-height", "max-height", "aspect-ratio"],
    "layout": ["display", "flex-direction", "flex-wrap", "align-items", "justify-content", "align-self",
               "justify-items", "grid-template-columns", "grid-column", "order", "position", "top", "z-index",
               "overflow", "object-fit"],
    "typography": ["font-family", "font-size", "font-weight", "font-style", "line-height", "letter-spacing",
                   "text-align", "text-transform", "text-decoration", "color", "max-width"],
    "color": ["color", "background-color", "background-image", "background-size", "background-position", "opacity"],
    "border": ["border", "border-top", "border-bottom", "border-left", "border-right", "border-color",
               "border-width", "border-style", "border-radius", "outline"],
    "effects": ["box-shadow", "opacity", "transform", "filter", "backdrop-filter", "mix-blend-mode"],
}
ALL_STYLE = list(STYLE_GROUPS)
CATEGORIES = ["layout", "content", "media", "commerce", "navigation", "global"]


@dataclass
class PropDef:
    name: str
    type: str
    label: str
    default: Any = None
    required: bool = False
    options: list[str] | None = None  # select
    min: float | None = None
    max: float | None = None  # number bounds, or max length for text
    fields: list[PropDef] | None = None  # list item schema
    max_items: int | None = None  # realistic per-list bound, not a feature cap
    help: str = ""
    bindable: bool = False  # text accepts {{store.name}}-style bindings

    def dump(self) -> dict:
        d = asdict(self)
        if self.fields is not None:
            d["fields"] = [f.dump() for f in self.fields]
        return {k: v for k, v in d.items() if v not in (None, "", False) or k in ("default", "required")}


@dataclass
class ComponentDef:
    type: str
    label: str
    category: str
    description: str
    props: list[PropDef] = field(default_factory=list)
    children: str | list[str] = "none"  # "none" | "any" | [allowed types]
    styles: list[str] = field(default_factory=lambda: list(ALL_STYLE))
    data_sources: list[str] = field(default_factory=list)
    a11y: dict = field(default_factory=dict)
    version: str = "1.0.0"
    source: str = "core"
    icon: str = "square"
    insertable: bool = True  # False for structural types such as the document root

    def prop(self, name: str) -> PropDef | None:
        return next((p for p in self.props if p.name == name), None)

    def defaults(self) -> dict:
        return {p.name: p.default for p in self.props if p.default is not None}

    def dump(self) -> dict:
        return {"type": self.type, "label": self.label, "category": self.category, "description": self.description,
                "props": [p.dump() for p in self.props], "children": self.children, "styles": self.styles,
                "data_sources": self.data_sources, "a11y": self.a11y, "version": self.version, "source": self.source,
                "icon": self.icon, "insertable": self.insertable}


COMPONENTS: dict[str, ComponentDef] = {}


def register_component(defn: ComponentDef | dict, replace: bool = False) -> ComponentDef:
    """Register a component definition. Plugins pass a declarative dict with the same shape as `ComponentDef.dump()`.

    Plugin components must name a renderer the web runtime knows (core types or a plugin bundle); validation only
    guarantees structural safety of the stored document.
    """
    if isinstance(defn, dict):
        d = dict(defn)
        d["props"] = [_prop_from_dict(p) for p in d.get("props", [])]
        defn = ComponentDef(**d)
    if not defn.type or not all(c.isalnum() or c in "-._" for c in defn.type):
        raise ValueError(f"Invalid component type {defn.type!r}")
    if defn.category not in CATEGORIES:
        raise ValueError(f"Unknown component category {defn.category!r}")
    for p in defn.props:
        _check_prop(p)
    for s in defn.styles:
        if s not in STYLE_GROUPS:
            raise ValueError(f"Unknown style group {s!r}")
    if defn.type in COMPONENTS and not replace:
        raise ValueError(f"Component {defn.type} is already registered")
    COMPONENTS[defn.type] = defn
    return defn


def unregister_component(type_: str, source: str) -> None:
    if type_ in COMPONENTS and COMPONENTS[type_].source == source:
        del COMPONENTS[type_]


def _prop_from_dict(p: dict) -> PropDef:
    p = dict(p)
    if p.get("fields"):
        p["fields"] = [_prop_from_dict(f) for f in p["fields"]]
    return PropDef(**p)


def _check_prop(p: PropDef) -> None:
    if p.type not in PROP_TYPES:
        raise ValueError(f"Unknown prop type {p.type!r} for {p.name}")
    if p.type == "select" and not p.options:
        raise ValueError(f"Select prop {p.name} needs options")
    if p.type == "list":
        if not p.fields:
            raise ValueError(f"List prop {p.name} needs fields")
        for f in p.fields:
            _check_prop(f)


# ---------------------------------------------------------------------------------------------------- core set
def P(name, type_, label, default=None, **kw) -> PropDef:  # noqa: N802 - compact DSL
    return PropDef(name=name, type=type_, label=label, default=default, **kw)


LINK_FIELDS = [P("label", "text", "Label", "", max=120), P("href", "link", "Link", "/")]
ALIGN = ["start", "center", "end"]
WIDTHS = ["narrow", "content", "wide", "full"]


def _core() -> list[ComponentDef]:
    return [
        # ---------------- layout
        ComponentDef("page", "Page", "layout", "Document root.", [
            P("gap", "select", "Space between sections", "none", options=["none", "sm", "md", "lg"])],
            children="any", styles=["color", "spacing"], insertable=False, icon="file"),
        ComponentDef("section", "Section", "layout", "Full-width band that holds content. Sets rhythm and tone.",
                     [P("width", "select", "Content width", "content", options=WIDTHS),
                      P("tone", "select", "Tone", "default", options=["default", "alt", "inverse", "accent"]),
                      P("spacing", "select", "Vertical spacing", "regular",
                        options=["none", "compact", "regular", "generous"]),
                      P("anchor", "text", "Anchor id", "", max=60, help="Lets links jump here with #anchor")],
                     children="any", icon="rows-3"),
        ComponentDef("container", "Container", "layout", "Constrains width and centres its children.",
                     [P("width", "select", "Width", "content", options=WIDTHS)], children="any", icon="square-dashed"),
        ComponentDef("stack", "Stack", "layout", "Arranges children vertically or horizontally with a gap.",
                     [P("direction", "select", "Direction", "vertical", options=["vertical", "horizontal"]),
                      P("gap", "select", "Gap", "md", options=["none", "xs", "sm", "md", "lg", "xl"]),
                      P("align", "select", "Align", "start", options=[*ALIGN, "stretch"]),
                      P("justify", "select", "Justify", "start", options=[*ALIGN, "between"]),
                      P("wrap", "boolean", "Wrap", False)], children="any", icon="stretch-vertical"),
        ComponentDef("grid", "Grid", "layout", "Responsive grid with independent column counts per device.",
                     [P("columns", "number", "Columns (desktop)", 3, min=1, max=12),
                      P("columns_tablet", "number", "Columns (tablet)", 2, min=1, max=12),
                      P("columns_mobile", "number", "Columns (mobile)", 1, min=1, max=6),
                      P("gap", "select", "Gap", "md", options=["none", "xs", "sm", "md", "lg", "xl"])],
                     children="any", icon="layout-grid"),
        ComponentDef("columns", "Columns", "layout", "Two to four columns with a ratio; stacks on mobile.",
                     [P("ratio", "select", "Ratio", "1:1", options=["1:1", "2:1", "1:2", "3:2", "2:3", "1:1:1",
                                                                     "1:2:1", "1:1:1:1"]),
                      P("gap", "select", "Gap", "lg", options=["none", "sm", "md", "lg", "xl"]),
                      P("align", "select", "Vertical align", "start", options=[*ALIGN, "stretch"]),
                      P("stack_on", "select", "Stack below", "mobile", options=["never", "tablet", "mobile"]),
                      P("reverse_on_mobile", "boolean", "Reverse order when stacked", False)],
                     children="any", icon="columns-3"),
        ComponentDef("spacer", "Spacer", "layout", "Explicit vertical space.",
                     [P("size", "select", "Size", "md", options=["xs", "sm", "md", "lg", "xl", "2xl"])],
                     styles=["size"], icon="move-vertical"),
        ComponentDef("divider", "Divider", "layout", "Horizontal rule.",
                     [P("style", "select", "Style", "line", options=["line", "strong", "dotted", "space"])],
                     styles=["spacing", "color", "border"], icon="minus"),
        ComponentDef("tabs", "Tabs", "layout", "Tabbed panels with keyboard navigation.",
                     [P("align", "select", "Tab alignment", "start", options=ALIGN)],
                     children=["tab-panel"], icon="panel-top"),
        ComponentDef("tab-panel", "Tab panel", "layout", "One tab and its content.",
                     [P("label", "text", "Tab label", "Tab", required=True, max=80)], children="any",
                     icon="square"),
        ComponentDef("accordion", "Accordion", "layout", "Disclosure list; each item expands independently.",
                     [P("single", "boolean", "Only one open at a time", False)],
                     children=["accordion-item"], icon="list-collapse"),
        ComponentDef("accordion-item", "Accordion item", "layout", "A titled disclosure.",
                     [P("title", "text", "Title", "Question", required=True, max=200),
                      P("open", "boolean", "Open by default", False)], children="any", icon="chevron-down"),
        # ---------------- content
        ComponentDef("heading", "Heading", "content", "Semantic heading (h1–h4) with display styles.",
                     [P("text", "text", "Text", "Heading", required=True, max=300, bindable=True),
                      P("level", "select", "Level", "h2", options=["h1", "h2", "h3", "h4"]),
                      P("size", "select", "Visual size", "auto", options=["auto", "xs", "sm", "md", "lg", "xl",
                                                                           "display"]),
                      P("font", "select", "Typeface", "display", options=["display", "body", "mono"]),
                      P("eyebrow", "text", "Eyebrow", "", max=80, bindable=True),
                      P("align", "select", "Align", "start", options=ALIGN)],
                     styles=["spacing", "typography", "color", "size"], icon="heading"),
        ComponentDef("text", "Rich text", "content", "Formatted paragraphs, lists and links (sanitised).",
                     [P("html", "richtext", "Text", "<p>Write something useful.</p>", max=100_000),
                      P("size", "select", "Size", "md", options=["sm", "md", "lg"]),
                      P("measure", "select", "Line length", "readable", options=["readable", "wide", "full"]),
                      P("align", "select", "Align", "start", options=ALIGN)],
                     styles=["spacing", "typography", "color", "size", "border"], icon="pilcrow"),
        ComponentDef("image", "Image", "media", "Responsive, lazy-loaded image. Alt text is required to publish.",
                     [P("image", "image", "Image", None),
                      P("ratio", "select", "Aspect ratio", "auto", options=["auto", "1:1", "4:3", "3:2", "16:9",
                                                                             "3:4", "2:3", "21:9"]),
                      P("fit", "select", "Fit", "cover", options=["cover", "contain"]),
                      P("caption", "text", "Caption", "", max=300),
                      P("link", "link", "Link", ""),
                      P("priority", "boolean", "Load eagerly (above the fold)", False)],
                     a11y={"requiresAlt": True}, icon="image"),
        ComponentDef("button", "Button", "content", "Link styled as a button or text link.",
                     [P("label", "text", "Label", "Shop now", required=True, max=80, bindable=True),
                      P("href", "link", "Link", "/shop", required=True),
                      P("style", "select", "Style", "primary", options=["primary", "secondary", "text"]),
                      P("size", "select", "Size", "md", options=["sm", "md", "lg"]),
                      P("action", "select", "Action", "navigate", options=["navigate", "open-cart"])],
                     styles=["spacing", "typography", "color", "border", "size"], icon="mouse-pointer-click"),
        ComponentDef("icon-list", "Icon list", "content", "Short list of points with icons.",
                     [P("items", "list", "Items", [], max_items=50,
                        fields=[P("icon", "select", "Icon", "check", options=[
                            "check", "truck", "shield", "refresh", "phone", "store", "clock", "map-pin", "wallet",
                            "package", "star", "info"]),
                            P("title", "text", "Title", "", max=120, bindable=True),
                            P("text", "text", "Text", "", max=300, bindable=True),
                            P("href", "link", "Link", "")]),
                      P("layout", "select", "Layout", "list", options=["list", "inline", "grid"])],
                     icon="list-checks"),
        ComponentDef("quote", "Quote", "content", "Pull quote with attribution. Use only real, attributable quotes.",
                     [P("text", "text", "Quote", "", required=True, max=1000),
                      P("cite", "text", "Attribution", "", max=160)],
                     styles=["spacing", "typography", "color", "border"], icon="quote"),
        ComponentDef("video", "Video", "media", "YouTube or Vimeo embed (privacy-enhanced, loads on demand).",
                     [P("url", "link", "Video URL", "", help="youtube.com, youtu.be or vimeo.com only"),
                      P("title", "text", "Accessible title", "Video", required=True, max=160),
                      P("poster", "image", "Poster image", None),
                      P("ratio", "select", "Aspect ratio", "16:9", options=["16:9", "4:3", "1:1", "9:16"])],
                     icon="play"),
        ComponentDef("hero", "Hero", "content", "Opening statement with optional media; five compositions.",
                     [P("variant", "select", "Composition", "split",
                        options=["split", "full-bleed", "editorial", "banner", "minimal"]),
                      P("eyebrow", "text", "Eyebrow", "", max=80, bindable=True),
                      P("title", "text", "Title", "{{store.name}}", required=True, max=200, bindable=True),
                      P("subtitle", "text", "Subtitle", "{{store.tagline}}", max=400, bindable=True),
                      P("image", "image", "Image", None),
                      P("primary", "list", "Buttons", [], max_items=3, fields=LINK_FIELDS),
                      P("align", "select", "Text align", "start", options=ALIGN),
                      P("height", "select", "Height", "auto", options=["auto", "medium", "tall", "screen"])],
                     a11y={"requiresAlt": True}, icon="panel-top-open"),
        ComponentDef("logo-cloud", "Logo cloud", "media", "Row of partner or brand logos.",
                     [P("title", "text", "Title", "", max=120),
                      P("logos", "list", "Logos", [], max_items=48,
                        fields=[P("image", "image", "Logo", None), P("href", "link", "Link", "")]),
                      P("grayscale", "boolean", "Monochrome", True)], icon="badge"),
        ComponentDef("faq", "FAQ", "content", "Questions and answers as an accessible disclosure list.",
                     [P("title", "text", "Title", "Questions", max=160),
                      P("items", "list", "Questions", [], max_items=200,
                        fields=[P("q", "text", "Question", "", max=300), P("a", "richtext", "Answer", "",
                                                                            max=20_000)])],
                     icon="circle-help"),
        ComponentDef("countdown", "Countdown", "content",
                     "Counts down to a date you set. Hides itself after the date — never resets to fake urgency.",
                     [P("title", "text", "Title", "", max=160),
                      P("ends_at", "date", "Ends at", None, required=True),
                      P("ended_text", "text", "Text after it ends", "", max=200)], icon="timer"),
        ComponentDef("marquee", "Marquee", "content",
                     "Scrolling line of short phrases; static when the visitor prefers reduced motion.",
                     [P("items", "list", "Phrases", [], max_items=30, fields=[P("text", "text", "Text", "", max=120,
                                                                                bindable=True)]),
                      P("speed", "select", "Speed", "slow", options=["slow", "medium"]),
                      P("pause_on_hover", "boolean", "Pause on hover", True)], icon="move-horizontal"),
        ComponentDef("policy-strip", "Policy strip", "content",
                     "Links to store policies. Items with a required setting hide until that setting is filled in.",
                     [P("items", "list", "Items", [], max_items=12,
                        fields=[P("icon", "select", "Icon", "info", options=[
                            "truck", "refresh", "shield", "phone", "wallet", "store", "info", "clock"]),
                            P("title", "text", "Title", "", max=80, bindable=True),
                            P("text", "text", "Text", "", max=200, bindable=True),
                            P("href", "link", "Link", ""),
                            P("requires_setting", "text", "Hide unless setting is set", "", max=80)])],
                     icon="shield-check"),
        # ---------------- commerce
        ComponentDef("product-grid", "Product grid", "commerce", "Live products from the catalog.",
                     [P("title", "text", "Title", "New arrivals", max=160, bindable=True),
                      P("category", "category", "Category", "", help="Empty = all products"),
                      P("sort", "select", "Sort", "newest", options=["newest", "price_asc", "price_desc", "title"]),
                      P("limit", "number", "Products", 8, min=1, max=48),
                      P("columns", "number", "Columns (desktop)", 4, min=2, max=6),
                      P("card", "select", "Card style", "theme",
                        options=["theme", "standard", "minimal", "editorial", "dense", "overlay"]),
                      P("in_stock_only", "boolean", "Only in-stock", False),
                      P("view_all", "boolean", "Show 'View all' link", True)],
                     data_sources=["catalog.products"], icon="layout-grid"),
        ComponentDef("product-rail", "Product rail", "commerce", "Horizontally scrolling product row.",
                     [P("title", "text", "Title", "Featured", max=160, bindable=True),
                      P("category", "category", "Category", ""),
                      P("sort", "select", "Sort", "newest", options=["newest", "price_asc", "price_desc", "title"]),
                      P("limit", "number", "Products", 10, min=2, max=24),
                      P("card", "select", "Card style", "theme",
                        options=["theme", "standard", "minimal", "editorial", "dense", "overlay"])],
                     data_sources=["catalog.products"], icon="gallery-horizontal"),
        ComponentDef("category-tiles", "Category tiles", "commerce", "Live categories with images and counts.",
                     [P("title", "text", "Title", "Shop by category", max=160, bindable=True),
                      P("limit", "number", "Categories", 6, min=1, max=48),
                      P("style", "select", "Style", "tiles", options=["tiles", "list", "chips"]),
                      P("show_counts", "boolean", "Show product counts", True),
                      P("top_level_only", "boolean", "Top-level only", True)],
                     data_sources=["catalog.categories"], icon="shapes"),
        ComponentDef("featured-product", "Featured product", "commerce", "One product told in depth.",
                     [P("product", "product", "Product", "", required=True),
                      P("eyebrow", "text", "Eyebrow", "", max=80),
                      P("blurb", "text", "Blurb", "", max=600),
                      P("layout", "select", "Layout", "media-start", options=["media-start", "media-end"])],
                     data_sources=["catalog.product"], icon="star"),
        # ---------------- navigation & global
        ComponentDef("menu", "Menu", "navigation", "List of links, optionally with nested groups.",
                     [P("items", "list", "Links", [], max_items=100,
                        fields=[*LINK_FIELDS, P("children", "list", "Sub-links", [], max_items=60,
                                                fields=LINK_FIELDS)]),
                      P("orientation", "select", "Orientation", "horizontal", options=["horizontal", "vertical"])],
                     icon="menu"),
        ComponentDef("site-header", "Site header", "global", "Store header. Layout follows the theme unless set.",
                     [P("layout", "select", "Layout", "theme", options=[
                         "theme", "classic", "centered", "split", "minimal", "marketplace", "stacked"]),
                      P("logo", "image", "Logo", None),
                      P("menu", "list", "Navigation", [], max_items=100,
                        fields=[*LINK_FIELDS, P("children", "list", "Sub-links", [], max_items=60,
                                                fields=LINK_FIELDS)]),
                      P("show_categories", "boolean", "Add a Categories menu from the catalog", True),
                      P("show_search", "boolean", "Search", True),
                      P("show_account", "boolean", "Account link", True),
                      P("sticky", "boolean", "Sticky", True)],
                     styles=["spacing", "color", "border", "effects"], icon="panel-top"),
        ComponentDef("site-footer", "Site footer", "global", "Store footer with link columns and store facts.",
                     [P("layout", "select", "Layout", "theme", options=[
                         "theme", "columns", "minimal", "editorial", "dense"]),
                      P("blurb", "text", "About text", "{{store.tagline}}", max=600, bindable=True),
                      P("columns", "list", "Link columns", [], max_items=12,
                        fields=[P("title", "text", "Title", "", max=80),
                                P("links", "list", "Links", [], max_items=40, fields=LINK_FIELDS)]),
                      P("show_contact", "boolean", "Show support email/phone/address from settings", True),
                      P("legal", "text", "Legal line", "© {{year}} {{store.name}}", max=300, bindable=True)],
                     styles=["spacing", "color", "border"], icon="panel-bottom"),
        ComponentDef("announcement", "Announcement bar", "global", "One-line notice above the header.",
                     [P("text", "text", "Text", "{{settings.store.announcement}}", max=200, bindable=True),
                      P("href", "link", "Link", ""),
                      P("dismissible", "boolean", "Dismissible", True)],
                     styles=["spacing", "color", "typography"], icon="megaphone"),
    ]


for _c in _core():
    register_component(_c)


def catalog() -> list[dict]:
    return [c.dump() for c in sorted(COMPONENTS.values(), key=lambda c: (CATEGORIES.index(c.category), c.label))]

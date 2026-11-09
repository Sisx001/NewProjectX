"""Page document validation, sanitisation, linting and diffing.

Limits are realistic resource bounds (configurable store settings), not feature caps: any number of pages, any
registered component, any nesting depth up to a stack-safety bound.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from ...core.errors import AppError
from .registry import COMPONENTS, STYLE_GROUPS, PropDef

SCHEMA_VERSION = 1
MAX_DEPTH = 64
NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
BREAKPOINTS = ("base", "tablet", "mobile")
DEVICES = {"desktop", "tablet", "mobile"}
MOTION_PRESETS = {"none", "fade", "rise", "scale", "stagger"}
EASINGS = {"standard", "out", "in-out", "linear", "emphasized"}


@dataclass
class Limits:
    max_nodes: int = 5000
    max_bytes: int = 1_000_000
    max_css_bytes: int = 20_000


@dataclass
class Report:
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def err(self, node: str | None, path: str, message: str, code: str = "invalid"):
        self.errors.append({"node": node, "path": path, "code": code, "message": message})

    def warn(self, node: str | None, path: str, message: str, code: str = "warning"):
        self.warnings.append({"node": node, "path": path, "code": code, "message": message})


# ------------------------------------------------------------------------------------------------ URL safety
SAFE_SCHEMES = ("http", "https", "mailto", "tel")


def safe_href(value: str) -> bool:
    v = value.strip()
    if not v:
        return True
    if v.startswith(("/", "#", "?")) and not v.startswith("//"):
        return True
    scheme = urlparse(v).scheme.lower()
    return scheme in SAFE_SCHEMES


def safe_src(value: str) -> bool:
    v = value.strip()
    if v.startswith("/") and not v.startswith("//"):
        return True
    return urlparse(v).scheme.lower() in ("http", "https")


VIDEO_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com",
               "www.youtube-nocookie.com", "vimeo.com", "www.vimeo.com", "player.vimeo.com"}


def video_allowed(url: str) -> bool:
    if not url:
        return True
    u = urlparse(url.strip())
    return u.scheme == "https" and (u.hostname or "").lower() in VIDEO_HOSTS


# ------------------------------------------------------------------------------------------------ rich text
RICH_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "s", "a", "ul", "ol", "li", "h2", "h3", "h4", "blockquote",
             "code", "pre", "small", "span", "hr", "sup", "sub"}
VOID = {"br", "hr"}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math"}


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in RICH_TAGS:
            return
        keep = ""
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href and safe_href(href):
                keep = f' href="{html.escape(href, quote=True)}"'
                if href.startswith(("http://", "https://")):
                    keep += ' rel="noopener noreferrer"'
        self.out.append(f"<{tag}{keep}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in self.stack and tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def sanitize_html(value: str) -> str:
    p = _Clean()
    p.feed(value or "")
    p.close()
    while p.stack:
        p.out.append(f"</{p.stack.pop()}>")
    return "".join(p.out)


# ------------------------------------------------------------------------------------------------ CSS
_CSS_BLOCKED = [
    (re.compile(r"@import[^;]*;?", re.I), "@import is not allowed"),
    (re.compile(r"@(font-face|charset|namespace|layer|property|document|page)\b[^{;]*(\{[^}]*\}|;)?", re.I),
     "This at-rule is not allowed in component CSS"),
    (re.compile(r"expression\s*\(", re.I), "expression() is not allowed"),
    (re.compile(r"(-moz-binding|behavior)\s*:[^;}]*;?", re.I), "behavior/-moz-binding is not allowed"),
    (re.compile(r"url\s*\(\s*['\"]?\s*(javascript|vbscript|data)\s*:[^)]*\)", re.I),
     "url() may only reference http(s) or site-relative files"),
    (re.compile(r"javascript\s*:", re.I), "javascript: is not allowed"),
    (re.compile(r"</?\s*style", re.I), "HTML style tags are not allowed"),
    (re.compile(r"<!--|-->|<\s*/?\s*script", re.I), "HTML is not allowed in CSS"),
]


def sanitize_css(css: str, limit: int = 20_000) -> tuple[str, list[str]]:
    """Strip dangerous constructs. Returns (clean_css, warnings). Scoping to the node happens at render time."""
    warnings: list[str] = []
    if not css:
        return "", warnings
    if len(css.encode()) > limit:
        warnings.append(f"Custom CSS was truncated to {limit} bytes")
        css = css.encode()[:limit].decode(errors="ignore")
    css = css.replace("\\", "")  # escapes are the usual way to smuggle blocked keywords
    changed = True
    while changed:
        changed = False
        for rx, msg in _CSS_BLOCKED:
            css, n = rx.subn("", css)
            if n:
                changed = True
                if msg not in warnings:
                    warnings.append(msg)
    if css.count("{") != css.count("}"):
        warnings.append("Unbalanced braces were removed")
        css = css.replace("{", "").replace("}", "")
    return css.strip(), warnings


def scope_css(css: str, node_id: str) -> str:
    """Prefix every selector with the node scope. `&` (or `:scope`) refers to the node itself.

    Supports plain rules and one level of @media/@supports nesting.
    """
    scope = f'[data-vn="{node_id}"]'
    out: list[str] = []
    i, n = 0, len(css)

    def prefix(selectors: str) -> str:
        parts = []
        for s in selectors.split(","):
            s = s.strip()
            if not s:
                continue
            if "&" in s or ":scope" in s:
                parts.append(s.replace("&", scope).replace(":scope", scope))
            else:
                parts.append(f"{scope} {s}")
        return ", ".join(parts) or scope

    while i < n:
        j = css.find("{", i)
        if j < 0:
            break
        head = css[i:j].strip()
        if head.startswith("@media") or head.startswith("@supports") or head.startswith("@container"):
            depth, k = 1, j + 1
            while k < n and depth:
                depth += {"{": 1, "}": -1}.get(css[k], 0)
                k += 1
            inner = css[j + 1:k - 1]
            out.append(f"{head}{{{scope_css(inner, node_id)}}}")
            i = k
            continue
        if head.startswith("@"):
            # keyframes and other at-rules: names are global, so namespace keyframes by node.
            k = css.find("}", j)
            depth, k = 1, j + 1
            while k < n and depth:
                depth += {"{": 1, "}": -1}.get(css[k], 0)
                k += 1
            if head.lower().startswith("@keyframes"):
                out.append(css[i:k].strip())
            i = k
            continue
        k = css.find("}", j)
        if k < 0:
            break
        out.append(f"{prefix(head)}{{{css[j + 1:k].strip()}}}")
        i = k + 1
    return "\n".join(out)


_STYLE_VALUE_BAD = re.compile(r"[;{}<>]|expression\s*\(|javascript:|@import|\\", re.I)
_URL_IN_VALUE = re.compile(r"url\(\s*['\"]?([^'\")]*)['\"]?\s*\)", re.I)


def clean_style_value(prop: str, value: Any) -> str | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or len(v) > 300 or _STYLE_VALUE_BAD.search(v):
        return None
    for m in _URL_IN_VALUE.finditer(v):
        if not safe_src(m.group(1)):
            return None
    if prop == "position" and v not in ("relative", "sticky", "static"):
        return None  # fixed/absolute escape the node's box and can cover the page
    return v


# ------------------------------------------------------------------------------------------------ props
def _validate_prop(pd: PropDef, value: Any, rep: Report, nid: str, path: str) -> Any:
    t = pd.type
    if value is None:
        return None
    if t in ("text", "category", "product"):
        if not isinstance(value, str):
            rep.err(nid, path, f"{pd.label} must be text", "prop_type")
            return None
        limit = int(pd.max) if pd.max else 10_000
        if len(value) > limit:
            rep.err(nid, path, f"{pd.label} is longer than {limit} characters", "prop_length")
        return value
    if t == "richtext":
        if not isinstance(value, str):
            rep.err(nid, path, f"{pd.label} must be text", "prop_type")
            return None
        limit = int(pd.max) if pd.max else 100_000
        if len(value) > limit:
            rep.err(nid, path, f"{pd.label} is longer than {limit} characters", "prop_length")
        return sanitize_html(value)
    if t == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            rep.err(nid, path, f"{pd.label} must be a number", "prop_type")
            return None
        if pd.min is not None and value < pd.min or pd.max is not None and value > pd.max:
            lo = "" if pd.min is None else f"{pd.min:g}"
            hi = "" if pd.max is None else f"{pd.max:g}"
            rep.err(nid, path, f"{pd.label} must be within {lo}–{hi}", "prop_range")
        return value
    if t == "boolean":
        if not isinstance(value, bool):
            rep.err(nid, path, f"{pd.label} must be true or false", "prop_type")
        return value
    if t == "select":
        if value not in (pd.options or []):
            rep.err(nid, path, f"{pd.label} must be one of: {', '.join(pd.options or [])}", "prop_choice")
        return value
    if t == "color":
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{3,8}|var\(--t-[a-z0-9-]+\)|", value):
            rep.err(nid, path, f"{pd.label} must be a hex colour or theme token", "prop_type")
        return value
    if t == "date":
        try:
            dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            rep.err(nid, path, f"{pd.label} must be an ISO date-time", "prop_type")
        return value
    if t == "link":
        if not isinstance(value, str):
            rep.err(nid, path, f"{pd.label} must be a link", "prop_type")
            return None
        if not safe_href(value):
            rep.err(nid, path, f"{pd.label} uses a blocked link scheme", "unsafe_link")
        return value.strip()
    if t == "image":
        if not isinstance(value, dict):
            rep.err(nid, path, f"{pd.label} must be an image", "prop_type")
            return None
        url = value.get("url") or ""
        if not isinstance(url, str) or (url and not safe_src(url)):
            rep.err(nid, path, f"{pd.label} URL must be http(s) or site-relative", "unsafe_src")
        alt = value.get("alt", "")
        if not isinstance(alt, str) or len(alt) > 500:
            rep.err(nid, path, f"{pd.label} alt text must be text (≤500 chars)", "prop_type")
        out = {"url": url, "alt": alt}
        if value.get("decorative") is True:
            out["decorative"] = True
        for k in ("width", "height"):
            if isinstance(value.get(k), int):
                out[k] = value[k]
        return out
    if t == "list":
        if not isinstance(value, list):
            rep.err(nid, path, f"{pd.label} must be a list", "prop_type")
            return []
        if pd.max_items and len(value) > pd.max_items:
            rep.err(nid, path, f"{pd.label} holds at most {pd.max_items} items", "prop_length")
        out_items = []
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                rep.err(nid, f"{path}[{i}]", f"{pd.label} item must be an object", "prop_type")
                continue
            clean = {}
            for f in pd.fields or []:
                if f.name in item:
                    clean[f.name] = _validate_prop(f, item[f.name], rep, nid, f"{path}[{i}].{f.name}")
            unknown = set(item) - {f.name for f in pd.fields or []} - {"_key"}
            if unknown:
                rep.err(nid, f"{path}[{i}]", f"Unknown field(s): {', '.join(sorted(unknown))}", "unknown_prop")
            if isinstance(item.get("_key"), str):
                clean["_key"] = item["_key"][:40]
            out_items.append(clean)
        return out_items
    return value


# ------------------------------------------------------------------------------------------------ node parts
def _validate_style(style: Any, allowed: list[str], rep: Report, nid: str) -> dict | None:
    if style is None:
        return None
    if not isinstance(style, dict):
        rep.err(nid, "style", "style must be an object", "style")
        return None
    props = {p for g in allowed for p in STYLE_GROUPS[g]}
    out: dict[str, dict] = {}
    for bp, decls in style.items():
        if bp not in BREAKPOINTS:
            rep.err(nid, f"style.{bp}", f"Unknown breakpoint {bp} (use base, tablet, mobile)", "style")
            continue
        if not isinstance(decls, dict):
            rep.err(nid, f"style.{bp}", "Breakpoint styles must be an object", "style")
            continue
        clean = {}
        for k, v in decls.items():
            if k not in props:
                rep.err(nid, f"style.{bp}.{k}", f"Style property {k} is not available for this component", "style")
                continue
            cv = clean_style_value(k, v)
            if cv is None:
                rep.err(nid, f"style.{bp}.{k}", f"Unsafe or invalid value for {k}", "style_value")
                continue
            clean[k] = cv
        if clean:
            out[bp] = clean
    return out or None


def _validate_visibility(v: Any, rep: Report, nid: str) -> dict | None:
    if v is None:
        return None
    if not isinstance(v, dict):
        rep.err(nid, "visibility", "visibility must be an object", "visibility")
        return None
    out: dict[str, Any] = {}
    for k, val in v.items():
        if k == "hidden":
            if not isinstance(val, bool):
                rep.err(nid, "visibility.hidden", "hidden must be true/false", "visibility")
            elif val:
                out["hidden"] = True
        elif k == "devices":
            if not isinstance(val, list) or not set(val) <= DEVICES or not val:
                rep.err(nid, "visibility.devices", "devices must list desktop, tablet and/or mobile", "visibility")
            else:
                out["devices"] = sorted(set(val))
        elif k == "signed_in":
            if val not in ("any", "signed_in", "signed_out"):
                rep.err(nid, "visibility.signed_in", "signed_in must be any, signed_in or signed_out", "visibility")
            elif val != "any":
                out["signed_in"] = val
        elif k == "locales":
            if not isinstance(val, list) or not all(isinstance(x, str) and 1 < len(x) <= 10 for x in val):
                rep.err(nid, "visibility.locales", "locales must be a list of language codes", "visibility")
            elif val:
                out["locales"] = val
        elif k in ("starts_at", "ends_at"):
            if val in (None, ""):
                continue
            try:
                dt.datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                out[k] = val
            except ValueError:
                rep.err(nid, f"visibility.{k}", f"{k} must be an ISO date-time", "visibility")
        elif k == "customer_tags":
            if not isinstance(val, list) or not all(isinstance(x, str) and len(x) <= 60 for x in val):
                rep.err(nid, "visibility.customer_tags", "customer_tags must be a list of tags", "visibility")
            elif val:
                out["customer_tags"] = val
        else:
            rep.err(nid, f"visibility.{k}", f"Unknown visibility rule {k}", "visibility")
    if out.get("starts_at") and out.get("ends_at") and _iso(out["starts_at"]) >= _iso(out["ends_at"]):
        rep.err(nid, "visibility.ends_at", "The end must be after the start", "visibility")
    return out or None


def _validate_motion(m: Any, rep: Report, nid: str) -> dict | None:
    if m is None:
        return None
    if not isinstance(m, dict):
        rep.err(nid, "motion", "motion must be an object", "motion")
        return None
    preset = m.get("preset", "none")
    if preset not in MOTION_PRESETS:
        rep.err(nid, "motion.preset", f"preset must be one of {', '.join(sorted(MOTION_PRESETS))}", "motion")
        return None
    if preset == "none":
        return None
    out: dict[str, Any] = {"preset": preset}
    for k, lo, hi, dflt in (("duration", 0, 2000, 480), ("delay", 0, 5000, 0), ("stagger", 0, 400, 60),
                            ("distance", 0, 64, 16)):
        val = m.get(k, dflt)
        if isinstance(val, bool) or not isinstance(val, int | float) or not lo <= val <= hi:
            rep.err(nid, f"motion.{k}", f"{k} must be between {lo} and {hi}", "motion")
            continue
        out[k] = val
    easing = m.get("easing", "out")
    if easing not in EASINGS:
        rep.err(nid, "motion.easing", f"easing must be one of {', '.join(sorted(EASINGS))}", "motion")
    out["easing"] = easing
    trigger = m.get("trigger", "enter")
    if trigger not in ("enter", "load"):
        rep.err(nid, "motion.trigger", "trigger must be enter or load", "motion")
    out["trigger"] = trigger
    return out


def _iso(v: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


# ------------------------------------------------------------------------------------------------ document
def validate_document(doc: Any, limits: Limits | None = None) -> tuple[dict, Report]:
    """Validate and normalise a PageDocument. Returns (clean_doc, report). Raises nothing; callers decide."""
    limits = limits or Limits()
    rep = Report()
    if not isinstance(doc, dict) or not isinstance(doc.get("root"), dict):
        rep.err(None, "root", "A document needs a root node", "document")
        return {"schemaVersion": SCHEMA_VERSION, "root": None}, rep
    size = len(json.dumps(doc, separators=(",", ":")).encode())
    if size > limits.max_bytes:
        rep.err(None, "document", f"Document is {size:,} bytes; the limit is {limits.max_bytes:,}", "too_large")
        return doc, rep
    if doc.get("schemaVersion", SCHEMA_VERSION) != SCHEMA_VERSION:
        rep.err(None, "schemaVersion", f"Unsupported schemaVersion {doc.get('schemaVersion')}", "document")
    seen: set[str] = set()
    count = 0

    def walk(node: Any, depth: int, parent_type: str | None, path: str) -> dict | None:
        nonlocal count
        if not isinstance(node, dict):
            rep.err(None, path, "Node must be an object", "node")
            return None
        count += 1
        if count > limits.max_nodes:
            if count == limits.max_nodes + 1:
                rep.err(None, path, f"Documents may hold at most {limits.max_nodes:,} nodes", "too_many_nodes")
            return None
        if depth > MAX_DEPTH:
            rep.err(node.get("id"), path, f"Nesting deeper than {MAX_DEPTH} levels", "too_deep")
            return None
        nid = node.get("id")
        if not isinstance(nid, str) or not NODE_ID.match(nid):
            rep.err(None, path, "Node id must be 1–64 letters, digits, - or _", "node_id")
            nid = f"invalid-{count}"
        elif nid in seen:
            rep.err(nid, path, f"Duplicate node id {nid}", "node_id")
        seen.add(nid)
        ctype = node.get("type")
        defn = COMPONENTS.get(ctype) if isinstance(ctype, str) else None
        if defn is None:
            rep.err(nid, f"{path}.type", f"Unknown component type {ctype!r}", "unknown_component")
            return None
        props_in = node.get("props") or {}
        if not isinstance(props_in, dict):
            rep.err(nid, f"{path}.props", "props must be an object", "prop_type")
            props_in = {}
        props: dict[str, Any] = {}
        for k, v in props_in.items():
            pd = defn.prop(k)
            if pd is None:
                rep.err(nid, f"props.{k}", f"{defn.label} has no property {k}", "unknown_prop")
                continue
            props[k] = _validate_prop(pd, v, rep, nid, f"props.{k}")
        for pd in defn.props:
            if pd.required and props.get(pd.name) in (None, "") and pd.default in (None, ""):
                rep.warn(nid, f"props.{pd.name}", f"{defn.label}: {pd.label} is empty", "required_empty")
        if ctype == "video" and props.get("url") and not video_allowed(props["url"]):
            rep.err(nid, "props.url", "Only https YouTube or Vimeo links can be embedded", "video_host")
        out: dict[str, Any] = {"id": nid, "type": ctype, "props": props}
        style = _validate_style(node.get("style"), defn.styles, rep, nid)
        if style:
            out["style"] = style
        vis = _validate_visibility(node.get("visibility"), rep, nid)
        if vis:
            out["visibility"] = vis
        motion = _validate_motion(node.get("motion"), rep, nid)
        if motion:
            out["motion"] = motion
        adv = node.get("advanced")
        if adv is not None:
            if not isinstance(adv, dict):
                rep.err(nid, "advanced", "advanced must be an object", "advanced")
            else:
                a: dict[str, Any] = {}
                if adv.get("css"):
                    css, warns = sanitize_css(str(adv["css"]), limits.max_css_bytes)
                    for w in warns:
                        rep.warn(nid, "advanced.css", w, "css_sanitized")
                    if css:
                        a["css"] = css
                for k in ("class_name", "label", "aria_label"):
                    if isinstance(adv.get(k), str) and adv[k].strip():
                        val = adv[k].strip()[:120]
                        if k == "class_name" and not re.fullmatch(r"[A-Za-z0-9_ -]+", val):
                            rep.err(nid, "advanced.class_name", "Class names may use letters, digits, - and _",
                                    "advanced")
                            continue
                        a[k] = val
                if adv.get("locked") is True:
                    a["locked"] = True
                if a:
                    out["advanced"] = a
        kids = node.get("children") or []
        if kids:
            if not isinstance(kids, list):
                rep.err(nid, "children", "children must be a list", "children")
                kids = []
            elif defn.children == "none":
                rep.err(nid, "children", f"{defn.label} cannot contain other components", "children")
                kids = []
            clean_kids = []
            for i, k in enumerate(kids):
                if isinstance(defn.children, list) and isinstance(k, dict) and k.get("type") not in defn.children:
                    rep.err(k.get("id"), f"{path}.children[{i}]",
                            f"{defn.label} only accepts {', '.join(defn.children)}", "children")
                    continue
                c = walk(k, depth + 1, ctype, f"{path}.children[{i}]")
                if c is not None:
                    clean_kids.append(c)
            if clean_kids:
                out["children"] = clean_kids
        return out

    root = walk(doc["root"], 0, None, "root")
    return {"schemaVersion": SCHEMA_VERSION, "root": root}, rep


def require_valid(doc: Any, limits: Limits | None = None) -> tuple[dict, list[dict]]:
    clean, rep = validate_document(doc, limits)
    if rep.errors:
        raise AppError("invalid_document", rep.errors[0]["message"] if len(rep.errors) == 1 else
                       f"{len(rep.errors)} problems in the document", status=422,
                       details={"errors": rep.errors[:200], "warnings": rep.warnings[:200]})
    return clean, rep.warnings


# ------------------------------------------------------------------------------------------------ helpers
def iter_nodes(node: dict | None):
    if not node:
        return
    yield node
    for c in node.get("children") or []:
        yield from iter_nodes(c)


def lint_document(doc: dict) -> list[dict]:
    """Accessibility/content lint. severity=error blocks publish; warnings are advisory."""
    out: list[dict] = []
    last_level = 0
    h1 = 0
    for n in iter_nodes(doc.get("root")):
        t, p, nid = n["type"], n.get("props") or {}, n["id"]
        if (n.get("visibility") or {}).get("hidden"):
            continue
        defn = COMPONENTS.get(t)
        for pd in defn.props if defn else []:
            if pd.type == "image":
                img = p.get(pd.name)
                if img and img.get("url") and not img.get("alt", "").strip() and not img.get("decorative"):
                    out.append({"node": nid, "severity": "error", "code": "missing_alt",
                                "message": f"{defn.label}: image needs alt text (or mark it decorative)"})
            if pd.type == "list":
                for i, item in enumerate(p.get(pd.name) or []):
                    for f in pd.fields or []:
                        if f.type == "image":
                            img = item.get(f.name)
                            if img and img.get("url") and not img.get("alt", "").strip() and not img.get(
                                    "decorative"):
                                out.append({"node": nid, "severity": "error", "code": "missing_alt",
                                            "message": f"{defn.label}: item {i + 1} image needs alt text"})
                        if f.type == "link" and "label" in item and item.get(f.name) and not str(
                                item.get("label", "")).strip():
                            out.append({"node": nid, "severity": "warning", "code": "empty_link",
                                        "message": f"{defn.label}: item {i + 1} link has no label"})
        if t == "heading":
            level = int(p.get("level", "h2")[1])
            if level == 1:
                h1 += 1
            if last_level and level > last_level + 1:
                out.append({"node": nid, "severity": "warning", "code": "heading_order",
                            "message": f"Heading jumps from h{last_level} to h{level}"})
            last_level = level
            if not str(p.get("text", "")).strip():
                out.append({"node": nid, "severity": "error", "code": "empty_heading", "message": "Empty heading"})
        if t == "hero":
            last_level = max(last_level, 1)
            h1 += 1 if p.get("variant") != "banner" else 0
        if t == "button" and (not str(p.get("label", "")).strip() or not p.get("href")):
            out.append({"node": nid, "severity": "error", "code": "empty_link",
                        "message": "Button needs a label and a link"})
        if t == "video" and p.get("url") and not str(p.get("title", "")).strip():
            out.append({"node": nid, "severity": "error", "code": "video_title", "message": "Video needs a title"})
        if t == "text":
            for m in re.finditer(r"<a(?: [^>]*)?>(.*?)</a>", p.get("html") or "", re.S):
                if not re.sub(r"<[^>]+>", "", m.group(1)).strip():
                    out.append({"node": nid, "severity": "warning", "code": "empty_link",
                                "message": "Rich text contains a link without text"})
        if t == "countdown" and p.get("ends_at"):
            try:
                if _iso(p["ends_at"]) < dt.datetime.now(dt.UTC):
                    out.append({"node": nid, "severity": "warning", "code": "countdown_past",
                                "message": "Countdown date has passed; it will show the 'ended' text"})
            except ValueError:
                pass
    if h1 > 1:
        out.append({"node": None, "severity": "warning", "code": "multiple_h1",
                    "message": "More than one top-level heading (h1 or hero title) on the page"})
    return out


def diff_summary(old: dict | None, new: dict | None) -> dict:
    def index(doc):
        return {n["id"]: n for n in iter_nodes((doc or {}).get("root"))}

    a, b = index(old), index(new)
    added = [{"id": i, "type": b[i]["type"]} for i in b.keys() - a.keys()]
    removed = [{"id": i, "type": a[i]["type"]} for i in a.keys() - b.keys()]
    changed = []
    moved = 0
    parents_a = _parents((old or {}).get("root"))
    parents_b = _parents((new or {}).get("root"))
    for i in a.keys() & b.keys():
        fields = [k for k in ("props", "style", "visibility", "motion", "advanced")
                  if (a[i].get(k) or None) != (b[i].get(k) or None)]
        if fields:
            changed.append({"id": i, "type": b[i]["type"], "fields": fields})
        if parents_a.get(i) != parents_b.get(i):
            moved += 1
    return {"added": sorted(added, key=lambda x: x["id"]), "removed": sorted(removed, key=lambda x: x["id"]),
            "changed": sorted(changed, key=lambda x: x["id"]), "moved": moved,
            "text": _diff_text(len(added), len(removed), len(changed), moved)}


def _parents(root, parent=None, acc=None):
    acc = {} if acc is None else acc
    if root:
        kids = root.get("children") or []
        acc[root["id"]] = (parent, None)
        for idx, c in enumerate(kids):
            _parents(c, root["id"], acc)
            acc[c["id"]] = (root["id"], idx)
    return acc


def _diff_text(a, r, c, m) -> str:
    parts = [f"{n} {w}" for n, w in ((a, "added"), (r, "removed"), (c, "changed"), (m, "moved")) if n]
    return ", ".join(parts) if parts else "No changes"

"""Theme packages: manifest schema, validation (incl. WCAG contrast) and the starter families.

A theme is data: semantic tokens + component variants. The web runtime maps tokens onto `--t-*` CSS variables
(documented in web/src/store/theme/tokens.ts). Families differ in composition (header layout, card grammar,
density, radius, type pairing, motion), not only colour.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from ...core.errors import AppError

# Installed font families (bundled with the web app) plus system stacks. Uploaded fonts would extend this map.
FONTS: dict[str, dict] = {
    "instrument-sans": {"label": "Instrument Sans", "stack": '"Instrument Sans Variable", "Hind Siliguri", system-ui, sans-serif', "kind": "sans"},
    "instrument-serif": {"label": "Instrument Serif", "stack": '"Instrument Serif", "Hind Siliguri", Georgia, serif', "kind": "serif"},
    "jetbrains-mono": {"label": "JetBrains Mono", "stack": '"JetBrains Mono Variable", ui-monospace, monospace', "kind": "mono"},
    "hind-siliguri": {"label": "Hind Siliguri (Bangla)", "stack": '"Hind Siliguri", "Instrument Sans Variable", system-ui, sans-serif', "kind": "sans"},
    "system-sans": {"label": "System sans", "stack": 'system-ui, -apple-system, "Segoe UI", Roboto, "Hind Siliguri", sans-serif', "kind": "sans"},
    "system-serif": {"label": "System serif", "stack": 'ui-serif, Georgia, "Times New Roman", "Hind Siliguri", serif', "kind": "serif"},
    "system-mono": {"label": "System mono", "stack": 'ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace', "kind": "mono"},
}

COLOR_KEYS = ["bg", "surface", "surface_alt", "text", "text_muted", "line", "primary", "on_primary", "accent",
              "on_accent", "sale", "success", "danger", "focus", "inverse_bg", "inverse_text", "announcement_bg",
              "announcement_text"]
# (foreground, background, kind). body pairs block publish < 4.5:1; large/ui pairs warn < 3:1.
CONTRAST_PAIRS = [
    ("text", "bg", "body"), ("text", "surface", "body"), ("text", "surface_alt", "body"),
    ("text_muted", "bg", "body"), ("text_muted", "surface", "body"),
    ("on_primary", "primary", "body"), ("on_accent", "accent", "body"),
    ("inverse_text", "inverse_bg", "body"), ("announcement_text", "announcement_bg", "body"),
    ("sale", "surface", "body"), ("danger", "bg", "body"),
    ("primary", "bg", "large"), ("accent", "bg", "large"), ("focus", "bg", "large"),
]

VARIANTS: dict[str, dict[str, list]] = {
    "header": {"layout": ["classic", "centered", "split", "minimal", "marketplace", "stacked"],
               "search": ["icon", "bar"]},
    "product-card": {"variant": ["standard", "minimal", "editorial", "dense", "overlay"],
                     "aspect": ["1:1", "4:5", "3:4", "4:3"]},
    "grid": {"density": ["airy", "regular", "compact"]},
    "button": {"style": ["solid", "outline", "pill", "sharp", "underline"], "case": ["none", "uppercase"]},
    "footer": {"layout": ["columns", "minimal", "editorial", "dense"], "tone": ["default", "inverse"]},
    "announcement": {"style": ["bar", "subtle", "ticker"]},
}
BOOL_VARIANTS = {("header", "sticky"), ("header", "border"), ("product-card", "show_brand"),
                 ("product-card", "hover_swap")}
INT_VARIANTS = {("grid", "columns"): (2, 6), ("grid", "columns_mobile"): (1, 2)}

NUM_TOKENS = {
    "type": {"base_size": (13, 22), "scale": (1.05, 1.6), "display_weight": (300, 800), "heading_weight": (300, 800),
             "body_leading": (1.2, 2.0), "display_tracking": (-0.08, 0.2), "label_tracking": (-0.05, 0.3)},
    "radius": {"sm": (0, 40), "md": (0, 40), "lg": (0, 60), "card": (0, 60), "button": (0, 999), "input": (0, 40)},
    "space": {"section": (16, 240), "section_mobile": (12, 160), "container": (720, 2400), "gutter": (8, 96),
              "grid_gap": (0, 96)},
    "motion": {"fast": (0, 600), "base": (0, 1200), "slow": (0, 2000)},
}
ENUM_TOKENS = {
    ("type", "heading_case"): ["none", "uppercase"],
    ("motion", "reveal"): ["none", "fade", "rise", "scale"],
    ("motion", "hover"): ["none", "lift", "zoom", "underline"],
    ("motion", "easing"): ["standard", "out", "in-out", "emphasized", "linear"],
}
SHADOW_RE = re.compile(r"^(none|[0-9a-z#(),.%\s-]{1,200})$", re.I)
HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


# ------------------------------------------------------------------------------------------------ contrast
def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def contrast_report(colors: dict) -> list[dict]:
    out = []
    for fg, bg, kind in CONTRAST_PAIRS:
        if not (HEX.match(str(colors.get(fg, ""))) and HEX.match(str(colors.get(bg, "")))):
            continue
        ratio = contrast_ratio(colors[fg], colors[bg])
        need = 4.5 if kind == "body" else 3.0
        out.append({"fg": fg, "bg": bg, "kind": kind, "ratio": ratio, "required": need, "ok": ratio >= need,
                    "severity": None if ratio >= need else ("error" if kind == "body" else "warning")})
    return out


# ------------------------------------------------------------------------------------------------ validation
def validate_manifest(m: Any) -> tuple[dict, list[dict], list[dict]]:
    """Returns (clean_manifest, errors, warnings). Contrast failures on body pairs are errors only at publish."""
    errors: list[dict] = []
    warnings: list[dict] = []

    def err(path, msg):
        errors.append({"path": path, "message": msg})

    if not isinstance(m, dict):
        return {}, [{"path": "", "message": "Theme manifest must be an object"}], []
    out: dict[str, Any] = {}
    for k, lim in (("name", 80), ("family", 60), ("author", 120), ("license", 60), ("description", 600)):
        v = m.get(k, "")
        if not isinstance(v, str) or len(v) > lim:
            err(k, f"{k} must be text up to {lim} characters")
        out[k] = (v or "").strip() if isinstance(v, str) else ""
    if not out["name"]:
        err("name", "Theme needs a name")
    out["version"] = m.get("version", "1.0.0")
    if not isinstance(out["version"], str) or not SEMVER.match(out["version"]):
        err("version", "version must be semantic (e.g. 1.2.0)")
    out["id"] = m.get("id") or ""
    if out["id"] and (not isinstance(out["id"], str) or not SLUG.match(out["id"])):
        err("id", "id must be lowercase letters, digits and dashes")
    out["license"] = out["license"] or "proprietary"

    tokens = m.get("tokens")
    if not isinstance(tokens, dict):
        err("tokens", "tokens are required")
        tokens = {}
    t_out: dict[str, Any] = {}
    colors = tokens.get("color") or {}
    c_out = {}
    for key in COLOR_KEYS:
        v = colors.get(key)
        if not isinstance(v, str) or not HEX.match(v):
            err(f"tokens.color.{key}", f"Colour {key} must be a hex value like #1a1a1a")
            continue
        c_out[key] = v.lower()
    extra = set(colors) - set(COLOR_KEYS)
    if extra:
        err("tokens.color", f"Unknown colour tokens: {', '.join(sorted(extra))}")
    t_out["color"] = c_out

    ty = tokens.get("type") or {}
    ty_out: dict[str, Any] = {}
    for role in ("display", "body", "mono", "bangla"):
        v = ty.get(role)
        if v not in FONTS:
            err(f"tokens.type.{role}", f"Font for {role} must be one of: {', '.join(FONTS)}")
        ty_out[role] = v
    t_out["type"] = ty_out
    for group, spec in NUM_TOKENS.items():
        src = tokens.get(group) or {}
        dst = t_out.setdefault(group, {})
        for key, (lo, hi) in spec.items():
            v = src.get(key)
            if isinstance(v, bool) or not isinstance(v, int | float) or not lo <= v <= hi:
                err(f"tokens.{group}.{key}", f"{group}.{key} must be a number between {lo} and {hi}")
                continue
            dst[key] = v
    for (group, key), choices in ENUM_TOKENS.items():
        v = (tokens.get(group) or {}).get(key)
        if v not in choices:
            err(f"tokens.{group}.{key}", f"{group}.{key} must be one of {', '.join(choices)}")
        t_out.setdefault(group, {})[key] = v
    sh = tokens.get("shadow") or {}
    t_out["shadow"] = {}
    for key in ("card", "raised", "overlay"):
        v = sh.get(key, "none")
        if not isinstance(v, str) or not SHADOW_RE.match(v) or "url" in v.lower():
            err(f"tokens.shadow.{key}", "Shadow must be a CSS box-shadow value or none")
            continue
        t_out["shadow"][key] = v
    out["tokens"] = t_out

    comps = m.get("components") or {}
    comp_out: dict[str, dict] = {}
    for comp, spec in VARIANTS.items():
        src = comps.get(comp) or {}
        dst: dict[str, Any] = {}
        for key, choices in spec.items():
            v = src.get(key, choices[0])
            if v not in choices:
                err(f"components.{comp}.{key}", f"{comp}.{key} must be one of {', '.join(choices)}")
            dst[key] = v
        for (c, key) in BOOL_VARIANTS:
            if c == comp:
                v = src.get(key, False)
                if not isinstance(v, bool):
                    err(f"components.{comp}.{key}", f"{comp}.{key} must be true/false")
                dst[key] = bool(v)
        for (c, key), (lo, hi) in INT_VARIANTS.items():
            if c == comp:
                v = src.get(key, lo)
                if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                    err(f"components.{comp}.{key}", f"{comp}.{key} must be {lo}–{hi}")
                dst[key] = v
        comp_out[comp] = dst
    unknown = set(comps) - set(VARIANTS)
    for u in sorted(unknown):
        # Plugin-provided component variants are kept as opaque, size-bounded data.
        if isinstance(comps[u], dict) and len(str(comps[u])) < 4000:
            comp_out[u] = comps[u]
            warnings.append({"path": f"components.{u}", "message": f"{u} variants are not understood by core"})
        else:
            err(f"components.{u}", "Unknown component variant block")
    out["components"] = comp_out
    for c in contrast_report(c_out):
        if not c["ok"]:
            warnings.append({"path": f"tokens.color.{c['fg']}", "message":
                             f"{c['fg']} on {c['bg']} contrast is {c['ratio']}:1 (needs {c['required']}:1)",
                             "contrast": c})
    return out, errors, warnings


def require_manifest(m: Any) -> tuple[dict, list[dict]]:
    clean, errors, warnings = validate_manifest(m)
    if errors:
        raise AppError("invalid_theme", errors[0]["message"] if len(errors) == 1 else
                       f"{len(errors)} problems in the theme", status=422,
                       details={"errors": errors, "warnings": warnings})
    return clean, warnings


def publish_blockers(manifest: dict) -> list[dict]:
    return [c for c in contrast_report(manifest.get("tokens", {}).get("color", {})) if c["severity"] == "error"]


# ------------------------------------------------------------------------------------------------ families
def _theme(id_, name, family, description, color, type_, radius, space, shadow, motion, components) -> dict:
    return {"id": id_, "name": name, "version": "1.0.0", "family": family, "author": "VOLTORA",
            "license": "MIT", "description": description,
            "tokens": {"color": color, "type": type_, "radius": radius, "space": space, "shadow": shadow,
                       "motion": motion},
            "components": components}


STARTER_THEMES: list[dict] = [
    _theme(
        "editorial-minimal", "Editorial Minimal", "Editorial Minimal",
        "Paper and ink. Serif display type, centred masthead, airy three-up grid, tall editorial cards.",
        {"bg": "#f6f4ef", "surface": "#fbfaf7", "surface_alt": "#ece8df", "text": "#1b1a17", "text_muted": "#5d5a52",
         "line": "#d9d4c8", "primary": "#1b1a17", "on_primary": "#f6f4ef", "accent": "#9a3b1b", "on_accent": "#ffffff",
         "sale": "#9a3b1b", "success": "#2d6a43", "danger": "#a3301c", "focus": "#1f4fd1", "inverse_bg": "#1b1a17",
         "inverse_text": "#f1eee6", "announcement_bg": "#1b1a17", "announcement_text": "#f1eee6"},
        {"display": "instrument-serif", "body": "instrument-sans", "mono": "jetbrains-mono", "bangla": "hind-siliguri",
         "base_size": 16, "scale": 1.333, "display_weight": 400, "heading_weight": 500, "body_leading": 1.6,
         "display_tracking": -0.02, "label_tracking": 0.08, "heading_case": "none"},
        {"sm": 0, "md": 2, "lg": 2, "card": 0, "button": 0, "input": 0},
        {"section": 112, "section_mobile": 64, "container": 1280, "gutter": 32, "grid_gap": 40},
        {"card": "none", "raised": "0 1px 0 rgba(27,26,23,.08)", "overlay": "0 24px 64px rgba(27,26,23,.18)"},
        {"fast": 160, "base": 320, "slow": 640, "easing": "out", "reveal": "rise", "hover": "underline"},
        {"header": {"layout": "centered", "search": "icon", "sticky": True, "border": True},
         "product-card": {"variant": "editorial", "aspect": "3:4", "show_brand": True, "hover_swap": False},
         "grid": {"density": "airy", "columns": 3, "columns_mobile": 1},
         "button": {"style": "sharp", "case": "uppercase"},
         "footer": {"layout": "editorial", "tone": "default"}, "announcement": {"style": "subtle"}},
    ),
    _theme(
        "dark-technology", "Dark Technology", "Dark Technology",
        "Graphite surfaces, one voltage accent, mono labels and full-bleed overlay cards for hardware.",
        {"bg": "#0e0f12", "surface": "#16181d", "surface_alt": "#1e2128", "text": "#ecedf0", "text_muted": "#a2a6b0",
         "line": "#2b2f38", "primary": "#c6f432", "on_primary": "#0e0f12", "accent": "#7aa2ff", "on_accent": "#0e0f12",
         "sale": "#ff8a6b", "success": "#5fd394", "danger": "#ff7a62", "focus": "#c6f432", "inverse_bg": "#ecedf0",
         "inverse_text": "#0e0f12", "announcement_bg": "#c6f432", "announcement_text": "#0e0f12"},
        {"display": "instrument-sans", "body": "instrument-sans", "mono": "jetbrains-mono", "bangla": "hind-siliguri",
         "base_size": 15, "scale": 1.25, "display_weight": 640, "heading_weight": 600, "body_leading": 1.55,
         "display_tracking": -0.035, "label_tracking": 0.12, "heading_case": "none"},
        {"sm": 4, "md": 8, "lg": 14, "card": 12, "button": 8, "input": 8},
        {"section": 96, "section_mobile": 56, "container": 1360, "gutter": 24, "grid_gap": 16},
        {"card": "none", "raised": "0 0 0 1px rgba(255,255,255,.06)", "overlay": "0 24px 64px rgba(0,0,0,.6)"},
        {"fast": 120, "base": 220, "slow": 420, "easing": "emphasized", "reveal": "fade", "hover": "zoom"},
        {"header": {"layout": "minimal", "search": "bar", "sticky": True, "border": True},
         "product-card": {"variant": "overlay", "aspect": "4:5", "show_brand": True, "hover_swap": False},
         "grid": {"density": "regular", "columns": 4, "columns_mobile": 2},
         "button": {"style": "solid", "case": "none"},
         "footer": {"layout": "columns", "tone": "default"}, "announcement": {"style": "bar"}},
    ),
    _theme(
        "modern-marketplace", "Modern Marketplace", "Modern Marketplace",
        "Search-first header with a category rail, compact five-up grid, clear prices and quick scanning.",
        {"bg": "#f3f4f6", "surface": "#ffffff", "surface_alt": "#e9ecf1", "text": "#111827", "text_muted": "#4b5563",
         "line": "#dde1e7", "primary": "#0f5bd8", "on_primary": "#ffffff", "accent": "#b93f0b", "on_accent": "#ffffff",
         "sale": "#c2410c", "success": "#15803d", "danger": "#b91c1c", "focus": "#0f5bd8", "inverse_bg": "#111827",
         "inverse_text": "#f9fafb", "announcement_bg": "#0f5bd8", "announcement_text": "#ffffff"},
        {"display": "instrument-sans", "body": "system-sans", "mono": "system-mono", "bangla": "hind-siliguri",
         "base_size": 15, "scale": 1.2, "display_weight": 700, "heading_weight": 650, "body_leading": 1.5,
         "display_tracking": -0.02, "label_tracking": 0.02, "heading_case": "none"},
        {"sm": 6, "md": 10, "lg": 14, "card": 10, "button": 8, "input": 8},
        {"section": 56, "section_mobile": 36, "container": 1440, "gutter": 20, "grid_gap": 12},
        {"card": "0 1px 2px rgba(17,24,39,.06)", "raised": "0 4px 16px rgba(17,24,39,.08)",
         "overlay": "0 16px 48px rgba(17,24,39,.2)"},
        {"fast": 100, "base": 180, "slow": 320, "easing": "standard", "reveal": "none", "hover": "lift"},
        {"header": {"layout": "marketplace", "search": "bar", "sticky": True, "border": False},
         "product-card": {"variant": "standard", "aspect": "1:1", "show_brand": True, "hover_swap": False},
         "grid": {"density": "compact", "columns": 5, "columns_mobile": 2},
         "button": {"style": "solid", "case": "none"},
         "footer": {"layout": "dense", "tone": "inverse"}, "announcement": {"style": "bar"}},
    ),
    _theme(
        "luxury-boutique", "Luxury Boutique", "Luxury Boutique",
        "Warm ivory and espresso, split masthead, spaced capitals, minimal cards with generous margins.",
        {"bg": "#f7f2ea", "surface": "#fbf8f3", "surface_alt": "#efe6d8", "text": "#2a211b", "text_muted": "#6a5b4e",
         "line": "#e0d4c2", "primary": "#2a211b", "on_primary": "#f7f2ea", "accent": "#7a5a2f", "on_accent": "#ffffff",
         "sale": "#8c3b2a", "success": "#3d6b45", "danger": "#9b2c1f", "focus": "#7a5a2f", "inverse_bg": "#2a211b",
         "inverse_text": "#f2e9dc", "announcement_bg": "#efe6d8", "announcement_text": "#2a211b"},
        {"display": "instrument-serif", "body": "instrument-sans", "mono": "jetbrains-mono", "bangla": "hind-siliguri",
         "base_size": 15, "scale": 1.414, "display_weight": 400, "heading_weight": 400, "body_leading": 1.7,
         "display_tracking": 0.0, "label_tracking": 0.22, "heading_case": "uppercase"},
        {"sm": 0, "md": 0, "lg": 0, "card": 0, "button": 0, "input": 0},
        {"section": 136, "section_mobile": 72, "container": 1200, "gutter": 40, "grid_gap": 48},
        {"card": "none", "raised": "none", "overlay": "0 32px 80px rgba(42,33,27,.16)"},
        {"fast": 200, "base": 420, "slow": 800, "easing": "in-out", "reveal": "fade", "hover": "none"},
        {"header": {"layout": "split", "search": "icon", "sticky": False, "border": False},
         "product-card": {"variant": "minimal", "aspect": "4:5", "show_brand": False, "hover_swap": False},
         "grid": {"density": "airy", "columns": 3, "columns_mobile": 2},
         "button": {"style": "outline", "case": "uppercase"},
         "footer": {"layout": "minimal", "tone": "default"}, "announcement": {"style": "subtle"}},
    ),
    _theme(
        "warm-lifestyle", "Warm Lifestyle", "Warm Lifestyle",
        "Soft clay tones, rounded pill controls, stacked header and friendly cards with room for stories.",
        {"bg": "#fbf6f0", "surface": "#ffffff", "surface_alt": "#f3e7da", "text": "#2b2420", "text_muted": "#6b5d53",
         "line": "#eadccd", "primary": "#8a3f22", "on_primary": "#ffffff", "accent": "#3f6b4f", "on_accent": "#ffffff",
         "sale": "#a33a1a", "success": "#3f6b4f", "danger": "#a52a1a", "focus": "#3a5fcd", "inverse_bg": "#3b2a22",
         "inverse_text": "#fbf1e6", "announcement_bg": "#3f6b4f", "announcement_text": "#ffffff"},
        {"display": "instrument-serif", "body": "instrument-sans", "mono": "jetbrains-mono", "bangla": "hind-siliguri",
         "base_size": 16, "scale": 1.28, "display_weight": 400, "heading_weight": 600, "body_leading": 1.65,
         "display_tracking": -0.01, "label_tracking": 0.04, "heading_case": "none"},
        {"sm": 8, "md": 14, "lg": 24, "card": 18, "button": 999, "input": 12},
        {"section": 88, "section_mobile": 52, "container": 1240, "gutter": 24, "grid_gap": 24},
        {"card": "0 1px 2px rgba(59,42,34,.05), 0 8px 24px rgba(59,42,34,.06)",
         "raised": "0 12px 32px rgba(59,42,34,.10)", "overlay": "0 24px 64px rgba(59,42,34,.22)"},
        {"fast": 160, "base": 280, "slow": 520, "easing": "out", "reveal": "scale", "hover": "lift"},
        {"header": {"layout": "stacked", "search": "icon", "sticky": True, "border": True},
         "product-card": {"variant": "standard", "aspect": "4:5", "show_brand": False, "hover_swap": False},
         "grid": {"density": "regular", "columns": 4, "columns_mobile": 2},
         "button": {"style": "pill", "case": "none"},
         "footer": {"layout": "columns", "tone": "inverse"}, "announcement": {"style": "bar"}},
    ),
    _theme(
        "wholesale-dense", "High-Density Wholesale", "High-Density Wholesale",
        "Information first: classic header with inline search, dense list-like cards, mono figures, tight rhythm.",
        {"bg": "#ffffff", "surface": "#ffffff", "surface_alt": "#f1f3f5", "text": "#14171a", "text_muted": "#4f5862",
         "line": "#d6dbe0", "primary": "#14532d", "on_primary": "#ffffff", "accent": "#1d4ed8", "on_accent": "#ffffff",
         "sale": "#b42318", "success": "#14532d", "danger": "#b42318", "focus": "#1d4ed8", "inverse_bg": "#14171a",
         "inverse_text": "#f1f3f5", "announcement_bg": "#f1f3f5", "announcement_text": "#14171a"},
        {"display": "instrument-sans", "body": "instrument-sans", "mono": "jetbrains-mono", "bangla": "hind-siliguri",
         "base_size": 14, "scale": 1.15, "display_weight": 650, "heading_weight": 600, "body_leading": 1.45,
         "display_tracking": -0.01, "label_tracking": 0.04, "heading_case": "none"},
        {"sm": 3, "md": 4, "lg": 6, "card": 4, "button": 4, "input": 4},
        {"section": 40, "section_mobile": 28, "container": 1520, "gutter": 16, "grid_gap": 8},
        {"card": "none", "raised": "0 1px 3px rgba(20,23,26,.12)", "overlay": "0 12px 32px rgba(20,23,26,.2)"},
        {"fast": 80, "base": 140, "slow": 240, "easing": "linear", "reveal": "none", "hover": "none"},
        {"header": {"layout": "classic", "search": "bar", "sticky": True, "border": True},
         "product-card": {"variant": "dense", "aspect": "1:1", "show_brand": True, "hover_swap": False},
         "grid": {"density": "compact", "columns": 6, "columns_mobile": 2},
         "button": {"style": "solid", "case": "none"},
         "footer": {"layout": "dense", "tone": "default"}, "announcement": {"style": "subtle"}},
    ),
]
DEFAULT_THEME_ID = "editorial-minimal"


def starter(id_: str) -> dict:
    for t in STARTER_THEMES:
        if t["id"] == id_:
            return copy.deepcopy(t)
    raise KeyError(id_)

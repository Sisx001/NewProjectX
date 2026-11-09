/**
 * Theme token → CSS custom property mapping. Every storefront surface reads these; nothing else defines --t-*.
 *
 * Colour
 *   --t-bg              page background            --t-surface        cards, drawers, inputs
 *   --t-surface-alt     alternate bands, chips     --t-line           hairlines / dividers
 *   --t-text            body text                  --t-text-muted     secondary text (≥4.5:1 enforced)
 *   --t-primary         primary buttons, links     --t-on-primary     text on primary
 *   --t-highlight       secondary emphasis         --t-on-highlight   text on highlight
 *   --t-sale --t-success --t-danger --t-focus      state colours
 *   --t-inverse-bg / --t-inverse-text              inverse bands (footer, tone=inverse sections)
 *   --t-announcement-bg / --t-announcement-text    announcement bar
 * Type
 *   --t-font-display --t-font-body --t-font-mono --t-font-bangla   font stacks
 *   --t-step--1 … --t-step-6   modular scale (steps ≥3 are fluid clamps)   --t-leading body line-height
 *   --t-display-weight --t-heading-weight --t-display-tracking --t-label-tracking --t-heading-case
 * Shape & space
 *   --t-radius-sm|md|lg|card|button|input   --t-shadow-card|raised|overlay
 *   --t-section --t-section-mobile (vertical rhythm) --t-container (max width) --t-gutter --t-grid-gap
 * Motion
 *   --t-dur-fast|base|slow   --t-ease
 * Storefront aliases (consumed by web/src/store/store.css):
 *   --t-ink --t-ink-2 --t-muted --t-accent(=primary) --t-accent-ink --t-signal(=highlight) --t-surface-sunken
 *   --t-line-strong --t-radius --t-radius-lg
 * Root data attributes: data-t-hover, data-t-reveal, data-t-button, data-t-density, data-t-card, data-t-family.
 */
import type { ThemeManifest, ThemeTokens } from "@/builder/types";

export const EASINGS: Record<string, string> = {
  standard: "cubic-bezier(0.2, 0, 0, 1)",
  out: "cubic-bezier(0.16, 1, 0.3, 1)",
  "in-out": "cubic-bezier(0.65, 0, 0.35, 1)",
  emphasized: "cubic-bezier(0.3, 0, 0, 1.15)",
  linear: "linear",
};

export const FALLBACK_FONTS: Record<string, string> = {
  "instrument-sans": '"Instrument Sans Variable", "Hind Siliguri", system-ui, sans-serif',
  "instrument-serif": '"Instrument Serif", "Hind Siliguri", Georgia, serif',
  "jetbrains-mono": '"JetBrains Mono Variable", ui-monospace, monospace',
  "hind-siliguri": '"Hind Siliguri", "Instrument Sans Variable", system-ui, sans-serif',
  "system-sans": 'system-ui, -apple-system, "Segoe UI", Roboto, "Hind Siliguri", sans-serif',
  "system-serif": 'ui-serif, Georgia, "Times New Roman", "Hind Siliguri", serif',
  "system-mono": 'ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace',
};

const px = (n: number) => `${Math.round(n * 100) / 100}px`;
const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);

export function tokensToVars(tokens: ThemeTokens, fonts: Record<string, string> = FALLBACK_FONTS): Record<string, string> {
  const c = tokens.color ?? {};
  const t = tokens.type ?? {};
  const v: Record<string, string> = {};
  const colorMap: Record<string, string> = {
    bg: "bg", surface: "surface", surface_alt: "surface-alt", text: "text", text_muted: "text-muted", line: "line",
    primary: "primary", on_primary: "on-primary", accent: "highlight", on_accent: "on-highlight", sale: "sale",
    success: "success", danger: "danger", focus: "focus", inverse_bg: "inverse-bg", inverse_text: "inverse-text",
    announcement_bg: "announcement-bg", announcement_text: "announcement-text",
  };
  for (const [k, name] of Object.entries(colorMap)) if (c[k]) v[`--t-${name}`] = c[k]!;
  const font = (key: unknown, d: string) => fonts[String(key)] ?? FALLBACK_FONTS[String(key)] ?? d;
  v["--t-font-display"] = font(t.display, FALLBACK_FONTS["instrument-serif"]!);
  v["--t-font-body"] = font(t.body, FALLBACK_FONTS["instrument-sans"]!);
  v["--t-font-mono"] = font(t.mono, FALLBACK_FONTS["jetbrains-mono"]!);
  v["--t-font-bangla"] = font(t.bangla, FALLBACK_FONTS["hind-siliguri"]!);
  const base = num(t.base_size, 16);
  const ratio = num(t.scale, 1.25);
  v["--t-text-base"] = px(base);
  v["--t-step--1"] = px(base / ratio);
  for (let i = 0; i <= 6; i++) {
    const size = base * ratio ** i;
    // Fluid for display steps: shrink toward a smaller step on narrow screens.
    v[`--t-step-${i}`] = i >= 3 ? `clamp(${px(base * ratio ** (i - 1.6))}, ${(size / 14.4).toFixed(2)}vw, ${px(size)})` : px(size);
  }
  v["--t-leading"] = String(num(t.body_leading, 1.55));
  v["--t-display-weight"] = String(num(t.display_weight, 400));
  v["--t-heading-weight"] = String(num(t.heading_weight, 600));
  v["--t-display-tracking"] = `${num(t.display_tracking, -0.02)}em`;
  v["--t-label-tracking"] = `${num(t.label_tracking, 0.08)}em`;
  v["--t-heading-case"] = String(t.heading_case ?? "none");
  for (const [k, val] of Object.entries(tokens.radius ?? {})) v[`--t-radius-${k}`] = px(num(val, 0));
  for (const [k, val] of Object.entries(tokens.shadow ?? {})) v[`--t-shadow-${k}`] = String(val);
  const sp = tokens.space ?? {};
  v["--t-section"] = px(num(sp.section, 96));
  v["--t-section-mobile"] = px(num(sp.section_mobile, 56));
  v["--t-container"] = px(num(sp.container, 1280));
  v["--t-gutter"] = px(num(sp.gutter, 24));
  v["--t-grid-gap"] = px(num(sp.grid_gap, 24));
  const m = tokens.motion ?? {};
  v["--t-dur-fast"] = `${num(m.fast, 140)}ms`;
  v["--t-dur-base"] = `${num(m.base, 240)}ms`;
  v["--t-dur-slow"] = `${num(m.slow, 480)}ms`;
  v["--t-ease"] = EASINGS[String(m.easing)] ?? EASINGS.out!;
  // Aliases for the storefront stylesheet.
  v["--t-ink"] = "var(--t-text)";
  v["--t-ink-2"] = "color-mix(in srgb, var(--t-text) 72%, var(--t-text-muted))";
  v["--t-muted"] = "var(--t-text-muted)";
  v["--t-accent"] = "var(--t-primary)";
  v["--t-accent-ink"] = "var(--t-on-primary)";
  v["--t-signal"] = "var(--t-highlight)";
  v["--t-surface-sunken"] = "var(--t-surface-alt)";
  v["--t-line-strong"] = "color-mix(in srgb, var(--t-line) 70%, var(--t-text))";
  v["--t-radius"] = "var(--t-radius-md)";
  v["--t-radius-lg"] = "var(--t-radius-lg)";
  return v;
}

export function themeAttrs(m: Pick<ThemeManifest, "tokens" | "components" | "family">): Record<string, string> {
  const comp = m.components ?? {};
  return {
    "data-t-hover": String(m.tokens?.motion?.hover ?? "none"),
    "data-t-reveal": String(m.tokens?.motion?.reveal ?? "none"),
    "data-t-button": String(comp.button?.style ?? "solid"),
    "data-t-button-case": String(comp.button?.case ?? "none"),
    "data-t-density": String(comp.grid?.density ?? "regular"),
    "data-t-card": String(comp["product-card"]?.variant ?? "standard"),
    "data-t-family": String(m.family ?? ""),
  };
}

export function varsToCss(selector: string, vars: Record<string, string>): string {
  return `${selector}{${Object.entries(vars)
    .map(([k, val]) => `${k}:${val.replace(/[;{}<>]/g, "")}`)
    .join(";")}}`;
}

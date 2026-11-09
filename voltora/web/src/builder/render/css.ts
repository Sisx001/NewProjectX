/** Pure helpers: responsive style compiler, custom-CSS scoping, visibility evaluation, bindings. Unit tested. */
import type { Device, Node, Visibility } from "../types";

/** Breakpoints used by every responsive override. Tablet ≤ 1024px, mobile ≤ 767px (desktop is the base). */
export const MEDIA = { tablet: "(max-width: 1024px)", mobile: "(max-width: 767.98px)" } as const;
export const DEVICE_MEDIA: Record<Device, string> = {
  desktop: "(min-width: 1024.02px)",
  tablet: "(min-width: 768px) and (max-width: 1024px)",
  mobile: "(max-width: 767.98px)",
};

const BAD_VALUE = /[;{}<>\\]|expression\s*\(|javascript:|@import/i;
const SAFE_PROP = /^[a-z-]{2,40}$/;

export const scopeSel = (id: string) => `[data-vn="${id.replace(/"/g, "")}"]`;

function decls(map: Record<string, string> | undefined): string {
  if (!map) return "";
  return Object.entries(map)
    .filter(([k, v]) => SAFE_PROP.test(k) && typeof v === "string" && v.trim() && !BAD_VALUE.test(v))
    .map(([k, v]) => `${k}:${v.trim()}`)
    .join(";");
}

/** Strip constructs the server sanitizer also removes (defence in depth for unsaved drafts). */
export function stripCss(css: string): string {
  let out = css.replace(/\\/g, "");
  const rules: RegExp[] = [
    /@import[^;]*;?/gi,
    /@(font-face|charset|namespace|layer|property|document|page)\b[^{;]*(\{[^}]*\}|;)?/gi,
    /expression\s*\(/gi,
    /(-moz-binding|behavior)\s*:[^;}]*;?/gi,
    /url\s*\(\s*['"]?\s*(javascript|vbscript|data)\s*:[^)]*\)/gi,
    /javascript\s*:/gi,
    /<\/?\s*style/gi,
    /<!--|-->|<\s*\/?\s*script/gi,
  ];
  let changed = true;
  while (changed) {
    changed = false;
    for (const r of rules) {
      const next = out.replace(r, "");
      if (next !== out) changed = true;
      out = next;
    }
  }
  if ((out.match(/{/g) ?? []).length !== (out.match(/}/g) ?? []).length) out = out.replace(/[{}]/g, "");
  return out.trim();
}

/** Prefix selectors with the node scope; `&` or `:scope` mean the node itself. Supports one level of @media/@supports. */
export function scopeCss(css: string, id: string): string {
  const scope = scopeSel(id);
  const out: string[] = [];
  let i = 0;
  const n = css.length;
  const prefix = (sel: string) =>
    sel
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => (s.includes("&") || s.includes(":scope") ? s.replace(/&/g, scope).replace(/:scope/g, scope) : `${scope} ${s}`))
      .join(", ") || scope;
  while (i < n) {
    const j = css.indexOf("{", i);
    if (j < 0) break;
    const head = css.slice(i, j).trim();
    if (head.startsWith("@")) {
      let depth = 1;
      let k = j + 1;
      while (k < n && depth) {
        if (css[k] === "{") depth++;
        else if (css[k] === "}") depth--;
        k++;
      }
      const inner = css.slice(j + 1, k - 1);
      if (/^@(media|supports|container)/.test(head)) out.push(`${head}{${scopeCss(inner, id)}}`);
      else if (/^@keyframes/i.test(head)) out.push(css.slice(i, k).trim());
      i = k;
      continue;
    }
    const k = css.indexOf("}", j);
    if (k < 0) break;
    out.push(`${prefix(head)}{${css.slice(j + 1, k).trim()}}`);
    i = k + 1;
  }
  return out.join("\n");
}

/** Compile per-node responsive styles, device visibility and custom CSS into one stylesheet for a document tree. */
export function compileCss(root: Node | null | undefined): string {
  const base: string[] = [];
  const tablet: string[] = [];
  const mobile: string[] = [];
  const devices: Record<Device, string[]> = { desktop: [], tablet: [], mobile: [] };
  const custom: string[] = [];
  const walk = (node: Node) => {
    const sel = scopeSel(node.id);
    const b = decls(node.style?.base);
    const t = decls(node.style?.tablet);
    const m = decls(node.style?.mobile);
    if (b) base.push(`${sel}{${b}}`);
    if (t) tablet.push(`${sel}{${t}}`);
    if (m) mobile.push(`${sel}{${m}}`);
    const dv = node.visibility?.devices;
    if (dv && dv.length && dv.length < 3) {
      for (const d of ["desktop", "tablet", "mobile"] as Device[]) if (!dv.includes(d)) devices[d].push(sel);
    }
    if (node.advanced?.css) custom.push(scopeCss(stripCss(node.advanced.css), node.id));
    node.children?.forEach(walk);
  };
  if (root) walk(root);
  const parts = [...base];
  if (tablet.length) parts.push(`@media ${MEDIA.tablet}{${tablet.join("")}}`);
  if (mobile.length) parts.push(`@media ${MEDIA.mobile}{${mobile.join("")}}`);
  for (const d of ["desktop", "tablet", "mobile"] as Device[]) {
    if (devices[d].length) parts.push(`@media ${DEVICE_MEDIA[d]}{${devices[d].join(",")}{display:none!important}}`);
  }
  parts.push(...custom);
  return parts.join("\n");
}

export type VisibilityEnv = { signedIn: boolean; locale: string; now: Date; device?: Device; editing?: boolean };

/** Client-side rule evaluation. Devices are normally enforced via CSS (resize-safe); pass env.device to evaluate them here. */
export function isVisible(v: Visibility | undefined, env: VisibilityEnv): boolean {
  if (!v) return true;
  if (v.hidden) return false;
  if (env.device && v.devices?.length && !v.devices.includes(env.device)) return false;
  if (v.signed_in === "signed_in" && !env.signedIn) return false;
  if (v.signed_in === "signed_out" && env.signedIn) return false;
  if (v.locales?.length) {
    const lang = env.locale.toLowerCase();
    if (!v.locales.some((l) => lang === l.toLowerCase() || lang.startsWith(l.toLowerCase() + "-"))) return false;
  }
  if (v.starts_at && new Date(v.starts_at).getTime() > env.now.getTime()) return false;
  if (v.ends_at && new Date(v.ends_at).getTime() <= env.now.getTime()) return false;
  return true;
}

export type BindingData = { store: Record<string, unknown>; settings: Record<string, unknown>; year: number };

/** Replace {{store.name}}, {{settings.store.tagline}}, {{year}}. Unknown bindings resolve to empty text. */
export function resolveBindings(text: string, data: BindingData): string {
  if (!text || !text.includes("{{")) return text;
  return text.replace(/\{\{\s*([a-z0-9_.]+)\s*\}\}/gi, (_m, path: string) => {
    if (path === "year") return String(data.year);
    if (path.startsWith("settings.")) {
      const v = data.settings[path.slice(9)];
      return v === undefined || v === null ? "" : String(v);
    }
    if (path === "store.tagline") return String(data.settings["store.tagline"] ?? "");
    if (path.startsWith("store.")) {
      const v = data.store[path.slice(6)];
      return v === undefined || v === null ? "" : String(v);
    }
    return "";
  });
}

/** Hide links to unpublished /pages/* so global navigation never points at a 404. */
export function linkPublished(href: string | undefined, published: Set<string> | null): boolean {
  if (!href) return false;
  const m = /^\/pages\/([a-z0-9-]+)/.exec(href);
  if (!m || !published) return true;
  return published.has(m[1]!);
}

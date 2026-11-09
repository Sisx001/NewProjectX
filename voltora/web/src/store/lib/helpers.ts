import type { ProductDetail, Variant } from "../types";

const BN_DIGITS = "০১২৩৪৫৬৭৮৯";
export const toLatinDigits = (s: string) => s.replace(/[০-৯]/g, (d) => String(BN_DIGITS.indexOf(d)));

/** Normalise a Bangladesh mobile number to the local 11-digit form (01XXXXXXXXX); null when it cannot be one. */
export function normalizeBdPhone(input: string): string | null {
  let d = toLatinDigits(input).replace(/[\s\-().]/g, "");
  if (d.startsWith("+880")) d = d.slice(4);
  else if (d.startsWith("00880")) d = d.slice(5);
  else if (d.startsWith("880") && d.length === 13) d = d.slice(3);
  if (/^1[3-9]\d{8}$/.test(d)) d = "0" + d;
  return /^01[3-9]\d{8}$/.test(d) ? d : null;
}
export function formatBdPhone(input: string): string {
  const n = normalizeBdPhone(input);
  return n ? `${n.slice(0, 5)} ${n.slice(5)}` : input;
}
export function phoneHint(input: string): string | null {
  if (!input.trim()) return null;
  return normalizeBdPhone(input) ? null : "Use an 11-digit Bangladesh mobile number, e.g. 01712 345678";
}

export type Selection = Record<string, string>;
export function optionMap(v: Variant): Selection {
  const ov = v.option_values;
  if (Array.isArray(ov)) return Object.fromEntries(ov.map((o) => [o.name, o.value]));
  return { ...ov };
}
export const purchasable = (v: Variant) => v.allow_backorder || (v.in_stock && (v.available === null || v.available > 0));

/** The variant matching every option the product defines, or null while the selection is incomplete or invalid. */
export function resolveVariant(p: Pick<ProductDetail, "options" | "variants">, sel: Selection): Variant | null {
  if (p.options.length === 0) return p.variants[0] ?? null;
  if (p.options.some((o) => !sel[o.name])) return null;
  return p.variants.find((v) => { const m = optionMap(v); return p.options.every((o) => m[o.name] === sel[o.name]); }) ?? null;
}
/** Whether choosing `value` for option `name` (keeping the other selections) still leaves a purchasable variant. */
export function optionValueAvailable(p: Pick<ProductDetail, "options" | "variants">, sel: Selection, name: string, value: string): boolean {
  const next = { ...sel, [name]: value };
  return p.variants.some((v) => {
    const m = optionMap(v);
    return purchasable(v) && Object.entries(next).every(([k, val]) => !val || m[k] === val);
  });
}
/** Pre-select the only purchasable variant, or the first purchasable one for single-option products. */
export function defaultSelection(p: Pick<ProductDetail, "options" | "variants">): Selection {
  const ok = p.variants.filter(purchasable);
  if (ok.length === 1 || (p.options.length === 1 && ok.length > 0)) return optionMap(ok[0]!);
  return {};
}

export type ShopState = { q: string; category: string; brand: string; min: string; max: string; inStock: boolean; sort: string; page: number; options: Record<string, string> };
export const SORTS = ["newest", "price_asc", "price_desc", "title"] as const;
export function parseShopParams(sp: URLSearchParams): ShopState {
  const options: Record<string, string> = {};
  sp.forEach((v, k) => { if (k.startsWith("option.") && v) options[k.slice(7)] = v; });
  const sort = sp.get("sort") ?? "newest";
  return {
    q: sp.get("q") ?? "", category: sp.get("category") ?? "", brand: sp.get("brand") ?? "",
    min: sp.get("min") ?? "", max: sp.get("max") ?? "", inStock: sp.get("in_stock") === "1",
    sort: (SORTS as readonly string[]).includes(sort) ? sort : "newest",
    page: Math.max(1, parseInt(sp.get("page") ?? "1", 10) || 1), options,
  };
}
export function buildShopParams(s: Partial<ShopState>): URLSearchParams {
  const sp = new URLSearchParams();
  if (s.q) sp.set("q", s.q);
  if (s.category) sp.set("category", s.category);
  if (s.brand) sp.set("brand", s.brand);
  if (s.min) sp.set("min", s.min);
  if (s.max) sp.set("max", s.max);
  if (s.inStock) sp.set("in_stock", "1");
  if (s.sort && s.sort !== "newest") sp.set("sort", s.sort);
  if (s.page && s.page > 1) sp.set("page", String(s.page));
  for (const [k, v] of Object.entries(s.options ?? {})) if (v) sp.set("option." + k, v);
  return sp;
}
export function activeFilterCount(s: ShopState, ignore: string[] = []): number {
  return [!ignore.includes("category") && s.category, !ignore.includes("brand") && s.brand, s.min || s.max, s.inStock, ...Object.values(s.options)].filter(Boolean).length;
}
export const discountPct = (price: number, compare: number | null) => (compare && compare > price ? Math.round(((compare - price) / compare) * 100) : 0);

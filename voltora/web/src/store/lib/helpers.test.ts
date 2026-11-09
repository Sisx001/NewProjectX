import { describe, expect, it } from "vitest";
import { buildShopParams, defaultSelection, formatBdPhone, normalizeBdPhone, optionValueAvailable, parseShopParams, phoneHint, resolveVariant } from "./helpers";
import type { Variant } from "../types";

const v = (id: string, ov: Record<string, string>, over: Partial<Variant> = {}): Variant => ({ id, sku: id, title: id, option_values: ov, price_minor: 100, compare_at_minor: null, in_stock: true, available: 5, allow_backorder: false, ...over });
const product = {
  options: [{ name: "Size", values: ["S", "M"] }, { name: "Color", values: ["Black", "Sand"] }],
  variants: [v("a", { Size: "S", Color: "Black" }), v("b", { Size: "S", Color: "Sand" }, { in_stock: false, available: 0 }), v("c", { Size: "M", Color: "Black" })],
};

describe("variant resolution", () => {
  it("needs a complete selection", () => {
    expect(resolveVariant(product, { Size: "S" })).toBeNull();
    expect(resolveVariant(product, { Size: "S", Color: "Black" })?.id).toBe("a");
    expect(resolveVariant(product, { Size: "M", Color: "Sand" })).toBeNull();
  });
  it("accepts array-shaped option values and option-less products", () => {
    const p = { options: [{ name: "Size", values: ["S"] }], variants: [v("x", {}, { option_values: [{ name: "Size", value: "S" }] })] };
    expect(resolveVariant(p, { Size: "S" })?.id).toBe("x");
    expect(resolveVariant({ options: [], variants: [v("solo", {})] }, {})?.id).toBe("solo");
  });
  it("disables values with no purchasable variant", () => {
    expect(optionValueAvailable(product, { Size: "S" }, "Color", "Sand")).toBe(false);
    expect(optionValueAvailable(product, { Size: "S" }, "Color", "Black")).toBe(true);
    expect(optionValueAvailable(product, { Color: "Sand" }, "Size", "M")).toBe(false);
  });
  it("treats backorder as purchasable", () => {
    const p = { ...product, variants: [v("b", { Size: "S", Color: "Sand" }, { in_stock: false, available: 0, allow_backorder: true })] };
    expect(optionValueAvailable(p, {}, "Size", "S")).toBe(true);
  });
  it("preselects a lone purchasable variant", () => {
    expect(defaultSelection({ options: product.options, variants: [product.variants[0]!] })).toEqual({ Size: "S", Color: "Black" });
    expect(defaultSelection(product)).toEqual({});
  });
});

describe("Bangladesh phone", () => {
  it("normalises prefixes and Bangla digits", () => {
    expect(normalizeBdPhone("+8801712345678")).toBe("01712345678");
    expect(normalizeBdPhone("8801712-345678")).toBe("01712345678");
    expect(normalizeBdPhone("০১৭১২৩৪৫৬৭৮")).toBe("01712345678");
    expect(normalizeBdPhone("01212345678")).toBeNull();
    expect(normalizeBdPhone("0171234")).toBeNull();
  });
  it("formats and hints", () => {
    expect(formatBdPhone("01712345678")).toBe("01712 345678");
    expect(phoneHint("")).toBeNull();
    expect(phoneHint("123")).toMatch(/11-digit/);
    expect(phoneHint("01712345678")).toBeNull();
  });
});

describe("shop URL state", () => {
  it("round-trips", () => {
    const sp = new URLSearchParams("q=tee&category=men&min=500&in_stock=1&sort=price_asc&page=3&option.Size=M");
    const s = parseShopParams(sp);
    expect(s).toMatchObject({ q: "tee", category: "men", min: "500", inStock: true, sort: "price_asc", page: 3, options: { Size: "M" } });
    expect(buildShopParams(s).toString()).toBe(sp.toString());
  });
  it("sanitises junk and omits defaults", () => {
    const s = parseShopParams(new URLSearchParams("sort=evil&page=-4"));
    expect(s.sort).toBe("newest");
    expect(s.page).toBe(1);
    expect(buildShopParams(s).toString()).toBe("");
  });
});

import { describe, expect, it } from "vitest";
import { compileCss, isVisible, linkPublished, resolveBindings, scopeCss, stripCss } from "./css";
import type { Node } from "../types";

const tree: Node = {
  id: "root",
  type: "page",
  props: {},
  children: [
    { id: "a", type: "heading", props: {}, style: { base: { "font-size": "48px", color: "red" }, tablet: { "font-size": "36px" }, mobile: { "font-size": "28px" } } },
    { id: "b", type: "text", props: {}, visibility: { devices: ["mobile"] } },
    { id: "c", type: "text", props: {}, style: { base: { color: "red;} body{display:none" } }, advanced: { css: "& { color: blue } h2 { margin: 0 }" } },
  ],
};

describe("compileCss", () => {
  const css = compileCss(tree);
  it("emits base rules scoped by node id", () => {
    expect(css).toContain('[data-vn="a"]{font-size:48px;color:red}');
  });
  it("emits tablet and mobile overrides inside media queries", () => {
    expect(css).toContain('@media (max-width: 1024px){[data-vn="a"]{font-size:36px}}');
    expect(css).toContain('@media (max-width: 767.98px){[data-vn="a"]{font-size:28px}}');
  });
  it("hides device-restricted nodes on the other devices via CSS", () => {
    expect(css).toMatch(/@media \(min-width: 1024\.02px\)\{\[data-vn="b"\]\{display:none!important\}\}/);
    expect(css).toMatch(/@media \(min-width: 768px\) and \(max-width: 1024px\)\{\[data-vn="b"\]\{display:none!important\}\}/);
    expect(css).not.toMatch(/max-width: 767.98px\)\{\[data-vn="b"\]\{display:none/);
  });
  it("drops unsafe declaration values and scopes custom CSS", () => {
    expect(css).not.toContain("body{display:none");
    expect(css).toContain('[data-vn="c"]{color: blue}');
    expect(css).toContain('[data-vn="c"] h2{margin: 0}');
  });
  it("returns empty for an empty tree", () => {
    expect(compileCss(null)).toBe("");
  });
});

describe("custom CSS safety", () => {
  it("strips imports, javascript urls, expressions and style breakouts", () => {
    const out = stripCss('@import "x.css"; & { background: url(javascript:alert(1)); width: expression(1) } </style><script>');
    expect(out).not.toMatch(/@import|javascript|expression|<\/style|<script/i);
  });
  it("scopes @media blocks", () => {
    expect(scopeCss("@media (max-width: 600px) { & { color: red } }", "n")).toBe('@media (max-width: 600px){[data-vn="n"]{color: red}}');
  });
});

describe("isVisible", () => {
  const env = { signedIn: false, locale: "en", now: new Date("2026-06-01T00:00:00Z") };
  it("respects hidden and auth state", () => {
    expect(isVisible(undefined, env)).toBe(true);
    expect(isVisible({ hidden: true }, env)).toBe(false);
    expect(isVisible({ signed_in: "signed_in" }, env)).toBe(false);
    expect(isVisible({ signed_in: "signed_out" }, env)).toBe(true);
    expect(isVisible({ signed_in: "signed_out" }, { ...env, signedIn: true })).toBe(false);
  });
  it("evaluates date windows", () => {
    expect(isVisible({ starts_at: "2026-07-01T00:00:00Z" }, env)).toBe(false);
    expect(isVisible({ ends_at: "2026-05-01T00:00:00Z" }, env)).toBe(false);
    expect(isVisible({ starts_at: "2026-05-01T00:00:00Z", ends_at: "2026-07-01T00:00:00Z" }, env)).toBe(true);
  });
  it("matches locales by language prefix", () => {
    expect(isVisible({ locales: ["bn"] }, env)).toBe(false);
    expect(isVisible({ locales: ["bn"] }, { ...env, locale: "bn-BD" })).toBe(true);
  });
  it("evaluates devices only when a device is given", () => {
    expect(isVisible({ devices: ["mobile"] }, env)).toBe(true);
    expect(isVisible({ devices: ["mobile"] }, { ...env, device: "desktop" })).toBe(false);
  });
});

describe("bindings", () => {
  const data = { store: { name: "Dhaka Goods" }, settings: { "store.tagline": "Everyday things", "store.support_phone": "" }, year: 2026 };
  it("resolves store, settings and year", () => {
    expect(resolveBindings("© {{year}} {{store.name}}", data)).toBe("© 2026 Dhaka Goods");
    expect(resolveBindings("{{store.tagline}}", data)).toBe("Everyday things");
    expect(resolveBindings("Call {{settings.store.support_phone}}", data)).toBe("Call ");
    expect(resolveBindings("{{unknown.x}}", data)).toBe("");
  });
  it("hides links to unpublished pages", () => {
    const pub = new Set(["home", "about"]);
    expect(linkPublished("/pages/about", pub)).toBe(true);
    expect(linkPublished("/pages/terms", pub)).toBe(false);
    expect(linkPublished("/shop", pub)).toBe(true);
  });
});

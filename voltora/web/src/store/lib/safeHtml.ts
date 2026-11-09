const ALLOWED = new Set(["P", "BR", "UL", "OL", "LI", "STRONG", "B", "EM", "I", "H2", "H3", "H4", "BLOCKQUOTE", "A", "SPAN", "SMALL", "HR"]);

/** Allowlist sanitiser for merchant-authored product descriptions: unknown tags are unwrapped, attributes dropped (except safe http(s)/relative links). */
export function sanitizeHtml(html: string): string {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  const walk = (node: Element) => {
    for (const child of Array.from(node.children)) {
      if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "SVG", "MATH", "TEMPLATE"].includes(child.tagName.toUpperCase())) { child.remove(); continue; }
      walk(child);
      if (!ALLOWED.has(child.tagName)) { child.replaceWith(...Array.from(child.childNodes)); continue; }
      const href = child.tagName === "A" ? child.getAttribute("href") ?? "" : "";
      for (const a of Array.from(child.attributes)) child.removeAttribute(a.name);
      if (child.tagName === "A" && /^(https?:\/\/|\/(?!\/)|mailto:|tel:)/i.test(href.trim())) {
        child.setAttribute("href", href.trim());
        child.setAttribute("rel", "noopener noreferrer nofollow");
      }
    }
  };
  walk(doc.body);
  return doc.body.innerHTML;
}

/** Plain-text excerpt for meta descriptions / schema.org. */
export function htmlToText(html: string): string {
  return new DOMParser().parseFromString(html, "text/html").body.textContent?.replace(/\s+/g, " ").trim() ?? "";
}

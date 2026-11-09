/** Client-side allowlist sanitiser for rich text (the server sanitises on save; this guards unsaved drafts). */
const TAGS = new Set(["P", "BR", "STRONG", "B", "EM", "I", "U", "S", "A", "UL", "OL", "LI", "H2", "H3", "H4", "BLOCKQUOTE", "CODE", "PRE", "SMALL", "SPAN", "HR", "SUP", "SUB"]);
const DROP = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "TEMPLATE", "NOSCRIPT", "SVG", "MATH"]);

export function safeHref(href: string): boolean {
  const v = href.trim();
  if (!v) return false;
  if ((v.startsWith("/") && !v.startsWith("//")) || v.startsWith("#") || v.startsWith("?")) return true;
  return /^(https?:|mailto:|tel:)/i.test(v);
}

export function sanitizeHtml(html: string): string {
  if (!html) return "";
  if (typeof DOMParser === "undefined") return "";
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  const clean = (el: Element) => {
    for (const child of Array.from(el.children)) {
      if (DROP.has(child.tagName)) {
        child.remove();
        continue;
      }
      clean(child);
      if (!TAGS.has(child.tagName)) {
        child.replaceWith(...Array.from(child.childNodes));
        continue;
      }
      for (const attr of Array.from(child.attributes)) {
        if (child.tagName === "A" && attr.name === "href" && safeHref(attr.value)) continue;
        child.removeAttribute(attr.name);
      }
      if (child.tagName === "A" && /^https?:/i.test(child.getAttribute("href") ?? "")) child.setAttribute("rel", "noopener noreferrer");
    }
  };
  clean(doc.body);
  return doc.body.innerHTML;
}

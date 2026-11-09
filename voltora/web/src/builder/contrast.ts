/** WCAG 2.x contrast (same formula as backend themes.contrast_ratio). */
export function luminance(hex: string): number | null {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  let h = m[1]!;
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const ch = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * ch[0]! + 0.7152 * ch[1]! + 0.0722 * ch[2]!;
}

export function contrast(a: string, b: string): number | null {
  const la = luminance(a), lb = luminance(b);
  if (la === null || lb === null) return null;
  return Math.round(((Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)) * 100) / 100;
}

export type ContrastRow = { fg: string; bg: string; kind: "body" | "large" | "ui"; ratio: number | null; required: number; ok: boolean };

export function contrastRows(colors: Record<string, string>, pairs: { fg: string; bg: string; kind: "body" | "large" | "ui" }[]): ContrastRow[] {
  return pairs.map((p) => {
    const ratio = colors[p.fg] && colors[p.bg] ? contrast(colors[p.fg]!, colors[p.bg]!) : null;
    const required = p.kind === "body" ? 4.5 : 3;
    return { ...p, ratio, required, ok: ratio !== null && ratio >= required };
  });
}

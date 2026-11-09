import { useLayoutEffect, useMemo, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import type { Node, PublicTheme, ThemeManifest } from "@/builder/types";
import { DocumentView } from "@/builder/render";
import { EMPTY_THEME, ThemeCtx, useThemeState, type ThemeState } from "./context";
import { themeAttrs, tokensToVars, varsToCss } from "./tokens";
import "./theme.css";

export const THEME_QUERY = ["store-theme"] as const;

/**
 * Loads the live theme (or a draft manifest supplied by the Studio) and applies it as --t-* variables on :root,
 * so portals (cart drawer, dialogs) inherit it too. See ./tokens.ts for what each variable means.
 */
export function ThemeProvider({ children, manifest, globals, editing = false }: { children: ReactNode; manifest?: ThemeManifest | null; globals?: Partial<ThemeState["globals"]>; editing?: boolean }) {
  const q = useQuery({ queryKey: THEME_QUERY, queryFn: () => get<PublicTheme>("/api/store/theme"), staleTime: 60_000, retry: 1 });
  const live = q.data;
  const tokens = manifest?.tokens ?? live?.tokens ?? null;
  const components = (manifest?.components ?? live?.components ?? {}) as ThemeState["components"];
  const family = manifest?.family ?? live?.family ?? "";

  useLayoutEffect(() => {
    if (!tokens) return;
    const el = document.createElement("style");
    el.dataset.voltoraTheme = "";
    el.textContent = varsToCss(":root", tokensToVars(tokens, live?.fonts));
    document.head.appendChild(el);
    const attrs = themeAttrs({ tokens, components, family });
    const html = document.documentElement;
    for (const [k, v] of Object.entries(attrs)) html.setAttribute(k, v);
    return () => {
      el.remove();
      for (const k of Object.keys(attrs)) html.removeAttribute(k);
    };
  }, [tokens, components, family, live?.fonts]);

  const value = useMemo<ThemeState>(
    () => ({
      theme: live ?? null,
      tokens,
      components,
      globals: {
        header: globals && "header" in globals ? globals.header ?? null : live?.globals.header ?? null,
        footer: globals && "footer" in globals ? globals.footer ?? null : live?.globals.footer ?? null,
        announcement: globals && "announcement" in globals ? globals.announcement ?? null : live?.globals.announcement ?? null,
      },
      published: editing ? null : live ? new Set(live.pages.map((p) => p.slug)) : null,
      loading: q.isLoading,
      error: q.isError,
      editing,
    }),
    [live, tokens, components, globals, editing, q.isLoading, q.isError],
  );
  return <ThemeCtx.Provider value={value}>{children}</ThemeCtx.Provider>;
}

function GlobalSlot({ node }: { node: Node | null }) {
  const { editing } = useThemeState();
  if (!node) return null;
  return <DocumentView doc={{ root: node }} editing={editing} className="vb-global" />;
}

/** Published announcement bar + header (global documents edited in the Studio). Renders nothing until published. */
export function SiteHeader() {
  const { globals } = useThemeState();
  return (
    <>
      <GlobalSlot node={globals.announcement} />
      <GlobalSlot node={globals.header} />
    </>
  );
}

export function SiteFooter() {
  const { globals } = useThemeState();
  return <GlobalSlot node={globals.footer} />;
}

/** Theme component variants (product-card, header, grid, button, footer…) and tokens for storefront components. */
export function useTheme(): { components: Record<string, { variant?: string } & Record<string, unknown>>; tokens: ThemeState["tokens"]; family: string | null; loading: boolean } {
  const s = useThemeState();
  return { components: s.components, tokens: s.tokens, family: s.theme?.family ?? null, loading: s.loading };
}

export { EMPTY_THEME };

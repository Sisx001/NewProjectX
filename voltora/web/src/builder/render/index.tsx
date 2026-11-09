import { useEffect, useMemo, useRef, type ComponentType, type CSSProperties, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useThemeState } from "@/store/theme/context";
import type { Motion, Node, PageDocument, PublicPage } from "../types";
import { compileCss, isVisible, resolveBindings, type BindingData } from "./css";
import { RenderCtx, useRender, type RenderEnv } from "./context";
import { LAYOUT } from "./layout";
import { CONTENT } from "./content";
import { COMMERCE } from "./commerce";
import { NAVIGATION } from "./navigation";
import "./render.css";

export type { NodeProps, Renderer } from "./attrs";
export { nodeAttrs } from "./attrs";

/** Renderer map. Plugins can add renderers for their registered component types with `registerRenderer`. */
const RENDERERS: Record<string, Renderer> = { ...LAYOUT, ...CONTENT, ...COMMERCE, ...NAVIGATION };
export function registerRenderer(type: string, r: Renderer) {
  RENDERERS[type] = r;
}
export const hasRenderer = (type: string) => type in RENDERERS;

type StoreConfig = { store: Record<string, unknown>; settings: Record<string, unknown> };
const useConfig = () => useQuery({ queryKey: ["sf-config"], queryFn: () => get<StoreConfig>("/api/storefront/config"), staleTime: 60_000 });

const EASE: Record<string, string> = {
  standard: "cubic-bezier(0.2, 0, 0, 1)",
  out: "cubic-bezier(0.16, 1, 0.3, 1)",
  "in-out": "cubic-bezier(0.65, 0, 0.35, 1)",
  emphasized: "cubic-bezier(0.3, 0, 0, 1.15)",
  linear: "linear",
};

/** Attributes every component puts on its outermost element: scope id, motion and advanced class/label. */
export function nodeAttrs(node: Node, extra?: { className?: string; style?: CSSProperties }) {
  const m: Motion | undefined = node.motion;
  const style: Record<string, string | number> = { ...(extra?.style as Record<string, string | number>) };
  if (m && m.preset !== "none") {
    style["--vm-dur"] = `${m.duration ?? 480}ms`;
    style["--vm-delay"] = `${m.delay ?? 0}ms`;
    style["--vm-ease"] = EASE[m.easing ?? "out"] ?? EASE.out!;
    style["--vm-dist"] = `${m.distance ?? 16}px`;
    style["--vm-stagger"] = `${m.stagger ?? 60}ms`;
  }
  const cls = [extra?.className, node.advanced?.class_name].filter(Boolean).join(" ");
  return {
    "data-vn": node.id,
    "data-vt": node.type,
    ...(m && m.preset !== "none" ? { "data-motion": m.preset, "data-trigger": m.trigger ?? "enter" } : {}),
    ...(node.visibility?.hidden ? { "data-hidden": "" } : {}),
    ...(node.visibility && Object.keys(node.visibility).some((k) => k !== "hidden") ? { "data-conditional": "" } : {}),
    ...(node.advanced?.aria_label ? { "aria-label": node.advanced.aria_label } : {}),
    className: cls || undefined,
    style: style as CSSProperties,
  };
}

/** Resolve {{bindings}} in bindable string props (and inside list items) without mutating the document. */
function resolveProps(node: Node, data: BindingData): Record<string, unknown> {
  const p: Record<string, unknown> = {};
  const res = (v: unknown): unknown => {
    if (typeof v === "string") return resolveBindings(v, data);
    if (Array.isArray(v)) return v.map(res);
    if (v && typeof v === "object" && !("url" in (v as object))) return Object.fromEntries(Object.entries(v as object).map(([k, x]) => [k, res(x)]));
    return v;
  };
  for (const [k, v] of Object.entries(node.props ?? {})) p[k] = res(v);
  return p;
}

export function NodeView({ node }: { node: Node }) {
  const env = useRender();
  if (!env.editing && !isVisible(node.visibility, { signedIn: env.signedIn, locale: env.locale, now: env.now })) return null;
  const R = RENDERERS[node.type];
  if (!R) {
    return env.editing ? (
      <div {...nodeAttrs(node, { className: "vb-missing" })}>
        <strong>{node.type}</strong> has no renderer installed. It will not appear on the storefront.
      </div>
    ) : null;
  }
  const childNodes = (node.children ?? []).filter((c) => env.editing || isVisible(c.visibility, { signedIn: env.signedIn, locale: env.locale, now: env.now }));
  const kids = childNodes.map((c) => <NodeView key={c.id} node={c} />);
  return <R node={node} p={resolveProps(node, env.data)} kids={kids} childNodes={childNodes} />;
}

/** Reveal-on-scroll: one IntersectionObserver per document; CSS hides only after JS is ready (no-JS stays visible). */
function useMotion(ref: React.RefObject<HTMLDivElement | null>, deps: unknown[]) {
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const els = Array.from(root.querySelectorAll<HTMLElement>("[data-motion]"));
    if (reduce || typeof IntersectionObserver === "undefined") {
      els.forEach((e) => e.setAttribute("data-in", ""));
      return;
    }
    const index = (el: HTMLElement) => {
      const items = el.querySelectorAll<HTMLElement>("[data-stagger-item]");
      (items.length ? Array.from(items) : (Array.from(el.children) as HTMLElement[])).forEach((c, i) => c.style.setProperty("--vm-i", String(Math.min(i, 12))));
    };
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            const el = e.target as HTMLElement;
            index(el);
            el.setAttribute("data-in", "");
            io.unobserve(el);
          }
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 },
    );
    els.forEach((el) => {
      if (el.hasAttribute("data-in")) return;
      if (el.dataset.trigger === "load") {
        index(el);
        requestAnimationFrame(() => el.setAttribute("data-in", ""));
      } else io.observe(el);
    });
    root.setAttribute("data-motion-ready", "");
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export function useRenderEnv(editing = false): RenderEnv {
  const cfg = useConfig();
  const { me } = useAuth();
  const theme = useThemeState();
  return useMemo(
    () => ({
      editing,
      data: { store: cfg.data?.store ?? {}, settings: cfg.data?.settings ?? {}, year: new Date().getFullYear() },
      signedIn: !!me?.user && me.user.kind === "customer",
      locale: (typeof navigator !== "undefined" && navigator.language) || String(cfg.data?.store.locale ?? "en"),
      now: new Date(),
      published: theme.published,
      currency: String(cfg.data?.store.currency ?? "BDT"),
    }),
    [editing, cfg.data, me, theme.published],
  );
}

/** Render a document tree. `editing` keeps hidden/conditional nodes visible (dimmed) for selection in the Studio. */
export function DocumentView({ doc, editing = false, className }: { doc: PageDocument | { root: Node | null } | null; editing?: boolean; className?: string }) {
  const env = useRenderEnv(editing);
  const ref = useRef<HTMLDivElement>(null);
  const css = useMemo(() => compileCss(doc?.root ?? null), [doc]);
  useMotion(ref, [doc, editing]);
  if (!doc?.root) return null;
  return (
    <RenderCtx.Provider value={env}>
      <div ref={ref} className={["vb", className].filter(Boolean).join(" ")} data-editing={editing || undefined}>
        {css ? <style>{css}</style> : null}
        <NodeView node={doc.root} />
      </div>
    </RenderCtx.Provider>
  );
}

/** Published storefront page by slug. Shares the ["page", slug] query with the storefront's page loader. */
export function PageView({ slug, fallback, notFound }: { slug: string; fallback?: ReactNode; notFound?: ReactNode }) {
  const q = useQuery({ queryKey: ["page", slug], retry: false, queryFn: () => get<PublicPage>(`/api/store/pages/${encodeURIComponent(slug)}`) });
  if (q.isLoading) return <div className="vb-loading" aria-busy="true" aria-label="Loading page" />;
  if (q.error instanceof ApiError && q.error.status === 404) return <>{notFound ?? fallback ?? null}</>;
  if (q.error || !q.data) return <>{fallback ?? null}</>;
  return <DocumentView doc={q.data.document} />;
}

export { compileCss, isVisible, resolveBindings } from "./css";

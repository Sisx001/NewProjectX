import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { get, errorMessage } from "@/lib/api";
import { DocumentView, PageView } from "./render";
import { ThemeProvider, SiteFooter, SiteHeader } from "@/store/theme";
import type { Node, PageDocument, PreviewMessage, StudioMessage, ThemeManifest } from "./types";
import "@/store/store.css";
import "./preview.css";

type PreviewPage = { id: string; kind: string; slug: string; title: string; document: PageDocument };

const inFrame = typeof window !== "undefined" && window.parent !== window;

function post(msg: PreviewMessage) {
  if (inFrame) window.parent.postMessage(msg, window.location.origin);
}

function findNode(root: Node | null | undefined, id: string): Node | null {
  if (!root) return null;
  if (root.id === id) return root;
  for (const c of root.children ?? []) {
    const f = findNode(c, id);
    if (f) return f;
  }
  return null;
}

/** Outline drawn over the selected / hovered node inside the canvas. */
function Overlay({ id, kind, label }: { id: string | null; kind: "select" | "hover"; label?: string }) {
  const [rect, setRect] = useState<DOMRect | null>(null);
  useLayoutEffect(() => {
    if (!id) return setRect(null);
    let raf = 0;
    const tick = () => {
      const el = document.querySelector(`[data-vn="${CSS.escape(id)}"]`);
      const r = el?.getBoundingClientRect() ?? null;
      setRect((prev) => (prev && r && prev.x === r.x && prev.y === r.y && prev.width === r.width && prev.height === r.height ? prev : r));
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [id]);
  if (!id || !rect || (rect.width === 0 && rect.height === 0)) return null;
  return (
    <div className={`vp-box vp-box--${kind}`} style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }} aria-hidden>
      {label ? <span className="vp-box__label">{label}</span> : null}
    </div>
  );
}

/**
 * Draft renderer. Inside the Studio iframe it receives the working document over postMessage (same-origin only) and
 * reports clicks/hover by node id. Opened directly with ?token=… it renders the saved draft read-only.
 */
export default function PreviewFrame() {
  const loc = useLocation();
  const parts = loc.pathname.replace(/^\/__preview\/?/, "").split("/").filter(Boolean);
  const token = new URLSearchParams(loc.search).get("token");
  const isTheme = parts[0] === "theme";
  const targetId = isTheme ? parts[1] : parts[0];

  const [doc, setDoc] = useState<PageDocument | null>(null);
  const [docKind, setDocKind] = useState<{ kind: string; slug: string } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [themeDraft, setThemeDraft] = useState<ThemeManifest | null>(null);
  const editing = inFrame;

  const saved = useQuery({
    queryKey: ["preview-page", targetId, token],
    enabled: !!targetId && !isTheme && targetId !== "studio",
    retry: false,
    queryFn: () => get<PreviewPage>(`/api/content/preview/pages/${targetId}${token ? `?token=${encodeURIComponent(token)}` : ""}`),
  });
  const savedTheme = useQuery({
    queryKey: ["preview-theme", targetId, token],
    enabled: isTheme && !!targetId,
    retry: false,
    queryFn: () => get<ThemeManifest & { tokens: ThemeManifest["tokens"] }>(`/api/content/preview/themes/${targetId}${token ? `?token=${encodeURIComponent(token)}` : ""}`),
  });

  useEffect(() => {
    if (!inFrame) return;
    const onMsg = (e: MessageEvent<StudioMessage>) => {
      if (e.origin !== window.location.origin || e.source !== window.parent) return;
      const m = e.data;
      if (!m || typeof m !== "object" || typeof m.type !== "string") return;
      if (m.type === "voltora:doc") {
        setDoc(m.doc);
        if (m.kind) setDocKind({ kind: m.kind, slug: m.slug ?? "" });
        if (m.selected !== undefined) setSelected(m.selected);
      } else if (m.type === "voltora:select") setSelected(m.id);
      else if (m.type === "voltora:theme") setThemeDraft(m.manifest);
    };
    window.addEventListener("message", onMsg);
    post({ type: "voltora:ready" });
    return () => window.removeEventListener("message", onMsg);
  }, []);

  const current = doc ?? saved.data?.document ?? null;
  const kind = docKind ?? (saved.data ? { kind: saved.data.kind, slug: saved.data.slug } : null);

  // Click/hover capture: the canvas is not interactive while editing (hold Alt to interact with tabs, menus…).
  const pathTo = useCallback((el: Element | null): string[] => {
    const ids: string[] = [];
    let cur: Element | null = el;
    while (cur) {
      const id = cur.getAttribute?.("data-vn");
      if (id) ids.unshift(id);
      cur = cur.parentElement;
    }
    return ids;
  }, []);
  useEffect(() => {
    if (!editing) return;
    const onClick = (e: MouseEvent) => {
      if (e.altKey) return;
      const t = (e.target as Element).closest?.("[data-vn]");
      e.preventDefault();
      e.stopPropagation();
      if (!t) return;
      const id = t.getAttribute("data-vn")!;
      setSelected(id);
      post({ type: "voltora:click", id, path: pathTo(t) });
    };
    const onOver = (e: MouseEvent) => {
      const t = (e.target as Element).closest?.("[data-vn]");
      const id = t?.getAttribute("data-vn") ?? null;
      setHover(id);
      post({ type: "voltora:hover", id });
    };
    const onSubmit = (e: Event) => e.preventDefault();
    document.addEventListener("click", onClick, true);
    document.addEventListener("mouseover", onOver, true);
    document.addEventListener("submit", onSubmit, true);
    return () => {
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("mouseover", onOver, true);
      document.removeEventListener("submit", onSubmit, true);
    };
  }, [editing, pathTo]);

  useEffect(() => {
    if (!selected || !editing) return;
    const el = document.querySelector(`[data-vn="${CSS.escape(selected)}"]`);
    const r = el?.getBoundingClientRect();
    if (el && r && (r.bottom < 0 || r.top > window.innerHeight)) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selected, editing]);

  const globals = useMemo(() => {
    if (kind?.kind !== "global" || !current) return undefined;
    return { [kind.slug]: current.root } as Record<string, Node>;
  }, [kind, current]);

  const manifest = themeDraft ?? (isTheme ? (savedTheme.data as ThemeManifest | undefined) ?? null : null);
  const label = (id: string | null) => (id ? findNode(current?.root, id)?.type : undefined);

  if (!inFrame && (saved.isError || savedTheme.isError)) {
    return (
      <main className="vp-error">
        <h1>Preview unavailable</h1>
        <p>{errorMessage(saved.error ?? savedTheme.error)}</p>
        <p>Preview links expire after 30 minutes. Ask for a new link from the Studio.</p>
      </main>
    );
  }

  const body =
    isTheme || kind?.kind === "global" ? (
      <PageView slug="home" notFound={<p className="vp-note">Publish a home page to preview it here.</p>} />
    ) : current ? (
      <DocumentView doc={current} editing={editing} />
    ) : (
      <div className="vp-note" aria-busy="true">Loading draft…</div>
    );

  return (
    <ThemeProvider manifest={manifest} globals={globals} editing={editing}>
      {!inFrame ? (
        <p className="vp-banner" role="status">
          Draft preview{saved.data ? ` · ${saved.data.title}` : ""} — not published. Customers can't see this version.
        </p>
      ) : null}
      <div className="s-root vp-root">
        <SiteHeader />
        <main id="main" className="s-main">{body}</main>
        <SiteFooter />
      </div>
      {editing ? (
        <>
          <Overlay id={hover && hover !== selected ? hover : null} kind="hover" />
          <Overlay id={selected} kind="select" label={label(selected)} />
        </>
      ) : null}
    </ThemeProvider>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as RKeyboardEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowDown, ArrowLeft, ArrowLeftToLine, ArrowRightToLine, ArrowUp, CheckCircle2, CircleDot, Copy, Download, ExternalLink, History as HistoryIcon,
  Laptop, Layers as LayersIcon, Monitor, MoreHorizontal, Plus, Redo2, Settings2, Smartphone, Tablet, Trash2, Undo2, Upload, FileText,
} from "lucide-react";
import { ApiError, errorMessage, get, post, put } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Badge, Button, Dialog, Drawer, Field, Input, Skeleton, StatusBadge, Textarea, relTime, useToast } from "@/ui";
import type { ComponentDef, Node, PageDocument, PreviewMessage, PublicTheme, StudioMessage } from "../types";
import { Inspector } from "./Inspector";
import { Layers } from "./Layers";
import { Library } from "./Library";
import { download, readJsonFile, useRegistry, usePages, type Diff, type Issue, type LintItem, type PageDetail, type Revision } from "./api";
import { initHistory, push, redo, undo, type History } from "./history";
import * as T from "./tree";
import "./studio.css";

type SaveState = "saved" | "dirty" | "saving" | "error" | "conflict";
const DEVICES = [
  { w: 1440, label: "Desktop 1440", icon: Monitor },
  { w: 1024, label: "Laptop / tablet landscape 1024", icon: Laptop },
  { w: 768, label: "Tablet 768", icon: Tablet },
  { w: 390, label: "Phone 390", icon: Smartphone },
  { w: 320, label: "Small phone 320", icon: Smartphone },
];

function useTyping() {
  return () => {
    const a = document.activeElement as HTMLElement | null;
    return !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT" || a.isContentEditable);
  };
}

export default function Studio() {
  const { pageId = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const canPublish = can("content.publish");
  const reg = useRegistry();
  const pages = usePages();
  const theme = useQuery({ queryKey: ["store-theme"], queryFn: () => get<PublicTheme>("/api/store/theme"), staleTime: 60_000 });
  const page = useQuery({ queryKey: ["content-page", pageId], queryFn: () => get<PageDetail>(`/api/content/pages/${pageId}`), refetchOnWindowFocus: false, staleTime: Infinity });

  const [hist, setHist] = useState<History<PageDocument> | null>(null);
  const [baseVersion, setBaseVersion] = useState(0);
  const savedRef = useRef<PageDocument | null>(null);
  const [save, setSave] = useState<SaveState>("saved");
  const [saveError, setSaveError] = useState<Issue[] | string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [panel, setPanel] = useState<"layers" | "add" | "pages">("layers");
  const [device, setDevice] = useState(1440);
  const [customW, setCustomW] = useState("");
  const [lint, setLint] = useState<{ errors: Issue[]; lint: LintItem[] }>({ errors: [], lint: [] });
  const [dialog, setDialog] = useState<null | "publish" | "settings" | "lint" | "revisions" | "more">(null);
  const typing = useTyping();

  // Load / reload the server draft into the editor.
  const load = useCallback((d: PageDetail) => {
    setHist(initHistory(d.document));
    savedRef.current = d.document;
    setBaseVersion(d.draft_version);
    setSave("saved");
    setSaveError(null);
    setLint({ errors: [], lint: d.lint });
  }, []);
  useEffect(() => {
    if (page.data && (!hist || page.data.id !== pageId)) load(page.data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page.data, pageId]);
  useEffect(() => {
    setHist(null);
    setSelected(null);
    setReady(false);
  }, [pageId]);

  const doc = hist?.present ?? null;
  const defs = reg.data?.byType ?? ({} as Record<string, ComponentDef>);
  const selNode = doc && selected ? T.find(doc.root, selected) : null;
  const locked = doc && selected ? T.isLocked(doc.root, selected) : false;

  const commit = useCallback((next: Node | null, key: string | null = null, select?: string | null) => {
    if (!next) return false;
    setHist((h) => (h ? push(h, { ...h.present, root: next }, key) : h));
    if (select !== undefined) setSelected(select);
    return true;
  }, []);

  /* -------------------------------------------------- autosave with optimistic concurrency */
  const saving = useRef(false);
  const doSave = useCallback(async () => {
    if (!hist || saving.current) return;
    const sending = hist.present;
    if (sending === savedRef.current) return;
    saving.current = true;
    setSave("saving");
    try {
      const res = await put<PageDetail>(`/api/content/pages/${pageId}/draft`, { base_version: baseVersion, document: sending });
      savedRef.current = sending;
      setBaseVersion(res.draft_version);
      setSave((s) => (s === "saving" ? "saved" : s));
      setSaveError(null);
      qc.setQueryData(["content-page", pageId], (old: PageDetail | undefined) => (old ? { ...old, ...res, document: res.document } : res));
      qc.invalidateQueries({ queryKey: ["content-pages"] });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setSave("conflict");
      else {
        setSave("error");
        const det = e instanceof ApiError ? (e.details as { errors?: Issue[] } | null) : null;
        setSaveError(det?.errors ?? errorMessage(e));
      }
    } finally {
      saving.current = false;
    }
  }, [hist, baseVersion, pageId, qc]);

  useEffect(() => {
    if (!hist || hist.present === savedRef.current || save === "conflict") return;
    if (save === "saved") setSave("dirty");
    const t = setTimeout(() => void doSave(), 1200);
    return () => clearTimeout(t);
  }, [hist, save, doSave]);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (save === "dirty" || save === "saving" || save === "conflict") e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [save]);

  /* -------------------------------------------------- live validation + lint */
  useEffect(() => {
    if (!doc) return;
    const t = setTimeout(async () => {
      try {
        const r = await post<{ errors: Issue[]; lint: LintItem[] }>("/api/content/validate", { document: doc });
        setLint({ errors: r.errors, lint: r.lint });
      } catch {
        /* lint is advisory; save errors surface separately */
      }
    }, 900);
    return () => clearTimeout(t);
  }, [doc]);

  /* -------------------------------------------------- preview iframe bridge */
  const frame = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const send = useCallback((m: StudioMessage) => frame.current?.contentWindow?.postMessage(m, window.location.origin), []);
  useEffect(() => {
    const onMsg = (e: MessageEvent<PreviewMessage>) => {
      if (e.origin !== window.location.origin || e.source !== frame.current?.contentWindow) return;
      const m = e.data;
      if (m?.type === "voltora:ready") setReady(true);
      else if (m?.type === "voltora:click") {
        setSelected(m.id);
        setPanel("layers");
      } else if (m?.type === "voltora:hover") setHover(m.id);
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);
  useEffect(() => {
    if (ready && doc && page.data) send({ type: "voltora:doc", doc, kind: page.data.kind, slug: page.data.slug });
  }, [ready, doc, page.data, send]);
  useEffect(() => {
    if (ready) send({ type: "voltora:select", id: selected });
  }, [ready, selected, send]);
  useEffect(() => {
    if (selected) document.getElementById(`layer-${selected}`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  /* -------------------------------------------------- operations */
  const op = {
    remove: (id: string) => {
      if (!doc || id === doc.root.id || T.isLocked(doc.root, id)) return;
      const loc = T.locate(doc.root, id);
      const next = T.remove(doc.root, id);
      const sib = loc?.[0]?.children?.[loc[1] + 1]?.id ?? loc?.[0]?.children?.[loc[1] - 1]?.id ?? loc?.[0]?.id ?? null;
      commit(next, null, sib);
    },
    duplicate: (id: string) => {
      if (!doc || id === doc.root.id) return;
      const r = T.duplicate(doc.root, id);
      if (r) commit(r.root, null, r.id);
    },
    moveBy: (id: string, d: -1 | 1) => doc && !T.isLocked(doc.root, id) && commit(T.moveBy(doc.root, id, d)),
    indent: (id: string) => doc && !T.isLocked(doc.root, id) && (commit(T.indent(doc.root, id, defs)) || toast("Can't indent here — the previous item doesn't accept this component.", "danger")),
    outdent: (id: string) => doc && !T.isLocked(doc.root, id) && (commit(T.outdent(doc.root, id, defs)) || toast("Can't outdent here.", "danger")),
    move: (id: string, parent: string, index: number) => {
      if (!doc || T.isLocked(doc.root, id)) return;
      if (!commit(T.move(doc.root, id, parent, index, defs))) toast(`${defs[T.find(doc.root, id)?.type ?? ""]?.label ?? "That"} can't be placed there.`, "danger");
    },
    insertType: (type: string, parent?: string, index?: number) => {
      if (!doc || !defs[type]) return;
      const at = parent !== undefined && index !== undefined ? { parent, index } : T.insertionPoint(doc.root, selected, type, defs);
      const host = at ? T.find(doc.root, at.parent) : null;
      if (!at || !host || !T.accepts(defs[host.type], type)) return toast(`${defs[type]!.label} can't be placed there.`, "danger");
      const node = T.create(defs[type]!, defs);
      commit(T.insert(doc.root, at.parent, at.index, node), null, node.id);
      setPanel("layers");
    },
    toggleHidden: (id: string) => doc && commit(T.update(doc.root, id, (n) => {
      const v = { ...(n.visibility ?? {}) };
      if (v.hidden) delete v.hidden;
      else v.hidden = true;
      return { ...n, visibility: Object.keys(v).length ? v : undefined };
    })),
    toggleLock: (id: string) => doc && commit(T.update(doc.root, id, (n) => {
      const a = { ...(n.advanced ?? {}) };
      if (a.locked) delete a.locked;
      else a.locked = true;
      return { ...n, advanced: Object.keys(a).length ? a : undefined };
    })),
  };

  const canInsert = (type: string) => !!doc && !!T.insertionPoint(doc.root, selected, type, defs);

  /* -------------------------------------------------- keyboard */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "z") {
        if (typing()) return;
        e.preventDefault();
        setHist((h) => (h ? (e.shiftKey ? redo(h) : undo(h)) : h));
      } else if (mod && e.key.toLowerCase() === "y") {
        if (typing()) return;
        e.preventDefault();
        setHist((h) => (h ? redo(h) : h));
      } else if (mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void doSave();
      } else if (!typing() && selected) {
        if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); op.remove(selected); }
        else if (mod && e.key.toLowerCase() === "d") { e.preventDefault(); op.duplicate(selected); }
        else if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); op.moveBy(selected, -1); }
        else if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); op.moveBy(selected, 1); }
        else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); op.indent(selected); }
        else if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); op.outdent(selected); }
        else if (e.key === "Escape" && doc) {
          const loc = T.locate(doc.root, selected);
          setSelected(loc?.[0]?.id ?? null);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const onLayersKey = (e: RKeyboardEvent) => {
    if (!doc || e.altKey || e.metaKey || e.ctrlKey) return;
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
    const order: string[] = [];
    const walk = (n: Node) => { order.push(n.id); n.children?.forEach(walk); };
    walk(doc.root);
    const i = selected ? order.indexOf(selected) : -1;
    const j = e.key === "Home" ? 0 : e.key === "End" ? order.length - 1 : Math.max(0, Math.min(order.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
    e.preventDefault();
    setSelected(order[j]!);
    requestAnimationFrame(() => document.getElementById(`layer-${order[j]}`)?.focus());
  };

  /* -------------------------------------------------- canvas sizing */
  const stage = useRef<HTMLDivElement>(null);
  const [stageSize, setStageSize] = useState({ w: 1000, h: 700 });
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => e && setStageSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  });
  const width = device;
  const scale = Math.min(1, (stageSize.w - 32) / width);
  const frameH = Math.max(400, (stageSize.h - 32) / scale);

  const pageLinks = useMemo(() => {
    const base = [{ label: "Shop", href: "/shop" }, { label: "New arrivals", href: "/shop?sort=newest" }, { label: "Search", href: "/search" }, { label: "Track order", href: "/track" }, { label: "Account", href: "/account" }];
    const ps = (pages.data?.items ?? []).filter((p) => p.kind === "page" && p.path).map((p) => ({ label: `${p.title}${p.status === "draft" ? " (draft)" : ""}`, href: p.path! }));
    return [...base, ...ps];
  }, [pages.data]);

  if (page.isError) {
    return (
      <div className="st-fatal">
        <Alert tone="danger" title="Couldn't open this page">{errorMessage(page.error)}</Alert>
        <Link className="v-btn" to="/admin/design">Back to Website</Link>
      </div>
    );
  }
  if (!doc || !page.data || !reg.data) {
    return (
      <div className="st-shell" aria-busy="true">
        <div className="st-top"><Skeleton h={28} w={320} /></div>
        <div className="st-body"><div className="st-left"><Skeleton h={400} /></div><div className="st-stage" /><div className="st-right"><Skeleton h={400} /></div></div>
      </div>
    );
  }

  const problems = lint.errors.length + lint.lint.filter((l) => l.severity === "error").length;
  const warnings = lint.lint.filter((l) => l.severity === "warning").length;
  const p = page.data;

  return (
    <div className="st-shell">
      {/* ---------------------------------------------------------------- top bar */}
      <header className="st-top">
        <div className="st-top__l">
          <Link to="/admin/design" className="v-btn v-btn--ghost v-btn--icon" aria-label="Back to Website"><ArrowLeft size={18} /></Link>
          <div className="st-title">
            <strong>{p.title}</strong>
            <span className="st-title__meta">
              <span className="v-mono">{p.kind === "page" ? p.path : `${p.kind} · ${p.slug}`}</span>
              <StatusBadge status={p.status === "changed" ? "pending" : p.status} label={p.status === "changed" ? "unpublished changes" : p.status} />
              {p.needs_review ? <Badge tone="warning">template · review</Badge> : null}
            </span>
          </div>
        </div>
        <div className="st-top__c">
          <div className="st-devices" role="group" aria-label="Preview width">
            {DEVICES.map((d) => {
              const I = d.icon;
              return (
                <button key={d.w} type="button" aria-pressed={device === d.w} title={d.label} onClick={() => { setDevice(d.w); setCustomW(""); }}>
                  <I size={15} /><span className="v-num">{d.w}</span>
                </button>
              );
            })}
            <input aria-label="Custom width in pixels" placeholder="custom" inputMode="numeric" value={customW} onChange={(e) => {
              setCustomW(e.target.value.replace(/\D/g, ""));
              const n = Number(e.target.value);
              if (n >= 280 && n <= 2560) setDevice(n);
            }} />
          </div>
        </div>
        <div className="st-top__r">
          <span className="st-save" data-state={save} role="status" aria-live="polite">
            {save === "saved" ? <><CheckCircle2 size={14} /> Draft saved</> : save === "saving" ? <>Saving…</> : save === "dirty" ? <><CircleDot size={14} /> Unsaved</> : save === "conflict" ? <><AlertTriangle size={14} /> Conflict</> : <><AlertTriangle size={14} /> Not saved</>}
          </span>
          <Button variant="ghost" icon aria-label="Undo (Ctrl+Z)" title="Undo" disabled={!hist?.past.length} onClick={() => setHist((h) => (h ? undo(h) : h))}><Undo2 size={16} /></Button>
          <Button variant="ghost" icon aria-label="Redo (Ctrl+Shift+Z)" title="Redo" disabled={!hist?.future.length} onClick={() => setHist((h) => (h ? redo(h) : h))}><Redo2 size={16} /></Button>
          <Button variant="ghost" size="sm" onClick={() => setDialog("lint")} aria-label={`Checks: ${problems} problems, ${warnings} warnings`}>
            {problems ? <Badge tone="danger">{problems}</Badge> : warnings ? <Badge tone="warning">{warnings}</Badge> : <Badge tone="success">✓</Badge>} Checks
          </Button>
          <Button variant="ghost" icon aria-label="Revision history" title="Revisions" onClick={() => setDialog("revisions")}><HistoryIcon size={16} /></Button>
          <Button variant="ghost" icon aria-label="Page settings" title="Page settings" onClick={() => setDialog("settings")}><Settings2 size={16} /></Button>
          <Button variant="ghost" icon aria-label="More: import, export, share preview" title="More" onClick={() => setDialog("more")}><MoreHorizontal size={16} /></Button>
          <Button variant="primary" onClick={() => setDialog("publish")} disabled={!canPublish} title={canPublish ? "Publish" : "Publishing needs the content.publish permission"}>Publish</Button>
        </div>
      </header>

      {save === "conflict" ? (
        <div className="st-banner" role="alert">
          <AlertTriangle size={16} />
          <span>Someone else saved this page while you were editing. Your changes are kept here but not saved.</span>
          <Button size="sm" onClick={() => download(`${p.slug}-my-draft.json`, { format: "voltora.page", formatVersion: 1, kind: p.kind, slug: p.slug, title: p.title, seo: p.seo, document: doc })}>Download my copy</Button>
          <Button size="sm" onClick={async () => { const fresh = await get<PageDetail>(`/api/content/pages/${pageId}`); qc.setQueryData(["content-page", pageId], fresh); load(fresh); toast("Loaded the latest saved draft."); }}>Load latest (discard mine)</Button>
          <Button size="sm" variant="danger" onClick={async () => { const fresh = await get<PageDetail>(`/api/content/pages/${pageId}`); setBaseVersion(fresh.draft_version); savedRef.current = null; setSave("dirty"); }}>Keep mine (overwrite)</Button>
        </div>
      ) : save === "error" && saveError ? (
        <div className="st-banner st-banner--error" role="alert">
          <AlertTriangle size={16} />
          <span>{typeof saveError === "string" ? saveError : `${saveError.length} problem(s) prevent saving: ${saveError[0]?.message ?? ""}`}</span>
          <Button size="sm" onClick={() => setDialog("lint")}>Show problems</Button>
          <Button size="sm" onClick={() => void doSave()}>Retry</Button>
        </div>
      ) : null}

      <div className="st-body">
        {/* ---------------------------------------------------------------- left */}
        <aside className="st-left" aria-label="Structure">
          <div className="st-ptabs" role="tablist">
            <button role="tab" aria-selected={panel === "layers"} onClick={() => setPanel("layers")}><LayersIcon size={14} /> Layers</button>
            <button role="tab" aria-selected={panel === "add"} onClick={() => setPanel("add")}><Plus size={14} /> Add</button>
            <button role="tab" aria-selected={panel === "pages"} onClick={() => setPanel("pages")}><FileText size={14} /> Pages</button>
          </div>
          {panel === "layers" ? (
            <>
              <div className="st-layer-tools" role="toolbar" aria-label="Selected layer">
                <Button size="sm" variant="ghost" icon aria-label="Move up (Alt+↑)" title="Move up" disabled={!selected || locked} onClick={() => selected && op.moveBy(selected, -1)}><ArrowUp size={14} /></Button>
                <Button size="sm" variant="ghost" icon aria-label="Move down (Alt+↓)" title="Move down" disabled={!selected || locked} onClick={() => selected && op.moveBy(selected, 1)}><ArrowDown size={14} /></Button>
                <Button size="sm" variant="ghost" icon aria-label="Outdent (Alt+←)" title="Outdent" disabled={!selected || locked} onClick={() => selected && op.outdent(selected)}><ArrowLeftToLine size={14} /></Button>
                <Button size="sm" variant="ghost" icon aria-label="Indent into previous (Alt+→)" title="Indent" disabled={!selected || locked} onClick={() => selected && op.indent(selected)}><ArrowRightToLine size={14} /></Button>
                <Button size="sm" variant="ghost" icon aria-label="Duplicate (Ctrl+D)" title="Duplicate" disabled={!selected || selected === doc.root.id} onClick={() => selected && op.duplicate(selected)}><Copy size={14} /></Button>
                <Button size="sm" variant="ghost" icon aria-label="Delete (Del)" title="Delete" disabled={!selected || selected === doc.root.id || locked} onClick={() => selected && op.remove(selected)}><Trash2 size={14} /></Button>
                <span className="st-count v-num" title="Components in this document">{T.count(doc.root)}/{reg.data.limits.max_nodes}</span>
              </div>
              <Layers root={doc.root} defs={defs} selected={selected} hover={hover} onSelect={setSelected} onHover={(id) => { setHover(id); }} onMove={op.move} onInsertType={(t, par, i) => op.insertType(t, par, i)} onToggleHidden={op.toggleHidden} onToggleLock={op.toggleLock} onKey={onLayersKey} />
            </>
          ) : panel === "add" ? (
            <Library components={reg.data.components} canInsert={canInsert} onInsert={(t) => op.insertType(t)} />
          ) : (
            <nav className="st-pages" aria-label="Pages">
              {(["page", "global", "template", "symbol"] as const).map((k) => {
                const list = (pages.data?.items ?? []).filter((x) => x.kind === k);
                if (!list.length) return null;
                return (
                  <section key={k}>
                    <h3 className="st-group">{{ page: "Pages", global: "Global sections", template: "Templates", symbol: "Symbols" }[k]}</h3>
                    {list.map((x) => (
                      <Link key={x.id} to={`/admin/design/studio/${x.id}`} className="st-pagelink" aria-current={x.id === pageId ? "page" : undefined}>
                        <span>{x.title}</span>
                        <StatusBadge status={x.status === "changed" ? "pending" : x.status} label={x.status === "changed" ? "changed" : x.status} />
                      </Link>
                    ))}
                  </section>
                );
              })}
            </nav>
          )}
        </aside>

        {/* ---------------------------------------------------------------- canvas */}
        <main className="st-stage" ref={stage} aria-label="Canvas">
          <div className="st-frame" style={{ width: width * scale, height: frameH * scale }}>
            <iframe
              key={pageId}
              ref={frame}
              title={`Preview of ${p.title} at ${width}px`}
              src={`/__preview/${pageId}`}
              style={{ width, height: frameH, transform: `scale(${scale})` }}
            />
          </div>
          <p className="st-stage__meta v-num">{width}px{scale < 1 ? ` · shown at ${Math.round(scale * 100)}%` : ""} · Alt+click in the canvas to interact</p>
        </main>

        {/* ---------------------------------------------------------------- inspector */}
        <aside className="st-right" aria-label="Inspector">
          {selNode ? (
            <>
              <nav className="st-crumbs" aria-label="Selection path">
                {T.pathTo(doc.root, selNode.id).map((n, i, arr) => (
                  <span key={n.id}>
                    <button type="button" onClick={() => setSelected(n.id)} aria-current={i === arr.length - 1 || undefined}>{n.advanced?.label || defs[n.type]?.label || n.type}</button>
                    {i < arr.length - 1 ? <span aria-hidden>/</span> : null}
                  </span>
                ))}
              </nav>
              <Inspector
                key={selNode.id}
                node={selNode}
                def={defs[selNode.type]}
                registry={reg.data}
                locked={locked}
                onChange={(next, key) => commit(T.update(doc.root, selNode.id, () => next), `${selNode.id}:${key}`)}
                onReplay={() => send({ type: "voltora:doc", doc, kind: p.kind, slug: p.slug, replay: true })}
                pageLinks={pageLinks}
                themeColors={theme.data?.tokens.color ?? {}}
              />
            </>
          ) : (
            <div className="st-inspector st-inspector--empty">
              <h2>Nothing selected</h2>
              <p>Click a component in the canvas or a row in Layers. Use the Add tab to insert components.</p>
              <ul className="st-keys">
                <li><kbd>Ctrl</kbd>+<kbd>Z</kbd> undo · <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> redo</li>
                <li><kbd>Alt</kbd>+<kbd>↑↓</kbd> move · <kbd>Alt</kbd>+<kbd>←→</kbd> outdent/indent</li>
                <li><kbd>Ctrl</kbd>+<kbd>D</kbd> duplicate · <kbd>Del</kbd> delete · <kbd>Esc</kbd> select parent</li>
              </ul>
            </div>
          )}
        </aside>
      </div>

      <PublishDialog open={dialog === "publish"} onClose={() => setDialog(null)} page={p} dirty={save !== "saved"} flush={doSave} baseVersion={baseVersion} lint={lint} onDone={async () => {
        const fresh = await get<PageDetail>(`/api/content/pages/${pageId}`);
        qc.setQueryData(["content-page", pageId], fresh);
        setBaseVersion(fresh.draft_version);
        qc.invalidateQueries({ queryKey: ["content-pages"] });
        qc.invalidateQueries({ queryKey: ["page"] });
        qc.invalidateQueries({ queryKey: ["store-theme"] });
      }} />
      <SettingsDialog open={dialog === "settings"} onClose={() => setDialog(null)} page={p} baseVersion={baseVersion} onSaved={(d) => { qc.setQueryData(["content-page", pageId], d); setBaseVersion(d.draft_version); qc.invalidateQueries({ queryKey: ["content-pages"] }); }} />
      <LintDrawer open={dialog === "lint"} onClose={() => setDialog(null)} lint={lint} saveError={Array.isArray(saveError) ? saveError : []} onPick={(id) => { setSelected(id); setDialog(null); }} defs={defs} doc={doc} />
      <RevisionsDrawer open={dialog === "revisions"} onClose={() => setDialog(null)} pageId={pageId} canPublish={canPublish} baseVersion={baseVersion} onLoaded={(d) => { qc.setQueryData(["content-page", pageId], d); load(d); qc.invalidateQueries({ queryKey: ["content-pages"] }); }} />
      <MoreDialog open={dialog === "more"} onClose={() => setDialog(null)} page={p} doc={doc} baseVersion={baseVersion} onImported={(d) => { qc.setQueryData(["content-page", pageId], d); load(d); }} onNavigate={(id) => nav(`/admin/design/studio/${id}`)} />
    </div>
  );
}

/* ====================================================================== dialogs */
function PublishDialog({ open, onClose, page, dirty, flush, baseVersion, lint, onDone }: { open: boolean; onClose: () => void; page: PageDetail; dirty: boolean; flush: () => Promise<void>; baseVersion: number; lint: { errors: Issue[]; lint: LintItem[] }; onDone: () => Promise<void> }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const toast = useToast();
  useEffect(() => {
    if (!open) return;
    setErr(null);
    get<Diff>(`/api/content/pages/${page.id}/diff`).then(setDiff).catch(() => setDiff(null));
  }, [open, page.id, baseVersion]);
  const blocking = [...lint.errors.map((e) => e.message), ...lint.lint.filter((l) => l.severity === "error").map((l) => l.message)];
  return (
    <Dialog open={open} onClose={onClose} title={`Publish “${page.title}”`} description={page.kind === "global" ? "Global sections appear on every page of the storefront." : `Goes live at ${page.path ?? page.slug}.`}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" loading={busy} disabled={blocking.length > 0} onClick={async () => {
          setBusy(true);
          setErr(null);
          try {
            if (dirty) await flush();
            const fresh = await get<PageDetail>(`/api/content/pages/${page.id}`);
            const r = await post<{ revision: Revision }>(`/api/content/pages/${page.id}/publish`, { message, base_version: fresh.draft_version });
            await onDone();
            toast(`Published revision ${r.revision.number}.`);
            setMessage("");
            onClose();
          } catch (e) {
            const det = e instanceof ApiError ? (e.details as { lint?: LintItem[] } | null) : null;
            setErr(det?.lint ? det.lint.map((l) => l.message).join(" · ") : errorMessage(e));
          } finally {
            setBusy(false);
          }
        }}>Publish now</Button>
      </>}>
      <div className="v-stack">
        {diff ? <p className="v-muted">Changes since the live version: <strong>{diff.text}</strong></p> : null}
        {blocking.length ? <Alert tone="danger" title="Fix before publishing">{blocking.slice(0, 5).join(" · ")}</Alert> : null}
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <Field label="What changed?" hint="Shown in revision history and the audit log.">
          {(id) => <Textarea id={id} rows={3} maxLength={500} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="e.g. New hero copy for Eid collection" />}
        </Field>
      </div>
    </Dialog>
  );
}

function SettingsDialog({ open, onClose, page, baseVersion, onSaved }: { open: boolean; onClose: () => void; page: PageDetail; baseVersion: number; onSaved: (d: PageDetail) => void }) {
  const [title, setTitle] = useState(page.title);
  const [slug, setSlug] = useState(page.slug);
  const [seoT, setSeoT] = useState(page.seo?.title ?? "");
  const [seoD, setSeoD] = useState(page.seo?.description ?? "");
  const [noindex, setNoindex] = useState(!!page.seo?.noindex);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) { setTitle(page.title); setSlug(page.slug); setSeoT(page.seo?.title ?? ""); setSeoD(page.seo?.description ?? ""); setNoindex(!!page.seo?.noindex); setErr(null); }
  }, [open, page]);
  const lockedSlug = page.kind === "global" || page.slug === "home";
  return (
    <Dialog open={open} onClose={onClose} title="Page settings" footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" loading={busy} onClick={async () => {
        setBusy(true);
        setErr(null);
        try {
          const d = await put<PageDetail>(`/api/content/pages/${page.id}/draft`, { base_version: baseVersion, title, ...(lockedSlug ? {} : { slug }), seo: { title: seoT, description: seoD, noindex } });
          onSaved(d);
          onClose();
        } catch (e) {
          setErr(errorMessage(e));
        } finally {
          setBusy(false);
        }
      }}>Save settings</Button>
    </>}>
      <div className="v-stack">
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <Field label="Title">{(id) => <Input id={id} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />}</Field>
        <Field label="Address" hint={lockedSlug ? "The home page and global sections keep a fixed address." : "Lowercase letters, digits and dashes. Changes apply when published."}>
          {(id) => <Input id={id} value={slug} disabled={lockedSlug} onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))} />}
        </Field>
        <Field label="Search title" hint={`${seoT.length}/120 — leave empty to use the page title.`}>{(id) => <Input id={id} value={seoT} maxLength={120} onChange={(e) => setSeoT(e.target.value)} />}</Field>
        <Field label="Search description" hint={`${seoD.length}/320`}>{(id) => <Textarea id={id} rows={3} value={seoD} maxLength={320} onChange={(e) => setSeoD(e.target.value)} />}</Field>
        <label className="v-check"><input type="checkbox" checked={noindex} onChange={(e) => setNoindex(e.target.checked)} /> Hide from search engines (noindex)</label>
      </div>
    </Dialog>
  );
}

function LintDrawer({ open, onClose, lint, saveError, onPick, defs, doc }: { open: boolean; onClose: () => void; lint: { errors: Issue[]; lint: LintItem[] }; saveError: Issue[]; onPick: (id: string) => void; defs: Record<string, ComponentDef>; doc: PageDocument }) {
  const all = [
    ...saveError.map((e) => ({ node: e.node, severity: "error" as const, message: e.message, code: e.code })),
    ...lint.errors.filter((e) => !saveError.some((s) => s.path === e.path && s.node === e.node)).map((e) => ({ node: e.node, severity: "error" as const, message: e.message, code: e.code })),
    ...lint.lint,
  ];
  return (
    <Drawer open={open} onClose={onClose} title="Checks" width={420}>
      <p className="v-muted" style={{ marginBottom: 12 }}>Validation runs as you edit. Errors block publishing; warnings are advice (heading order, empty links, contrast, past countdowns).</p>
      {!all.length ? <Alert tone="success">No problems found.</Alert> : (
        <ul className="st-lint">
          {all.map((l, i) => {
            const n = l.node ? T.find(doc.root, l.node) : null;
            return (
              <li key={i} data-sev={l.severity}>
                <Badge tone={l.severity === "error" ? "danger" : "warning"}>{l.severity}</Badge>
                <div>
                  <p>{l.message}</p>
                  {n ? <button type="button" className="v-btn v-btn--sm v-btn--ghost" onClick={() => onPick(n.id)}>Select {n.advanced?.label || defs[n.type]?.label || n.type}</button> : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Drawer>
  );
}

function RevisionsDrawer({ open, onClose, pageId, canPublish, baseVersion, onLoaded }: { open: boolean; onClose: () => void; pageId: string; canPublish: boolean; baseVersion: number; onLoaded: (d: PageDetail) => void }) {
  const q = useQuery({ queryKey: ["content-revisions", pageId], queryFn: () => get<{ items: Revision[] }>(`/api/content/pages/${pageId}/revisions`), enabled: open });
  const [diff, setDiff] = useState<{ n: number; d: Diff } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useToast();
  const qc = useQueryClient();
  const act = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try {
      await fn();
    } catch (e) {
      toast(errorMessage(e), "danger");
    } finally {
      setBusy(null);
    }
  };
  return (
    <Drawer open={open} onClose={onClose} title="Revision history" width={460}>
      {q.isLoading ? <Skeleton h={200} /> : q.isError ? <Alert tone="danger">{errorMessage(q.error)}</Alert> : !q.data?.items.length ? <p className="v-muted">No revisions yet. Publishing creates the first one.</p> : (
        <ol className="st-revs">
          {q.data.items.map((r) => (
            <li key={r.number} data-live={r.live || undefined}>
              <div className="st-revs__head">
                <strong className="v-num">#{r.number}</strong>
                <Badge tone={r.kind === "publish" || r.kind === "seed" ? "success" : r.kind === "rollback" ? "warning" : "neutral"}>{r.kind}</Badge>
                {r.live ? <Badge tone="signal">live</Badge> : null}
                <span className="v-muted" style={{ marginLeft: "auto" }}>{relTime(r.created_at)}</span>
              </div>
              <p>{r.message || "—"}</p>
              <p className="v-muted st-small">{r.author} · {r.diff?.text ?? ""}{r.source_number ? ` · copy of #${r.source_number}` : ""}</p>
              <div className="v-row v-row--wrap">
                <Button size="sm" variant="ghost" onClick={() => act(`d${r.number}`, async () => setDiff({ n: r.number, d: await get<Diff>(`/api/content/pages/${pageId}/diff?from=${r.number}`) }))} loading={busy === `d${r.number}`}>Compare with draft</Button>
                <Button size="sm" variant="ghost" onClick={() => act(`r${r.number}`, async () => {
                  const d = await post<PageDetail>(`/api/content/pages/${pageId}/restore`, { number: r.number, base_version: baseVersion });
                  onLoaded(d);
                  toast(`Draft replaced with revision #${r.number}. Nothing is live until you publish.`);
                })} loading={busy === `r${r.number}`}>Restore to draft</Button>
                {!r.live ? (
                  <Button size="sm" variant="danger" disabled={!canPublish} title={canPublish ? undefined : "Needs content.publish"} loading={busy === `b${r.number}`} onClick={() => act(`b${r.number}`, async () => {
                    if (!window.confirm(`Make revision #${r.number} live now? This publishes a copy as a new revision.`)) return;
                    const res = await post<{ page: PageDetail }>(`/api/content/pages/${pageId}/rollback`, { number: r.number, message: `Rolled back to #${r.number}` });
                    onLoaded(res.page);
                    await qc.invalidateQueries({ queryKey: ["content-revisions", pageId] });
                    qc.invalidateQueries({ queryKey: ["page"] });
                    qc.invalidateQueries({ queryKey: ["store-theme"] });
                    toast(`Revision #${r.number} is live again.`);
                  })}>Roll back live</Button>
                ) : null}
              </div>
              {diff?.n === r.number ? (
                <div className="st-diff">
                  <p><strong>Draft vs #{r.number}:</strong> {diff.d.text}</p>
                  {diff.d.added.length ? <p>Added: {diff.d.added.map((x) => x.type).join(", ")}</p> : null}
                  {diff.d.removed.length ? <p>Removed: {diff.d.removed.map((x) => x.type).join(", ")}</p> : null}
                  {diff.d.changed.length ? <p>Changed: {diff.d.changed.map((x) => `${x.type} (${x.fields.join("/")})`).join(", ")}</p> : null}
                </div>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </Drawer>
  );
}

function MoreDialog({ open, onClose, page, doc, baseVersion, onImported, onNavigate }: { open: boolean; onClose: () => void; page: PageDetail; doc: PageDocument; baseVersion: number; onImported: (d: PageDetail) => void; onNavigate: (id: string) => void }) {
  const [link, setLink] = useState<{ url: string; expires_at: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [dupSlug, setDupSlug] = useState("");
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) { setErr(null); setLink(null); setDupSlug(`${page.slug}-copy`); } }, [open, page.slug]);
  return (
    <Dialog open={open} onClose={onClose} title="Share, import & export" wide>
      <div className="v-stack">
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <section className="v-stack v-stack--sm">
          <h3 className="st-group">Preview link</h3>
          <p className="v-muted">A signed link to the saved draft that works without signing in. It expires after 30 minutes.</p>
          <div className="v-row">
            <Button onClick={async () => {
              try {
                const r = await post<{ url: string; expires_at: string }>(`/api/content/pages/${page.id}/preview-token`);
                setLink({ url: `${window.location.origin}${r.url}`, expires_at: r.expires_at });
              } catch (e) { setErr(errorMessage(e)); }
            }}>Create preview link</Button>
            {link ? <a className="v-btn v-btn--ghost" href={link.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Open</a> : null}
          </div>
          {link ? <Input readOnly value={link.url} onFocus={(e) => e.currentTarget.select()} aria-label="Preview link" /> : null}
        </section>
        <section className="v-stack v-stack--sm">
          <h3 className="st-group">Export / import</h3>
          <div className="v-row v-row--wrap">
            <Button onClick={() => download(`${page.kind}-${page.slug}.voltora-page.json`, { format: "voltora.page", formatVersion: 1, kind: page.kind, slug: page.slug, title: page.title, seo: page.seo, document: doc })}><Download size={14} /> Export draft JSON</Button>
            <Button onClick={() => file.current?.click()}><Upload size={14} /> Import into this draft…</Button>
            <input ref={file} type="file" accept="application/json,.json" hidden onChange={async (e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (!f) return;
              try {
                const data = await readJsonFile(f);
                const d = await post<PageDetail>("/api/content/pages/import", { data, page_id: page.id, base_version: baseVersion });
                onImported(d);
                toast("Imported. The draft was replaced; publish when ready.");
                onClose();
              } catch (ex) { setErr(ex instanceof SyntaxError ? "That file is not valid JSON." : errorMessage(ex)); }
            }} />
          </div>
          <p className="v-muted">Imports are validated against the component registry; unknown components or unsafe values are rejected with details.</p>
        </section>
        {page.kind !== "global" ? (
          <section className="v-stack v-stack--sm">
            <h3 className="st-group">Duplicate as a new page</h3>
            <div className="v-row">
              <Input aria-label="New page address" value={dupSlug} onChange={(e) => setDupSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))} />
              <Button onClick={async () => {
                try {
                  const d = await post<PageDetail>("/api/content/pages", { kind: page.kind, slug: dupSlug, title: `${page.title} (copy)`, document: doc });
                  onClose();
                  onNavigate(d.id);
                } catch (e) { setErr(errorMessage(e)); }
              }}><Copy size={14} /> Duplicate</Button>
            </div>
          </section>
        ) : null}
      </div>
    </Dialog>
  );
}

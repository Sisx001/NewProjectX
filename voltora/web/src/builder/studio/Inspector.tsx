import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Bold, Code2, Italic, Link2, List, Lock, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { get, post, errorMessage } from "@/lib/api";
import { Alert, Badge, Button, Checkbox, Input, Segmented, Select, Switch, Textarea } from "@/ui";
import type { ComponentDef, Motion, Node, PropDef, Registry, StyleMap, Visibility } from "../types";
import { sanitizeHtml } from "../render/sanitize";
import { contrast } from "../contrast";

type Tab = "content" | "style" | "visibility" | "motion" | "advanced";
type Bp = "base" | "tablet" | "mobile";

export type InspectorProps = {
  node: Node;
  def: ComponentDef | undefined;
  registry: Registry;
  locked: boolean;
  onChange: (next: Node, key: string) => void;
  onReplay: () => void;
  pageLinks: { label: string; href: string }[];
  themeColors: Record<string, string>;
};

function Row({ label, hint, children, htmlFor }: { label: ReactNode; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="st-field">
      <label className="st-label" htmlFor={htmlFor}>{label}</label>
      {children}
      {hint ? <p className="st-hint">{hint}</p> : null}
    </div>
  );
}

/* -------------------------------------------------------------- data pickers */
type Cat = { id: string; name: string; slug: string; parent_id: string | null };
const useCats = () => useQuery({ queryKey: ["categories"], queryFn: () => get<{ items: Cat[] }>("/api/store/categories"), staleTime: 60_000, retry: false });
const useProductOptions = () => useQuery({ queryKey: ["studio-products"], queryFn: () => get<{ items: { slug: string; title: string }[] }>("/api/store/products?page_size=60&sort=title"), staleTime: 60_000, retry: false });

function RichEditor({ id, value, onChange }: { id: string; value: string; onChange: (v: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [raw, setRaw] = useState(false);
  useEffect(() => {
    if (ref.current && document.activeElement !== ref.current && ref.current.innerHTML !== value) ref.current.innerHTML = sanitizeHtml(value);
  }, [value, raw]);
  const exec = (cmd: string, arg?: string) => {
    ref.current?.focus();
    // execCommand is deprecated but remains the only dependency-free way to toggle inline formatting in contentEditable.
    document.execCommand(cmd, false, arg);
    onChange(sanitizeHtml(ref.current?.innerHTML ?? ""));
  };
  return (
    <div className="st-rich">
      <div className="st-rich__bar" role="toolbar" aria-label="Formatting">
        <button type="button" onClick={() => exec("bold")} aria-label="Bold" disabled={raw}><Bold size={14} /></button>
        <button type="button" onClick={() => exec("italic")} aria-label="Italic" disabled={raw}><Italic size={14} /></button>
        <button type="button" onClick={() => exec("insertUnorderedList")} aria-label="Bulleted list" disabled={raw}><List size={14} /></button>
        <button type="button" disabled={raw} aria-label="Link" onClick={() => {
          const href = window.prompt("Link address (https://… or /page)");
          if (href) exec("createLink", href);
        }}><Link2 size={14} /></button>
        <button type="button" onClick={() => exec("formatBlock", "h3")} disabled={raw} aria-label="Subheading">H</button>
        <span style={{ flex: 1 }} />
        <button type="button" aria-pressed={raw} onClick={() => setRaw((v) => !v)} aria-label="Edit HTML"><Code2 size={14} /></button>
      </div>
      {raw ? (
        <Textarea id={id} rows={8} value={value} onChange={(e) => onChange(e.target.value)} className="st-mono" />
      ) : (
        <div id={id} ref={ref} className="st-rich__area" contentEditable suppressContentEditableWarning role="textbox" aria-multiline="true" onInput={() => onChange(sanitizeHtml(ref.current?.innerHTML ?? ""))} />
      )}
    </div>
  );
}

function ImageField({ id, value, onChange }: { id: string; value: { url?: string; alt?: string; decorative?: boolean } | null; onChange: (v: unknown) => void }) {
  const v = value ?? {};
  const missingAlt = !!v.url && !v.alt?.trim() && !v.decorative;
  return (
    <div className="st-stack">
      <Input id={id} placeholder="https://… or /media/file.jpg" value={v.url ?? ""} onChange={(e) => onChange({ ...v, url: e.target.value })} />
      {v.url ? <img src={v.url} alt="" className="st-thumb" /> : null}
      <Input aria-label="Alt text" placeholder="Alt text — describe the image" value={v.alt ?? ""} onChange={(e) => onChange({ ...v, alt: e.target.value })} aria-invalid={missingAlt || undefined} />
      <Checkbox checked={!!v.decorative} onChange={(d) => onChange({ ...v, decorative: d || undefined })} label="Decorative (no alt needed)" />
      {missingAlt ? <p className="st-hint st-hint--warn">Alt text is required to publish.</p> : null}
    </div>
  );
}

function PropField({ def, value, onChange, pageLinks, depth = 0 }: { def: PropDef; value: unknown; onChange: (v: unknown) => void; pageLinks: { label: string; href: string }[]; depth?: number }) {
  const id = useId();
  const cats = useCats();
  const products = useProductOptions();
  const listId = `${id}-links`;
  const hint = def.help || (def.bindable ? "Supports {{store.name}}, {{store.tagline}}, {{settings.store.support_phone}}, {{year}}" : undefined);
  let control: ReactNode;
  switch (def.type) {
    case "text":
      control = (def.max ?? 0) > 200 ? <Textarea id={id} rows={3} value={String(value ?? "")} maxLength={def.max} onChange={(e) => onChange(e.target.value)} /> : <Input id={id} value={String(value ?? "")} maxLength={def.max} onChange={(e) => onChange(e.target.value)} />;
      break;
    case "richtext":
      control = <RichEditor id={id} value={String(value ?? "")} onChange={onChange} />;
      break;
    case "number":
      control = <Input id={id} type="number" inputMode="numeric" min={def.min} max={def.max} value={value === undefined || value === null ? "" : String(value)} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />;
      break;
    case "boolean":
      return (
        <div className="st-field st-field--inline">
          <span className="st-label">{def.label}</span>
          <Switch checked={!!value} onChange={onChange} label={def.label} />
        </div>
      );
    case "select":
      control = (def.options?.length ?? 0) <= 3 && def.options!.every((o) => o.length < 9) ? (
        <Segmented label={def.label} value={String(value ?? def.default ?? def.options![0])} onChange={onChange} items={def.options!.map((o) => ({ value: o, label: o }))} />
      ) : (
        <Select id={id} value={String(value ?? def.default ?? "")} onChange={(e) => onChange(e.target.value)}>
          {def.options!.map((o) => <option key={o} value={o}>{o}</option>)}
        </Select>
      );
      break;
    case "color":
      control = (
        <div className="v-row">
          <input type="color" aria-label={`${def.label} picker`} value={/^#[0-9a-f]{6}$/i.test(String(value)) ? String(value) : "#000000"} onChange={(e) => onChange(e.target.value)} />
          <Input id={id} value={String(value ?? "")} placeholder="#1a1a1a or var(--t-primary)" onChange={(e) => onChange(e.target.value)} />
        </div>
      );
      break;
    case "date":
      control = <Input id={id} type="datetime-local" value={value ? String(value).slice(0, 16) : ""} onChange={(e) => onChange(e.target.value ? new Date(e.target.value).toISOString() : undefined)} />;
      break;
    case "link":
      control = (
        <>
          <Input id={id} list={listId} value={String(value ?? "")} placeholder="/shop, /pages/about, https://…" onChange={(e) => onChange(e.target.value)} />
          <datalist id={listId}>{pageLinks.map((l) => <option key={l.href} value={l.href}>{l.label}</option>)}</datalist>
        </>
      );
      break;
    case "image":
      control = <ImageField id={id} value={value as never} onChange={onChange} />;
      break;
    case "category":
      control = (
        <Select id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          <option value="">All products</option>
          {(cats.data?.items ?? []).map((c) => <option key={c.id} value={c.slug}>{c.parent_id ? "— " : ""}{c.name}</option>)}
          {value && !cats.data?.items.some((c) => c.slug === value) ? <option value={String(value)}>{String(value)} (not found)</option> : null}
        </Select>
      );
      break;
    case "product":
      control = (
        <>
          <Input id={id} list={`${id}-p`} value={String(value ?? "")} placeholder="product-slug" onChange={(e) => onChange(e.target.value)} />
          <datalist id={`${id}-p`}>{(products.data?.items ?? []).map((p) => <option key={p.slug} value={p.slug}>{p.title}</option>)}</datalist>
        </>
      );
      break;
    case "list":
      return <ListField def={def} value={(value as Record<string, unknown>[]) ?? []} onChange={onChange} pageLinks={pageLinks} depth={depth} />;
  }
  return <Row label={<>{def.label}{def.required ? <span aria-hidden> *</span> : null}</>} hint={hint} htmlFor={id}>{control}</Row>;
}

function ListField({ def, value, onChange, pageLinks, depth }: { def: PropDef; value: Record<string, unknown>[]; onChange: (v: unknown) => void; pageLinks: { label: string; href: string }[]; depth: number }) {
  const [open, setOpen] = useState<number | null>(null);
  const full = def.max_items !== undefined && value.length >= def.max_items;
  const set = (i: number, item: Record<string, unknown>) => onChange(value.map((v, j) => (j === i ? item : v)));
  const swap = (i: number, j: number) => {
    const next = [...value];
    [next[i], next[j]] = [next[j]!, next[i]!];
    onChange(next);
    setOpen(j);
  };
  const summary = (item: Record<string, unknown>, i: number) => String(item.label ?? item.title ?? item.q ?? item.text ?? (item.image as { alt?: string })?.alt ?? `Item ${i + 1}`) || `Item ${i + 1}`;
  return (
    <fieldset className="st-list" data-depth={depth}>
      <legend className="st-label">{def.label} <span className="v-muted v-num">{value.length}{def.max_items ? `/${def.max_items}` : ""}</span></legend>
      {value.map((item, i) => (
        <div key={i} className="st-list__item" data-open={open === i || undefined}>
          <div className="st-list__head">
            <button type="button" className="st-list__toggle" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>{summary(item, i)}</button>
            <button type="button" aria-label="Move up" disabled={i === 0} onClick={() => swap(i, i - 1)}><ArrowUp size={13} /></button>
            <button type="button" aria-label="Move down" disabled={i === value.length - 1} onClick={() => swap(i, i + 1)}><ArrowDown size={13} /></button>
            <button type="button" aria-label="Remove item" onClick={() => onChange(value.filter((_, j) => j !== i))}><X size={13} /></button>
          </div>
          {open === i ? (
            <div className="st-list__body">
              {(def.fields ?? []).map((f) => <PropField key={f.name} def={f} value={item[f.name]} onChange={(v) => set(i, { ...item, [f.name]: v })} pageLinks={pageLinks} depth={depth + 1} />)}
            </div>
          ) : null}
        </div>
      ))}
      <Button size="sm" variant="ghost" disabled={full} onClick={() => {
        const blank: Record<string, unknown> = {};
        for (const f of def.fields ?? []) if (f.default !== undefined && f.default !== null) blank[f.name] = structuredClone(f.default);
        onChange([...value, blank]);
        setOpen(value.length);
      }}><Plus size={14} /> Add {def.label.toLowerCase().replace(/s$/, "")}</Button>
    </fieldset>
  );
}

/* -------------------------------------------------------------- style */
const CURATED: Record<string, string[]> = {
  spacing: ["padding", "margin-top", "margin-bottom", "gap"],
  size: ["max-width", "min-height", "aspect-ratio"],
  layout: ["display", "justify-content", "align-items", "order"],
  typography: ["font-family", "font-size", "font-weight", "line-height", "letter-spacing", "text-align", "text-transform"],
  color: ["color", "background-color", "background-image"],
  border: ["border", "border-radius"],
  effects: ["box-shadow", "opacity", "transform"],
};
const SUGGEST: Record<string, string[]> = {
  "font-family": ["var(--t-font-display)", "var(--t-font-body)", "var(--t-font-mono)", "var(--t-font-bangla)"],
  "font-size": ["var(--t-step--1)", "var(--t-step-0)", "var(--t-step-1)", "var(--t-step-2)", "var(--t-step-3)", "var(--t-step-4)", "var(--t-step-5)", "var(--t-step-6)"],
  color: ["var(--t-text)", "var(--t-text-muted)", "var(--t-primary)", "var(--t-highlight)", "var(--t-inverse-text)"],
  "background-color": ["var(--t-bg)", "var(--t-surface)", "var(--t-surface-alt)", "var(--t-primary)", "var(--t-inverse-bg)"],
  "border-radius": ["var(--t-radius-sm)", "var(--t-radius-md)", "var(--t-radius-lg)", "var(--t-radius-card)", "999px"],
  "box-shadow": ["var(--t-shadow-card)", "var(--t-shadow-raised)", "var(--t-shadow-overlay)", "none"],
  padding: ["0", "var(--t-gutter)", "16px 24px", "clamp(24px, 5vw, 64px)"],
  "text-align": ["start", "center", "end"],
  "text-transform": ["none", "uppercase"],
  display: ["block", "flex", "grid", "none"],
};

function resolveColor(v: string | undefined, theme: Record<string, string>): string | undefined {
  if (!v) return undefined;
  const m = /^var\(--t-([a-z-]+)\)$/.exec(v.trim());
  if (!m) return v;
  const map: Record<string, string> = { text: "text", "text-muted": "text_muted", primary: "primary", highlight: "accent", bg: "bg", surface: "surface", "surface-alt": "surface_alt", "inverse-bg": "inverse_bg", "inverse-text": "inverse_text", "on-primary": "on_primary" };
  return theme[map[m[1]!] ?? ""];
}

function StyleTab({ node, def, registry, locked, onChange, themeColors }: Pick<InspectorProps, "node" | "def" | "registry" | "locked" | "onChange" | "themeColors">) {
  const [bp, setBp] = useState<Bp>("base");
  const [adding, setAdding] = useState("");
  const style: StyleMap = node.style ?? {};
  const decls = style[bp] ?? {};
  const groups = def?.styles ?? [];
  const allowed = useMemo(() => [...new Set(groups.flatMap((g) => registry.style_groups[g] ?? []))], [groups, registry]);
  const setProp = (k: string, v: string) => {
    const nextBp = { ...decls };
    if (v.trim()) nextBp[k] = v;
    else delete nextBp[k];
    const next: StyleMap = { ...style, [bp]: nextBp };
    if (!Object.keys(nextBp).length) delete next[bp];
    onChange({ ...node, style: Object.keys(next).length ? next : undefined }, `style.${bp}.${k}`);
  };
  const shown = [...new Set([...groups.flatMap((g) => CURATED[g] ?? []).filter((p) => allowed.includes(p)), ...Object.keys(decls)])];
  const fg = resolveColor(decls.color ?? style.base?.color, themeColors);
  const bg = resolveColor(decls["background-color"] ?? style.base?.["background-color"], themeColors) ?? themeColors.bg;
  const ratio = fg && bg ? contrast(fg, bg) : null;
  if (!groups.length) return <p className="st-hint">This component has no style controls.</p>;
  return (
    <div className="st-stack">
      <Segmented label="Breakpoint" value={bp} onChange={setBp} items={[{ value: "base", label: "Desktop" }, { value: "tablet", label: "≤1024" }, { value: "mobile", label: "≤767" }]} />
      <p className="st-hint">{bp === "base" ? "Base styles apply everywhere unless overridden." : `Overrides for ${bp === "tablet" ? "tablets and smaller" : "phones"}. Empty fields inherit.`}</p>
      {ratio !== null ? <Alert tone={ratio >= 4.5 ? "success" : ratio >= 3 ? "warning" : "danger"}>Text contrast {ratio}:1 {ratio >= 4.5 ? "passes" : ratio >= 3 ? "passes for large text only" : "fails"} WCAG AA</Alert> : null}
      <div className="st-style-grid">
        {shown.map((k) => {
          const inherited = bp !== "base" ? (bp === "mobile" ? style.tablet?.[k] ?? style.base?.[k] : style.base?.[k]) : undefined;
          return (
            <div key={k} className="st-style-row">
              <label htmlFor={`${node.id}-${bp}-${k}`} className="st-mono">{k}</label>
              <Input id={`${node.id}-${bp}-${k}`} list={`sg-${k}`} disabled={locked} value={decls[k] ?? ""} placeholder={inherited ? `inherits ${inherited}` : "—"} onChange={(e) => setProp(k, e.target.value)} />
              {SUGGEST[k] ? <datalist id={`sg-${k}`}>{SUGGEST[k]!.map((s) => <option key={s} value={s} />)}</datalist> : null}
            </div>
          );
        })}
      </div>
      <div className="v-row">
        <Select aria-label="Add a style property" value={adding} onChange={(e) => setAdding(e.target.value)} disabled={locked}>
          <option value="">Add property…</option>
          {allowed.filter((p) => !shown.includes(p)).map((p) => <option key={p} value={p}>{p}</option>)}
        </Select>
        <Button size="sm" disabled={!adding || locked} onClick={() => { setProp(adding, "initial"); setAdding(""); }}>Add</Button>
      </div>
      {Object.keys(decls).length ? <Button size="sm" variant="ghost" disabled={locked} onClick={() => { const next = { ...style }; delete next[bp]; onChange({ ...node, style: Object.keys(next).length ? next : undefined }, `style.${bp}.clear`); }}><RotateCcw size={13} /> Clear {bp} styles</Button> : null}
    </div>
  );
}

/* -------------------------------------------------------------- visibility */
function VisibilityTab({ node, locked, onChange }: Pick<InspectorProps, "node" | "locked" | "onChange">) {
  const v: Visibility = node.visibility ?? {};
  const set = (patch: Partial<Visibility>) => {
    const next: Visibility = { ...v, ...patch };
    for (const k of Object.keys(next) as (keyof Visibility)[]) {
      const val = next[k];
      if (val === undefined || val === "" || val === false || (Array.isArray(val) && !val.length) || val === "any") delete next[k];
    }
    onChange({ ...node, visibility: Object.keys(next).length ? next : undefined }, `vis.${Object.keys(patch)[0]}`);
  };
  const devices = v.devices ?? ["desktop", "tablet", "mobile"];
  const toggle = (d: "desktop" | "tablet" | "mobile") => {
    const next = devices.includes(d) ? devices.filter((x) => x !== d) : [...devices, d];
    set({ devices: next.length === 3 ? undefined : next });
  };
  const local = (iso?: string) => (iso ? new Date(iso).toISOString().slice(0, 16) : "");
  return (
    <fieldset className="st-stack" disabled={locked}>
      <div className="st-field st-field--inline"><span className="st-label">Hidden everywhere</span><Switch checked={!!v.hidden} onChange={(h) => set({ hidden: h || undefined })} label="Hidden" /></div>
      <Row label="Show on devices" hint="Enforced with CSS so it adapts when the window resizes.">
        <div className="v-row v-row--wrap">
          {(["desktop", "tablet", "mobile"] as const).map((d) => <Checkbox key={d} checked={devices.includes(d)} onChange={() => toggle(d)} label={d} />)}
        </div>
      </Row>
      <Row label="Visitors">
        <Select value={v.signed_in ?? "any"} onChange={(e) => set({ signed_in: e.target.value as Visibility["signed_in"] })}>
          <option value="any">Everyone</option>
          <option value="signed_in">Signed-in customers</option>
          <option value="signed_out">Guests only</option>
        </Select>
      </Row>
      <Row label="Languages" hint="Comma-separated language codes, e.g. bn, en. Empty = all.">
        <Input value={(v.locales ?? []).join(", ")} onChange={(e) => set({ locales: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
      </Row>
      <div className="st-two">
        <Row label="Show from"><Input type="datetime-local" value={local(v.starts_at)} onChange={(e) => set({ starts_at: e.target.value ? new Date(e.target.value).toISOString() : undefined })} /></Row>
        <Row label="Hide after"><Input type="datetime-local" value={local(v.ends_at)} onChange={(e) => set({ ends_at: e.target.value ? new Date(e.target.value).toISOString() : undefined })} /></Row>
      </div>
      <Row label="Customer tags" hint="Only customers with one of these tags see it. Checked on the server.">
        <Input value={(v.customer_tags ?? []).join(", ")} onChange={(e) => set({ customer_tags: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
      </Row>
      <p className="st-hint">The canvas shows every component so you can edit it; dimmed outlines mark hidden ones.</p>
    </fieldset>
  );
}

/* -------------------------------------------------------------- motion */
function MotionTab({ node, locked, onChange, onReplay }: Pick<InspectorProps, "node" | "locked" | "onChange" | "onReplay">) {
  const m: Motion = node.motion ?? { preset: "none" };
  const set = (patch: Partial<Motion>) => {
    const next = { ...m, ...patch };
    onChange({ ...node, motion: next.preset === "none" ? undefined : next }, `motion.${Object.keys(patch)[0]}`);
  };
  const slider = (k: "duration" | "delay" | "stagger" | "distance", label: string, max: number, step: number, dflt: number, unit: string) => (
    <Row label={<>{label} <span className="v-muted v-num">{m[k] ?? dflt}{unit}</span></>}>
      <input type="range" min={0} max={max} step={step} value={m[k] ?? dflt} onChange={(e) => set({ [k]: Number(e.target.value) })} aria-label={label} />
    </Row>
  );
  return (
    <fieldset className="st-stack" disabled={locked}>
      <Row label="Entrance">
        <Select value={m.preset} onChange={(e) => set({ preset: e.target.value as Motion["preset"] })}>
          <option value="none">None</option>
          <option value="fade">Fade in</option>
          <option value="rise">Rise</option>
          <option value="scale">Scale in</option>
          <option value="stagger">Stagger children</option>
        </Select>
      </Row>
      {m.preset !== "none" ? (
        <>
          <Row label="Trigger"><Segmented label="Trigger" value={m.trigger ?? "enter"} onChange={(t) => set({ trigger: t })} items={[{ value: "enter", label: "On scroll into view" }, { value: "load", label: "On load" }]} /></Row>
          {slider("duration", "Duration", 2000, 20, 480, "ms")}
          {slider("delay", "Delay", 2000, 20, 0, "ms")}
          {m.preset === "stagger" ? slider("stagger", "Step between items", 400, 10, 60, "ms") : null}
          {m.preset === "rise" ? slider("distance", "Distance", 64, 2, 16, "px") : null}
          <Row label="Easing">
            <Select value={m.easing ?? "out"} onChange={(e) => set({ easing: e.target.value as Motion["easing"] })}>
              <option value="out">Ease out (natural)</option>
              <option value="standard">Standard</option>
              <option value="in-out">Ease in-out</option>
              <option value="emphasized">Emphasized</option>
              <option value="linear">Linear</option>
            </Select>
          </Row>
          <Button size="sm" onClick={onReplay}><RotateCcw size={13} /> Replay in canvas</Button>
        </>
      ) : null}
      <p className="st-hint">Visitors who prefer reduced motion see content immediately without animation. Only opacity and transform animate.</p>
    </fieldset>
  );
}

/* -------------------------------------------------------------- advanced */
function AdvancedTab({ node, locked, onChange }: Pick<InspectorProps, "node" | "locked" | "onChange">) {
  const a = node.advanced ?? {};
  const [check, setCheck] = useState<{ warnings: string[]; scoped: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const set = (patch: Partial<typeof a>) => {
    const next = { ...a, ...patch };
    for (const k of Object.keys(next) as (keyof typeof a)[]) if (next[k] === "" || next[k] === undefined || next[k] === false) delete next[k];
    onChange({ ...node, advanced: Object.keys(next).length ? next : undefined }, `adv.${Object.keys(patch)[0]}`);
  };
  return (
    <div className="st-stack">
      <div className="st-field st-field--inline"><span className="st-label"><Lock size={13} /> Lock (prevents edits and moves)</span><Switch checked={!!a.locked} onChange={(l) => set({ locked: l || undefined })} label="Lock" /></div>
      <fieldset className="st-stack" disabled={locked && !a.locked}>
        <Row label="Layer name"><Input value={a.label ?? ""} maxLength={120} onChange={(e) => set({ label: e.target.value })} /></Row>
        <Row label="Accessible label" hint="aria-label for landmarks or icon-only elements."><Input value={a.aria_label ?? ""} maxLength={120} onChange={(e) => set({ aria_label: e.target.value })} /></Row>
        <Row label="CSS class names"><Input value={a.class_name ?? ""} maxLength={120} onChange={(e) => set({ class_name: e.target.value })} /></Row>
        <Row label="Custom CSS" hint={<>Scoped to this component: <code>&amp;</code> is the component itself, other selectors match inside it. @import, url(javascript:), expression() and behavior are removed.</>}>
          <Textarea className="st-mono" rows={7} value={a.css ?? ""} onChange={(e) => set({ css: e.target.value })} placeholder={"& { border-top: 1px solid var(--t-line); }\n& h2 { letter-spacing: -0.02em; }"} spellCheck={false} />
        </Row>
        <Button size="sm" onClick={async () => {
          setErr(null);
          try {
            setCheck(await post<{ warnings: string[]; scoped: string }>("/api/content/css/sanitize", { css: a.css ?? "", node_id: node.id }));
          } catch (e) {
            setErr(errorMessage(e));
          }
        }} disabled={!a.css}>Check CSS</Button>
        {err ? <Alert tone="danger">{err}</Alert> : null}
        {check ? (
          <div className="st-stack">
            {check.warnings.length ? <Alert tone="warning" title="Removed by the sanitizer">{check.warnings.join(" · ")}</Alert> : <Alert tone="success">CSS is clean.</Alert>}
            <pre className="st-pre">{check.scoped || "(empty)"}</pre>
          </div>
        ) : null}
      </fieldset>
      <p className="st-hint st-mono">id: {node.id} · type: {node.type}</p>
    </div>
  );
}

export function Inspector(props: InspectorProps) {
  const { node, def, locked, onChange, pageLinks } = props;
  const [tab, setTab] = useState<Tab>("content");
  const setProp = (name: string, v: unknown) => {
    const next = { ...node.props };
    if (v === undefined) delete next[name];
    else next[name] = v;
    onChange({ ...node, props: next }, `props.${name}`);
  };
  const tabs: { value: Tab; label: string; dot?: boolean }[] = [
    { value: "content", label: "Content" },
    { value: "style", label: "Style", dot: !!node.style },
    { value: "visibility", label: "Visibility", dot: !!node.visibility },
    { value: "motion", label: "Motion", dot: !!node.motion },
    { value: "advanced", label: "Advanced", dot: !!node.advanced?.css },
  ];
  return (
    <div className="st-inspector">
      <header className="st-inspector__head">
        <div>
          <h2>{node.advanced?.label || def?.label || node.type}</h2>
          <p className="st-hint">{def?.description ?? "Unknown component (no definition installed)."}</p>
        </div>
        {def?.source !== "core" && def ? <Badge tone="info">{def.source}</Badge> : null}
      </header>
      {locked ? <Alert tone="warning">This component (or a parent) is locked. Unlock it in Advanced to edit.</Alert> : null}
      <div className="st-tabs" role="tablist" aria-label="Inspector">
        {tabs.map((t) => (
          <button key={t.value} role="tab" type="button" aria-selected={tab === t.value} onClick={() => setTab(t.value)}>
            {t.label}{t.dot ? <span className="st-dot" aria-label="(customised)" /> : null}
          </button>
        ))}
      </div>
      <div className="st-inspector__body" role="tabpanel">
        {tab === "content" ? (
          <fieldset className="st-stack" disabled={locked}>
            {def?.props.length ? def.props.map((p) => <PropField key={p.name} def={p} value={node.props[p.name]} onChange={(v) => setProp(p.name, v)} pageLinks={pageLinks} />) : <p className="st-hint">No content settings.</p>}
          </fieldset>
        ) : tab === "style" ? (
          <StyleTab {...props} />
        ) : tab === "visibility" ? (
          <VisibilityTab node={node} locked={locked} onChange={onChange} />
        ) : tab === "motion" ? (
          <MotionTab node={node} locked={locked} onChange={onChange} onReplay={props.onReplay} />
        ) : (
          <AdvancedTab node={node} locked={locked} onChange={onChange} />
        )}
      </div>
    </div>
  );
}

export function EmptyInspector({ onDelete }: { onDelete?: () => void }) {
  return (
    <div className="st-inspector st-inspector--empty">
      <p>Select a component in the canvas or the layers panel to edit it.</p>
      {onDelete ? <Button variant="ghost" onClick={onDelete}><Trash2 size={14} /></Button> : null}
    </div>
  );
}

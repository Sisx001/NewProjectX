import { useId, useState, type CSSProperties, type KeyboardEvent } from "react";
import type { Renderer } from "./index";
import { nodeAttrs } from "./index";

const s = (v: unknown, d: string) => (typeof v === "string" && v ? v : d);
const n = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);
const GAP: Record<string, string> = { none: "0", xs: "4px", sm: "8px", md: "16px", lg: "32px", xl: "56px" };

const Page: Renderer = ({ node, p, kids }) => (
  <div {...nodeAttrs(node, { className: `vb-page vb-gap-${s(p.gap, "none")}` })}>{kids}</div>
);

const Section: Renderer = ({ node, p, kids }) => {
  const anchor = s(p.anchor, "").replace(/[^a-zA-Z0-9_-]/g, "");
  return (
    <section {...nodeAttrs(node, { className: "vb-section" })} id={anchor || undefined} data-tone={s(p.tone, "default")} data-spacing={s(p.spacing, "regular")}>
      <div className="vb-wrap" data-width={s(p.width, "content")}>{kids}</div>
    </section>
  );
};

const Container: Renderer = ({ node, p, kids }) => (
  <div {...nodeAttrs(node, { className: "vb-wrap vb-container" })} data-width={s(p.width, "content")}>{kids}</div>
);

const Stack: Renderer = ({ node, p, kids }) => {
  const dir = s(p.direction, "vertical");
  const justify: Record<string, string> = { start: "flex-start", center: "center", end: "flex-end", between: "space-between" };
  const align: Record<string, string> = { start: "flex-start", center: "center", end: "flex-end", stretch: "stretch" };
  const style: CSSProperties = {
    display: "flex",
    flexDirection: dir === "horizontal" ? "row" : "column",
    gap: GAP[s(p.gap, "md")],
    alignItems: align[s(p.align, dir === "horizontal" ? "center" : "stretch")],
    justifyContent: justify[s(p.justify, "start")],
    flexWrap: p.wrap ? "wrap" : undefined,
  };
  return <div {...nodeAttrs(node, { className: "vb-stack", style })}>{kids}</div>;
};

const Grid: Renderer = ({ node, p, kids }) => {
  const style = {
    "--cols": n(p.columns, 3),
    "--cols-t": n(p.columns_tablet, 2),
    "--cols-m": n(p.columns_mobile, 1),
    "--gap": GAP[s(p.gap, "md")],
  } as CSSProperties;
  return <div {...nodeAttrs(node, { className: "vb-grid", style })}>{kids.map((k, i) => <div key={i} className="vb-cell" data-stagger-item>{k}</div>)}</div>;
};

const Columns: Renderer = ({ node, p, kids }) => {
  const ratio = s(p.ratio, "1:1").split(":").map((x) => `${Number(x) || 1}fr`).join(" ");
  const style = { "--ratio": ratio, "--gap": GAP[s(p.gap, "lg")] } as CSSProperties;
  return (
    <div {...nodeAttrs(node, { className: "vb-columns", style })} data-align={s(p.align, "start")} data-stack={s(p.stack_on, "mobile")} data-reverse={p.reverse_on_mobile ? "" : undefined}>
      {kids.map((k, i) => <div key={i} className="vb-col" data-stagger-item>{k}</div>)}
    </div>
  );
};

const Spacer: Renderer = ({ node, p }) => <div {...nodeAttrs(node, { className: "vb-spacer" })} data-size={s(p.size, "md")} aria-hidden />;

const Divider: Renderer = ({ node, p }) => <hr {...nodeAttrs(node, { className: "vb-divider" })} data-style={s(p.style, "line")} />;

const Tabs: Renderer = ({ node, p, kids, childNodes }) => {
  const [active, setActive] = useState(0);
  const id = useId();
  const current = Math.min(active, Math.max(0, kids.length - 1));
  const onKey = (e: KeyboardEvent) => {
    const last = kids.length - 1;
    const next = e.key === "ArrowRight" ? (current + 1) % kids.length : e.key === "ArrowLeft" ? (current - 1 + kids.length) % kids.length : e.key === "Home" ? 0 : e.key === "End" ? last : -1;
    if (next < 0) return;
    e.preventDefault();
    setActive(next);
    document.getElementById(`${id}-t${next}`)?.focus();
  };
  return (
    <div {...nodeAttrs(node, { className: "vb-tabs" })}>
      <div role="tablist" className="vb-tabs__list" data-align={s(p.align, "start")} onKeyDown={onKey}>
        {childNodes.map((c, i) => (
          <button key={c.id} id={`${id}-t${i}`} role="tab" type="button" aria-selected={i === current} aria-controls={`${id}-p${i}`} tabIndex={i === current ? 0 : -1} onClick={() => setActive(i)}>
            {String(c.props.label ?? `Tab ${i + 1}`)}
          </button>
        ))}
      </div>
      {kids.map((k, i) => (
        <div key={i} id={`${id}-p${i}`} role="tabpanel" aria-labelledby={`${id}-t${i}`} hidden={i !== current} className="vb-tabs__panel">
          {k}
        </div>
      ))}
    </div>
  );
};

const TabPanel: Renderer = ({ node, kids }) => <div {...nodeAttrs(node, { className: "vb-tabpanel" })}>{kids}</div>;

const Accordion: Renderer = ({ node, p, kids }) => (
  <div {...nodeAttrs(node, { className: "vb-accordion" })} data-single={p.single ? "" : undefined}
    onToggle={(e) => {
      // "Only one open": close siblings when one opens.
      const t = e.target as HTMLDetailsElement;
      if (!p.single || !t.open) return;
      (e.currentTarget as HTMLElement).querySelectorAll<HTMLDetailsElement>(":scope > details[open]").forEach((d) => d !== t && (d.open = false));
    }}>
    {kids}
  </div>
);

const AccordionItem: Renderer = ({ node, p, kids }) => (
  <details {...nodeAttrs(node, { className: "vb-acc-item" })} open={!!p.open || undefined}>
    <summary>
      <span>{String(p.title ?? "")}</span>
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden><path d="M3 5l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
    </summary>
    <div className="vb-acc-item__body">{kids}</div>
  </details>
);

export const LAYOUT: Record<string, Renderer> = {
  page: Page,
  section: Section,
  container: Container,
  stack: Stack,
  grid: Grid,
  columns: Columns,
  spacer: Spacer,
  divider: Divider,
  tabs: Tabs,
  "tab-panel": TabPanel,
  accordion: Accordion,
  "accordion-item": AccordionItem,
};

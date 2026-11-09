import { useMemo, useState } from "react";
import * as Icons from "lucide-react";
import { Input } from "@/ui";
import type { ComponentDef } from "../types";
import { DRAG_TYPE } from "./Layers";

const GROUPS: { key: ComponentDef["category"]; label: string }[] = [
  { key: "layout", label: "Layout" },
  { key: "content", label: "Content" },
  { key: "media", label: "Media" },
  { key: "commerce", label: "Commerce" },
  { key: "navigation", label: "Navigation" },
  { key: "global", label: "Global" },
];

function toPascal(s: string) {
  return s.split("-").map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join("");
}

export function ComponentIcon({ name, size = 16 }: { name: string; size?: number }) {
  const I = (Icons as unknown as Record<string, Icons.LucideIcon>)[toPascal(name)] ?? Icons.Square;
  return <I size={size} strokeWidth={1.6} aria-hidden />;
}

export function Library({ components, canInsert, onInsert }: { components: ComponentDef[]; canInsert: (type: string) => boolean; onInsert: (type: string) => void }) {
  const [q, setQ] = useState("");
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return components.filter((c) => c.insertable && (!t || c.label.toLowerCase().includes(t) || c.type.includes(t) || c.description.toLowerCase().includes(t)));
  }, [components, q]);
  return (
    <div className="st-library">
      <Input type="search" placeholder="Search components" aria-label="Search components" value={q} onChange={(e) => setQ(e.target.value)} />
      <p className="st-hint">Click to insert at the selection, or drag onto a layer.</p>
      {GROUPS.map((g) => {
        const items = list.filter((c) => c.category === g.key);
        if (!items.length) return null;
        return (
          <section key={g.key}>
            <h3 className="st-group">{g.label}</h3>
            <div className="st-lib-grid">
              {items.map((c) => {
                const ok = canInsert(c.type);
                return (
                  <button
                    key={c.type}
                    type="button"
                    className="st-lib-item"
                    disabled={!ok}
                    title={ok ? c.description : `${c.label} can't be placed here`}
                    draggable={ok}
                    onDragStart={(e) => { e.dataTransfer.setData(DRAG_TYPE, c.type); e.dataTransfer.effectAllowed = "copy"; }}
                    onClick={() => onInsert(c.type)}
                  >
                    <ComponentIcon name={c.icon} />
                    <span>{c.label}</span>
                    {c.source !== "core" ? <small>{c.source}</small> : null}
                  </button>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

import { useState, type DragEvent, type KeyboardEvent } from "react";
import { ChevronRight, Eye, EyeOff, GripVertical, Lock, Unlock } from "lucide-react";
import type { ComponentDef, Node } from "../types";
import { accepts } from "./tree";

type Drop = { id: string; where: "before" | "after" | "inside" } | null;

export type LayersProps = {
  root: Node;
  defs: Record<string, ComponentDef>;
  selected: string | null;
  hover: string | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
  onMove: (id: string, parent: string, index: number) => void;
  onInsertType: (type: string, parent: string, index: number) => void;
  onToggleHidden: (id: string) => void;
  onToggleLock: (id: string) => void;
  onKey: (e: KeyboardEvent) => void;
};

export const DRAG_NODE = "application/x-voltora-node";
export const DRAG_TYPE = "application/x-voltora-type";

function preview(n: Node): string {
  const p = n.props ?? {};
  const t = (p.text ?? p.title ?? p.label ?? "") as string;
  if (typeof t === "string" && t.trim()) return t.replace(/\{\{\s*([a-z.]+)\s*\}\}/gi, "‹$1›").slice(0, 40);
  if (typeof p.html === "string") return p.html.replace(/<[^>]+>/g, " ").trim().slice(0, 40);
  return "";
}

export function Layers(props: LayersProps) {
  const { root, defs } = props;
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [drop, setDrop] = useState<Drop>(null);
  const [dragging, setDragging] = useState<string | null>(null);

  const flat: { node: Node; depth: number; parent: Node | null; index: number }[] = [];
  const walk = (n: Node, depth: number, parent: Node | null, index: number) => {
    flat.push({ node: n, depth, parent, index });
    if (!collapsed.has(n.id)) (n.children ?? []).forEach((c, i) => walk(c, depth + 1, n, i));
  };
  walk(root, 0, null, 0);

  const onDragOver = (e: DragEvent, row: (typeof flat)[number]) => {
    const isNode = e.dataTransfer.types.includes(DRAG_NODE);
    const isType = e.dataTransfer.types.includes(DRAG_TYPE);
    if (!isNode && !isType) return;
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const y = (e.clientY - rect.top) / rect.height;
    const container = defs[row.node.type]?.children !== "none";
    const where: NonNullable<Drop>["where"] = row.parent === null ? "inside" : y < 0.28 ? "before" : y > 0.72 && !(container && !collapsed.has(row.node.id) && row.node.children?.length) ? "after" : container ? "inside" : y < 0.5 ? "before" : "after";
    e.preventDefault();
    e.dataTransfer.dropEffect = isNode ? "move" : "copy";
    if (drop?.id !== row.node.id || drop.where !== where) setDrop({ id: row.node.id, where });
  };

  const onDrop = (e: DragEvent, row: (typeof flat)[number]) => {
    e.preventDefault();
    const d = drop;
    setDrop(null);
    setDragging(null);
    if (!d) return;
    const target = d.where === "inside" ? { parent: row.node.id, index: row.node.children?.length ?? 0 } : { parent: row.parent!.id, index: row.index + (d.where === "after" ? 1 : 0) };
    const nodeId = e.dataTransfer.getData(DRAG_NODE);
    const type = e.dataTransfer.getData(DRAG_TYPE);
    if (nodeId) props.onMove(nodeId, target.parent, target.index);
    else if (type) props.onInsertType(type, target.parent, target.index);
  };

  return (
    <div className="st-layers" role="tree" aria-label="Layers" onKeyDown={props.onKey} onDragLeave={(e) => !e.currentTarget.contains(e.relatedTarget as globalThis.Node) && setDrop(null)}>
      {flat.map((row) => {
        const { node, depth } = row;
        const def = defs[node.type];
        const kids = node.children?.length ?? 0;
        const isSel = props.selected === node.id;
        const invalid = drop?.id === node.id && drop.where === "inside" && dragging !== null && !accepts(def, dragging);
        return (
          <div
            key={node.id}
            role="treeitem"
            aria-selected={isSel}
            aria-level={depth + 1}
            aria-expanded={kids ? !collapsed.has(node.id) : undefined}
            tabIndex={isSel || (!props.selected && depth === 0) ? 0 : -1}
            className="st-layer"
            data-selected={isSel || undefined}
            data-hover={props.hover === node.id || undefined}
            data-hidden={node.visibility?.hidden || undefined}
            data-drop={drop?.id === node.id ? (invalid ? "invalid" : drop.where) : undefined}
            style={{ paddingLeft: 6 + depth * 14 }}
            onClick={() => props.onSelect(node.id)}
            onMouseEnter={() => props.onHover(node.id)}
            onMouseLeave={() => props.onHover(null)}
            draggable={depth > 0 && !node.advanced?.locked}
            onDragStart={(e) => {
              e.dataTransfer.setData(DRAG_NODE, node.id);
              e.dataTransfer.effectAllowed = "move";
              setDragging(node.type);
            }}
            onDragEnd={() => { setDrop(null); setDragging(null); }}
            onDragOver={(e) => onDragOver(e, row)}
            onDrop={(e) => onDrop(e, row)}
            id={`layer-${node.id}`}
          >
            {depth > 0 ? <GripVertical size={12} className="st-layer__grip" aria-hidden /> : null}
            {kids ? (
              <button type="button" className="st-layer__caret" tabIndex={-1} aria-label={collapsed.has(node.id) ? "Expand" : "Collapse"} onClick={(e) => {
                e.stopPropagation();
                setCollapsed((s) => {
                  const n = new Set(s);
                  if (n.has(node.id)) n.delete(node.id);
                  else n.add(node.id);
                  return n;
                });
              }}>
                <ChevronRight size={12} style={{ transform: collapsed.has(node.id) ? undefined : "rotate(90deg)" }} />
              </button>
            ) : <span className="st-layer__caret" />}
            <span className="st-layer__name">{node.advanced?.label || def?.label || node.type}</span>
            <span className="st-layer__meta">{preview(node)}</span>
            {node.visibility && Object.keys(node.visibility).some((k) => k !== "hidden") ? <span className="st-layer__flag" title="Has visibility rules">◐</span> : null}
            {node.motion ? <span className="st-layer__flag" title={`Motion: ${node.motion.preset}`}>↗</span> : null}
            {depth > 0 ? (
              <span className="st-layer__acts">
                <button type="button" tabIndex={-1} aria-label={node.visibility?.hidden ? "Show" : "Hide"} onClick={(e) => { e.stopPropagation(); props.onToggleHidden(node.id); }}>{node.visibility?.hidden ? <EyeOff size={13} /> : <Eye size={13} />}</button>
                <button type="button" tabIndex={-1} aria-label={node.advanced?.locked ? "Unlock" : "Lock"} data-on={node.advanced?.locked || undefined} onClick={(e) => { e.stopPropagation(); props.onToggleLock(node.id); }}>{node.advanced?.locked ? <Lock size={13} /> : <Unlock size={13} />}</button>
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

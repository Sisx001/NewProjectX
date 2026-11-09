/** Immutable page-tree operations used by the Studio (layers, library, inspector). */
import type { ComponentDef, Node } from "../types";

export function newId(type: string): string {
  const rnd = Math.random().toString(36).slice(2, 8);
  return `${type.replace(/[^a-z0-9]/gi, "").slice(0, 12) || "n"}-${rnd}`;
}

export function find(root: Node, id: string): Node | null {
  if (root.id === id) return root;
  for (const c of root.children ?? []) {
    const f = find(c, id);
    if (f) return f;
  }
  return null;
}

/** Returns [parent, index] for a node id (parent null for the root). */
export function locate(root: Node, id: string, parent: Node | null = null, index = 0): [Node | null, number] | null {
  if (root.id === id) return [parent, index];
  const kids = root.children ?? [];
  for (let i = 0; i < kids.length; i++) {
    const f = locate(kids[i]!, id, root, i);
    if (f) return f;
  }
  return null;
}

export function pathTo(root: Node, id: string): Node[] {
  if (root.id === id) return [root];
  for (const c of root.children ?? []) {
    const p = pathTo(c, id);
    if (p.length) return [root, ...p];
  }
  return [];
}

export function count(root: Node | null): number {
  if (!root) return 0;
  return 1 + (root.children ?? []).reduce((n, c) => n + count(c), 0);
}

export function isLocked(root: Node, id: string): boolean {
  return pathTo(root, id).some((n) => n.advanced?.locked);
}

/** Map a single node by id (structural sharing for untouched branches). */
export function update(root: Node, id: string, fn: (n: Node) => Node): Node {
  if (root.id === id) return fn(root);
  if (!root.children?.length) return root;
  let changed = false;
  const kids = root.children.map((c) => {
    const u = update(c, id, fn);
    if (u !== c) changed = true;
    return u;
  });
  return changed ? { ...root, children: kids } : root;
}

export function remove(root: Node, id: string): Node {
  if (!root.children?.length) return root;
  const idx = root.children.findIndex((c) => c.id === id);
  if (idx >= 0) {
    const kids = root.children.filter((c) => c.id !== id);
    return { ...root, children: kids };
  }
  let changed = false;
  const kids = root.children.map((c) => {
    const u = remove(c, id);
    if (u !== c) changed = true;
    return u;
  });
  return changed ? { ...root, children: kids } : root;
}

export function insert(root: Node, parentId: string, index: number, node: Node): Node {
  return update(root, parentId, (p) => {
    const kids = [...(p.children ?? [])];
    kids.splice(Math.max(0, Math.min(index, kids.length)), 0, node);
    return { ...p, children: kids };
  });
}

export function accepts(def: ComponentDef | undefined, childType: string): boolean {
  if (!def) return false;
  if (def.children === "any") return true;
  if (def.children === "none") return false;
  return def.children.includes(childType);
}

export function contains(root: Node, ancestorId: string, id: string): boolean {
  const a = find(root, ancestorId);
  return !!a && !!find(a, id);
}

/** Move a node to parent/index. Refuses moves into itself or into containers that do not accept the type. */
export function move(root: Node, id: string, parentId: string, index: number, defs: Record<string, ComponentDef>): Node | null {
  if (id === root.id || contains(root, id, parentId)) return null;
  const node = find(root, id);
  const parent = find(root, parentId);
  if (!node || !parent || !accepts(defs[parent.type], node.type)) return null;
  const loc = locate(root, id);
  if (!loc) return null;
  const [oldParent, oldIndex] = loc;
  let target = index;
  if (oldParent?.id === parentId && oldIndex < index) target -= 1;
  return insert(remove(root, id), parentId, target, node);
}

export function moveBy(root: Node, id: string, delta: -1 | 1): Node | null {
  const loc = locate(root, id);
  if (!loc || !loc[0]) return null;
  const [parent, i] = loc;
  const j = i + delta;
  const kids = [...(parent.children ?? [])];
  if (j < 0 || j >= kids.length) return null;
  [kids[i], kids[j]] = [kids[j]!, kids[i]!];
  return update(root, parent.id, (p) => ({ ...p, children: kids }));
}

/** Indent: become the last child of the previous sibling (if it accepts this type). */
export function indent(root: Node, id: string, defs: Record<string, ComponentDef>): Node | null {
  const loc = locate(root, id);
  if (!loc || !loc[0] || loc[1] === 0) return null;
  const prev = loc[0].children![loc[1] - 1]!;
  return move(root, id, prev.id, prev.children?.length ?? 0, defs);
}

/** Outdent: move after the current parent inside the grandparent. */
export function outdent(root: Node, id: string, defs: Record<string, ComponentDef>): Node | null {
  const loc = locate(root, id);
  if (!loc || !loc[0]) return null;
  const ploc = locate(root, loc[0].id);
  if (!ploc || !ploc[0]) return null;
  return move(root, id, ploc[0].id, ploc[1] + 1, defs);
}

export function cloneWithIds(node: Node): Node {
  return { ...structuredClone(node), id: newId(node.type), children: node.children?.map(cloneWithIds) };
}

export function duplicate(root: Node, id: string): { root: Node; id: string } | null {
  const loc = locate(root, id);
  const node = find(root, id);
  if (!loc || !loc[0] || !node) return null;
  const copy = cloneWithIds(node);
  return { root: insert(root, loc[0].id, loc[1] + 1, copy), id: copy.id };
}

/** New node from a definition's defaults; containers that only accept one child type get one starter child. */
export function create(def: ComponentDef, defs: Record<string, ComponentDef>): Node {
  const props: Record<string, unknown> = {};
  for (const p of def.props) if (p.default !== undefined && p.default !== null) props[p.name] = structuredClone(p.default);
  const node: Node = { id: newId(def.type), type: def.type, props };
  if (Array.isArray(def.children) && def.children.length === 1) {
    const childDef = defs[def.children[0]!];
    if (childDef) node.children = [create(childDef, defs), create(childDef, defs)];
  }
  return node;
}

/** Where to insert a new node relative to the selection: inside a container, else after the node. */
export function insertionPoint(root: Node, selected: string | null, type: string, defs: Record<string, ComponentDef>): { parent: string; index: number } | null {
  const sel = selected ? find(root, selected) : null;
  if (sel && accepts(defs[sel.type], type)) return { parent: sel.id, index: sel.children?.length ?? 0 };
  if (sel) {
    let loc = locate(root, sel.id);
    while (loc && loc[0]) {
      if (accepts(defs[loc[0].type], type)) return { parent: loc[0].id, index: loc[1] + 1 };
      loc = locate(root, loc[0].id);
    }
  }
  return accepts(defs[root.type], type) ? { parent: root.id, index: root.children?.length ?? 0 } : null;
}

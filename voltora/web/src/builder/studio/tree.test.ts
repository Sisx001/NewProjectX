import { describe, expect, it } from "vitest";
import type { ComponentDef, Node } from "../types";
import { duplicate, find, indent, insertionPoint, move, moveBy, outdent, remove } from "./tree";
import { initHistory, push, redo, undo, HISTORY_LIMIT } from "./history";

const def = (type: string, children: ComponentDef["children"]): ComponentDef => ({ type, label: type, category: "layout", description: "", props: [], children, styles: [], data_sources: [], a11y: {}, version: "1", source: "core", icon: "", insertable: true });
const defs: Record<string, ComponentDef> = { page: def("page", "any"), section: def("section", "any"), heading: def("heading", "none"), tabs: def("tabs", ["tab-panel"]), "tab-panel": def("tab-panel", "any") };
const h = (id: string): Node => ({ id, type: "heading", props: {} });
const tree: Node = { id: "root", type: "page", props: {}, children: [{ id: "s1", type: "section", props: {}, children: [h("a"), h("b")] }, { id: "s2", type: "section", props: {}, children: [] }, h("c")] };
const ids = (n: Node | null | undefined) => (n?.children ?? []).map((c) => c.id);

describe("tree ops", () => {
  it("moves within and across parents and refuses invalid targets", () => {
    expect(ids(find(move(tree, "a", "s1", 2, defs)!, "s1"))).toEqual(["b", "a"]);
    const m = move(tree, "a", "s2", 0, defs)!;
    expect(ids(find(m, "s1"))).toEqual(["b"]);
    expect(ids(find(m, "s2"))).toEqual(["a"]);
    expect(move(tree, "s1", "s1", 0, defs)).toBeNull(); // into itself
    expect(move(tree, "a", "c", 0, defs)).toBeNull(); // heading takes no children
  });
  it("moves up/down, indents and outdents", () => {
    expect(ids(find(moveBy(tree, "b", -1)!, "s1"))).toEqual(["b", "a"]);
    expect(moveBy(tree, "a", -1)).toBeNull();
    const ind = indent(tree, "s2", defs)!;
    expect(ids(find(ind, "s1"))).toEqual(["a", "b", "s2"]);
    const out = outdent(tree, "b", defs)!;
    expect(ids(out)).toEqual(["s1", "b", "s2", "c"]);
  });
  it("duplicates with fresh ids and removes", () => {
    const d = duplicate(tree, "s1")!;
    expect(ids(d.root)).toHaveLength(4);
    expect(d.id).not.toBe("s1");
    expect(find(d.root, d.id)!.children!.map((c) => c.id)).not.toContain("a");
    expect(ids(remove(tree, "s2"))).toEqual(["s1", "c"]);
  });
  it("finds an insertion point that accepts the type", () => {
    expect(insertionPoint(tree, "a", "heading", defs)).toEqual({ parent: "s1", index: 1 });
    expect(insertionPoint(tree, "s2", "heading", defs)).toEqual({ parent: "s2", index: 0 });
    expect(insertionPoint(tree, null, "section", defs)).toEqual({ parent: "root", index: 3 });
  });
});

describe("history", () => {
  it("undoes, redoes and coalesces rapid edits to the same key", () => {
    let hs = initHistory(0);
    hs = push(hs, 1, "k", 1000);
    hs = push(hs, 2, "k", 1200);
    expect(hs.past).toEqual([0]);
    hs = push(hs, 3, "k", 5000);
    expect(hs.past).toEqual([0, 2]);
    hs = undo(hs);
    expect(hs.present).toBe(2);
    hs = redo(hs);
    expect(hs.present).toBe(3);
  });
  it("is bounded", () => {
    let hs = initHistory(0);
    for (let i = 1; i <= HISTORY_LIMIT + 20; i++) hs = push(hs, i, null, i);
    expect(hs.past.length).toBe(HISTORY_LIMIT);
  });
});

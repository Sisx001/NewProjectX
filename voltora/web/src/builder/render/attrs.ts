import type { ComponentType, CSSProperties, ReactNode } from "react";
import type { Motion, Node } from "../types";

export type NodeProps = { node: Node; p: Record<string, unknown>; kids: ReactNode[]; childNodes: Node[] };
export type Renderer = ComponentType<NodeProps>;

export const EASE: Record<string, string> = {
  standard: "cubic-bezier(0.2, 0, 0, 1)",
  out: "cubic-bezier(0.16, 1, 0.3, 1)",
  "in-out": "cubic-bezier(0.65, 0, 0.35, 1)",
  emphasized: "cubic-bezier(0.3, 0, 0, 1.15)",
  linear: "linear",
};

export const str = (v: unknown, d = "") => (typeof v === "string" && v ? v : d);
export const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);

/** Attributes every component puts on its outermost element: scope id, motion and advanced class/label. */
export function nodeAttrs(node: Node, extra?: { className?: string; style?: CSSProperties }) {
  const m: Motion | undefined = node.motion;
  const style: Record<string, string | number> = { ...(extra?.style as Record<string, string | number>) };
  const moving = !!m && m.preset !== "none";
  if (moving) {
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
    ...(moving ? { "data-motion": m.preset, "data-trigger": m.trigger ?? "enter" } : {}),
    ...(node.visibility?.hidden ? { "data-hidden": "" } : {}),
    ...(node.visibility && Object.keys(node.visibility).some((k) => k !== "hidden") ? { "data-conditional": "" } : {}),
    ...(node.advanced?.aria_label ? { "aria-label": node.advanced.aria_label } : {}),
    className: cls || undefined,
    style: style as CSSProperties,
  };
}

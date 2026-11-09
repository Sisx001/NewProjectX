import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import type { ComponentDef, PageDocument, Registry, ThemeManifest } from "../types";

export type PageSummary = {
  id: string;
  kind: "page" | "global" | "template" | "symbol";
  slug: string;
  title: string;
  status: "draft" | "published" | "changed" | "archived";
  needs_review: boolean;
  draft_version: number;
  draft_updated_at: string;
  published_revision: number | null;
  published_at: string | null;
  path: string | null;
  node_count: number;
};
export type LintItem = { node: string | null; severity: "error" | "warning"; code: string; message: string };
export type PageDetail = PageSummary & { seo: { title?: string; description?: string; noindex?: boolean }; document: PageDocument; meta: Record<string, unknown>; lint: LintItem[]; warnings?: Issue[] };
export type Issue = { node: string | null; path: string; code: string; message: string };
export type Revision = { number: number; kind: string; message: string; title: string; source_number: number | null; diff: Diff; author: string; created_at: string; live?: boolean };
export type Diff = { added: { id: string; type: string }[]; removed: { id: string; type: string }[]; changed: { id: string; type: string; fields: string[] }[]; moved: number; text: string };
export type ThemeSummary = {
  id: string; key: string; name: string; family: string; is_live: boolean; origin: string; forked_from: string | null; draft_version: number; version: string;
  description: string; published_revision: number | null; status: "draft" | "published" | "changed"; draft_updated_at: string; swatch: Record<string, string>; components: Record<string, Record<string, unknown>>;
};
export type ContrastRowApi = { fg: string; bg: string; kind: string; ratio: number; required: number; ok: boolean; severity: "error" | "warning" | null };
export type ThemeDetail = ThemeSummary & { manifest: ThemeManifest; contrast: ContrastRowApi[]; warnings?: { path: string; message: string }[] };

export const useRegistry = () =>
  useQuery({
    queryKey: ["content-registry"],
    queryFn: () => get<Registry>("/api/content/components"),
    staleTime: 5 * 60_000,
    select: (r) => ({ ...r, byType: Object.fromEntries(r.components.map((c) => [c.type, c])) as Record<string, ComponentDef> }),
  });

export const usePages = () => useQuery({ queryKey: ["content-pages"], queryFn: () => get<{ items: PageSummary[] }>("/api/content/pages") });

export function download(name: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function readJsonFile(file: File): Promise<unknown> {
  const text = await file.text();
  return JSON.parse(text);
}

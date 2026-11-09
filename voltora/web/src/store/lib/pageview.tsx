import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, ApiError } from "@/lib/api";
import { Skeleton } from "@/ui";
import { useDocTitle } from "./data";

type PageDoc = { slug: string; title: string; seo?: { title?: string; description?: string }; document: unknown };
type PV = ComponentType<{ slug: string; fallback?: ReactNode; notFound?: ReactNode }>;

// The builder renderer is owned by the design studio and may land after the storefront; resolve it at build time if present.
const found = import.meta.glob("/src/builder/render.tsx") as Record<string, () => Promise<{ PageView?: PV }>>;
const loader = Object.values(found)[0];
const Builder: PV | null = loader ? lazy(async () => ({ default: ((await loader()).PageView ?? (() => null)) as PV })) : null;

/** Renders a published builder page; when the renderer module is unavailable shows the page title only (honest fallback). */
export function Page({ slug, fallback, notFound }: { slug: string; fallback?: ReactNode; notFound?: ReactNode }) {
  const q = useQuery({ queryKey: ["page", slug], retry: false, queryFn: () => get<PageDoc>(`/api/store/pages/${slug}`) });
  useDocTitle(q.data?.seo?.title || q.data?.title);
  if (q.isLoading) return <div className="s-wrap"><Skeleton h={320} /></div>;
  if (q.error instanceof ApiError && q.error.status === 404) return <>{notFound ?? fallback ?? null}</>;
  if (q.error || !q.data) return <>{fallback ?? null}</>;
  if (!Builder) return <div className="s-wrap"><h1 className="s-h1">{q.data.title}</h1>{fallback}</div>;
  return <Suspense fallback={<div className="s-wrap"><Skeleton h={320} /></div>}><Builder slug={slug} fallback={fallback} notFound={notFound} /></Suspense>;
}

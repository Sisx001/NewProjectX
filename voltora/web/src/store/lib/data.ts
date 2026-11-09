import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, qs } from "@/lib/api";
import type { Category, Division, ProductCardData, ProductDetail, ProductList, StorefrontConfig } from "../types";

export const useStorefrontConfig = () => useQuery({ queryKey: ["sf-config"], queryFn: () => get<StorefrontConfig>("/api/storefront/config"), staleTime: 60_000 });
export const useCategories = () => useQuery({ queryKey: ["categories"], queryFn: () => get<{ items: Category[] }>("/api/store/categories"), staleTime: 60_000 });
export const useLocations = () => useQuery({ queryKey: ["locations"], queryFn: () => get<{ divisions: Division[] }>("/api/store/locations"), staleTime: Infinity });
export const useProduct = (slug: string | undefined) => useQuery({ queryKey: ["product", slug], enabled: !!slug, queryFn: () => get<ProductDetail>(`/api/store/products/${encodeURIComponent(slug!)}`), staleTime: 20_000 });
export function useProducts(params: Record<string, string | number | boolean | undefined>, enabled = true) {
  return useQuery({ queryKey: ["products", params], enabled, queryFn: ({ signal }) => get<ProductList>(`/api/store/products${qs(params)}`, signal), placeholderData: (p) => p });
}

/** Debounce a fast-changing value (search-as-you-type). */
export function useDebounced<T>(value: T, ms = 220): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Card-shaped summary derived from a product detail (used by wishlist/compare/recently viewed). */
export function detailToCard(p: ProductDetail): ProductCardData {
  const prices = p.variants.map((v) => v.price_minor);
  const first = p.images[0];
  const cheapest = p.variants.reduce((a, v) => (v.price_minor < a.price_minor ? v : a), p.variants[0]!);
  return {
    id: p.id, slug: p.slug, title: p.title, image: first ? { url: first.url, alt: first.alt } : null, images_count: p.images.length,
    price_minor: Math.min(...prices), price_max_minor: Math.max(...prices), compare_at_minor: cheapest?.compare_at_minor ?? null,
    currency: p.currency, in_stock: p.variants.some((v) => v.in_stock), variant_count: p.variants.length, brand: p.brand, is_demo: p.is_demo,
  };
}

export function useDocTitle(title: string | null | undefined, store?: string) {
  useEffect(() => {
    if (!title) return;
    const prev = document.title;
    document.title = store ? `${title} · ${store}` : title;
    return () => void (document.title = prev);
  }, [title, store]);
}

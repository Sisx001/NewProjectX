import { useCallback, useEffect, useState } from "react";

/** Browser-local lists (wishlist, compare, recently viewed, recent searches). Slugs only: prices and stock are always re-fetched. */
function read(key: string): string[] {
  try {
    const v = JSON.parse(localStorage.getItem("voltora:store:" + key) ?? "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}
function write(key: string, v: string[]) {
  try {
    localStorage.setItem("voltora:store:" + key, JSON.stringify(v));
  } catch {
    /* storage unavailable */
  }
  window.dispatchEvent(new CustomEvent("voltora:local", { detail: key }));
}

export function useLocalList(key: string, max = 50) {
  const [items, setItems] = useState<string[]>(() => read(key));
  useEffect(() => {
    const sync = (e: Event) => {
      if (!(e instanceof CustomEvent) || e.detail === key) setItems(read(key));
    };
    window.addEventListener("voltora:local", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("voltora:local", sync);
      window.removeEventListener("storage", sync);
    };
  }, [key]);
  const has = useCallback((s: string) => items.includes(s), [items]);
  const toggle = useCallback((s: string) => {
    const cur = read(key);
    write(key, cur.includes(s) ? cur.filter((x) => x !== s) : [s, ...cur].slice(0, max));
  }, [key, max]);
  const push = useCallback((s: string) => write(key, [s, ...read(key).filter((x) => x !== s)].slice(0, max)), [key, max]);
  const remove = useCallback((s: string) => write(key, read(key).filter((x) => x !== s)), [key]);
  const clear = useCallback(() => write(key, []), [key]);
  return { items, has, toggle, push, remove, clear };
}
export const useWishlist = () => useLocalList("wishlist", 100);
export const useCompare = () => useLocalList("compare", 4);
export const useRecentlyViewed = () => useLocalList("recent", 12);
export const useRecentSearches = () => useLocalList("searches", 6);

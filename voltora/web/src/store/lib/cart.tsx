import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, del, get, patch, post } from "@/lib/api";
import type { Cart } from "../types";

type CartApi = {
  cart: Cart | undefined;
  loading: boolean;
  busy: boolean;
  open: boolean;
  setOpen: (v: boolean) => void;
  add: (variantId: string, quantity: number, opts?: { openDrawer?: boolean }) => Promise<Cart>;
  setQty: (lineId: string, quantity: number) => Promise<void>;
  remove: (lineId: string) => Promise<void>;
  applyCode: (code: string) => Promise<void>;
  removeCode: () => Promise<void>;
  refresh: () => Promise<void>;
};
const Ctx = createContext<CartApi | null>(null);

export function CartProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const q = useQuery({ queryKey: ["cart"], queryFn: () => get<Cart>("/api/store/cart"), staleTime: 5_000, retry: 1 });

  useEffect(() => {
    const h = () => setOpen(true);
    window.addEventListener("voltora:open-cart", h);
    return () => window.removeEventListener("voltora:open-cart", h);
  }, []);

  const run = useCallback(async (fn: () => Promise<Cart>) => {
    setBusy(true);
    try {
      const c = await fn();
      qc.setQueryData(["cart"], c);
      return c;
    } catch (e) {
      // A stock conflict means our view is stale: reload before the caller explains it.
      if (e instanceof ApiError && e.status === 409) await qc.invalidateQueries({ queryKey: ["cart"] });
      throw e;
    } finally {
      setBusy(false);
    }
  }, [qc]);

  const value = useMemo<CartApi>(() => ({
    cart: q.data,
    loading: q.isLoading,
    busy,
    open,
    setOpen,
    add: async (variantId, quantity, opts) => {
      const c = await run(() => post<Cart>("/api/store/cart/lines", { variant_id: variantId, quantity }));
      if (opts?.openDrawer !== false) setOpen(true);
      return c;
    },
    setQty: async (id, quantity) => void (await run(() => patch<Cart>(`/api/store/cart/lines/${id}`, { quantity }))),
    remove: async (id) => void (await run(() => del<Cart>(`/api/store/cart/lines/${id}`))),
    applyCode: async (code) => void (await run(() => post<Cart>("/api/store/cart/discount", { code }))),
    removeCode: async () => void (await run(() => del<Cart>("/api/store/cart/discount"))),
    refresh: () => qc.invalidateQueries({ queryKey: ["cart"] }),
  }), [q.data, q.isLoading, busy, open, run, qc]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCart() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCart outside CartProvider");
  return v;
}

export const openCart = () => window.dispatchEvent(new Event("voltora:open-cart"));

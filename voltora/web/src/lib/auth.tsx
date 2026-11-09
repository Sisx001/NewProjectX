import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "./api";

export type User = {
  id: string;
  email: string;
  name: string;
  phone: string | null;
  kind: "staff" | "customer";
  mfa_enabled: boolean;
  email_verified: boolean;
};
export type Me = {
  user: User | null;
  permissions: string[];
  mfa_pending: boolean;
  store: { id: string; name: string; currency: string; locale: string; timezone: string; environment: string };
};

const Ctx = createContext<{ me: Me | undefined; loading: boolean; refresh: () => Promise<void>; can: (p: string) => boolean; logout: () => Promise<void> } | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/api/auth/me"), retry: false, staleTime: 30_000 });
  const refresh = useCallback(async () => {
    await qc.invalidateQueries({ queryKey: ["me"] });
  }, [qc]);
  const can = useCallback((p: string) => !!q.data && (q.data.permissions.includes("*") || q.data.permissions.includes(p)), [q.data]);
  const logout = useCallback(async () => {
    await post("/api/auth/logout");
    qc.clear();
    await refresh();
  }, [qc, refresh]);
  return <Ctx.Provider value={{ me: q.data, loading: q.isLoading, refresh, can, logout }}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

/** Persisted UI preference (theme mode, density, saved views). Scoped per browser, not a business setting. */
export function usePref<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const raw = localStorage.getItem("voltora:" + key);
      return raw ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("voltora:" + key, JSON.stringify(v));
    } catch {
      /* storage may be unavailable (private mode) */
    }
  }, [key, v]);
  return [v, setV];
}

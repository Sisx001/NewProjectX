import type { ReactNode } from "react";

/** Interim theme API; the design workstream owns and replaces this file (keep these export names). */
export function ThemeProvider({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
export function SiteHeader() {
  return null;
}
export function SiteFooter() {
  return null;
}
export function useTheme(): { components: Record<string, { variant?: string } & Record<string, unknown>> } {
  return { components: {} };
}

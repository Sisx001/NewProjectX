import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";

export const NAV_GROUPS = ["Overview", "Operate", "Money & delivery", "Grow", "Build", "Intelligence", "Platform"] as const;
export type NavGroup = (typeof NAV_GROUPS)[number];

export type NavItem = {
  label: string;
  path: string; // relative to /admin
  icon: LucideIcon;
  group: NavGroup;
  permission?: string;
  order?: number;
  keywords?: string[];
  mobile?: boolean; // show in mobile tab bar (max 4 + menu)
};

export type AdminRoute = {
  path: string; // relative to /admin, react-router pattern
  load: () => Promise<{ default: ComponentType }>;
  permission?: string;
  fullscreen?: boolean; // render without the shell (e.g. the visual studio)
};

export type Command = { label: string; path?: string; run?: () => void; permission?: string; keywords?: string[]; group?: string };

export type AdminModule = { id: string; nav?: NavItem[]; routes: AdminRoute[]; commands?: Command[] };

const found = import.meta.glob<{ default: AdminModule }>("./modules/*/index.ts", { eager: true });

export const MODULES: AdminModule[] = Object.values(found)
  .map((m) => m.default)
  .filter(Boolean);

export const NAV: NavItem[] = MODULES.flatMap((m) => m.nav ?? []).sort((a, b) => (a.order ?? 100) - (b.order ?? 100));
export const ROUTES: AdminRoute[] = MODULES.flatMap((m) => m.routes);
export const COMMANDS: Command[] = MODULES.flatMap((m) => m.commands ?? []);

import type { ReactElement } from "react";

/** System tabs. Other modules (backups, plugins diagnostics…) append here via `registerSystemTab`. */
export type SystemTab = { path: string; label: string; permission?: string; element?: ReactElement };

export const SYSTEM_TABS: SystemTab[] = [
  { path: "", label: "Health" },
  { path: "events", label: "Events & jobs" },
  { path: "messages", label: "Messages" },
  { path: "logs", label: "Logs" },
  { path: "audit", label: "Audit trail", permission: "audit.read" },
];

export function registerSystemTab(tab: SystemTab) {
  if (!SYSTEM_TABS.some((t) => t.path === tab.path)) SYSTEM_TABS.push(tab);
}

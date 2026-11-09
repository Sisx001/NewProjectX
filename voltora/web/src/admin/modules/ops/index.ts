import { Suspense, createElement, lazy } from "react";
import type { AdminModule } from "../../registry";
import { registerSystemTab } from "../core/systemTabs";

const Backups = lazy(() => import("./Backups"));

registerSystemTab({ path: "backups", label: "Backups", permission: "system.read", element: createElement(Suspense, { fallback: null }, createElement(Backups)) });

const mod: AdminModule = {
  id: "ops",
  routes: [],
  commands: [{ label: "Backups & restore drills", path: "system/backups", permission: "system.read", group: "Go to", keywords: ["backup", "restore", "pg_dump", "disaster"] }],
};
export default mod;

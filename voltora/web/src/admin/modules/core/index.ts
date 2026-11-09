import { Settings2, ShieldCheck, Users, Activity, UserCircle } from "lucide-react";
import type { AdminModule } from "../../registry";

const mod: AdminModule = {
  id: "core",
  nav: [
    { label: "Team & roles", path: "team", icon: Users, group: "Platform", permission: "staff.read", order: 900, keywords: ["staff", "permissions", "invite"] },
    { label: "Settings", path: "settings", icon: Settings2, group: "Platform", permission: "settings.read", order: 910, keywords: ["email", "smtp", "store", "maintenance"] },
    { label: "System", path: "system", icon: Activity, group: "Platform", permission: "system.read", order: 920, keywords: ["health", "logs", "jobs", "audit", "backups"] },
  ],
  routes: [
    { path: "team", load: () => import("./Team"), permission: "staff.read" },
    { path: "settings", load: () => import("./Settings"), permission: "settings.read" },
    { path: "system/*", load: () => import("./System"), permission: "system.read" },
    { path: "account", load: () => import("./Account") },
  ],
  commands: [
    { label: "Invite a staff member", path: "team?invite=1", permission: "staff.manage", group: "Actions" },
    { label: "Security: sessions & two-step verification", path: "account", group: "Actions", keywords: ["mfa", "2fa", "password"] },
    { label: "Audit trail", path: "system/audit", permission: "audit.read", group: "Go to" },
  ],
};
export default mod;
export const icons = { ShieldCheck, UserCircle };

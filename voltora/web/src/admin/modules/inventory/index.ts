import { Warehouse } from "lucide-react";
import type { AdminModule } from "../../registry";

const mod: AdminModule = {
  id: "inventory",
  nav: [
    { label: "Inventory", path: "inventory", icon: Warehouse, group: "Operate", permission: "inventory.read", order: 30, keywords: ["stock", "warehouse", "suppliers", "purchase orders", "reorder"] },
  ],
  routes: [{ path: "inventory", load: () => import("./Inventory"), permission: "inventory.read" }],
  commands: [
    { label: "Low-stock report", path: "inventory?tab=low", permission: "inventory.read", group: "Go to", keywords: ["reorder"] },
    { label: "Stock movement ledger", path: "inventory?tab=movements", permission: "inventory.read", group: "Go to" },
    { label: "Purchase orders", path: "inventory?tab=purchasing", permission: "inventory.read", group: "Go to" },
  ],
};
export default mod;

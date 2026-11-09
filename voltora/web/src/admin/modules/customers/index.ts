import { Users } from "lucide-react";
import type { AdminModule } from "../../registry";

const mod: AdminModule = {
  id: "customers",
  nav: [
    { label: "Customers", path: "customers", icon: Users, group: "Operate", permission: "customers.read", order: 40, keywords: ["buyers", "people", "consent", "privacy", "erase"] },
  ],
  routes: [
    { path: "customers", load: () => import("./Customers"), permission: "customers.read" },
    { path: "customers/:id", load: () => import("./CustomerDetail"), permission: "customers.read" },
  ],
};
export default mod;

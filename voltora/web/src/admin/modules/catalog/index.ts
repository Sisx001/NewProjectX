import { Package, Tags } from "lucide-react";
import type { AdminModule } from "../../registry";

const mod: AdminModule = {
  id: "catalog",
  nav: [
    { label: "Products", path: "catalog/products", icon: Package, group: "Operate", permission: "catalog.read", order: 20, mobile: true, keywords: ["catalog", "sku", "variants", "items"] },
    { label: "Categories & brands", path: "catalog/taxonomy", icon: Tags, group: "Operate", permission: "catalog.read", order: 21, keywords: ["collections", "brand", "category", "taxonomy"] },
  ],
  routes: [
    { path: "catalog/products", load: () => import("./Products"), permission: "catalog.read" },
    { path: "catalog/products/:id", load: () => import("./ProductEditor"), permission: "catalog.read" },
    { path: "catalog/taxonomy", load: () => import("./Taxonomy"), permission: "catalog.read" },
  ],
  commands: [
    { label: "New product", path: "catalog/products/new", permission: "catalog.write", group: "Actions", keywords: ["add", "create"] },
    { label: "Import products from CSV", path: "catalog/products?import=1", permission: "catalog.write", group: "Actions" },
    { label: "Categories & brands", path: "catalog/taxonomy", permission: "catalog.read", group: "Go to" },
  ],
};
export default mod;

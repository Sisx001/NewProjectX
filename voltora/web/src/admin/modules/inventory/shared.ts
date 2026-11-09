import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";

export type Level = { warehouse_id: string; warehouse: string; code: string; on_hand: number; reserved: number; available: number; reorder_point: number; warehouse_active: boolean };
export type StockRow = { variant_id: string; sku: string; variant_title: string; product_id: string; product_title: string; track_inventory: boolean; allow_backorder: boolean; on_hand: number; reserved: number; available: number; reorder_point: number; state: "ok" | "low" | "out" | "untracked"; is_demo: boolean; levels: Level[] };
export type Wh = { id: string; name: string; code: string; is_default: boolean; is_active: boolean; district: string | null; units_on_hand: number; variants_stocked: number; address: Record<string, string> };

export function useWarehouses() {
  return useQuery({ queryKey: ["inventory", "warehouses"], queryFn: () => get<{ items: Wh[] }>("/api/inventory/warehouses") });
}

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { EyeOff, Search } from "lucide-react";
import { errorMessage, get, qs, type Paged } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { Alert, Badge, Empty, Input, Select, relTime } from "@/ui";
import { DataTable, SavedViews } from "@/ui/DataTable";
import { PageHead } from "../../AdminApp";
import { useDebounced } from "../catalog/common";

export type Customer = {
  id: string; name: string; email: string | null; phone: string | null; tags: string[]; note: string; marketing_consent: boolean; consent_updated_at: string | null;
  orders_count: number; total_spent_minor: number; currency: string; last_order_at: string | null; has_account: boolean; is_test: boolean; erased: boolean; pii_masked: boolean; created_at: string;
};
type Filters = { q: string; tag: string; consent: string; has_orders: string; sort: string };
const EMPTY: Filters = { q: "", tag: "", consent: "", has_orders: "", sort: "recent" };

export default function Customers() {
  const nav = useNavigate();
  const [f, setF] = useState<Filters>(EMPTY);
  const [page, setPage] = useState(1);
  const dq = useDebounced(f.q);
  const query = { ...f, q: dq, page, page_size: 25 };
  const list = useQuery({ queryKey: ["customers", query], queryFn: () => get<Paged<Customer> & { pii_masked: boolean }>("/api/customers" + qs(query)), placeholderData: (p) => p });
  const tags = useQuery({ queryKey: ["customers", "tags"], queryFn: () => get<{ items: string[] }>("/api/customers/tags") });
  const set = (p: Partial<Filters>) => { setF({ ...f, ...p }); setPage(1); };
  const filtered = !!(f.q || f.tag || f.consent || f.has_orders);
  return (
    <>
      <PageHead title="Customers" description="Everyone who has checked out or created an account. Customers appear automatically; there is nothing to import." />
      {list.data?.pii_masked ? <div style={{ marginBottom: 16 }}><Alert tone="info" title="Contact details are masked"><span className="v-row" style={{ gap: 6 }}><EyeOff size={14} aria-hidden /> Your role can't see full email addresses and phone numbers. Search by exact email or phone to find a customer.</span></Alert></div> : null}
      <div className="a-toolbar">
        <div style={{ position: "relative", flex: "1 1 240px", maxWidth: 380 }}>
          <Search size={16} style={{ position: "absolute", left: 10, top: 11, color: "var(--v-ink-3)" }} aria-hidden />
          <Input aria-label="Search customers" placeholder={list.data?.pii_masked ? "Search name, or exact email / phone" : "Search name, email or phone"} value={f.q} onChange={(e) => set({ q: e.target.value })} style={{ paddingLeft: 32 }} />
        </div>
        <Select aria-label="Tag" value={f.tag} onChange={(e) => set({ tag: e.target.value })} style={{ width: "auto" }}><option value="">Any tag</option>{tags.data?.items.map((t) => <option key={t}>{t}</option>)}</Select>
        <Select aria-label="Marketing consent" value={f.consent} onChange={(e) => set({ consent: e.target.value })} style={{ width: "auto" }}><option value="">Any consent</option><option value="yes">Opted in</option><option value="no">Not opted in</option></Select>
        <Select aria-label="Orders" value={f.has_orders} onChange={(e) => set({ has_orders: e.target.value })} style={{ width: "auto" }}><option value="">With or without orders</option><option value="yes">Has orders</option><option value="no">No orders yet</option></Select>
        <Select aria-label="Sort" value={f.sort} onChange={(e) => set({ sort: e.target.value })} style={{ width: "auto" }}><option value="recent">Newest first</option><option value="name">Name</option><option value="spent">Highest spend</option><option value="orders">Most orders</option></Select>
      </div>
      <div style={{ marginBottom: 12 }}><SavedViews id="customers" current={f as unknown as Record<string, unknown>} onApply={(v) => { setF({ ...EMPTY, ...(v as unknown as Filters) }); setPage(1); }} /></div>
      <DataTable<Customer>
        label="Customers" rows={list.data?.items} loading={list.isLoading} error={list.error ? errorMessage(list.error) : null} rowKey={(c) => c.id}
        onRowClick={(c) => nav(`/admin/customers/${c.id}`)} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
        empty={<Empty title={filtered ? "No customers match" : "No customers yet"}>{filtered ? "Try different filters." : "Customers are created when someone checks out or registers on the storefront."}</Empty>}
        columns={[
          { key: "n", header: "Customer", cell: (c) => <div><span style={{ fontWeight: 550 }}>{c.name || "Unnamed"}</span> {c.erased ? <Badge plain>erased</Badge> : null}{c.is_test ? <Badge tone="info" plain>test</Badge> : null}<div className="v-muted" style={{ fontSize: 12 }}>{c.email ?? c.phone ?? "—"}</div></div> },
          { key: "t", header: "Tags", hideBelow: 800, cell: (c) => <span className="v-row v-row--wrap" style={{ gap: 4 }}>{c.tags.map((t) => <Badge key={t} plain>{t}</Badge>)}</span> },
          { key: "m", header: "Marketing", hideBelow: 900, cell: (c) => (c.marketing_consent ? <Badge tone="success" plain>opted in</Badge> : <span className="v-muted">no</span>) },
          { key: "o", header: "Orders", align: "right", cell: (c) => c.orders_count },
          { key: "s", header: "Spent", align: "right", hideBelow: 600, cell: (c) => formatMoney(c.total_spent_minor, c.currency) },
          { key: "l", header: "Last order", hideBelow: 1000, cell: (c) => <span className="v-muted">{c.last_order_at ? relTime(c.last_order_at) : "—"}</span> },
        ]}
      />
    </>
  );
}

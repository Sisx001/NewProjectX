import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightLeft, ClipboardCheck, PackagePlus, SlidersHorizontal, Bell } from "lucide-react";
import { errorMessage, get, post, put, qs, type Paged } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Badge, Button, Dialog, Empty, Field, Input, Select, Tabs, Textarea, TextField, dateTime, useToast } from "@/ui";
import { DataTable, SavedViews } from "@/ui/DataTable";
import { PageHead } from "../../AdminApp";
import { Warehouses, Suppliers } from "./Warehouses";
import { Purchasing } from "./Purchasing";
import { useWarehouses, type Level, type StockRow } from "./shared";
import { useDebounced } from "../catalog/common";


type Tab = "stock" | "low" | "movements" | "warehouses" | "suppliers" | "purchasing";
const TABS: Tab[] = ["stock", "low", "movements", "warehouses", "suppliers", "purchasing"];

export default function Inventory() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.includes(params.get("tab") as Tab) ? params.get("tab") : "stock") as Tab;
  const setTab = (t: Tab) => { const p = new URLSearchParams(params); p.set("tab", t); setParams(p, { replace: true }); };
  const low = useQuery({ queryKey: ["inventory", "low"], queryFn: () => get<{ items: unknown[] }>("/api/inventory/low-stock") });
  return (
    <>
      <PageHead title="Inventory" description="Stock by warehouse, every movement in an immutable ledger, and purchasing to refill it." />
      <Tabs<Tab> label="Inventory sections" value={tab} onChange={setTab} items={[
        { value: "stock", label: "Stock" }, { value: "low", label: "Low stock", count: low.data?.items.length }, { value: "movements", label: "Movements" },
        { value: "warehouses", label: "Warehouses" }, { value: "suppliers", label: "Suppliers" }, { value: "purchasing", label: "Purchase orders" },
      ]} />
      <div style={{ height: 16 }} />
      {tab === "stock" ? <StockTab initialQ={params.get("q") ?? ""} /> : null}
      {tab === "low" ? <LowStock /> : null}
      {tab === "movements" ? <Movements /> : null}
      {tab === "warehouses" ? <Warehouses /> : null}
      {tab === "suppliers" ? <Suppliers /> : null}
      {tab === "purchasing" ? <Purchasing /> : null}
    </>
  );
}

const STATE_TONE = { ok: "success", low: "warning", out: "danger", untracked: "neutral" } as const;
const STATE_LABEL = { ok: "In stock", low: "Low", out: "Out of stock", untracked: "Not tracked" } as const;
type Filters = { q: string; warehouse_id: string; state: string };

function StockTab({ initialQ }: { initialQ: string }) {
  const { can } = useAuth();
  const adjust = can("inventory.adjust");
  const [f, setF] = useState<Filters>({ q: initialQ, warehouse_id: "", state: "" });
  const [page, setPage] = useState(1);
  const [dialog, setDialog] = useState<{ kind: DialogKind; row: StockRow } | null>(null);
  const dq = useDebounced(f.q);
  const whs = useWarehouses();
  const query = { q: dq, warehouse_id: f.warehouse_id, state: f.state, page, page_size: 25 };
  const list = useQuery({ queryKey: ["inventory", "stock", query], queryFn: () => get<Paged<StockRow>>("/api/inventory/stock" + qs(query)), placeholderData: (p) => p });
  const set = (p: Partial<Filters>) => { setF({ ...f, ...p }); setPage(1); };
  return (
    <>
      <div className="a-toolbar">
        <Input aria-label="Search stock" placeholder="Search product, SKU or barcode" value={f.q} onChange={(e) => set({ q: e.target.value })} style={{ flex: "1 1 220px", maxWidth: 360 }} />
        <Select aria-label="Warehouse" value={f.warehouse_id} onChange={(e) => set({ warehouse_id: e.target.value })} style={{ width: "auto" }}>
          <option value="">All warehouses</option>{whs.data?.items.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </Select>
        <Select aria-label="Stock state" value={f.state} onChange={(e) => set({ state: e.target.value })} style={{ width: "auto" }}>
          <option value="">Any state</option><option value="ok">In stock</option><option value="low">Low</option><option value="out">Out of stock</option><option value="untracked">Not tracked</option>
        </Select>
        <SavedViews id="inventory-stock" current={f as unknown as Record<string, unknown>} onApply={(v) => { setF(v as unknown as Filters); setPage(1); }} />
      </div>
      <DataTable<StockRow>
        label="Stock levels" rows={list.data?.items} loading={list.isLoading} error={list.error ? errorMessage(list.error) : null} rowKey={(r) => r.variant_id}
        page={page} pageSize={25} total={list.data?.total} onPage={setPage}
        empty={<Empty title="No stock to show">{f.q || f.state || f.warehouse_id ? "No variants match these filters." : "Create products first; their variants appear here."}</Empty>}
        columns={[
          { key: "p", header: "Product", cell: (r) => (
            <div style={{ minWidth: 0 }}>
              <Link to={`/admin/catalog/products/${r.product_id}`} style={{ fontWeight: 550 }}>{r.product_title}</Link> {r.is_demo ? <Badge tone="info" plain>Demo</Badge> : null}
              <div className="v-muted" style={{ fontSize: 12 }}><span className="v-mono">{r.sku}</span>{r.variant_title !== "Default" ? ` · ${r.variant_title}` : ""}</div>
              {r.levels.length > 1 || r.levels.some((l) => !l.warehouse_active) ? <div className="v-muted v-mono" style={{ fontSize: 11 }}>{r.levels.map((l) => `${l.code} ${l.on_hand}`).join(" · ")}</div> : null}
            </div>) },
          { key: "state", header: "State", cell: (r) => <Badge tone={STATE_TONE[r.state]}>{STATE_LABEL[r.state]}</Badge> },
          { key: "oh", header: "On hand", align: "right", cell: (r) => (r.track_inventory ? r.on_hand : "—") },
          { key: "rs", header: "Reserved", align: "right", hideBelow: 700, cell: (r) => (r.track_inventory ? r.reserved : "—") },
          { key: "av", header: "Available", align: "right", cell: (r) => (r.track_inventory ? <strong>{r.available}</strong> : "∞") },
          { key: "ro", header: "Reorder at", align: "right", hideBelow: 900, cell: (r) => (r.track_inventory && r.reorder_point ? r.reorder_point : <span className="v-muted">—</span>) },
          ...(adjust ? [{ key: "act", header: <span className="v-sr">Actions</span>, align: "right" as const, cell: (r: StockRow) => r.track_inventory ? (
            <div className="v-row" style={{ gap: 4, justifyContent: "flex-end" }}>
              <Button size="sm" onClick={() => setDialog({ kind: "receive", row: r })}><PackagePlus size={14} /> Receive</Button>
              <Button size="sm" onClick={() => setDialog({ kind: "adjust", row: r })} aria-label={`Adjust ${r.sku}`}><SlidersHorizontal size={14} /> Adjust</Button>
              <Button size="sm" icon aria-label={`Transfer ${r.sku}`} title="Transfer between warehouses" onClick={() => setDialog({ kind: "transfer", row: r })}><ArrowRightLeft size={14} /></Button>
              <Button size="sm" icon aria-label={`Count ${r.sku}`} title="Cycle count" onClick={() => setDialog({ kind: "count", row: r })}><ClipboardCheck size={14} /></Button>
              <Button size="sm" icon aria-label={`Reorder point for ${r.sku}`} title="Reorder point" onClick={() => setDialog({ kind: "reorder", row: r })}><Bell size={14} /></Button>
            </div>) : <span className="v-muted">—</span> }] : []),
        ]}
      />
      {dialog ? <StockDialog kind={dialog.kind} row={dialog.row} onClose={() => setDialog(null)} /> : null}
    </>
  );
}

export type DialogKind = "receive" | "adjust" | "transfer" | "count" | "reorder";
const TITLES: Record<DialogKind, string> = { receive: "Receive stock", adjust: "Adjust stock", transfer: "Transfer stock", count: "Cycle count", reorder: "Reorder point" };

export function StockDialog({ kind, row, onClose, defaultWarehouse }: { kind: DialogKind; row: { variant_id: string; sku: string; product_title: string; variant_title?: string; levels?: Level[] }; onClose: () => void; defaultWarehouse?: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const whs = useWarehouses();
  const active = (whs.data?.items ?? []).filter((w) => w.is_active);
  const def = defaultWarehouse || active.find((w) => w.is_default)?.id || active[0]?.id || "";
  const [wh, setWh] = useState(def);
  const [to, setTo] = useState("");
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState("adjust");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const reasons = useQuery({ queryKey: ["inventory", "reasons"], queryFn: () => get<{ items: { code: string; label: string }[] }>("/api/inventory/reasons"), enabled: kind === "adjust" });
  const lv = row.levels?.find((l) => l.warehouse_id === (wh || def));
  const wid = wh || def;
  const num = Number(qty);
  const valid = qty.trim() !== "" && Number.isInteger(num) && (kind === "adjust" ? num !== 0 : kind === "count" || kind === "reorder" ? num >= 0 : num > 0) && !!wid && (kind !== "transfer" || (!!to && to !== wid));
  const run = useMutation({
    mutationFn: () => {
      const base = { variant_id: row.variant_id, warehouse_id: wid };
      switch (kind) {
        case "receive": return post("/api/inventory/receive", { ...base, quantity: num, note: note || null });
        case "adjust": return post("/api/inventory/adjust", { ...base, delta: reason === "damage" || reason === "loss" ? -Math.abs(num) : reason === "return" ? Math.abs(num) : num, reason, note: note || null });
        case "transfer": return post("/api/inventory/transfer", { variant_id: row.variant_id, from_warehouse_id: wid, to_warehouse_id: to, quantity: num, note: note || null });
        case "count": return post("/api/inventory/count", { ...base, counted: num, note: note || null });
        case "reorder": return put("/api/inventory/reorder-point", { ...base, reorder_point: num });
      }
    },
    onSuccess: () => { toast(`${TITLES[kind]} saved`); qc.invalidateQueries({ queryKey: ["inventory"] }); qc.invalidateQueries({ queryKey: ["catalog"] }); qc.invalidateQueries({ queryKey: ["nav-counts"] }); onClose(); },
    onError: (e) => setErr(errorMessage(e)),
  });
  const qtyLabel = { receive: "Quantity received", adjust: reason === "damage" || reason === "loss" ? "Units removed" : reason === "return" ? "Units returned" : "Change (+ adds, − removes)", transfer: "Quantity to move", count: "Counted quantity", reorder: "Reorder when available is at or below" }[kind];
  return (
    <Dialog open onClose={onClose} title={TITLES[kind]} description={<><span className="v-mono">{row.sku}</span> · {row.product_title}</>}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={run.isPending} disabled={!valid} onClick={() => run.mutate()}>{TITLES[kind]}</Button></>}>
      <div className="v-stack">
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <Field label={kind === "transfer" ? "From warehouse" : "Warehouse"}>{(id) => <Select id={id} value={wid} onChange={(e) => setWh(e.target.value)}>{active.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.code})</option>)}</Select>}</Field>
        {lv ? <p className="v-muted" style={{ margin: 0 }}>Currently {lv.on_hand} on hand, {lv.reserved} reserved, {lv.available} available.</p> : null}
        {kind === "transfer" ? <Field label="To warehouse">{(id) => <Select id={id} value={to} onChange={(e) => setTo(e.target.value)}><option value="">Choose…</option>{active.filter((w) => w.id !== wid).map((w) => <option key={w.id} value={w.id}>{w.name} ({w.code})</option>)}</Select>}</Field> : null}
        {kind === "adjust" ? <Field label="Reason">{(id) => <Select id={id} value={reason} onChange={(e) => setReason(e.target.value)}>{(reasons.data?.items ?? [{ code: "adjust", label: "Stock correction" }]).map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}</Select>}</Field> : null}
        <TextField label={qtyLabel} type="number" inputMode="numeric" value={qty} onChange={setQty} autoFocus data-autofocus />
        {kind !== "reorder" ? <Field label="Note (optional)">{(id) => <Textarea id={id} rows={2} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />}</Field> : <p className="v-hint" style={{ margin: 0 }}>Set 0 to turn off low-stock alerts for this warehouse.</p>}
      </div>
    </Dialog>
  );
}

type LowRow = { variant_id: string; sku: string; variant_title: string; product_id: string; product_title: string; warehouse_id: string; warehouse: string; available: number; on_hand: number; reserved: number; reorder_point: number; suggested_reorder: number };

function LowStock() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["inventory", "low"], queryFn: () => get<{ items: LowRow[] }>("/api/inventory/low-stock") });
  const [recv, setRecv] = useState<LowRow | null>(null);
  return (
    <>
      <p className="v-muted" style={{ marginTop: 0 }}>Variants whose available units are at or below the reorder point of a warehouse. Set reorder points from the Stock tab.</p>
      <DataTable<LowRow>
        label="Low stock" rows={q.data?.items} loading={q.isLoading} error={q.error ? errorMessage(q.error) : null} rowKey={(r) => r.variant_id + r.warehouse_id}
        empty={<Empty title="Nothing is below its reorder point">Items appear here when available stock drops to the reorder point you set.</Empty>}
        columns={[
          { key: "p", header: "Product", cell: (r) => <div><Link to={`/admin/catalog/products/${r.product_id}`} style={{ fontWeight: 550 }}>{r.product_title}</Link><div className="v-muted v-mono" style={{ fontSize: 12 }}>{r.sku}</div></div> },
          { key: "w", header: "Warehouse", cell: (r) => r.warehouse },
          { key: "a", header: "Available", align: "right", cell: (r) => <Badge tone={r.available <= 0 ? "danger" : "warning"}>{r.available}</Badge> },
          { key: "r", header: "Reorder at", align: "right", cell: (r) => r.reorder_point },
          { key: "s", header: "Suggested order", align: "right", hideBelow: 700, cell: (r) => r.suggested_reorder },
          ...(can("inventory.adjust") ? [{ key: "act", header: <span className="v-sr">Actions</span>, align: "right" as const, cell: (r: LowRow) => <Button size="sm" onClick={() => setRecv(r)}><PackagePlus size={14} /> Receive</Button> }] : []),
        ]}
      />
      {recv ? <StockDialog kind="receive" defaultWarehouse={recv.warehouse_id} row={{ variant_id: recv.variant_id, sku: recv.sku, product_title: recv.product_title }} onClose={() => setRecv(null)} /> : null}
    </>
  );
}

type Move = { id: number; at: string; sku: string; product_title: string; variant_title: string; warehouse: string; warehouse_code: string; reason: string; delta_on_hand: number; delta_reserved: number; balance_on_hand: number; note: string | null; actor: string | null; ref_type: string | null };
const REASONS = ["receive", "adjust", "count", "damage", "return", "transfer_in", "transfer_out", "reserve", "release", "ship"];

function Movements() {
  const [f, setF] = useState({ q: "", reason: "", warehouse_id: "", date_from: "", date_to: "" });
  const [page, setPage] = useState(1);
  const dq = useDebounced(f.q);
  const whs = useWarehouses();
  const query = { ...f, q: dq, page, page_size: 50 };
  const list = useQuery({ queryKey: ["inventory", "movements", query], queryFn: () => get<Paged<Move>>("/api/inventory/movements" + qs(query)), placeholderData: (p) => p });
  const set = (p: Partial<typeof f>) => { setF({ ...f, ...p }); setPage(1); };
  return (
    <>
      <div className="a-toolbar">
        <Input aria-label="Search movements" placeholder="Search product or SKU" value={f.q} onChange={(e) => set({ q: e.target.value })} style={{ flex: "1 1 200px", maxWidth: 300 }} />
        <Select aria-label="Reason" value={f.reason} onChange={(e) => set({ reason: e.target.value })} style={{ width: "auto" }}><option value="">All reasons</option>{REASONS.map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}</Select>
        <Select aria-label="Warehouse" value={f.warehouse_id} onChange={(e) => set({ warehouse_id: e.target.value })} style={{ width: "auto" }}><option value="">All warehouses</option>{whs.data?.items.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</Select>
        <Input aria-label="From date" type="date" value={f.date_from} onChange={(e) => set({ date_from: e.target.value })} style={{ width: "auto" }} />
        <Input aria-label="To date" type="date" value={f.date_to} onChange={(e) => set({ date_to: e.target.value })} style={{ width: "auto" }} />
      </div>
      <DataTable<Move>
        label="Stock movements" rows={list.data?.items} loading={list.isLoading} error={list.error ? errorMessage(list.error) : null} rowKey={(m) => String(m.id)}
        page={page} pageSize={50} total={list.data?.total} onPage={setPage}
        empty={<Empty title="No movements recorded">Every receipt, adjustment, transfer, reservation and shipment is logged here.</Empty>}
        columns={[
          { key: "at", header: "When", cell: (m) => <span className="v-muted" style={{ whiteSpace: "nowrap" }}>{dateTime(m.at)}</span> },
          { key: "p", header: "Product", cell: (m) => <div><div>{m.product_title}</div><div className="v-muted v-mono" style={{ fontSize: 12 }}>{m.sku}</div></div> },
          { key: "w", header: "Warehouse", hideBelow: 700, cell: (m) => m.warehouse_code },
          { key: "r", header: "Reason", cell: (m) => <Badge plain>{m.reason.replace("_", " ")}</Badge> },
          { key: "d", header: "Change", align: "right", cell: (m) => <strong style={{ color: m.delta_on_hand < 0 ? "var(--v-danger, #b3261e)" : undefined }}>{m.delta_on_hand > 0 ? "+" : ""}{m.delta_on_hand}{m.delta_reserved ? <span className="v-muted"> ({m.delta_reserved > 0 ? "+" : ""}{m.delta_reserved} res.)</span> : null}</strong> },
          { key: "b", header: "Balance", align: "right", hideBelow: 700, cell: (m) => m.balance_on_hand },
          { key: "n", header: "Note", hideBelow: 1000, cell: (m) => <span className="v-muted">{m.note ?? ""}</span> },
          { key: "u", header: "By", hideBelow: 1100, cell: (m) => <span className="v-muted">{m.actor ?? (m.ref_type ? "system" : "")}</span> },
        ]}
      />
    </>
  );
}

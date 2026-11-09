import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { errorMessage, get, patch, post, qs, type Paged } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatMoney, parseMoney, toMajorInput } from "@/lib/money";
import { Alert, Badge, Button, Confirm, Drawer, Empty, Field, Input, Select, StatusBadge, Textarea, TextField, dateTime, useToast } from "@/ui";
import { DataTable } from "@/ui/DataTable";
import { useDebounced } from "../catalog/common";
import { useWarehouses, type StockRow } from "./shared";
import { useSuppliers } from "./Warehouses";

type PO = { id: string; number: string; status: string; supplier_id: string; supplier: string | null; warehouse_id: string; warehouse: string | null; reference: string; note: string; expected_at: string | null; ordered_at: string | null; received_at: string | null; created_at: string; total_minor: number; currency: string; units: number; units_received: number; lines?: Line[] };
type Line = { id: string; variant_id: string; sku: string; product_title: string; variant_title: string; quantity: number; received_quantity: number; unit_cost_minor: number };

const STATUSES = ["draft", "ordered", "partially_received", "received", "cancelled"];
const TONE: Record<string, "neutral" | "info" | "warning" | "success" | "danger"> = { draft: "neutral", ordered: "info", partially_received: "warning", received: "success", cancelled: "danger" };

export function Purchasing() {
  const { can } = useAuth();
  const write = can("inventory.adjust");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | "new" | null>(null);
  const q = useQuery({ queryKey: ["inventory", "pos", status, page], queryFn: () => get<Paged<PO>>("/api/inventory/purchase-orders" + qs({ status, page, page_size: 25 })) });
  return (
    <>
      <div className="a-toolbar">
        <Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} style={{ width: "auto" }}>
          <option value="">All statuses</option>{STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
        </Select>
        <span style={{ flex: 1 }} />
        {write ? <Button variant="primary" onClick={() => setOpen("new")}><Plus size={16} /> New purchase order</Button> : null}
      </div>
      <DataTable<PO>
        label="Purchase orders" rows={q.data?.items} loading={q.isLoading} error={q.error ? errorMessage(q.error) : null} rowKey={(p) => p.id} onRowClick={(p) => setOpen(p.id)}
        page={page} pageSize={25} total={q.data?.total} onPage={setPage}
        empty={<Empty title="No purchase orders" action={write ? <Button variant="primary" onClick={() => setOpen("new")}>Create one</Button> : undefined}>Order stock from a supplier, then receive it into a warehouse as it arrives.</Empty>}
        columns={[
          { key: "n", header: "Number", cell: (p) => <span className="v-mono" style={{ fontWeight: 550 }}>{p.number}</span> },
          { key: "s", header: "Supplier", cell: (p) => p.supplier },
          { key: "st", header: "Status", cell: (p) => <Badge tone={TONE[p.status] ?? "neutral"}>{p.status.replace("_", " ")}</Badge> },
          { key: "u", header: "Received", align: "right", hideBelow: 700, cell: (p) => `${p.units_received} / ${p.units}` },
          { key: "t", header: "Total", align: "right", cell: (p) => formatMoney(p.total_minor, p.currency) },
          { key: "e", header: "Expected", hideBelow: 900, cell: (p) => (p.expected_at ? new Date(p.expected_at).toLocaleDateString() : <span className="v-muted">—</span>) },
        ]}
      />
      {open ? <PODrawer id={open === "new" ? null : open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

type FormLine = { variant_id: string; sku: string; label: string; quantity: string; cost: string };

function PODrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { can, me } = useAuth();
  const cur = me?.store.currency ?? "BDT";
  const write = can("inventory.adjust");
  const qc = useQueryClient();
  const toast = useToast();
  const [poId, setPoId] = useState<string | null>(id);
  const po = useQuery({ queryKey: ["inventory", "po", poId], queryFn: () => get<PO>(`/api/inventory/purchase-orders/${poId}`), enabled: !!poId });
  const suppliers = useSuppliers();
  const whs = useWarehouses();
  const [init, setInit] = useState<string | null>(null);
  const [supplier, setSupplier] = useState("");
  const [warehouse, setWarehouse] = useState("");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [expected, setExpected] = useState("");
  const [lines, setLines] = useState<FormLine[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [recv, setRecv] = useState<Record<string, string>>({});
  const [confirmCancel, setConfirmCancel] = useState(false);

  const data = po.data;
  if (data && init !== data.id + data.status + data.units_received) {
    setInit(data.id + data.status + data.units_received);
    setSupplier(data.supplier_id); setWarehouse(data.warehouse_id); setReference(data.reference); setNote(data.note); setExpected(data.expected_at ?? "");
    setLines((data.lines ?? []).map((l) => ({ variant_id: l.variant_id, sku: l.sku, label: `${l.product_title}${l.variant_title !== "Default" ? " · " + l.variant_title : ""}`, quantity: String(l.quantity), cost: toMajorInput(l.unit_cost_minor, cur) })));
    setRecv(Object.fromEntries((data.lines ?? []).map((l) => [l.id, String(l.quantity - l.received_quantity)])));
  }
  const activeSuppliers = (suppliers.data?.items ?? []).filter((s) => s.is_active || s.id === supplier);
  const activeWh = (whs.data?.items ?? []).filter((w) => w.is_active || w.id === warehouse);
  const defWh = activeWh.find((w) => w.is_default)?.id ?? "";
  const editable = !data || data.status === "draft";

  const refresh = () => { qc.invalidateQueries({ queryKey: ["inventory"] }); qc.invalidateQueries({ queryKey: ["catalog"] }); };
  const body = () => {
    const out = lines.map((l) => ({ variant_id: l.variant_id, quantity: Number(l.quantity), unit_cost_minor: l.cost.trim() ? parseMoney(l.cost, cur) : 0 }));
    if (out.some((l) => !Number.isInteger(l.quantity) || l.quantity <= 0 || l.unit_cost_minor === null || l.unit_cost_minor < 0)) throw new Error("Each line needs a whole quantity above zero and a valid unit cost.");
    return { supplier_id: supplier, warehouse_id: warehouse || defWh, reference, note, expected_at: expected || null, lines: out };
  };
  const save = useMutation({
    mutationFn: () => (poId ? patch<PO>(`/api/inventory/purchase-orders/${poId}`, body()) : post<PO>("/api/inventory/purchase-orders", body())),
    onSuccess: (p) => { toast("Purchase order saved"); setPoId(p.id); setInit(null); setErr(null); refresh(); qc.setQueryData(["inventory", "po", p.id], p); },
    onError: (e) => setErr(errorMessage(e)),
  });
  const act = useMutation({
    mutationFn: (a: "order" | "cancel") => post<PO>(`/api/inventory/purchase-orders/${poId}/${a}`),
    onSuccess: (p, a) => { toast(a === "order" ? "Order placed" : "Purchase order cancelled"); setInit(null); setConfirmCancel(false); refresh(); qc.setQueryData(["inventory", "po", p.id], p); },
    onError: (e) => { setConfirmCancel(false); setErr(errorMessage(e)); },
  });
  const receive = useMutation({
    mutationFn: () => post<PO>(`/api/inventory/purchase-orders/${poId}/receive`, { lines: (data?.lines ?? []).map((l) => ({ line_id: l.id, quantity: Number(recv[l.id] || 0) })).filter((l) => l.quantity > 0) }),
    onSuccess: (p) => { toast("Stock received into " + (p.warehouse ?? "warehouse")); setInit(null); setErr(null); refresh(); qc.setQueryData(["inventory", "po", p.id], p); },
    onError: (e) => setErr(errorMessage(e)),
  });
  const total = lines.reduce((s, l) => s + (Number(l.quantity) || 0) * (parseMoney(l.cost || "0", cur) ?? 0), 0);
  const canReceive = write && data && (data.status === "ordered" || data.status === "partially_received");

  return (
    <Drawer open onClose={onClose} title={data ? `${data.number}` : "New purchase order"} width={720}
      footer={<>
        {data && (data.status === "draft" || data.status === "ordered") && write ? <Button variant="ghost" onClick={() => setConfirmCancel(true)}>Cancel order</Button> : null}
        <span style={{ flex: 1 }} /><Button onClick={onClose}>Close</Button>
        {editable && write ? <Button loading={save.isPending} disabled={!supplier || lines.length === 0} onClick={() => save.mutate()}>Save draft</Button> : null}
        {data?.status === "draft" && write ? <Button variant="primary" loading={act.isPending} disabled={lines.length === 0} onClick={() => act.mutate("order")}>Place order</Button> : null}
        {canReceive ? <Button variant="primary" loading={receive.isPending} onClick={() => receive.mutate()}>Receive stock</Button> : null}
      </>}>
      <div className="v-stack">
        {err ? <Alert tone="danger">{err}</Alert> : null}
        {data ? <div className="v-row v-row--wrap"><StatusBadge status={data.status} />{data.ordered_at ? <span className="v-muted">Ordered {dateTime(data.ordered_at)}</span> : null}{data.received_at ? <span className="v-muted">Completed {dateTime(data.received_at)}</span> : null}</div> : null}
        <div className="v-grid-2">
          <Field label="Supplier">{(fid) => <Select id={fid} value={supplier} disabled={!editable} onChange={(e) => setSupplier(e.target.value)}><option value="">Choose…</option>{activeSuppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select>}</Field>
          <Field label="Receive into">{(fid) => <Select id={fid} value={warehouse || defWh} disabled={!editable} onChange={(e) => setWarehouse(e.target.value)}>{activeWh.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</Select>}</Field>
          <TextField label="Supplier reference" value={reference} onChange={setReference} disabled={!editable} maxLength={80} />
          <TextField label="Expected on" type="date" value={expected} onChange={setExpected} disabled={!editable} />
        </div>
        {suppliers.data && suppliers.data.items.length === 0 ? <Alert tone="warning">Add a supplier in the Suppliers tab before creating a purchase order.</Alert> : null}
        <Field label="Notes">{(fid) => <Textarea id={fid} rows={2} value={note} disabled={!editable} onChange={(e) => setNote(e.target.value)} />}</Field>

        <h3 style={{ margin: "8px 0 0", fontSize: 15 }}>Lines</h3>
        {lines.length === 0 ? <p className="v-muted" style={{ margin: 0 }}>No lines yet. Search for a variant below.</p> : (
          <div className="v-table-wrap"><table className="v-table" aria-label="Purchase order lines">
            <thead><tr><th>Item</th><th style={{ width: 90 }}>Qty</th><th style={{ width: 110 }}>Unit cost ({cur})</th>{data && !editable ? <><th>Received</th>{canReceive ? <th style={{ width: 100 }}>Receive now</th> : null}</> : null}{editable ? <th style={{ width: 48 }}><span className="v-sr">Remove</span></th> : null}</tr></thead>
            <tbody>{lines.map((l, i) => {
              const dl = data?.lines?.[i];
              return (
                <tr key={l.variant_id}>
                  <td><div>{l.label}</div><div className="v-muted v-mono" style={{ fontSize: 12 }}>{l.sku}</div></td>
                  <td><Input aria-label={`Quantity for ${l.sku}`} type="number" min={1} value={l.quantity} disabled={!editable} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} /></td>
                  <td><Input aria-label={`Unit cost for ${l.sku}`} inputMode="decimal" value={l.cost} disabled={!editable} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, cost: e.target.value } : x)))} /></td>
                  {data && !editable ? <>
                    <td className="v-num">{dl?.received_quantity ?? 0} / {dl?.quantity}</td>
                    {canReceive && dl ? <td><Input aria-label={`Receive now for ${l.sku}`} type="number" min={0} max={dl.quantity - dl.received_quantity} value={recv[dl.id] ?? "0"} disabled={dl.quantity === dl.received_quantity} onChange={(e) => setRecv({ ...recv, [dl.id]: e.target.value })} /></td> : null}
                  </> : null}
                  {editable ? <td><Button size="sm" icon aria-label={`Remove ${l.sku}`} onClick={() => setLines(lines.filter((_, j) => j !== i))}><Trash2 size={14} /></Button></td> : null}
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
        <div className="v-right"><strong>Total {formatMoney(editable ? total : data?.total_minor, cur)}</strong></div>
        {editable && write ? <VariantPicker exclude={new Set(lines.map((l) => l.variant_id))} onPick={(r) => setLines([...lines, { variant_id: r.variant_id, sku: r.sku, label: `${r.product_title}${r.variant_title !== "Default" ? " · " + r.variant_title : ""}`, quantity: "1", cost: "" }])} /> : null}
      </div>
      <Confirm open={confirmCancel} onClose={() => setConfirmCancel(false)} onConfirm={() => (poId ? act.mutate("cancel") : onClose())} busy={act.isPending} danger title="Cancel this purchase order?" confirmLabel="Cancel order">
        Cancelled orders can't be reopened. Orders with received stock can't be cancelled.
      </Confirm>
    </Drawer>
  );
}

function VariantPicker({ onPick, exclude }: { onPick: (r: StockRow) => void; exclude: Set<string> }) {
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim());
  const res = useQuery({ queryKey: ["inventory", "picker", dq], queryFn: () => get<Paged<StockRow>>("/api/inventory/stock" + qs({ q: dq, page_size: 8 })), enabled: dq.length >= 2 });
  const rows = (res.data?.items ?? []).filter((r) => !exclude.has(r.variant_id));
  return (
    <div className="v-stack v-stack--sm">
      <Field label="Add item" hint="Search by product name, SKU or barcode.">{(id) => <Input id={id} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Start typing…" />}</Field>
      {dq.length >= 2 && !res.isFetching && rows.length === 0 ? <span className="v-muted">No matching variants.</span> : null}
      {rows.map((r) => (
        <button key={r.variant_id} type="button" className="v-btn" style={{ justifyContent: "space-between", textAlign: "left" }} onClick={() => { onPick(r); setQ(""); }}>
          <span>{r.product_title}{r.variant_title !== "Default" ? ` · ${r.variant_title}` : ""}</span><span className="v-mono v-muted">{r.sku}</span>
        </button>
      ))}
    </div>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { del, errorMessage, get, patch, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Badge, Button, Checkbox, Confirm, Dialog, Empty, Textarea, Field, TextField, useToast } from "@/ui";
import { DataTable } from "@/ui/DataTable";
import { useWarehouses, type Wh } from "./shared";

export function Warehouses() {
  const { can } = useAuth();
  const write = can("inventory.adjust");
  const q = useWarehouses();
  const [edit, setEdit] = useState<Wh | "new" | null>(null);
  return (
    <>
      {write ? <div className="a-toolbar"><Button variant="primary" onClick={() => setEdit("new")}><Plus size={16} /> New warehouse</Button></div> : null}
      <DataTable<Wh>
        label="Warehouses" rows={q.data?.items} loading={q.isLoading} error={q.error ? errorMessage(q.error) : null} rowKey={(w) => w.id} onRowClick={write ? setEdit : undefined}
        empty={<Empty title="No warehouses" />}
        columns={[
          { key: "n", header: "Warehouse", cell: (w) => <div><span style={{ fontWeight: 550 }}>{w.name}</span> {w.is_default ? <Badge tone="signal">Default</Badge> : null}<div className="v-muted v-mono" style={{ fontSize: 12 }}>{w.code}</div></div> },
          { key: "d", header: "District", hideBelow: 700, cell: (w) => w.district ?? <span className="v-muted">—</span> },
          { key: "u", header: "Units on hand", align: "right", cell: (w) => w.units_on_hand },
          { key: "v", header: "Variants stocked", align: "right", hideBelow: 700, cell: (w) => w.variants_stocked },
          { key: "s", header: "Status", cell: (w) => (w.is_active ? <Badge tone="success" plain>active</Badge> : <Badge plain>inactive</Badge>) },
        ]}
      />
      <p className="v-hint">Shoppers see availability summed across active warehouses. Inactive warehouses keep their history but their stock is not sold.</p>
      {edit ? <WarehouseDialog wh={edit === "new" ? null : edit} onClose={() => setEdit(null)} /> : null}
    </>
  );
}

function WarehouseDialog({ wh, onClose }: { wh: Wh | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(wh?.name ?? "");
  const [code, setCode] = useState(wh?.code ?? "");
  const [district, setDistrict] = useState(wh?.district ?? "");
  const [line1, setLine1] = useState(wh?.address?.line1 ?? "");
  const [isDefault, setDefault] = useState(wh?.is_default ?? false);
  const [active, setActive] = useState(wh?.is_active ?? true);
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const done = (m: string) => { toast(m); qc.invalidateQueries({ queryKey: ["inventory"] }); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); };
  const save = useMutation({
    mutationFn: () => {
      const body = { name, code, district: district || null, address: { line1, district }, is_default: isDefault, is_active: active };
      return wh ? patch(`/api/inventory/warehouses/${wh.id}`, body) : post("/api/inventory/warehouses", body);
    },
    onSuccess: () => done("Warehouse saved"),
    onError: (e) => setErr(errorMessage(e)),
  });
  const remove = useMutation({ mutationFn: () => del(`/api/inventory/warehouses/${wh!.id}`), onSuccess: () => done("Warehouse deleted"), onError: (e) => { setConfirm(false); setErr(errorMessage(e)); } });
  return (
    <>
      <Dialog open onClose={onClose} title={wh ? `Edit ${wh.name}` : "New warehouse"}
        footer={<>{wh && !wh.is_default ? <Button variant="ghost" onClick={() => setConfirm(true)}>Delete</Button> : null}<span style={{ flex: 1 }} /><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!name.trim() || !code.trim()} onClick={() => save.mutate()}>Save</Button></>}>
        <div className="v-stack">
          {err ? <Alert tone="danger">{err}</Alert> : null}
          <TextField label="Name" value={name} onChange={setName} maxLength={160} autoFocus />
          <TextField label="Code" value={code} onChange={(v) => setCode(v.toUpperCase())} maxLength={20} hint="2–20 characters: A–Z, 0–9, dash, underscore. Unique." className="v-mono" />
          <TextField label="Address" value={line1} onChange={setLine1} />
          <TextField label="District" value={district} onChange={setDistrict} />
          <Checkbox label="Default warehouse (used when a location isn't specified)" checked={isDefault} onChange={setDefault} />
          <Checkbox label="Active (stock here can be sold)" checked={active} onChange={setActive} />
        </div>
      </Dialog>
      <Confirm open={confirm} onClose={() => setConfirm(false)} onConfirm={() => remove.mutate()} busy={remove.isPending} danger title="Delete warehouse?" confirmLabel="Delete">
        Only warehouses without stock history can be deleted; otherwise deactivate it instead.
      </Confirm>
    </>
  );
}

export type Supplier = { id: string; name: string; contact_name: string; email: string | null; phone: string | null; address: string; lead_time_days: number; note: string; is_active: boolean; purchase_orders: number };

export function useSuppliers() {
  return useQuery({ queryKey: ["inventory", "suppliers"], queryFn: () => get<{ items: Supplier[] }>("/api/inventory/suppliers") });
}

export function Suppliers() {
  const { can } = useAuth();
  const write = can("inventory.adjust");
  const q = useSuppliers();
  const [edit, setEdit] = useState<Supplier | "new" | null>(null);
  return (
    <>
      {write ? <div className="a-toolbar"><Button variant="primary" onClick={() => setEdit("new")}><Plus size={16} /> New supplier</Button></div> : null}
      <DataTable<Supplier>
        label="Suppliers" rows={q.data?.items} loading={q.isLoading} error={q.error ? errorMessage(q.error) : null} rowKey={(s) => s.id} onRowClick={write ? setEdit : undefined}
        empty={<Empty title="No suppliers yet" action={write ? <Button variant="primary" onClick={() => setEdit("new")}>Add a supplier</Button> : undefined}>Suppliers are who you place purchase orders with.</Empty>}
        columns={[
          { key: "n", header: "Supplier", cell: (s) => <div><span style={{ fontWeight: 550 }}>{s.name}</span><div className="v-muted" style={{ fontSize: 12 }}>{s.contact_name}</div></div> },
          { key: "c", header: "Contact", hideBelow: 700, cell: (s) => <span>{s.phone ?? ""}{s.phone && s.email ? " · " : ""}{s.email ?? ""}</span> },
          { key: "l", header: "Lead time", align: "right", hideBelow: 700, cell: (s) => (s.lead_time_days ? `${s.lead_time_days} d` : "—") },
          { key: "po", header: "POs", align: "right", cell: (s) => s.purchase_orders },
          { key: "a", header: "Status", cell: (s) => (s.is_active ? <Badge tone="success" plain>active</Badge> : <Badge plain>inactive</Badge>) },
        ]}
      />
      {edit ? <SupplierDialog s={edit === "new" ? null : edit} onClose={() => setEdit(null)} /> : null}
    </>
  );
}

function SupplierDialog({ s, onClose }: { s: Supplier | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ name: s?.name ?? "", contact_name: s?.contact_name ?? "", email: s?.email ?? "", phone: s?.phone ?? "", address: s?.address ?? "", lead_time_days: String(s?.lead_time_days ?? 0), note: s?.note ?? "", is_active: s?.is_active ?? true });
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const up = (p: Partial<typeof f>) => setF({ ...f, ...p });
  const done = (m: string) => { toast(m); qc.invalidateQueries({ queryKey: ["inventory"] }); onClose(); };
  const save = useMutation({
    mutationFn: () => { const body = { ...f, lead_time_days: Number(f.lead_time_days) || 0, email: f.email || null, phone: f.phone || null }; return s ? patch(`/api/inventory/suppliers/${s.id}`, body) : post("/api/inventory/suppliers", body); },
    onSuccess: () => done("Supplier saved"),
    onError: (e) => setErr(errorMessage(e)),
  });
  const remove = useMutation({ mutationFn: () => del(`/api/inventory/suppliers/${s!.id}`), onSuccess: () => done("Supplier deleted"), onError: (e) => { setConfirm(false); setErr(errorMessage(e)); } });
  return (
    <>
      <Dialog open onClose={onClose} title={s ? `Edit ${s.name}` : "New supplier"}
        footer={<>{s ? <Button variant="ghost" onClick={() => setConfirm(true)}>Delete</Button> : null}<span style={{ flex: 1 }} /><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.name.trim()} onClick={() => save.mutate()}>Save</Button></>}>
        <div className="v-stack">
          {err ? <Alert tone="danger">{err}</Alert> : null}
          <TextField label="Supplier name" value={f.name} onChange={(v) => up({ name: v })} maxLength={160} autoFocus />
          <div className="v-grid-2">
            <TextField label="Contact person" value={f.contact_name} onChange={(v) => up({ contact_name: v })} />
            <TextField label="Lead time (days)" type="number" value={f.lead_time_days} onChange={(v) => up({ lead_time_days: v })} />
            <TextField label="Phone" value={f.phone} onChange={(v) => up({ phone: v })} />
            <TextField label="Email" type="email" value={f.email} onChange={(v) => up({ email: v })} />
          </div>
          <Field label="Address">{(id) => <Textarea id={id} rows={2} value={f.address} onChange={(e) => up({ address: e.target.value })} />}</Field>
          <Field label="Notes">{(id) => <Textarea id={id} rows={2} value={f.note} onChange={(e) => up({ note: e.target.value })} />}</Field>
          <Checkbox label="Active" checked={f.is_active} onChange={(v) => up({ is_active: v })} />
        </div>
      </Dialog>
      <Confirm open={confirm} onClose={() => setConfirm(false)} onConfirm={() => remove.mutate()} busy={remove.isPending} danger title="Delete supplier?" confirmLabel="Delete">Suppliers with purchase orders can't be deleted; mark them inactive instead.</Confirm>
    </>
  );
}

import { useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileUp, Plus, Search, Trash2 } from "lucide-react";
import { del, errorMessage, get, post, qs, type Paged } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatMoney } from "@/lib/money";
import { Alert, Badge, Button, Confirm, Dialog, Empty, Field, Input, Select, StatusBadge, Textarea, TextField, relTime, useToast } from "@/ui";
import { DataTable, SavedViews } from "@/ui/DataTable";
import { PageHead } from "../../AdminApp";
import { Thumb, useDebounced, type Img } from "./common";

type Row = {
  id: string; title: string; slug: string; status: string; is_demo: boolean; image: Img; variant_count: number;
  price_min_minor: number | null; price_max_minor: number | null; currency: string; stock_state: "in" | "low" | "out";
  tracked_available: number; brand: string | null; categories: string[]; tags: string[]; updated_at: string;
};
type Cat = { id: string; name: string; parent_id: string | null };
type Brand = { id: string; name: string };
type Filters = { q: string; status: string; stock: string; category_id: string; brand_id: string; tag: string };
const EMPTY: Filters = { q: "", status: "", stock: "", category_id: "", brand_id: "", tag: "" };
type ImportReport = { dry_run: boolean; rows: number; applied: boolean; products_to_create: number; products_to_update: number; variants_to_create: number; variants_to_update: number; brands_to_create: number; categories_to_create: number; errors: { row: number; field: string; message: string }[]; error_count: number };

const STOCK_LABEL = { in: "In stock", low: "Low stock", out: "Out of stock" } as const;

export default function Products() {
  const nav = useNavigate();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [f, setF] = useState<Filters>(EMPTY);
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "updated", dir: "desc" });
  const [page, setPage] = useState(1);
  const [tagAction, setTagAction] = useState<{ ids: string[]; mode: "add_tag" | "remove_tag" } | null>(null);
  const [removeDemo, setRemoveDemo] = useState(false);
  const dq = useDebounced(f.q);
  const query = { q: dq, status: f.status, stock: f.stock, category_id: f.category_id, brand_id: f.brand_id, tag: f.tag, sort: sort.key, dir: sort.dir, page, page_size: 25 };

  const list = useQuery({ queryKey: ["catalog", "products", query], queryFn: () => get<Paged<Row>>("/api/catalog/products" + qs(query)), placeholderData: (p) => p });
  const cats = useQuery({ queryKey: ["catalog", "categories"], queryFn: () => get<{ items: Cat[] }>("/api/catalog/categories") });
  const brands = useQuery({ queryKey: ["catalog", "brands"], queryFn: () => get<{ items: Brand[] }>("/api/catalog/brands") });
  const demo = useQuery({ queryKey: ["catalog", "demo-count"], queryFn: () => get<Paged<Row>>("/api/catalog/products?is_demo=true&page_size=1") });

  const set = (patch: Partial<Filters>) => { setF({ ...f, ...patch }); setPage(1); };
  const bulk = useMutation({
    mutationFn: (v: { ids: string[]; action: string; value?: string }) => post<{ updated: number; selected: number }>("/api/catalog/products/bulk", v),
    onSuccess: (r) => { toast(`${r.updated} of ${r.selected} products updated`); qc.invalidateQueries({ queryKey: ["catalog"] }); setTagAction(null); },
    onError: (e) => toast(errorMessage(e), "danger"),
  });
  const wipeDemo = useMutation({
    mutationFn: () => del<{ removed: number }>("/api/catalog/demo"),
    onSuccess: (r) => { toast(`Removed ${r.removed} demo products`); qc.invalidateQueries({ queryKey: ["catalog"] }); setRemoveDemo(false); },
    onError: (e) => toast(errorMessage(e), "danger"),
  });
  const write = can("catalog.write");
  const filtered = Object.values({ ...f, q: dq }).some(Boolean);
  const catName = (c: Cat): string => { const p = cats.data?.items.find((x) => x.id === c.parent_id); return p ? `${catName(p)} › ${c.name}` : c.name; };

  return (
    <>
      <PageHead
        title="Products"
        description="Everything you sell: variants, pricing, media and publishing state."
        actions={
          <div className="v-row v-row--wrap">
            <a className="v-btn" href="/api/catalog/products/export.csv" download><Download size={16} /> Export CSV</a>
            {write ? <Button onClick={() => setParams({ import: "1" })}><FileUp size={16} /> Import CSV</Button> : null}
            {write ? <Button variant="primary" onClick={() => nav("/admin/catalog/products/new")}><Plus size={16} /> New product</Button> : null}
          </div>
        }
      />
      {write && (demo.data?.total ?? 0) > 0 ? (
        <div style={{ marginBottom: 16 }}>
          <Alert tone="info" title="Demo catalog is loaded" action={<Button size="sm" onClick={() => setRemoveDemo(true)}><Trash2 size={14} /> Remove demo products</Button>}>
            {demo.data?.total} sample products are labelled “Demo”. Remove them before you open the store.
          </Alert>
        </div>
      ) : null}
      <div className="a-toolbar">
        <div style={{ position: "relative", flex: "1 1 220px", minWidth: 180 }}>
          <Search size={16} style={{ position: "absolute", left: 10, top: 11, color: "var(--v-ink-3)" }} aria-hidden />
          <Input aria-label="Search products" placeholder="Search title, SKU or tag" value={f.q} onChange={(e) => set({ q: e.target.value })} style={{ paddingLeft: 32 }} />
        </div>
        <Select aria-label="Status" value={f.status} onChange={(e) => set({ status: e.target.value })} style={{ width: "auto" }}>
          <option value="">All statuses</option><option value="active">Active</option><option value="draft">Draft</option><option value="archived">Archived</option>
        </Select>
        <Select aria-label="Stock" value={f.stock} onChange={(e) => set({ stock: e.target.value })} style={{ width: "auto" }}>
          <option value="">Any stock</option><option value="in">In stock</option><option value="low">Low stock</option><option value="out">Out of stock</option>
        </Select>
        <Select aria-label="Category" value={f.category_id} onChange={(e) => set({ category_id: e.target.value })} style={{ width: "auto", maxWidth: 200 }}>
          <option value="">All categories</option>{cats.data?.items.map((c) => <option key={c.id} value={c.id}>{catName(c)}</option>)}
        </Select>
        <Select aria-label="Brand" value={f.brand_id} onChange={(e) => set({ brand_id: e.target.value })} style={{ width: "auto", maxWidth: 180 }}>
          <option value="">All brands</option>{brands.data?.items.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </Select>
        {filtered ? <Button variant="ghost" onClick={() => { setF(EMPTY); setPage(1); }}>Clear</Button> : null}
      </div>
      <div style={{ marginBottom: 12 }}><SavedViews id="catalog-products" current={f as unknown as Record<string, unknown>} onApply={(v) => { setF({ ...EMPTY, ...(v as unknown as Filters) }); setPage(1); }} /></div>
      <DataTable<Row>
        label="Products"
        rows={list.data?.items}
        loading={list.isLoading}
        error={list.error ? errorMessage(list.error) : null}
        rowKey={(r) => r.id}
        onRowClick={(r) => nav(`/admin/catalog/products/${r.id}`)}
        sort={sort}
        onSort={(s) => { setSort(s); setPage(1); }}
        page={page}
        pageSize={25}
        total={list.data?.total}
        onPage={setPage}
        bulkActions={write ? [
          { label: "Publish", onRun: (rows) => bulk.mutate({ ids: rows.map((r) => r.id), action: "set_status", value: "active" }) },
          { label: "Move to draft", onRun: (rows) => bulk.mutate({ ids: rows.map((r) => r.id), action: "set_status", value: "draft" }) },
          { label: "Add tag", onRun: (rows) => setTagAction({ ids: rows.map((r) => r.id), mode: "add_tag" }) },
          { label: "Remove tag", onRun: (rows) => setTagAction({ ids: rows.map((r) => r.id), mode: "remove_tag" }) },
          { label: "Archive", danger: true, onRun: (rows) => bulk.mutate({ ids: rows.map((r) => r.id), action: "archive" }) },
        ] : undefined}
        empty={filtered ? <Empty title="No products match these filters"><Button onClick={() => { setF(EMPTY); setPage(1); }}>Clear filters</Button></Empty> : (
          <Empty title="No products yet" action={write ? <Button variant="primary" onClick={() => nav("/admin/catalog/products/new")}><Plus size={16} /> Add your first product</Button> : undefined}>
            Add products one by one or import a CSV{write ? "" : " (ask someone with catalog write access)"}.
          </Empty>
        )}
        columns={[
          { key: "title", header: "Product", sortable: true, cell: (r) => (
            <div className="v-row" style={{ gap: 10, minWidth: 0 }}>
              <Thumb image={r.image} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 550 }}>{r.title} {r.is_demo ? <Badge tone="info" plain>Demo</Badge> : null}</div>
                <div className="v-muted v-mono" style={{ fontSize: 12 }}>{r.variant_count} variant{r.variant_count === 1 ? "" : "s"}{r.brand ? ` · ${r.brand}` : ""}</div>
              </div>
            </div>
          ) },
          { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
          { key: "stock", header: "Stock", sortable: true, cell: (r) => <span><Badge tone={r.stock_state === "in" ? "success" : r.stock_state === "low" ? "warning" : "danger"}>{STOCK_LABEL[r.stock_state]}</Badge>{r.tracked_available > 0 ? <span className="v-muted v-num"> {r.tracked_available}</span> : null}</span> },
          { key: "price", header: "Price", sortable: true, align: "right", cell: (r) => r.price_min_minor === null ? "—" : r.price_min_minor === r.price_max_minor ? formatMoney(r.price_min_minor, r.currency) : `${formatMoney(r.price_min_minor, r.currency)} – ${formatMoney(r.price_max_minor, r.currency)}` },
          { key: "cats", header: "Categories", hideBelow: 1000, cell: (r) => r.categories.length ? r.categories.join(", ") : <span className="v-muted">—</span> },
          { key: "updated", header: "Updated", sortable: true, hideBelow: 800, cell: (r) => <span className="v-muted">{relTime(r.updated_at)}</span> },
        ]}
      />
      <Confirm open={removeDemo} onClose={() => setRemoveDemo(false)} onConfirm={() => wipeDemo.mutate()} busy={wipeDemo.isPending} danger title="Remove all demo products?" confirmLabel="Remove demo catalog">
        Demo products, demo categories and brands, and their opening stock are deleted. Your own products are not touched. This action is recorded in the audit trail.
      </Confirm>
      {tagAction ? <TagDialog action={tagAction} busy={bulk.isPending} onClose={() => setTagAction(null)} onRun={(value) => bulk.mutate({ ids: tagAction.ids, action: tagAction.mode, value })} /> : null}
      {params.get("import") === "1" && write ? <ImportDialog onClose={() => setParams({})} /> : null}
    </>
  );
}

function TagDialog({ action, onClose, onRun, busy }: { action: { ids: string[]; mode: "add_tag" | "remove_tag" }; onClose: () => void; onRun: (v: string) => void; busy: boolean }) {
  const [tag, setTag] = useState("");
  return (
    <Dialog open onClose={onClose} title={action.mode === "add_tag" ? "Add a tag" : "Remove a tag"} description={`Applies to ${action.ids.length} selected product${action.ids.length === 1 ? "" : "s"}.`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!tag.trim()} onClick={() => onRun(tag.trim())}>Apply</Button></>}>
      <TextField label="Tag" value={tag} onChange={setTag} maxLength={40} autoFocus data-autofocus />
    </Dialog>
  );
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const [csv, setCsv] = useState("");
  const [report, setReport] = useState<ImportReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const run = useMutation({
    mutationFn: (dry: boolean) => post<ImportReport>("/api/catalog/products/import", { csv, dry_run: dry }),
    onSuccess: (r) => {
      setErr(null); setReport(r);
      if (r.applied) { toast(`Imported ${r.rows} rows`); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); }
    },
    onError: (e) => {
      const d = (e as { details?: ImportReport }).details;
      setReport(d && Array.isArray(d.errors) ? d : null);
      setErr(errorMessage(e));
    },
  });
  const ready = report && report.dry_run && report.errors.length === 0 && report.rows > 0;
  return (
    <Dialog open wide onClose={onClose} title="Import products from CSV"
      description="Rows are grouped by handle, one row per variant, and matched to existing variants by SKU, so re-importing updates instead of duplicating. Prices are in major units (৳)."
      footer={<><Button onClick={onClose}>Close</Button><Button loading={run.isPending && run.variables === true} disabled={!csv.trim()} onClick={() => run.mutate(true)}>Validate</Button><Button variant="primary" loading={run.isPending && run.variables === false} disabled={!ready} onClick={() => run.mutate(false)}>Apply import</Button></>}>
      <div className="v-stack">
        <div className="v-row v-row--wrap">
          <input ref={file} type="file" accept=".csv,text/csv" className="v-sr" aria-label="Choose CSV file" onChange={async (e) => { const t = await e.target.files?.[0]?.text(); if (t !== undefined) { setCsv(t); setReport(null); setErr(null); } e.target.value = ""; }} />
          <Button onClick={() => file.current?.click()}><FileUp size={16} /> Choose file</Button>
          <span className="v-muted">or paste CSV below. Columns: handle, title, status, brand, categories, tags, description, image_url, sku, barcode, option1_name, option1_value, price, compare_at, cost, weight_grams.</span>
        </div>
        <Field label="CSV">{(id) => <Textarea id={id} value={csv} onChange={(e) => { setCsv(e.target.value); setReport(null); }} rows={8} className="v-mono" style={{ fontSize: 12 }} spellCheck={false} />}</Field>
        {err ? <Alert tone="danger">{err}</Alert> : null}
        {report ? (
          <Alert tone={report.errors.length ? "warning" : "success"} title={report.errors.length ? `${report.error_count} problem${report.error_count === 1 ? "" : "s"} found — nothing has been imported` : "Validation passed — nothing has been written yet"}>
            <div>{report.rows} rows · {report.products_to_create} new / {report.products_to_update} existing products · {report.variants_to_create} new / {report.variants_to_update} existing variants{report.brands_to_create ? ` · ${report.brands_to_create} new brands` : ""}{report.categories_to_create ? ` · ${report.categories_to_create} new categories` : ""}</div>
            {report.errors.length ? <ul style={{ margin: "8px 0 0", paddingLeft: 18, maxHeight: 160, overflow: "auto" }}>{report.errors.map((e, i) => <li key={i}>Row {e.row} · {e.field}: {e.message}</li>)}</ul> : null}
          </Alert>
        ) : null}
      </div>
    </Dialog>
  );
}

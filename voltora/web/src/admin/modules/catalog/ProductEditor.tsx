import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Copy, ImagePlus, Link2, Plus, Trash2, X } from "lucide-react";
import { ApiError, api, del, errorMessage, get, patch, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { parseMoney, toMajorInput } from "@/lib/money";
import { Alert, Badge, Button, Card, Checkbox, Confirm, Empty, Field, Input, Select, Skeleton, StatusBadge, Textarea, TextField, useToast } from "@/ui";
import { PageHead } from "../../AdminApp";
import "./catalog.css";
import { Thumb, useUnsavedGuard } from "./common";

type StockRow = { warehouse_id: string; warehouse: string; code: string; on_hand: number; reserved: number; available: number };
type VarApi = { id: string | null; sku: string; barcode: string | null; title: string; option_values: Record<string, string>; price_minor: number; compare_at_minor: number | null; cost_minor: number | null; weight_grams: number; track_inventory: boolean; allow_backorder: boolean; is_active: boolean; available?: number | null; stock?: StockRow[] };
type ProductApi = { id: string; title: string; slug: string; description: string; status: string; product_type: string; brand_id: string | null; tags: string[]; options: { name: string; values: string[] }[]; seo: { title: string; description: string }; is_demo: boolean; currency: string; category_ids: string[]; media: { id: string; url: string; alt: string }[]; variants: VarApi[] };
type Cat = { id: string; name: string; parent_id: string | null };

type VarForm = { id?: string; sku: string; barcode: string; option_values: Record<string, string>; price: string; compare: string; cost: string; weight: string; track: boolean; backorder: boolean; active: boolean; title: string; stock: StockRow[] };
type Form = { title: string; description: string; status: string; product_type: string; brand_id: string; tags: string; options: { name: string; values: string[] }[]; variants: VarForm[]; media: { id?: string; url: string; alt: string }[]; category_ids: string[]; seo_title: string; seo_description: string };

const blankVariant = (): VarForm => ({ sku: "", barcode: "", option_values: {}, price: "", compare: "", cost: "", weight: "0", track: true, backorder: false, active: true, title: "", stock: [] });
const BLANK: Form = { title: "", description: "", status: "draft", product_type: "physical", brand_id: "", tags: "", options: [], variants: [blankVariant()], media: [], category_ids: [], seo_title: "", seo_description: "" };

const fromVariant = (v: VarApi, cur: string): VarForm => ({
  id: v.id ?? undefined, sku: v.sku, barcode: v.barcode ?? "", option_values: v.option_values, price: toMajorInput(v.price_minor, cur), compare: toMajorInput(v.compare_at_minor, cur),
  cost: toMajorInput(v.cost_minor, cur), weight: String(v.weight_grams), track: v.track_inventory, backorder: v.allow_backorder, active: v.is_active, title: v.title, stock: v.stock ?? [],
});
const fromApi = (p: ProductApi): Form => ({
  title: p.title, description: p.description, status: p.status, product_type: p.product_type, brand_id: p.brand_id ?? "", tags: p.tags.join(", "), options: p.options,
  variants: p.variants.map((v) => fromVariant(v, p.currency)), media: p.media.map((m) => ({ id: m.id, url: m.url, alt: m.alt })), category_ids: p.category_ids,
  seo_title: p.seo.title, seo_description: p.seo.description,
});

function variantPayload(v: VarForm, cur: string, errors: Record<string, string>, i: number) {
  const price = parseMoney(v.price, cur);
  const compare = v.compare.trim() ? parseMoney(v.compare, cur) : null;
  const cost = v.cost.trim() ? parseMoney(v.cost, cur) : null;
  if (!v.sku.trim()) errors[`variants.${i}.sku`] = "SKU is required";
  if (price === null || price < 0) errors[`variants.${i}.price`] = "Enter a valid price";
  if (v.compare.trim() && (compare === null || (price !== null && compare < price))) errors[`variants.${i}.compare`] = "Must be a number not lower than the price";
  if (v.cost.trim() && (cost === null || cost < 0)) errors[`variants.${i}.cost`] = "Enter a valid cost";
  const weight = Number(v.weight || 0);
  if (!Number.isInteger(weight) || weight < 0) errors[`variants.${i}.weight`] = "Whole grams";
  return { id: v.id, sku: v.sku.trim(), barcode: v.barcode.trim() || null, title: v.title || null, option_values: v.option_values, price_minor: price ?? 0, compare_at_minor: compare, cost_minor: cost, weight_grams: weight, track_inventory: v.track, allow_backorder: v.backorder, is_active: v.active };
}

export default function ProductEditor() {
  const { id = "new" } = useParams();
  const isNew = id === "new";
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { can, me } = useAuth();
  const cur = me?.store.currency ?? "BDT";
  const write = can("catalog.write");
  const product = useQuery({ queryKey: ["catalog", "product", id], queryFn: () => get<ProductApi>(`/api/catalog/products/${id}`), enabled: !isNew });
  const cats = useQuery({ queryKey: ["catalog", "categories"], queryFn: () => get<{ items: Cat[] }>("/api/catalog/categories") });
  const brands = useQuery({ queryKey: ["catalog", "brands"], queryFn: () => get<{ items: { id: string; name: string }[] }>("/api/catalog/brands") });
  const [form, setForm] = useState<Form>(BLANK);
  const [initial, setInitial] = useState<string>(JSON.stringify(BLANK));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const dirty = JSON.stringify(form) !== initial;
  const guard = useUnsavedGuard(dirty);

  useEffect(() => {
    if (isNew) { setForm(BLANK); setInitial(JSON.stringify(BLANK)); return; }
    if (product.data) { const f = fromApi(product.data); setForm(f); setInitial(JSON.stringify(f)); }
  }, [isNew, product.data]);

  const up = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));
  const setVariant = (i: number, patch: Partial<VarForm>) => setForm((f) => ({ ...f, variants: f.variants.map((v, j) => (j === i ? { ...v, ...patch } : v)) }));

  const optionNames = form.options.map((o) => o.name.trim()).filter(Boolean);
  const variantsOk = form.variants.every((v) => optionNames.length === Object.keys(v.option_values).length && optionNames.every((n) => form.options.find((o) => o.name.trim() === n)?.values.includes(v.option_values[n] ?? "\0")));
  const optionsOk = form.options.every((o) => o.name.trim() && o.values.length);

  const save = useMutation({
    mutationFn: () => {
      const errs: Record<string, string> = {};
      if (!form.title.trim()) errs.title = "A product needs a title";
      const variants = form.variants.map((v, i) => variantPayload(v, cur, errs, i));
      setErrors(errs);
      if (Object.keys(errs).length) throw new Error("Fix the highlighted fields and try again.");
      const body = {
        title: form.title.trim(), description: form.description, status: form.status, product_type: form.product_type, brand_id: form.brand_id || null,
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean), options: form.options.map((o) => ({ name: o.name.trim(), values: o.values })), variants,
        category_ids: form.category_ids, media: form.media.map((m) => ({ id: m.id, url: m.url, alt: m.alt })), seo: { title: form.seo_title, description: form.seo_description },
      };
      return isNew ? post<ProductApi>("/api/catalog/products", body) : patch<ProductApi>(`/api/catalog/products/${id}`, body);
    },
    onSuccess: (p) => {
      const f = fromApi(p);
      setForm(f); setInitial(JSON.stringify(f)); setErrors({}); setBanner(null);
      toast("Product saved");
      qc.invalidateQueries({ queryKey: ["catalog"] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
      if (isNew) nav(`/admin/catalog/products/${p.id}`, { replace: true });
    },
    onError: (e) => {
      setBanner(errorMessage(e));
      if (e instanceof ApiError) { const fe = e.fieldErrors(); if (Object.keys(fe).length) setErrors(fe); }
    },
  });
  const duplicate = useMutation({
    mutationFn: () => post<ProductApi>(`/api/catalog/products/${id}/duplicate`),
    onSuccess: (p) => { toast("Duplicated as a draft"); qc.invalidateQueries({ queryKey: ["catalog"] }); guard.go(`/admin/catalog/products/${p.id}`); },
    onError: (e) => toast(errorMessage(e), "danger"),
  });
  const remove = useMutation({
    mutationFn: () => del(`/api/catalog/products/${id}`),
    onSuccess: () => { toast("Product deleted"); qc.invalidateQueries({ queryKey: ["catalog"] }); setInitial(JSON.stringify(form)); nav("/admin/catalog/products"); },
    onError: (e) => { setConfirmDelete(false); toast(errorMessage(e), "danger"); },
  });
  const rebuild = useMutation({
    mutationFn: () => {
      const errs: Record<string, string> = {};
      const existing = form.variants.map((v, i) => variantPayload(v, cur, errs, i));
      const first = form.variants[0];
      const base = parseMoney(first?.price ?? "", cur) ?? 0;
      const prefix = (first?.sku ?? "").split("-")[0] || form.title.replace(/[^A-Za-z0-9]/g, "").slice(0, 8);
      return post<{ variants: VarApi[] }>("/api/catalog/products/generate-variants", {
        options: form.options.map((o) => ({ name: o.name.trim(), values: o.values })), price_minor: base, sku_prefix: prefix,
        existing: existing.filter((v) => v.sku).map((v) => ({ ...v, id: v.id ?? null })),
      });
    },
    onSuccess: (r) => setForm((f) => ({ ...f, variants: r.variants.map((v) => { const old = f.variants.find((x) => x.id && x.id === v.id); return old ?? fromVariant(v, cur); }) })),
    onError: (e) => toast(errorMessage(e), "danger"),
  });

  const upload = useRef<HTMLInputElement>(null);
  const [mediaUrl, setMediaUrl] = useState("");
  const [uploading, setUploading] = useState(false);
  async function onFiles(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    try {
      const added: Form["media"] = [];
      for (const file of Array.from(files)) {
        const fd = new FormData();
        fd.append("file", file);
        const r = await api<{ url: string }>("/api/catalog/media", { method: "POST", body: fd });
        added.push({ url: r.url, alt: form.title });
      }
      setForm((f) => ({ ...f, media: [...f.media, ...added] }));
    } catch (e) { toast(errorMessage(e), "danger"); } finally { setUploading(false); if (upload.current) upload.current.value = ""; }
  }
  const moveMedia = (i: number, d: number) => setForm((f) => { const m = [...f.media]; const j = i + d; if (j < 0 || j >= m.length) return f; [m[i], m[j]] = [m[j]!, m[i]!]; return { ...f, media: m }; });

  const catRows = useMemo(() => {
    const items = cats.data?.items ?? [];
    const out: { c: Cat; depth: number }[] = [];
    const walk = (parent: string | null, depth: number) => items.filter((c) => c.parent_id === parent).forEach((c) => { out.push({ c, depth }); walk(c.id, depth + 1); });
    walk(null, 0);
    return out;
  }, [cats.data]);

  if (!isNew && product.isLoading) return <div className="v-stack"><Skeleton h={32} w={280} /><Skeleton h={220} /><Skeleton h={320} /></div>;
  if (!isNew && product.error) return <Empty title="Couldn't load this product" action={<Link className="v-btn" to="/admin/catalog/products">Back to products</Link>}>{errorMessage(product.error)}</Empty>;
  const ro = !write;
  const fe = (k: string) => errors[k] ?? null;
  const p = product.data;

  return (
    <>
      <PageHead
        title={isNew ? "New product" : form.title || "Product"}
        crumbs={[{ label: "Products", to: "/admin/catalog/products" }]}
        description={p ? <span className="v-row v-row--wrap" style={{ gap: 8 }}><StatusBadge status={p.status} />{p.is_demo ? <Badge tone="info">Demo product</Badge> : null}<span className="v-mono v-muted" style={{ fontSize: 12 }}>/{p.slug}</span></span> : "Add a product with at least one sellable variant."}
        actions={!isNew && write ? (
          <div className="v-row v-row--wrap">
            <Button loading={duplicate.isPending} onClick={() => duplicate.mutate()}><Copy size={16} /> Duplicate</Button>
            <Button variant="danger" onClick={() => setConfirmDelete(true)}><Trash2 size={16} /> Delete</Button>
          </div>
        ) : null}
      />
      {ro ? <div style={{ marginBottom: 16 }}><Alert tone="info">You have read-only access to the catalog.</Alert></div> : null}
      {banner ? <div style={{ marginBottom: 16 }}><Alert tone="danger">{banner}</Alert></div> : null}
      <fieldset disabled={ro} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <div className="cx-editor">
          <div className="v-stack">
            <Card title="Basics">
              <div className="v-stack">
                <TextField label="Title" value={form.title} onChange={(v) => up("title", v)} error={fe("title")} maxLength={300} />
                <Field label="Description" hint="Basic formatting is kept (paragraphs, lists, bold, links). Scripts, styles and unknown tags are removed when you save.">
                  {(fid) => <Textarea id={fid} rows={8} value={form.description} onChange={(e) => up("description", e.target.value)} />}
                </Field>
              </div>
            </Card>

            <Card title="Media" actions={
              <div className="v-row">
                <input ref={upload} type="file" accept="image/png,image/jpeg,image/webp" multiple className="v-sr" aria-label="Upload images" onChange={(e) => onFiles(e.target.files)} />
                <Button size="sm" loading={uploading} disabled={ro} onClick={() => upload.current?.click()}><ImagePlus size={14} /> Upload</Button>
              </div>}>
              {form.media.length === 0 ? <Empty title="No images yet">Upload PNG, JPEG or WebP (up to 8 MB), or add an image URL.</Empty> : null}
              {form.media.map((m, i) => (
                <div key={m.id ?? m.url + i} className="cx-media">
                  <Thumb image={{ url: m.url, alt: m.alt }} size={56} />
                  <div className="v-stack v-stack--sm" style={{ minWidth: 0 }}>
                    <Input aria-label={`Image ${i + 1} alt text`} placeholder="Alt text (describe the image)" value={m.alt} onChange={(e) => up("media", form.media.map((x, j) => (j === i ? { ...x, alt: e.target.value } : x)))} />
                    <span className="v-muted v-mono" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{i === 0 ? "Primary · " : ""}{m.url}</span>
                  </div>
                  <div className="v-row cx-media__actions" style={{ gap: 4 }}>
                    <Button size="sm" icon aria-label={`Move image ${i + 1} up`} disabled={i === 0} onClick={() => moveMedia(i, -1)}><ArrowUp size={14} /></Button>
                    <Button size="sm" icon aria-label={`Move image ${i + 1} down`} disabled={i === form.media.length - 1} onClick={() => moveMedia(i, 1)}><ArrowDown size={14} /></Button>
                    <Button size="sm" icon aria-label={`Remove image ${i + 1}`} onClick={() => up("media", form.media.filter((_, j) => j !== i))}><X size={14} /></Button>
                  </div>
                </div>
              ))}
              <div className="v-row" style={{ marginTop: 12 }}>
                <Input aria-label="Image URL" placeholder="https://… image URL" value={mediaUrl} onChange={(e) => setMediaUrl(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && mediaUrl.trim()) { e.preventDefault(); up("media", [...form.media, { url: mediaUrl.trim(), alt: form.title }]); setMediaUrl(""); } }} />
                <Button disabled={!/^https?:\/\//.test(mediaUrl.trim())} onClick={() => { up("media", [...form.media, { url: mediaUrl.trim(), alt: form.title }]); setMediaUrl(""); }}><Link2 size={14} /> Add URL</Button>
              </div>
            </Card>

            <Card title="Options & variants">
              <div className="v-stack">
                <p className="v-muted" style={{ margin: 0 }}>Add options like Size or Colour; each combination becomes a variant with its own SKU, price and stock.</p>
                {form.options.map((o, i) => (
                  <OptionEditor key={i} option={o} index={i} onChange={(n) => up("options", form.options.map((x, j) => (j === i ? n : x)))} onRemove={() => up("options", form.options.filter((_, j) => j !== i))} />
                ))}
                <div className="v-row v-row--wrap">
                  <Button size="sm" disabled={form.options.length >= 5 || ro} onClick={() => up("options", [...form.options, { name: "", values: [] }])}><Plus size={14} /> Add option</Button>
                  {form.options.length ? <Button size="sm" variant="primary" disabled={!optionsOk} loading={rebuild.isPending} onClick={() => rebuild.mutate()}>Update variant list</Button> : null}
                </div>
                {!variantsOk && optionsOk ? <Alert tone="warning">The options changed. Choose “Update variant list” so each variant matches an option combination before saving.</Alert> : null}
                <div className="cx-scroll">
                  <table className="cx-vtable" aria-label="Variants">
                    <thead><tr><th>Variant</th><th>SKU</th><th>Price ({cur})</th><th>Compare-at</th><th>Cost</th><th>Weight (g)</th><th>Track</th><th>Backorder</th><th>Active</th><th><span className="v-sr">Remove</span></th></tr></thead>
                    <tbody>
                      {form.variants.map((v, i) => (
                        <tr key={v.id ?? `n${i}`}>
                          <td style={{ minWidth: 110 }}>{Object.values(v.option_values).join(" / ") || (form.variants.length === 1 ? "Default" : "Variant " + (i + 1))}</td>
                          <td><Input aria-label={`SKU, variant ${i + 1}`} value={v.sku} invalid={!!fe(`variants.${i}.sku`)} title={fe(`variants.${i}.sku`) ?? undefined} onChange={(e) => setVariant(i, { sku: e.target.value })} className="v-mono" style={{ width: 130 }} /></td>
                          <td><Input aria-label={`Price, variant ${i + 1}`} inputMode="decimal" value={v.price} invalid={!!fe(`variants.${i}.price`)} title={fe(`variants.${i}.price`) ?? undefined} onChange={(e) => setVariant(i, { price: e.target.value })} style={{ width: 96 }} /></td>
                          <td><Input aria-label={`Compare-at price, variant ${i + 1}`} inputMode="decimal" value={v.compare} invalid={!!fe(`variants.${i}.compare`)} title={fe(`variants.${i}.compare`) ?? undefined} onChange={(e) => setVariant(i, { compare: e.target.value })} style={{ width: 96 }} /></td>
                          <td><Input aria-label={`Cost, variant ${i + 1}`} inputMode="decimal" value={v.cost} invalid={!!fe(`variants.${i}.cost`)} onChange={(e) => setVariant(i, { cost: e.target.value })} style={{ width: 88 }} /></td>
                          <td><Input aria-label={`Weight in grams, variant ${i + 1}`} inputMode="numeric" value={v.weight} invalid={!!fe(`variants.${i}.weight`)} onChange={(e) => setVariant(i, { weight: e.target.value })} style={{ width: 80 }} /></td>
                          <td><Checkbox label={<span className="v-sr">Track inventory</span>} checked={v.track} onChange={(c) => setVariant(i, { track: c })} /></td>
                          <td><Checkbox label={<span className="v-sr">Allow backorder</span>} checked={v.backorder} onChange={(c) => setVariant(i, { backorder: c })} /></td>
                          <td><Checkbox label={<span className="v-sr">Active</span>} checked={v.active} onChange={(c) => setVariant(i, { active: c })} /></td>
                          <td>{form.options.length === 0 && form.variants.length === 1 ? null : <Button size="sm" icon aria-label={`Remove variant ${i + 1}`} onClick={() => up("variants", form.variants.filter((_, j) => j !== i))}><X size={14} /></Button>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {form.options.length === 0 ? <div><Button size="sm" onClick={() => up("variants", [...form.variants, blankVariant()])}><Plus size={14} /> Add variant without options</Button></div> : null}
                <p className="v-hint" style={{ margin: 0 }}>Variants that already have stock history are deactivated instead of deleted. Prices are entered in taka and stored exactly in paisa.</p>
              </div>
            </Card>

            {!isNew ? (
              <Card title="Stock" actions={can("inventory.read") ? <Link className="v-btn v-btn--sm" to={`/admin/inventory?q=${encodeURIComponent(form.variants[0]?.sku ?? "")}`}>Manage in Inventory</Link> : null}>
                <StockReadout variants={form.variants} />
              </Card>
            ) : null}
          </div>

          <div className="v-stack">
            <Card title="Status">
              <Field label="Visibility" hint={form.status === "active" ? "Visible on the storefront." : form.status === "draft" ? "Hidden from shoppers." : "Hidden and kept for order history."}>
                {(fid) => <Select id={fid} value={form.status} onChange={(e) => up("status", e.target.value)}><option value="draft">Draft</option><option value="active">Active</option><option value="archived">Archived</option></Select>}
              </Field>
            </Card>
            <Card title="Organisation">
              <div className="v-stack">
                <Field label="Brand">{(fid) => <Select id={fid} value={form.brand_id} onChange={(e) => up("brand_id", e.target.value)}><option value="">No brand</option>{brands.data?.items.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</Select>}</Field>
                <Field label="Product type">{(fid) => <Select id={fid} value={form.product_type} onChange={(e) => up("product_type", e.target.value)}><option value="physical">Physical</option><option value="digital">Digital</option><option value="service">Service</option></Select>}</Field>
                <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
                  <legend className="v-label">Categories</legend>
                  {catRows.length === 0 ? <p className="v-muted" style={{ margin: 0 }}>No categories yet. <Link to="/admin/catalog/taxonomy">Create one</Link>.</p> : (
                    <div className="cx-catlist">
                      {catRows.map(({ c, depth }) => (
                        <div key={c.id} style={{ paddingLeft: depth * 16 }}>
                          <Checkbox label={c.name} checked={form.category_ids.includes(c.id)} onChange={(on) => up("category_ids", on ? [...form.category_ids, c.id] : form.category_ids.filter((x) => x !== c.id))} />
                        </div>
                      ))}
                    </div>
                  )}
                </fieldset>
                <TextField label="Tags" value={form.tags} onChange={(v) => up("tags", v)} hint="Comma separated." />
              </div>
            </Card>
            <Card title="Search engine listing">
              <div className="v-stack">
                <TextField label="Page title" value={form.seo_title} onChange={(v) => up("seo_title", v)} maxLength={120} hint={`${form.seo_title.length}/120 · defaults to the product title`} />
                <Field label="Meta description" hint={`${form.seo_description.length}/320 · defaults to the start of the description`}>{(fid) => <Textarea id={fid} rows={3} maxLength={320} value={form.seo_description} onChange={(e) => up("seo_description", e.target.value)} />}</Field>
              </div>
            </Card>
          </div>
        </div>
      </fieldset>

      {write ? (
        <div className="cx-sticky" role="region" aria-label="Save changes">
          <span aria-live="polite">{dirty ? <Badge tone="warning">Unsaved changes</Badge> : <span className="v-muted">All changes saved</span>}</span>
          <div className="v-row">
            <Button disabled={!dirty} onClick={() => { setForm(isNew ? BLANK : fromApi(product.data!)); setErrors({}); setBanner(null); }}>Discard</Button>
            <Button variant="primary" loading={save.isPending} disabled={!dirty || !variantsOk || !optionsOk} onClick={() => save.mutate()}>{isNew ? "Create product" : "Save changes"}</Button>
          </div>
        </div>
      ) : null}
      <Confirm open={confirmDelete} onClose={() => setConfirmDelete(false)} onConfirm={() => remove.mutate()} busy={remove.isPending} danger title="Delete this product?" confirmLabel="Delete product">
        “{form.title}” and its variants are permanently removed. Products with stock history or orders can't be deleted — archive them instead.
      </Confirm>
      {guard.dialog}
    </>
  );
}

function OptionEditor({ option, index, onChange, onRemove }: { option: { name: string; values: string[] }; index: number; onChange: (o: { name: string; values: string[] }) => void; onRemove: () => void }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const vals = draft.split(",").map((s) => s.trim()).filter((s) => s && !option.values.includes(s));
    if (vals.length) onChange({ ...option, values: [...option.values, ...vals] });
    setDraft("");
  };
  return (
    <div style={{ border: "1px solid var(--v-line)", borderRadius: "var(--v-radius)", padding: 12 }} className="v-stack v-stack--sm">
      <div className="v-row">
        <div style={{ flex: 1 }}><TextField label={`Option ${index + 1} name`} value={option.name} onChange={(v) => onChange({ ...option, name: v })} placeholder="Size" maxLength={40} /></div>
        <Button size="sm" icon aria-label={`Remove option ${index + 1}`} onClick={onRemove} style={{ marginTop: 22 }}><Trash2 size={14} /></Button>
      </div>
      <div className="cx-chips">
        {option.values.map((v) => (
          <span key={v} className="cx-chip">{v}<button type="button" aria-label={`Remove value ${v}`} onClick={() => onChange({ ...option, values: option.values.filter((x) => x !== v) })}><X size={12} /></button></span>
        ))}
      </div>
      <Input aria-label={`Values for option ${index + 1}`} placeholder="Type a value, press Enter (or comma separate several)" value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={add}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }} />
    </div>
  );
}

function StockReadout({ variants }: { variants: VarForm[] }) {
  const tracked = variants.filter((v) => v.id);
  if (!tracked.length) return <Empty title="No stock yet">Save the product first, then receive stock in Inventory.</Empty>;
  return (
    <div className="cx-scroll">
      <table className="cx-vtable" style={{ minWidth: 520 }} aria-label="Stock by warehouse">
        <thead><tr><th>Variant</th><th>Warehouse</th><th>On hand</th><th>Reserved</th><th>Available</th></tr></thead>
        <tbody>
          {tracked.flatMap((v) => (!v.track ? [<tr key={v.id}><td>{v.sku}</td><td colSpan={4} className="v-muted">Inventory not tracked — always available</td></tr>]
            : v.stock.length === 0 ? [<tr key={v.id}><td>{v.sku}</td><td colSpan={4} className="v-muted">No stock recorded yet</td></tr>]
            : v.stock.map((s) => <tr key={v.id + s.warehouse_id}><td className="v-mono">{v.sku}</td><td>{s.warehouse}</td><td className="v-num">{s.on_hand}</td><td className="v-num">{s.reserved}</td><td className="v-num"><strong>{s.available}</strong></td></tr>)))}
        </tbody>
      </table>
      <p className="v-hint" style={{ margin: "8px 0 0" }}>Stock is changed only through Inventory (receive, adjust, transfer, count) so every change is in the movement ledger.</p>
    </div>
  );
}

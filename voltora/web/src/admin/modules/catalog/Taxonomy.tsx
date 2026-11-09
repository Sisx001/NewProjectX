import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { del, errorMessage, get, patch, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Badge, Button, Checkbox, Confirm, Dialog, Drawer, Empty, Field, Select, Tabs, Textarea, TextField, useToast } from "@/ui";
import { DataTable } from "@/ui/DataTable";
import { PageHead } from "../../AdminApp";

type Cat = { id: string; name: string; slug: string; parent_id: string | null; description: string; image_url: string | null; position: number; is_active: boolean; is_demo: boolean; product_count: number };
type Brand = { id: string; name: string; slug: string; logo_url: string | null; is_demo: boolean; product_count: number };

export default function Taxonomy() {
  const [tab, setTab] = useState<"categories" | "brands">("categories");
  return (
    <>
      <PageHead title="Categories & brands" description="How shoppers browse and filter the catalog. Subcategories roll up into their parents." />
      <Tabs label="Taxonomy sections" value={tab} onChange={setTab} items={[{ value: "categories", label: "Categories" }, { value: "brands", label: "Brands" }]} />
      <div style={{ height: 16 }} />
      {tab === "categories" ? <Categories /> : <Brands />}
    </>
  );
}

function Categories() {
  const { can } = useAuth();
  const write = can("catalog.write");
  const q = useQuery({ queryKey: ["catalog", "categories"], queryFn: () => get<{ items: Cat[] }>("/api/catalog/categories") });
  const [edit, setEdit] = useState<Cat | "new" | null>(null);
  const rows = useMemo(() => {
    const items = q.data?.items ?? [];
    const out: (Cat & { depth: number })[] = [];
    const walk = (parent: string | null, depth: number) => items.filter((c) => c.parent_id === parent).forEach((c) => { out.push({ ...c, depth }); walk(c.id, depth + 1); });
    walk(null, 0);
    return out;
  }, [q.data]);
  return (
    <>
      {write ? <div className="a-toolbar"><Button variant="primary" onClick={() => setEdit("new")}><Plus size={16} /> New category</Button></div> : null}
      <DataTable<Cat & { depth: number }>
        label="Categories" rows={q.data ? rows : undefined} loading={q.isLoading} error={q.error ? errorMessage(q.error) : null} rowKey={(c) => c.id}
        onRowClick={write ? (c) => setEdit(c) : undefined}
        empty={<Empty title="No categories yet" action={write ? <Button variant="primary" onClick={() => setEdit("new")}>Create a category</Button> : undefined}>Categories group products for browsing and navigation.</Empty>}
        columns={[
          { key: "name", header: "Category", cell: (c) => <span style={{ paddingLeft: c.depth * 20, fontWeight: c.depth ? 400 : 550 }}>{c.depth ? "↳ " : ""}{c.name} {c.is_demo ? <Badge tone="info" plain>Demo</Badge> : null}</span> },
          { key: "slug", header: "URL slug", hideBelow: 700, cell: (c) => <span className="v-mono v-muted">{c.slug}</span> },
          { key: "n", header: "Products", align: "right", cell: (c) => c.product_count },
          { key: "active", header: "Visible", cell: (c) => (c.is_active ? <Badge tone="success" plain>shown</Badge> : <Badge plain>hidden</Badge>) },
        ]}
      />
      {edit ? <CategoryDrawer cat={edit === "new" ? null : edit} all={q.data?.items ?? []} onClose={() => setEdit(null)} /> : null}
    </>
  );
}

function CategoryDrawer({ cat, all, onClose }: { cat: Cat | null; all: Cat[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(cat?.name ?? "");
  const [slug, setSlug] = useState(cat?.slug ?? "");
  const [parent, setParent] = useState(cat?.parent_id ?? "");
  const [description, setDescription] = useState(cat?.description ?? "");
  const [image, setImage] = useState(cat?.image_url ?? "");
  const [active, setActive] = useState(cat?.is_active ?? true);
  const [position, setPosition] = useState(String(cat?.position ?? 0));
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const blocked = useMemo(() => {
    if (!cat) return new Set<string>();
    const s = new Set<string>([cat.id]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const c of all) if (c.parent_id && s.has(c.parent_id) && !s.has(c.id)) { s.add(c.id); grew = true; }
    }
    return s;
  }, [cat, all]);
  const save = useMutation({
    mutationFn: () => {
      const body = { name, slug: slug || undefined, parent_id: parent || null, description, image_url: image || null, is_active: active, position: Number(position) || 0 };
      return cat ? patch(`/api/catalog/categories/${cat.id}`, body) : post("/api/catalog/categories", body);
    },
    onSuccess: () => { toast("Category saved"); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); },
    onError: (e) => setErr(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => del(`/api/catalog/categories/${cat!.id}`),
    onSuccess: () => { toast("Category deleted"); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); },
    onError: (e) => { setConfirm(false); setErr(errorMessage(e)); },
  });
  return (
    <Drawer open onClose={onClose} title={cat ? `Edit ${cat.name}` : "New category"}
      footer={<>{cat ? <Button variant="ghost" onClick={() => setConfirm(true)}>Delete</Button> : null}<span style={{ flex: 1 }} /><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!name.trim()} onClick={() => save.mutate()}>Save</Button></>}>
      <div className="v-stack">
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <TextField label="Name" value={name} onChange={setName} maxLength={160} />
        <TextField label="URL slug" value={slug} onChange={setSlug} hint="Leave empty to generate from the name." className="v-mono" />
        <Field label="Parent category">{(id) => <Select id={id} value={parent} onChange={(e) => setParent(e.target.value)}><option value="">None (top level)</option>{all.filter((c) => !blocked.has(c.id)).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select>}</Field>
        <Field label="Description">{(id) => <Textarea id={id} rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />}</Field>
        <TextField label="Image URL" value={image} onChange={setImage} placeholder="https://… or /api/media/…" />
        <TextField label="Sort position" type="number" value={position} onChange={setPosition} hint="Lower numbers appear first." />
        <Checkbox label="Visible on the storefront" checked={active} onChange={setActive} />
      </div>
      <Confirm open={confirm} onClose={() => setConfirm(false)} onConfirm={() => remove.mutate()} busy={remove.isPending} danger title="Delete category?" confirmLabel="Delete">
        Products stay in the catalog but lose this category. Subcategories move up one level.
      </Confirm>
    </Drawer>
  );
}

function Brands() {
  const { can } = useAuth();
  const write = can("catalog.write");
  const q = useQuery({ queryKey: ["catalog", "brands"], queryFn: () => get<{ items: Brand[] }>("/api/catalog/brands") });
  const [edit, setEdit] = useState<Brand | "new" | null>(null);
  return (
    <>
      {write ? <div className="a-toolbar"><Button variant="primary" onClick={() => setEdit("new")}><Plus size={16} /> New brand</Button></div> : null}
      <DataTable<Brand>
        label="Brands" rows={q.data?.items} loading={q.isLoading} error={q.error ? errorMessage(q.error) : null} rowKey={(b) => b.id} onRowClick={write ? (b) => setEdit(b) : undefined}
        empty={<Empty title="No brands yet" action={write ? <Button variant="primary" onClick={() => setEdit("new")}>Add a brand</Button> : undefined}>Brands become a filter on the storefront.</Empty>}
        columns={[
          { key: "name", header: "Brand", cell: (b) => <span style={{ fontWeight: 550 }}>{b.name} {b.is_demo ? <Badge tone="info" plain>Demo</Badge> : null}</span> },
          { key: "slug", header: "URL slug", hideBelow: 700, cell: (b) => <span className="v-mono v-muted">{b.slug}</span> },
          { key: "n", header: "Products", align: "right", cell: (b) => b.product_count },
        ]}
      />
      {edit ? <BrandDialog brand={edit === "new" ? null : edit} onClose={() => setEdit(null)} /> : null}
    </>
  );
}

function BrandDialog({ brand, onClose }: { brand: Brand | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(brand?.name ?? "");
  const [slug, setSlug] = useState(brand?.slug ?? "");
  const [logo, setLogo] = useState(brand?.logo_url ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const save = useMutation({
    mutationFn: () => { const body = { name, slug: slug || undefined, logo_url: logo || null }; return brand ? patch(`/api/catalog/brands/${brand.id}`, body) : post("/api/catalog/brands", body); },
    onSuccess: () => { toast("Brand saved"); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); },
    onError: (e) => setErr(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => del(`/api/catalog/brands/${brand!.id}`),
    onSuccess: () => { toast("Brand deleted"); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); },
    onError: (e) => { setConfirm(false); setErr(errorMessage(e)); },
  });
  return (
    <>
      <Dialog open onClose={onClose} title={brand ? `Edit ${brand.name}` : "New brand"}
        footer={<>{brand ? <Button variant="ghost" onClick={() => setConfirm(true)}>Delete</Button> : null}<span style={{ flex: 1 }} /><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!name.trim()} onClick={() => save.mutate()}>Save</Button></>}>
        <div className="v-stack">
          {err ? <Alert tone="danger">{err}</Alert> : null}
          <TextField label="Name" value={name} onChange={setName} maxLength={160} autoFocus />
          <TextField label="URL slug" value={slug} onChange={setSlug} hint="Leave empty to generate from the name." />
          <TextField label="Logo URL" value={logo} onChange={setLogo} placeholder="https://…" />
        </div>
      </Dialog>
      <Confirm open={confirm} onClose={() => setConfirm(false)} onConfirm={() => remove.mutate()} busy={remove.isPending} danger title="Delete brand?" confirmLabel="Delete">Products keep existing but will no longer have a brand.</Confirm>
    </>
  );
}

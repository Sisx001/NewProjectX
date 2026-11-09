import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, EyeOff, ShieldAlert, ShoppingBag } from "lucide-react";
import { errorMessage, get, patch, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatMoney } from "@/lib/money";
import { Alert, Badge, Button, Card, Checkbox, Confirm, Empty, Field, Skeleton, Textarea, TextField, dateTime, useToast } from "@/ui";
import { PageHead } from "../../AdminApp";
import { download, useUnsavedGuard } from "../catalog/common";
import type { Customer } from "./Customers";

type Address = { id: string; label: string; name: string; phone: string; line1: string; line2: string; area: string; upazila: string; district: string; division: string; postal_code: string; is_default: boolean };
type Detail = Customer & { addresses: Address[]; orders_link: string };

export default function CustomerDetail() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["customers", "detail", id], queryFn: () => get<Detail>(`/api/customers/${id}`) });
  const c = q.data;
  const [name, setName] = useState("");
  const [tags, setTags] = useState("");
  const [note, setNote] = useState("");
  const [consent, setConsent] = useState(false);
  const [base, setBase] = useState("");
  const [eraseOpen, setEraseOpen] = useState(false);
  const write = can("customers.write");
  const pii = can("customers.pii");
  const form = JSON.stringify([name, tags, note, consent]);
  const dirty = !!c && form !== base;
  const guard = useUnsavedGuard(dirty);

  useEffect(() => {
    if (!c) return;
    setName(c.name); setTags(c.tags.join(", ")); setNote(c.note); setConsent(c.marketing_consent);
    setBase(JSON.stringify([c.name, c.tags.join(", "), c.note, c.marketing_consent]));
  }, [c]);

  const save = useMutation({
    mutationFn: () => patch(`/api/customers/${id}`, { name, tags: tags.split(",").map((t) => t.trim()).filter(Boolean), note, marketing_consent: consent }),
    onSuccess: () => { toast("Customer saved"); qc.invalidateQueries({ queryKey: ["customers"] }); },
    onError: (e) => toast(errorMessage(e), "danger"),
  });
  const exportData = useMutation({
    mutationFn: () => post<unknown>(`/api/customers/${id}/export`),
    onSuccess: (d) => { download(`customer-${id}.json`, JSON.stringify(d, null, 2), "application/json"); toast("Export downloaded and recorded in the audit trail"); },
    onError: (e) => toast(errorMessage(e), "danger"),
  });
  const erase = useMutation({
    mutationFn: () => post(`/api/customers/${id}/erase`, { confirm: true }),
    onSuccess: () => { toast("Personal data erased"); setEraseOpen(false); qc.invalidateQueries({ queryKey: ["customers"] }); },
    onError: (e) => { setEraseOpen(false); toast(errorMessage(e), "danger"); },
  });

  if (q.isLoading) return <div className="v-stack"><Skeleton h={32} w={260} /><Skeleton h={200} /></div>;
  if (q.error || !c) return <Empty title="Customer not found" action={<Link className="v-btn" to="/admin/customers">Back to customers</Link>}>{q.error ? errorMessage(q.error) : ""}</Empty>;

  return (
    <>
      <PageHead
        title={c.name || "Unnamed customer"}
        crumbs={[{ label: "Customers", to: "/admin/customers" }]}
        description={<span className="v-row v-row--wrap" style={{ gap: 8 }}>{c.erased ? <Badge>Personal data erased</Badge> : null}{c.has_account ? <Badge tone="info" plain>has account</Badge> : <Badge plain>guest</Badge>}{c.is_test ? <Badge tone="info" plain>test</Badge> : null}<span className="v-muted">Customer since {dateTime(c.created_at, { dateStyle: "medium" })}</span></span>}
        actions={<div className="v-row v-row--wrap">
          {can("orders.read") ? <Link className="v-btn" to={c.orders_link.replace("/admin", "")}><ShoppingBag size={16} /> View orders</Link> : null}
          {pii && !c.erased ? <Button loading={exportData.isPending} onClick={() => exportData.mutate()}><Download size={16} /> Export data</Button> : null}
          {write && pii && !c.erased ? <Button variant="danger" onClick={() => setEraseOpen(true)}><ShieldAlert size={16} /> Erase personal data</Button> : null}
        </div>}
      />
      {c.pii_masked && !c.erased ? <div style={{ marginBottom: 16 }}><Alert tone="info" title="Contact details are masked"><span className="v-row" style={{ gap: 6 }}><EyeOff size={14} aria-hidden /> Your role doesn't include customers.pii. Export and erasure need that permission.</span></Alert></div> : null}
      <div className="a-split">
        <div className="v-stack">
          <Card title="Profile">
            <dl className="v-kv">
              <dt>Email</dt><dd>{c.email ?? <span className="v-muted">—</span>} {c.pii_masked && c.email ? <Badge plain>masked</Badge> : null}</dd>
              <dt>Phone</dt><dd>{c.phone ?? <span className="v-muted">—</span>} {c.pii_masked && c.phone ? <Badge plain>masked</Badge> : null}</dd>
              <dt>Orders</dt><dd className="v-num">{c.orders_count}{c.last_order_at ? <span className="v-muted"> · last {dateTime(c.last_order_at)}</span> : null}</dd>
              <dt>Lifetime spend</dt><dd className="v-num">{formatMoney(c.total_spent_minor, c.currency)}</dd>
              <dt>Marketing consent</dt><dd>{c.marketing_consent ? <Badge tone="success" plain>opted in</Badge> : <Badge plain>not opted in</Badge>}{c.consent_updated_at ? <span className="v-muted"> · updated {dateTime(c.consent_updated_at, { dateStyle: "medium" })}</span> : null}</dd>
            </dl>
          </Card>
          <Card title="Addresses">
            {c.addresses.length === 0 ? <Empty title="No saved addresses">{c.erased ? "Addresses were deleted when this customer was erased." : "Addresses appear when the customer saves one at checkout."}</Empty> : (
              <div className="v-grid-2">
                {c.addresses.map((a) => (
                  <div key={a.id} style={{ border: "1px solid var(--v-line)", borderRadius: "var(--v-radius)", padding: 12 }}>
                    <div className="v-row" style={{ gap: 6 }}><strong>{a.label}</strong>{a.is_default ? <Badge tone="signal" plain>default</Badge> : null}</div>
                    <div>{a.name}</div>
                    <div className="v-muted">{[a.line1, a.line2, a.area].filter(Boolean).join(", ")}</div>
                    <div className="v-muted">{[a.upazila, a.district, a.division, a.postal_code].filter(Boolean).join(", ")}</div>
                    <div className="v-muted">{a.phone}</div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
        <div className="v-stack">
          <Card title="Notes & tags">
            <fieldset disabled={!write || c.erased} style={{ border: 0, padding: 0, margin: 0 }}>
              <div className="v-stack">
                <TextField label="Display name" value={name} onChange={setName} maxLength={160} />
                <TextField label="Tags" value={tags} onChange={setTags} hint="Comma separated, e.g. vip, wholesale." />
                <Field label="Internal note" hint="Only staff can see this.">{(fid) => <Textarea id={fid} rows={4} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
                <Checkbox label="Opted in to marketing" checked={consent} onChange={setConsent} />
                {write && !c.erased ? <div className="v-row"><Button variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>Save changes</Button>{dirty ? <Badge tone="warning">Unsaved</Badge> : null}</div> : null}
              </div>
            </fieldset>
          </Card>
        </div>
      </div>
      <Confirm open={eraseOpen} onClose={() => setEraseOpen(false)} onConfirm={() => erase.mutate()} busy={erase.isPending} danger title="Erase this customer's personal data?" confirmLabel="Erase permanently">
        Name, email, phone, saved addresses, notes and tags are anonymised, the linked shopper account is disabled and marketing consent is withdrawn. Order and payment records are kept for financial and legal reasons. This can't be undone and is written to the audit trail.
      </Confirm>
      {guard.dialog}
    </>
  );
}

import { useEffect, useState } from "react";
import { Link, NavLink, Navigate, Outlet, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatMoney } from "@/lib/money";
import { Alert, Confirm, Skeleton, StatusBadge, useToast } from "@/ui";
import { emptyLoc, LocationPicker, type Loc } from "../components/LocationPicker";
import { useCart } from "../lib/cart";
import { useDocTitle } from "../lib/data";
import { normalizeBdPhone, phoneHint } from "../lib/helpers";
import { rememberedOrders } from "../lib/orders";
import type { Order } from "../types";
import { OrderView } from "./Order";

export function AccountLayout() {
  const { me, loading, logout } = useAuth();
  const nav = useNavigate();
  if (loading) return <div className="s-wrap"><Skeleton h={200} /></div>;
  if (me?.user?.kind !== "customer") return <Navigate to={`/account/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  return (
    <div className="s-wrap s-acct">
      <header className="s-pagehead"><p className="s-eyebrow">Account</p><h1 className="s-h1">Hello, {me.user.name.split(" ")[0]}</h1></header>
      <div className="s-acct__grid">
        <nav className="s-acct__nav" aria-label="Account">
          <NavLink to="/account" end>Orders</NavLink><NavLink to="/account/addresses">Addresses</NavLink><NavLink to="/account/profile">Profile</NavLink><NavLink to="/wishlist">Wishlist</NavLink>
          <button type="button" onClick={async () => { await logout(); nav("/"); }}>Sign out</button>
        </nav>
        <div><Outlet /></div>
      </div>
    </div>
  );
}

type Row = { number: string; placed_at: string; status: string; payment_status: string; total_minor: number; currency: string; item_count: number };
export function Orders() {
  useDocTitle("Your orders");
  const q = useQuery({ queryKey: ["acct-orders"], queryFn: () => get<{ items: Row[] }>("/api/account/orders") });
  if (q.isLoading) return <Skeleton h={160} />;
  if (q.error) return <Alert tone="danger">Orders couldn't be loaded.</Alert>;
  if (!q.data!.items.length) return <div className="s-empty"><p className="s-empty__t">No orders yet</p><Link className="s-btn s-btn--primary" to="/shop">Start shopping</Link></div>;
  return (
    <ul className="s-orders">
      {q.data!.items.map((o) => (
        <li key={o.number}><Link to={`/account/orders/${o.number}`}>
          <span><strong>{o.number}</strong><small>{new Date(o.placed_at).toLocaleDateString("en-GB", { dateStyle: "medium" })} · {o.item_count} {o.item_count === 1 ? "item" : "items"}</small></span>
          <span className="s-orders__st"><StatusBadge status={o.status} /><StatusBadge status={o.payment_status} label={o.payment_status.replace(/_/g, " ")} /></span><b>{formatMoney(o.total_minor, o.currency)}</b></Link></li>
      ))}
    </ul>
  );
}

export function AccountOrder() {
  const { number = "" } = useParams();
  const cart = useCart();
  const nav = useNavigate();
  const [msg, setMsg] = useState<string | null>(null);
  useDocTitle(`Order ${number}`);
  const q = useQuery({ queryKey: ["acct-order", number], retry: false, queryFn: () => get<Order>(`/api/account/orders/${encodeURIComponent(number)}`) });
  if (q.isLoading) return <Skeleton h={240} />;
  if (!q.data) return <Alert tone="danger">We can't open that order. <Link to="/account">Back to orders</Link></Alert>;
  const o = q.data;
  const again = async () => {
    setMsg(null);
    let added = 0, skipped = 0;
    for (const l of o.lines) {
      if (!l.variant_id) { skipped++; continue; }
      try { await cart.add(l.variant_id, l.quantity, { openDrawer: false }); added++; } catch { skipped++; }
    }
    setMsg(added ? `Added ${added} ${added === 1 ? "item" : "items"} to your cart${skipped ? `; ${skipped} no longer available or out of stock` : ""}.` : "None of these items are available right now.");
    if (added) nav("/cart");
  };
  return (
    <>
      <p><Link className="s-link" to="/account">← All orders</Link></p>
      <OrderView o={o} retry={async () => { const r = await post<{ next: { type: string; url?: string } }>(`/api/store/orders/${o.number}/pay`); if (r.next.type === "redirect" && r.next.url) nav(r.next.url.startsWith("/") ? r.next.url : "/"); else await q.refetch(); }} />
      <p><button className="s-btn s-btn--quiet" onClick={again}>Buy again</button></p>
      {msg ? <Alert tone="info">{msg}</Alert> : null}
    </>
  );
}

type Addr = { id: string; label?: string; name?: string; phone?: string; line1: string; line2?: string; area?: string; upazila: string; district: string; division: string; postal_code?: string; is_default?: boolean };
function AddressForm({ initial, onDone, onCancel }: { initial?: Addr; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState({ label: initial?.label ?? "", name: initial?.name ?? "", phone: initial?.phone ?? "", line1: initial?.line1 ?? "", line2: initial?.line2 ?? "", area: initial?.area ?? "", postal_code: initial?.postal_code ?? "" });
  const [loc, setLoc] = useState<Loc>(initial ? { division: initial.division, district: initial.district, upazila: initial.upazila } : emptyLoc);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const s = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const fld = (k: keyof typeof f, label: string, ac?: string) => (
    <div className="s-field"><label htmlFor={`ad-${k}`}>{label}</label><input id={`ad-${k}`} className="s-input" value={f[k]} onChange={s(k)} autoComplete={ac} aria-invalid={!!errs[k]} />{errs[k] ? <p className="s-error">{errs[k]}</p> : null}</div>
  );
  return (
    <form className="s-form s-panel" noValidate onSubmit={async (e) => {
      e.preventDefault();
      const e2: Record<string, string> = {};
      if (f.phone && !normalizeBdPhone(f.phone)) e2.phone = phoneHint(f.phone) ?? "Invalid phone";
      if (f.line1.trim().length < 3) e2.line1 = "Enter house and road";
      if (!loc.division || !loc.district || !loc.upazila) e2.loc = "Choose division, district and upazila";
      setErrs(e2);
      if (Object.keys(e2).length) return;
      setBusy(true); setErr(null);
      const body = { ...f, phone: f.phone ? normalizeBdPhone(f.phone) : "", ...loc };
      try { await (initial ? patch(`/api/account/addresses/${initial.id}`, body) : post("/api/account/addresses", body)); onDone(); }
      catch (x) { setErr(x instanceof ApiError ? x.message : "Couldn't save the address."); } finally { setBusy(false); }
    }}>
      {err ? <Alert tone="danger">{err}</Alert> : null}
      {fld("label", "Label (Home, Office…)")}{fld("name", "Recipient name", "name")}{fld("phone", "Mobile number", "tel")}
      <LocationPicker value={loc} onChange={setLoc} idPrefix="ad" />{errs.loc ? <p className="s-error">{errs.loc}</p> : null}
      {fld("line1", "House, road, village", "address-line1")}{fld("line2", "Apartment, landmark")}{fld("area", "Area")}{fld("postal_code", "Postal code", "postal-code")}
      <div className="s-row"><button className="s-btn s-btn--primary" disabled={busy}>{busy ? "Saving…" : "Save address"}</button><button type="button" className="s-btn s-btn--quiet" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}

export function Addresses() {
  useDocTitle("Addresses");
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["addresses"], queryFn: () => get<{ items: Addr[] }>("/api/account/addresses") });
  const [edit, setEdit] = useState<Addr | "new" | null>(null);
  const [rm, setRm] = useState<Addr | null>(null);
  const remove = useMutation({ mutationFn: (id: string) => del(`/api/account/addresses/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["addresses"] }); setRm(null); toast("Address removed"); } });
  const done = () => { setEdit(null); qc.invalidateQueries({ queryKey: ["addresses"] }); };
  return (
    <>
      <div className="s-row s-row--between"><h2 className="s-h3">Saved addresses</h2>{edit ? null : <button className="s-btn s-btn--primary" onClick={() => setEdit("new")}>Add address</button>}</div>
      {edit ? <AddressForm initial={edit === "new" ? undefined : edit} onDone={done} onCancel={() => setEdit(null)} /> : null}
      {q.isLoading ? <Skeleton h={100} /> : q.data?.items.length === 0 && !edit ? <div className="s-empty"><p className="s-empty__t">No saved addresses</p><p className="s-muted">Add one to check out faster.</p></div> : (
        <ul className="s-addrs">{q.data?.items.map((a) => (
          <li key={a.id} className="s-panel"><strong>{a.label || "Address"}{a.is_default ? " · Default" : ""}</strong><address>{a.name ? <>{a.name}<br /></> : null}{[a.line1, a.line2, a.area].filter(Boolean).join(", ")}<br />{a.upazila}, {a.district}, {a.division}{a.phone ? <><br />{a.phone}</> : null}</address>
            <div className="s-row"><button className="s-btn s-btn--quiet" onClick={() => setEdit(a)}>Edit</button><button className="s-btn s-btn--quiet" onClick={() => setRm(a)}>Remove</button></div></li>
        ))}</ul>
      )}
      <Confirm open={!!rm} onClose={() => setRm(null)} onConfirm={() => rm && remove.mutate(rm.id)} title="Remove this address?" confirmLabel="Remove" danger busy={remove.isPending}>It will no longer be offered at checkout. Past orders aren't affected.</Confirm>
    </>
  );
}

export function Profile() {
  useDocTitle("Profile");
  const qc = useQueryClient();
  const { refresh } = useAuth();
  const q = useQuery({ queryKey: ["profile"], queryFn: () => get<{ name: string; phone: string | null; email?: string; marketing_consent: boolean }>("/api/account/profile") });
  const [v, setV] = useState({ name: "", phone: "", consent: false });
  const [msg, setMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  useEffect(() => { if (q.data) setV({ name: q.data.name, phone: q.data.phone ?? "", consent: q.data.marketing_consent }); }, [q.data]);
  if (q.isLoading) return <Skeleton h={160} />;
  return (
    <form className="s-form" noValidate style={{ maxWidth: 480 }} onSubmit={async (e) => {
      e.preventDefault(); setMsg(null);
      if (v.phone && !normalizeBdPhone(v.phone)) return setMsg({ tone: "danger", text: phoneHint(v.phone) ?? "Invalid phone" });
      try { await patch("/api/account/profile", { name: v.name.trim(), phone: v.phone ? normalizeBdPhone(v.phone) : null, marketing_consent: v.consent }); await qc.invalidateQueries({ queryKey: ["profile"] }); await refresh(); setMsg({ tone: "success", text: "Profile saved." }); }
      catch (x) { setMsg({ tone: "danger", text: x instanceof ApiError ? x.message : "Couldn't save." }); }
    }}>
      {msg ? <Alert tone={msg.tone}>{msg.text}</Alert> : null}
      {q.data?.email ? <p className="s-muted">Signed in as {q.data.email}</p> : null}
      <div className="s-field"><label htmlFor="pf-n">Full name</label><input id="pf-n" className="s-input" value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} autoComplete="name" /></div>
      <div className="s-field"><label htmlFor="pf-p">Mobile number</label><input id="pf-p" className="s-input" type="tel" value={v.phone} onChange={(e) => setV({ ...v, phone: e.target.value })} autoComplete="tel" /></div>
      <label className="s-check"><input type="checkbox" checked={v.consent} onChange={(e) => setV({ ...v, consent: e.target.checked })} /> <span>Email me offers and news</span></label>
      <button className="s-btn s-btn--primary">Save changes</button>
    </form>
  );
}

export { rememberedOrders };

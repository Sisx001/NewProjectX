import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, get, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatMoney } from "@/lib/money";
import { Alert, Badge, Skeleton } from "@/ui";
import { emptyLoc, LocationPicker, type Loc } from "../components/LocationPicker";
import { useCart } from "../lib/cart";
import { useDocTitle } from "../lib/data";
import { formatBdPhone, normalizeBdPhone, phoneHint } from "../lib/helpers";
import { rememberOrder } from "../lib/orders";
import type { Quote } from "../types";

type SavedAddress = { id: string; label?: string; name?: string; phone?: string; line1: string; line2?: string; area?: string; upazila: string; district: string; division: string; postal_code?: string };
const STEPS = ["Contact", "Address", "Delivery", "Payment", "Review"] as const;
const KEY = "voltora:checkout:key";
const idemKey = () => { let k = sessionStorage.getItem(KEY); if (!k) { k = crypto.randomUUID(); sessionStorage.setItem(KEY, k); } return k; };

export default function Checkout() {
  useDocTitle("Checkout");
  const nav = useNavigate();
  const { me } = useAuth();
  const cart = useCart();
  const user = me?.user?.kind === "customer" ? me.user : null;
  const [step, setStep] = useState(0);
  const [f, setF] = useState({ name: "", phone: "", email: "", line1: "", line2: "", area: "", postal: "", note: "" });
  const [loc, setLoc] = useState<Loc>(emptyLoc);
  const [ship, setShip] = useState("");
  const [pay, setPay] = useState("");
  const [terms, setTerms] = useState(false);
  const [save, setSave] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<{ tone: "danger" | "warning"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const top = useRef<HTMLDivElement>(null);
  const set = (k: keyof typeof f) => (v: string) => { setF((s) => ({ ...s, [k]: v })); setErrs((e) => ({ ...e, [k]: "" })); };

  useEffect(() => { if (user) setF((s) => ({ name: s.name || user.name, email: s.email || user.email, phone: s.phone || user.phone || "", line1: s.line1, line2: s.line2, area: s.area, postal: s.postal, note: s.note })); }, [user?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const saved = useQuery({ queryKey: ["addresses"], enabled: !!user, queryFn: () => get<{ items: SavedAddress[] }>("/api/account/addresses"), retry: false });

  const quote = useQuery({
    queryKey: ["quote", loc.district, loc.upazila, pay, cart.cart?.subtotal_minor, cart.cart?.discount?.code, cart.cart?.item_count],
    enabled: !!loc.district && !!loc.upazila && !!cart.cart?.lines.length,
    placeholderData: (p) => p,
    queryFn: () => post<Quote>("/api/store/checkout/quote", { address: { district: loc.district, upazila: loc.upazila }, payment_method: pay || undefined }),
    retry: false,
  });
  const q = quote.data;
  useEffect(() => {
    if (!q) return;
    if (!q.shipping_options.some((o) => o.id === ship)) setShip(q.shipping_options[0]?.id ?? "");
  }, [q, ship]);
  const shipOpt = q?.shipping_options.find((o) => o.id === ship);
  const methods = q?.payment_methods ?? [];
  useEffect(() => {
    const cur = methods.find((m) => m.key === pay);
    if (!cur || !cur.available || (cur.kind === "cod" && shipOpt && !shipOpt.cod_allowed)) setPay(methods.find((m) => m.available && !(m.kind === "cod" && shipOpt && !shipOpt.cod_allowed))?.key ?? "");
  }, [methods.map((m) => m.key + m.available).join(), shipOpt?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const lines = cart.cart?.lines ?? [];
  const stale = lines.some((l) => l.issues.length > 0) || (cart.cart?.issues.length ?? 0) > 0;

  function validate(upTo = 4): { errors: Record<string, string>; firstStep: number } {
    const e: Record<string, string> = {};
    let first = 99;
    const bad = (k: string, m: string, s: number) => { e[k] = m; first = Math.min(first, s); };
    if (f.name.trim().length < 2) bad("name", "Enter your full name", 0);
    if (!normalizeBdPhone(f.phone)) bad("phone", "Enter a valid Bangladesh mobile number", 0);
    if (f.email.trim() && !/^\S+@\S+\.\S+$/.test(f.email.trim())) bad("email", "That email doesn't look right", 0);
    if (upTo >= 1) {
      if (!loc.division) bad("division", "Choose a division", 1);
      if (!loc.district) bad("district", "Choose a district", 1);
      if (!loc.upazila) bad("upazila", "Choose an upazila", 1);
      if (f.line1.trim().length < 3) bad("line1", "Enter your house, road and area", 1);
    }
    if (upTo >= 2 && !ship) bad("ship", "Choose a delivery option", 2);
    if (upTo >= 3 && !pay) bad("pay", "Choose a payment method", 3);
    if (upTo >= 4 && !terms) bad("terms", "Please accept the terms to place your order", 4);
    return { errors: e, firstStep: first };
  }
  function next() {
    const { errors, firstStep } = validate(step);
    const relevant = Object.fromEntries(Object.entries(errors).filter(([, ]) => true));
    if (Object.keys(relevant).length && firstStep <= step) { setErrs(errors); setStep(firstStep); return; }
    setErrs({});
    setStep(Math.min(4, step + 1));
    top.current?.scrollIntoView({ block: "start" });
  }

  async function place() {
    const { errors, firstStep } = validate(4);
    if (Object.keys(errors).length) {
      setErrs(errors); setStep(firstStep);
      setBanner({ tone: "warning", text: "Please fix the highlighted fields." });
      setTimeout(() => document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 50);
      return;
    }
    setBusy(true); setBanner(null);
    try {
      const r = await post<{ order: { number: string; access_token: string }; next: { type: string; url?: string } }>("/api/store/checkout", {
        idempotency_key: idemKey(), email: f.email.trim(), phone: normalizeBdPhone(f.phone), name: f.name.trim(),
        address: { line1: f.line1.trim(), line2: f.line2.trim() || undefined, area: f.area.trim() || undefined, upazila: loc.upazila, district: loc.district, division: loc.division, postal_code: f.postal.trim() || undefined },
        shipping_option_id: ship, payment_method: pay, note: f.note.trim() || undefined, save_address: save && !!user, accept_terms: true,
      });
      sessionStorage.removeItem(KEY);
      rememberOrder(r.order.number, r.order.access_token);
      cart.refresh();
      const to = `/order/${r.order.number}?token=${encodeURIComponent(r.order.access_token)}`;
      if (r.next.type === "redirect" && r.next.url) {
        if (r.next.url.startsWith("/")) nav(r.next.url); else window.location.assign(r.next.url);
      } else nav(to);
    } catch (e) {
      if (e instanceof ApiError && e.code === "insufficient_stock") {
        await cart.refresh();
        setBanner({ tone: "warning", text: "Stock changed while you were checking out. We've refreshed your cart — review the quantities below and try again." });
      } else if (e instanceof ApiError && e.status === 422) {
        const fe = e.fieldErrors();
        setErrs((s) => ({ ...s, ...Object.fromEntries(Object.entries(fe).map(([k, v]) => [k.replace(/^address\./, ""), v])) }));
        setBanner({ tone: "danger", text: e.message || "Some details need attention." });
      } else setBanner({ tone: "danger", text: e instanceof ApiError ? e.message : "Couldn't place your order. Nothing was charged; please try again." });
      top.current?.scrollIntoView({ block: "start" });
    } finally { setBusy(false); }
  }

  const sections = useMemo(() => STEPS, []);
  if (cart.loading) return <div className="s-wrap"><Skeleton h={300} /></div>;
  if (!cart.cart || cart.cart.lines.length === 0)
    return <div className="s-wrap s-empty s-empty--page" data-testid="checkout-empty"><h1 className="s-h1">Your cart is empty</h1><p className="s-muted">Add something before checking out.</p><Link className="s-btn s-btn--primary" to="/shop">Browse the shop</Link></div>;

  const fe = (k: string) => errs[k] || undefined;
  const text = (k: keyof typeof f, label: string, p: { type?: string; autoComplete?: string; hint?: string; inputMode?: "tel" | "email" | "text"; optional?: boolean } = {}) => (
    <div className="s-field">
      <label htmlFor={`co-${k}`}>{label}{p.optional ? <span className="s-opt-tag"> (optional)</span> : null}</label>
      <input id={`co-${k}`} name={k} className="s-input" type={p.type ?? "text"} inputMode={p.inputMode} autoComplete={p.autoComplete} value={f[k]} aria-invalid={!!fe(k)} aria-describedby={`co-${k}-h`}
        onChange={(e) => set(k)(e.target.value)} onBlur={() => k === "phone" && f.phone && normalizeBdPhone(f.phone) && set("phone")(formatBdPhone(f.phone))} />
      <p id={`co-${k}-h`} className={fe(k) || (k === "phone" && phoneHint(f.phone)) ? "s-error" : "s-hint"}>{fe(k) || (k === "phone" ? phoneHint(f.phone) : null) || p.hint || ""}</p>
    </div>
  );
  const sec = (i: number, title: string, body: React.ReactNode) => (
    <section className="s-co__sec" data-active={step === i} data-done={step > i} aria-labelledby={`co-h${i}`}>
      <h2 id={`co-h${i}`} className="s-h3"><span className="s-co__n" aria-hidden>{i + 1}</span>{title}</h2>
      <div className="s-co__body">{body}</div>
      {i < 4 ? <button type="button" className="s-btn s-btn--primary s-only-m s-co__next" onClick={next}>Continue</button> : null}
    </section>
  );
  const t = q?.totals;
  const cur = cart.cart.currency;
  const codBlocked = (k: string) => methods.find((m) => m.key === k)?.kind === "cod" && shipOpt && !shipOpt.cod_allowed;

  return (
    <div className="s-wrap s-co" ref={top}>
      <h1 className="s-h1">Checkout</h1>
      <ol className="s-stepper s-only-m" aria-label="Checkout steps">
        {sections.map((s, i) => <li key={s} aria-current={i === step ? "step" : undefined} data-done={i < step}><button type="button" onClick={() => i < step && setStep(i)} disabled={i > step}>{i + 1}<span>{s}</span></button></li>)}
      </ol>
      {banner ? <Alert tone={banner.tone}>{banner.text}</Alert> : null}
      {stale ? <Alert tone="warning">Some items in your cart changed. <Link to="/cart">Review your cart</Link> before placing the order.</Alert> : null}
      <div className="s-co__grid">
        <form className="s-co__form" noValidate onSubmit={(e) => { e.preventDefault(); void place(); }}>
          {sec(0, "Contact", <>
            {text("name", "Full name", { autoComplete: "name" })}
            {text("phone", "Mobile number", { type: "tel", inputMode: "tel", autoComplete: "tel", hint: "For delivery updates, e.g. 01712 345678" })}
            {text("email", "Email", { type: "email", inputMode: "email", autoComplete: "email", optional: true, hint: "For your receipt and order link" })}
            {!user ? <p className="s-muted">Have an account? <Link to="/account/login?next=/checkout">Sign in</Link> to use saved addresses.</p> : null}
          </>)}
          {sec(1, "Delivery address", <>
            {saved.data?.items.length ? (
              <div className="s-field"><label htmlFor="co-saved">Saved address</label>
                <select id="co-saved" className="s-input" defaultValue="" onChange={(e) => { const a = saved.data!.items.find((x) => x.id === e.target.value); if (a) { setLoc({ division: a.division, district: a.district, upazila: a.upazila }); setF((s) => ({ ...s, line1: a.line1, line2: a.line2 ?? "", area: a.area ?? "", postal: a.postal_code ?? "", name: a.name || s.name, phone: a.phone || s.phone })); } }}>
                  <option value="">Enter a new address</option>{saved.data.items.map((a) => <option key={a.id} value={a.id}>{a.label || a.line1}, {a.district}</option>)}
                </select></div>
            ) : null}
            <LocationPicker value={loc} onChange={(v) => { setLoc(v); setErrs((e) => ({ ...e, division: "", district: "", upazila: "" })); }} idPrefix="co" errors={{ division: fe("division"), district: fe("district"), upazila: fe("upazila") }} />
            {text("line1", "House, road, village", { autoComplete: "address-line1" })}
            {text("line2", "Apartment, landmark", { autoComplete: "address-line2", optional: true })}
            <div className="s-two">{text("area", "Area / locality", { optional: true })}{text("postal", "Postal code", { inputMode: "text", autoComplete: "postal-code", optional: true })}</div>
            {user ? <label className="s-check"><input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} /> <span>Save this address to my account</span></label> : null}
          </>)}
          {sec(2, "Delivery method", !loc.upazila ? <p className="s-muted">Choose your delivery area to see options.</p> : quote.isLoading ? <Skeleton h={60} /> : quote.error ? <Alert tone="danger">{quote.error instanceof ApiError ? quote.error.message : "Couldn't load delivery options."}</Alert> : !q?.shipping_options.length ? <Alert tone="warning">We don't deliver to this area yet.</Alert> : (
            <div role="radiogroup" aria-label="Delivery method" className="s-opts">
              {q.shipping_options.map((o) => (
                <label key={o.id} className="s-choice" data-on={ship === o.id}><input type="radio" name="ship" value={o.id} checked={ship === o.id} onChange={() => { setShip(o.id); setErrs((e) => ({ ...e, ship: "" })); }} />
                  <span><strong>{o.label}</strong><small>{o.eta_label}</small></span><b>{o.amount_minor ? formatMoney(o.amount_minor, q.currency) : "Free"}</b></label>
              ))}
              {fe("ship") ? <p className="s-error">{fe("ship")}</p> : null}
            </div>
          ))}
          {sec(3, "Payment", !q ? <p className="s-muted">Choose your delivery area to see payment methods.</p> : (
            <div role="radiogroup" aria-label="Payment method" className="s-opts">
              {methods.map((m) => {
                const off = !m.available || !!codBlocked(m.key);
                return (
                  <label key={m.key} className="s-choice" data-on={pay === m.key} data-off={off}><input type="radio" name="pay" value={m.key} disabled={off} checked={pay === m.key} onChange={() => { setPay(m.key); setErrs((e) => ({ ...e, pay: "" })); }} />
                    <span><strong>{m.label} {m.sandbox ? <Badge tone="warning">Sandbox — simulated</Badge> : null}</strong>
                      <small>{off ? m.unavailable_reason || "Not available for this delivery option" : m.description}</small>{!off && pay === m.key && m.instructions ? <small>{m.instructions}</small> : null}</span>
                    {m.fee_minor ? <b>+{formatMoney(m.fee_minor, q.currency)}</b> : null}</label>
                );
              })}
              {methods.length === 0 ? <Alert tone="warning">No payment methods are available right now.</Alert> : null}
              {fe("pay") ? <p className="s-error">{fe("pay")}</p> : null}
            </div>
          ))}
          {sec(4, "Review & place order", <>
            <div className="s-field"><label htmlFor="co-note">Order note<span className="s-opt-tag"> (optional)</span></label><textarea id="co-note" className="s-input" rows={2} value={f.note} onChange={(e) => set("note")(e.target.value)} /></div>
            <label className="s-check"><input type="checkbox" checked={terms} aria-invalid={!!fe("terms")} onChange={(e) => { setTerms(e.target.checked); setErrs((x) => ({ ...x, terms: "" })); }} data-testid="accept-terms" /> <span>I agree to the <Link to="/pages/terms" target="_blank">terms</Link> and <Link to="/pages/privacy" target="_blank">privacy policy</Link></span></label>
            {fe("terms") ? <p className="s-error">{fe("terms")}</p> : null}
            <button type="submit" className="s-btn s-btn--primary s-btn--lg s-btn--block" data-testid="place-order" disabled={busy || stale}>{busy ? "Placing order…" : t ? `Place order · ${formatMoney(t.total_minor, cur)}` : "Place order"}</button>
            {pay && methods.find((m) => m.key === pay)?.kind === "cod" ? <p className="s-muted">You'll pay in cash when your order arrives.</p> : null}
          </>)}
        </form>
        <aside className="s-co__sum s-panel" aria-label="Order summary">
          <h2 className="s-h3">Order summary</h2>
          <ul className="s-minilines">{lines.map((l) => <li key={l.id}><span className="s-minilines__img">{l.image ? <img src={l.image.url} alt="" /> : null}<b>{l.quantity}</b></span><span>{l.title}{l.variant_title ? <small>{l.variant_title}</small> : null}</span><span>{formatMoney(l.line_total_minor, cur)}</span></li>)}</ul>
          <dl className="s-sum" aria-live="polite">
            <div><dt>Subtotal</dt><dd>{formatMoney(t?.subtotal_minor ?? cart.cart.subtotal_minor, cur)}</dd></div>
            {(t?.discount_minor ?? cart.cart.discount_minor) > 0 ? <div><dt>Discount{cart.cart.discount ? ` (${cart.cart.discount.code})` : ""}</dt><dd>−{formatMoney(t?.discount_minor ?? cart.cart.discount_minor, cur)}</dd></div> : null}
            <div><dt>Shipping</dt><dd>{t ? (t.shipping_minor ? formatMoney(t.shipping_minor, cur) : "Free") : "Enter address"}</dd></div>
            {t && t.payment_fee_minor > 0 ? <div><dt>Payment fee</dt><dd>{formatMoney(t.payment_fee_minor, cur)}</dd></div> : null}
            {t && t.tax_minor > 0 ? <div><dt>Tax</dt><dd>{formatMoney(t.tax_minor, cur)}</dd></div> : null}
            <div className="s-sum__total"><dt>Total</dt><dd>{t ? formatMoney(t.total_minor, cur) : "—"}</dd></div>
          </dl>
          <p className="s-muted">Final amounts are confirmed by the store when you place the order.</p>
        </aside>
      </div>
    </div>
  );
}

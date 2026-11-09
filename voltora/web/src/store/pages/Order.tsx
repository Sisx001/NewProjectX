import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Circle, Clock, XCircle } from "lucide-react";
import { ApiError, get, post, qs } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { Alert, Skeleton, StatusBadge } from "@/ui";
import { useDocTitle } from "../lib/data";
import { rememberedOrders, rememberOrder } from "../lib/orders";
import type { Next, Order } from "../types";
import { NotFound } from "./Misc";

const dt = (s: string) => new Date(s).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
const human = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function PaymentBanner({ o }: { o: Order }) {
  const s = o.payment_status;
  if (s === "paid") return <Alert tone="success" title="Payment received">Thank you. Your payment was confirmed by the payment provider.</Alert>;
  if (s === "pending" && o.payment.method !== "cod" && o.payment.can_retry === false) return <Alert tone="info" title="Confirming your payment…">We're waiting for the payment provider to confirm. This page updates automatically.</Alert>;
  if (s === "failed" || s === "cancelled") return <Alert tone="danger" title={s === "failed" ? "Payment failed" : "Payment cancelled"}>Your order is on hold and hasn't been paid. {o.payment.can_retry ? "You can try paying again below." : ""}</Alert>;
  if (o.payment.method === "cod" || s === "cod_pending" || s === "unpaid") return o.payment.method === "cod" ? <Alert tone="info" title="Pay on delivery">Keep the exact amount ready when your parcel arrives.</Alert> : null;
  return null;
}

export function OrderView({ o, retry }: { o: Order; retry?: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const cur = o.currency;
  return (
    <div className="s-order" data-testid="order-view">
      <div className="s-order__head">
        <div><p className="s-eyebrow">Order</p><h1 className="s-h1" data-testid="order-number">{o.number}</h1><p className="s-muted">Placed {dt(o.placed_at)}</p></div>
        <div className="s-order__badges"><StatusBadge status={o.status} label={human(o.status)} /><span data-testid="payment-status"><StatusBadge status={o.payment_status} label={`Payment: ${human(o.payment_status)}`} /></span><StatusBadge status={o.fulfillment_status} label={human(o.fulfillment_status)} /></div>
      </div>
      <PaymentBanner o={o} />
      {o.payment.can_retry && retry ? (
        <p><button className="s-btn s-btn--primary" disabled={busy} onClick={async () => { setBusy(true); setErr(null); try { await retry(); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't restart payment."); } finally { setBusy(false); } }}>Pay now</button>{err ? <span className="s-error" role="alert"> {err}</span> : null}</p>
      ) : null}
      <div className="s-order__grid">
        <section aria-label="Items">
          <h2 className="s-h3">Items</h2>
          <ul className="s-lines">{o.lines.map((l, i) => (
            <li key={i} className="s-line" data-compact><span className="s-line__img">{l.image ? <img src={l.image.url} alt="" loading="lazy" /> : null}</span>
              <div className="s-line__main"><p className="s-line__title">{l.title}</p>{l.variant_title ? <p className="s-muted">{l.variant_title}</p> : null}<p className="s-muted">Qty {l.quantity}</p></div>
              <p className="s-line__total">{l.line_total_minor !== undefined ? formatMoney(l.line_total_minor, cur) : ""}</p></li>))}
          </ul>
          <dl className="s-sum">
            <div><dt>Subtotal</dt><dd>{formatMoney(o.totals.subtotal_minor, cur)}</dd></div>
            {o.totals.discount_minor > 0 ? <div><dt>Discount</dt><dd>−{formatMoney(o.totals.discount_minor, cur)}</dd></div> : null}
            <div><dt>Shipping</dt><dd>{o.totals.shipping_minor ? formatMoney(o.totals.shipping_minor, cur) : "Free"}</dd></div>
            {o.totals.payment_fee_minor > 0 ? <div><dt>Payment fee</dt><dd>{formatMoney(o.totals.payment_fee_minor, cur)}</dd></div> : null}
            {o.totals.tax_minor > 0 ? <div><dt>Tax</dt><dd>{formatMoney(o.totals.tax_minor, cur)}</dd></div> : null}
            <div className="s-sum__total"><dt>Total</dt><dd data-testid="order-total">{formatMoney(o.totals.total_minor, cur)}</dd></div>
          </dl>
        </section>
        <aside>
          <section className="s-panel"><h2 className="s-h3">Delivery</h2>
            <address>{o.shipping_address.name ? <>{o.shipping_address.name}<br /></> : null}{[o.shipping_address.line1, o.shipping_address.line2, o.shipping_address.area].filter(Boolean).join(", ")}<br />{[o.shipping_address.upazila, o.shipping_address.district, o.shipping_address.division].filter(Boolean).join(", ")}{o.shipping_address.phone ? <><br />{o.shipping_address.phone}</> : null}</address>
            <p className="s-muted">Payment: {o.payment.label}</p>
          </section>
          {o.shipments.map((s, i) => (
            <section key={i} className="s-panel"><h2 className="s-h3">Shipment · {s.courier}</h2><p>{s.tracking_code ? <>Tracking <code>{s.tracking_code}</code> · </> : null}{human(s.status)}</p>
              <ol className="s-timeline">{s.events.map((e, k) => <li key={k}><span>{human(e.status)}</span><small>{e.message} · {dt(e.at)}</small></li>)}</ol></section>
          ))}
          <section className="s-panel"><h2 className="s-h3">Timeline</h2>
            <ol className="s-timeline">{o.timeline.map((e, i) => <li key={i}><span>{e.kind.includes("fail") || e.kind.includes("cancel") ? <XCircle size={16} aria-hidden /> : i === 0 ? <CheckCircle2 size={16} aria-hidden /> : <Circle size={16} aria-hidden />} {e.message}</span><small>{dt(e.at)}</small></li>)}</ol></section>
        </aside>
      </div>
    </div>
  );
}

export default function OrderPage() {
  const { number = "" } = useParams();
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const token = sp.get("token") ?? rememberedOrders().find((o) => o.number === number)?.token ?? "";
  const polls = useRef(0);
  useDocTitle(`Order ${number}`);
  useEffect(() => { if (sp.get("token")) rememberOrder(number, sp.get("token")!); }, [number, sp]);
  const q = useQuery({
    queryKey: ["order", number, token],
    retry: false,
    queryFn: () => get<Order>(`/api/store/orders/${encodeURIComponent(number)}${qs({ token })}`),
    refetchInterval: (query) => {
      const o = query.state.data;
      if (!o || o.payment.method === "cod" || !["pending", "processing", "authorized"].includes(o.payment_status)) return false;
      return ++polls.current > 40 ? false : 3000;
    },
  });
  if (q.isLoading) return <div className="s-wrap"><Skeleton h={300} /></div>;
  if (q.error instanceof ApiError && [403, 404].includes(q.error.status)) return <div className="s-wrap s-empty s-empty--page"><h1 className="s-h1">We can't open this order</h1><p className="s-muted">The link may be incomplete. Use the link from your confirmation, or <Link to="/track">track with your order number and phone</Link>.</p></div>;
  if (!q.data) return <div className="s-wrap"><Alert tone="danger" action={<button className="s-btn s-btn--quiet" onClick={() => q.refetch()}>Retry</button>}>Order couldn't be loaded.</Alert></div>;
  const pending = ["pending", "processing", "authorized"].includes(q.data.payment_status) && q.data.payment.method !== "cod";
  return (
    <div className="s-wrap">
      {sp.get("token") ? <p className="s-saved"><Clock size={14} aria-hidden /> Save this page's link — it's your access to this order. We've also kept it on this device.</p> : null}
      {pending && polls.current > 40 ? <Alert tone="warning">Still waiting for payment confirmation. Refresh later or contact support with your order number.</Alert> : null}
      <OrderView o={q.data} retry={async () => {
        const r = await post<{ next: Next }>(`/api/store/orders/${encodeURIComponent(number)}/pay${qs({ token })}`);
        if (r.next.type === "redirect") { if (r.next.url.startsWith("/")) nav(r.next.url); else window.location.assign(r.next.url); } else await q.refetch();
      }} />
    </div>
  );
}
export { NotFound };

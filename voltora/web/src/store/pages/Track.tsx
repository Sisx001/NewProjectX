import { useState } from "react";
import { ApiError, get, qs } from "@/lib/api";
import { Alert, StatusBadge } from "@/ui";
import { normalizeBdPhone, phoneHint } from "../lib/helpers";
import { useDocTitle } from "../lib/data";

type Tracking = { number?: string; status: string; payment_status?: string; shipments?: { courier: string; tracking_code: string | null; status: string; events: { status: string; message: string; at: string }[] }[]; events?: { status: string; message: string; at: string }[] };
const human = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export default function Track() {
  useDocTitle("Track your order");
  const [number, setNumber] = useState("");
  const [phone, setPhone] = useState("");
  const [res, setRes] = useState<Tracking | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="s-wrap s-narrow">
      <h1 className="s-h1">Track your order</h1>
      <p className="s-muted">Enter your order number and the mobile number you ordered with.</p>
      <form className="s-form" noValidate onSubmit={async (e) => {
        e.preventDefault(); setErr(null); setRes(null);
        const p = normalizeBdPhone(phone);
        if (!number.trim() || !p) return setErr(!number.trim() ? "Enter your order number." : "Enter a valid Bangladesh mobile number.");
        setBusy(true);
        try { setRes(await get<Tracking>(`/api/store/track${qs({ number: number.trim(), phone: p })}`)); }
        catch (x) { setErr(x instanceof ApiError && x.status === 404 ? "We couldn't find an order with those details." : x instanceof ApiError ? x.message : "Tracking is unavailable right now."); }
        finally { setBusy(false); }
      }}>
        <div className="s-field"><label htmlFor="tr-n">Order number</label><input id="tr-n" className="s-input" value={number} onChange={(e) => setNumber(e.target.value)} autoComplete="off" /></div>
        <div className="s-field"><label htmlFor="tr-p">Mobile number</label><input id="tr-p" className="s-input" type="tel" inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} aria-describedby="tr-h" /><p id="tr-h" className={phoneHint(phone) ? "s-error" : "s-hint"}>{phoneHint(phone) ?? ""}</p></div>
        <button className="s-btn s-btn--primary s-btn--block" disabled={busy}>{busy ? "Looking…" : "Track order"}</button>
      </form>
      {err ? <Alert tone="danger">{err}</Alert> : null}
      {res ? (
        <section className="s-panel" data-testid="track-result" aria-live="polite">
          <h2 className="s-h3">Status: <StatusBadge status={res.status} label={human(res.status)} /></h2>
          {(res.shipments ?? []).map((s, i) => <div key={i}><p><strong>{s.courier}</strong>{s.tracking_code ? <> · <code>{s.tracking_code}</code></> : null} · {human(s.status)}</p>
            <ol className="s-timeline">{s.events.map((e, k) => <li key={k}><span>{human(e.status)}</span><small>{e.message} · {new Date(e.at).toLocaleString("en-GB")}</small></li>)}</ol></div>)}
          {!res.shipments?.length ? <p className="s-muted">No shipment yet — we'll update this as soon as your order is handed to a courier.</p> : null}
        </section>
      ) : null}
    </div>
  );
}

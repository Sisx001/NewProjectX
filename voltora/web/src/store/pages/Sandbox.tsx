import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { ApiError, get, post } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { Alert, Skeleton } from "@/ui";
import { useDocTitle } from "../lib/data";

type Intent = { intent_id: string; amount_minor: number; currency: string; order_number: string; merchant: string; status: string };

/** Hosted-page simulation for the built-in sandbox gateway. It never moves money; the server decides paid state from a signed webhook. */
export default function Sandbox() {
  const { intentId = "" } = useParams();
  const nav = useNavigate();
  useDocTitle("VOLTORA Sandbox Gateway");
  const q = useQuery({ queryKey: ["sandbox", intentId], retry: false, queryFn: () => get<Intent>(`/api/payments/sandbox/${encodeURIComponent(intentId)}`) });
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function go(outcome: "success" | "fail" | "cancel") {
    setBusy(outcome); setErr(null);
    try {
      const r = await post<{ redirect_url: string }>(`/api/payments/sandbox/${encodeURIComponent(intentId)}/complete`, { outcome });
      if (r.redirect_url.startsWith("/")) nav(r.redirect_url); else window.location.assign(r.redirect_url);
    } catch (e) { setErr(e instanceof ApiError ? e.message : "The sandbox couldn't complete this payment."); setBusy(null); }
  }
  const i = q.data;
  return (
    <main className="s-sbx" data-testid="sandbox-page">
      <div className="s-sbx__banner"><FlaskConical size={18} aria-hidden /> <strong>VOLTORA Sandbox Gateway — simulation, no real money</strong></div>
      <div className="s-sbx__card">
        {q.isLoading ? <Skeleton h={160} /> : q.error || !i ? <Alert tone="danger">This sandbox payment link isn't valid or has expired.</Alert> : (
          <>
            <p className="s-eyebrow">{i.merchant}</p>
            <h1 className="s-h2">Pay {formatMoney(i.amount_minor, i.currency)}</h1>
            <p className="s-muted">Order {i.order_number}</p>
            {i.status !== "pending" && i.status !== "requires_action" && i.status !== "created" ? <Alert tone="info">This payment is already {i.status}.</Alert> : null}
            <p className="s-muted">Choose the outcome to simulate. The store only marks the order paid after it verifies the gateway's signed notification.</p>
            {err ? <Alert tone="danger">{err}</Alert> : null}
            <div className="s-sbx__acts">
              <button className="s-btn s-btn--primary s-btn--lg" disabled={!!busy} onClick={() => go("success")} data-testid="sbx-success">{busy === "success" ? "Working…" : "Pay successfully"}</button>
              <button className="s-btn s-btn--quiet s-btn--lg" disabled={!!busy} onClick={() => go("fail")} data-testid="sbx-fail">Fail payment</button>
              <button className="s-btn s-btn--quiet s-btn--lg" disabled={!!busy} onClick={() => go("cancel")} data-testid="sbx-cancel">Cancel</button>
            </div>
          </>
        )}
      </div>
    </main>
  );
}

import { useEffect, useState } from "react";
import { Check, CircleAlert, KeyRound } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { Alert, Button, Field, Select, TextField } from "@/ui";
import { Mark } from "@/admin/AdminApp";
import "@/admin/admin.css";
import "./install.css";

type Check = { key: string; label: string; ok: boolean; detail: string; warning_only?: boolean };
type Progress = Record<string, { done?: boolean } & Record<string, unknown>>;
const STEPS = [
  { key: "token", label: "Setup token" },
  { key: "environment", label: "Environment" },
  { key: "store", label: "Store" },
  { key: "owner", label: "Owner account" },
  { key: "catalog", label: "Starting data" },
  { key: "review", label: "Launch" },
] as const;
type StepKey = (typeof STEPS)[number]["key"];

function call<T>(path: string, token: string, body?: unknown) {
  return api<T>(`/api/install/${path}`, { method: body === undefined ? "GET" : "POST", body, headers: { "X-Bootstrap-Token": token } });
}

export default function InstallWizard() {
  const [status, setStatus] = useState<{ installed: boolean; token_location: string } | null>(null);
  const [token, setToken] = useState(() => sessionStorage.getItem("voltora:bootstrap") ?? "");
  const [step, setStep] = useState<StepKey>("token");
  const [progress, setProgress] = useState<Progress>({});
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [store, setStore] = useState({ name: "", currency: "BDT", locale: "en", timezone: "Asia/Dhaka", support_email: "", support_phone: "" });
  const [owner, setOwner] = useState({ name: "", email: "", password: "" });
  const [catalog, setCatalog] = useState<"empty" | "demo">("empty");

  useEffect(() => {
    document.title = "Install · VOLTORA";
    api<{ installed: boolean; token_location: string }>("/api/install/status").then(setStatus).catch((e) => setErr(errorMessage(e)));
  }, []);

  async function run(fn: () => Promise<void>) {
    setErr(null);
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const verify = () =>
    run(async () => {
      const r = await call<{ progress: Progress }>("verify", token.trim(), {});
      sessionStorage.setItem("voltora:bootstrap", token.trim());
      setProgress(r.progress);
      // Resume at the first unfinished step.
      const next = (["environment", "store", "owner", "catalog"] as StepKey[]).find((k) => !r.progress[k]?.done) ?? "review";
      setStep(next);
      if (next === "environment") loadChecks();
    });

  const loadChecks = () => run(async () => setChecks((await call<{ checks: Check[] }>("checks", token.trim())).checks));

  if (status?.installed) {
    return (
      <Frame step={null}>
        <h1>Already installed</h1>
        <p className="v-muted">The installer is closed. Staff sign in from the admin.</p>
        <a className="v-btn v-btn--primary" href="/admin">Go to admin</a>
      </Frame>
    );
  }

  return (
    <Frame step={step} progress={progress}>
      {err ? <Alert tone="danger">{err}</Alert> : null}
      {step === "token" ? (
        <form className="v-stack" onSubmit={(e) => { e.preventDefault(); verify(); }}>
          <div>
            <h1>Set up VOLTORA</h1>
            <p className="v-muted">The installer only opens with the one-time setup token generated on your server. It closes permanently when setup finishes.</p>
          </div>
          <Alert tone="info" title="Where is the token?">
            On the server, run <code className="v-mono">cat {status?.token_location ?? ".data/bootstrap-token"}</code>
          </Alert>
          <TextField label="Setup token" value={token} onChange={setToken} autoComplete="off" spellCheck={false} required autoFocus />
          <Button type="submit" variant="primary" size="lg" loading={busy} disabled={!token.trim()}><KeyRound size={16} /> Continue</Button>
        </form>
      ) : null}

      {step === "environment" ? (
        <div className="v-stack">
          <div>
            <h1>Environment checks</h1>
            <p className="v-muted">Real checks against this server. Warnings can be fixed later; failures block launch.</p>
          </div>
          <ul className="i-checks">
            {(checks ?? []).map((c) => (
              <li key={c.key} data-ok={c.ok} data-warn={!c.ok && c.warning_only}>
                {c.ok ? <Check size={16} /> : <CircleAlert size={16} />}
                <div>
                  <strong>{c.label}</strong>
                  <div className="v-muted">{c.detail}</div>
                </div>
              </li>
            ))}
            {!checks ? <li>Running checks…</li> : null}
          </ul>
          <div className="v-row">
            <Button onClick={loadChecks} loading={busy}>Re-run</Button>
            <Button variant="primary" disabled={!checks || checks.some((c) => !c.ok && !c.warning_only)} onClick={() => setStep("store")}>Continue</Button>
          </div>
        </div>
      ) : null}

      {step === "store" ? (
        <form className="v-stack" onSubmit={(e) => { e.preventDefault(); run(async () => { const r = await call<{ progress: Progress }>("store", token, { ...store, support_email: store.support_email || null, support_phone: store.support_phone || null }); setProgress(r.progress); setStep("owner"); }); }}>
          <div><h1>Your store</h1><p className="v-muted">You can change all of this later in Settings.</p></div>
          <TextField label="Store name" value={store.name} onChange={(v) => setStore({ ...store, name: v })} required minLength={2} />
          <div className="v-grid-2">
            <Field label="Currency">{(id) => <Select id={id} value={store.currency} onChange={(e) => setStore({ ...store, currency: e.target.value })}><option value="BDT">BDT — Bangladeshi Taka</option><option value="USD">USD — US Dollar</option></Select>}</Field>
            <Field label="Language">{(id) => <Select id={id} value={store.locale} onChange={(e) => setStore({ ...store, locale: e.target.value })}><option value="en">English</option><option value="bn">বাংলা (Bangla)</option></Select>}</Field>
          </div>
          <TextField label="Time zone" value={store.timezone} onChange={(v) => setStore({ ...store, timezone: v })} />
          <div className="v-grid-2">
            <TextField label="Support email" type="email" value={store.support_email} onChange={(v) => setStore({ ...store, support_email: v })} />
            <TextField label="Support phone" type="tel" value={store.support_phone} onChange={(v) => setStore({ ...store, support_phone: v })} />
          </div>
          <Button type="submit" variant="primary" loading={busy}>Save and continue</Button>
        </form>
      ) : null}

      {step === "owner" ? (
        <form className="v-stack" onSubmit={(e) => { e.preventDefault(); run(async () => { const r = await call<{ progress: Progress }>("owner", token, owner); setProgress(r.progress); setStep("catalog"); }); }}>
          <div><h1>Owner account</h1><p className="v-muted">The owner holds every permission. Turn on two-step verification right after launch.</p></div>
          <TextField label="Full name" value={owner.name} onChange={(v) => setOwner({ ...owner, name: v })} autoComplete="name" required />
          <TextField label="Email" type="email" value={owner.email} onChange={(v) => setOwner({ ...owner, email: v })} autoComplete="username" required />
          <TextField label="Password" type="password" value={owner.password} onChange={(v) => setOwner({ ...owner, password: v })} autoComplete="new-password" hint="At least 10 characters, mixing letters with numbers or symbols." required />
          <Button type="submit" variant="primary" loading={busy}>Create owner</Button>
        </form>
      ) : null}

      {step === "catalog" ? (
        <div className="v-stack">
          <div><h1>Starting data</h1><p className="v-muted">Foundations (warehouse, Bangladesh delivery zones, cash on delivery, default theme and pages) are always created.</p></div>
          <label className="i-choice" data-on={catalog === "empty"}>
            <input type="radio" name="cat" checked={catalog === "empty"} onChange={() => setCatalog("empty")} />
            <div><strong>Start empty</strong><div className="v-muted">Add your own products. Recommended for a real launch.</div></div>
          </label>
          <label className="i-choice" data-on={catalog === "demo"}>
            <input type="radio" name="cat" checked={catalog === "demo"} onChange={() => setCatalog("demo")} />
            <div><strong>Add labelled demo products</strong><div className="v-muted">Sample items tagged “demo” to explore the store. Remove them in one step before launch.</div></div>
          </label>
          <Button variant="primary" loading={busy} onClick={() => run(async () => { const r = await call<{ progress: Progress }>("catalog", token, { mode: catalog }); setProgress(r.progress); setStep("review"); })}>Continue</Button>
        </div>
      ) : null}

      {step === "review" ? (
        <div className="v-stack">
          <div><h1>Ready to launch</h1><p className="v-muted">Finishing closes this installer and deletes the setup token.</p></div>
          <ul className="i-checks">
            {(["environment", "store", "owner", "catalog"] as const).map((k) => (
              <li key={k} data-ok={!!progress[k]?.done}>
                {progress[k]?.done ? <Check size={16} /> : <CircleAlert size={16} />}
                <div><strong>{STEPS.find((s) => s.key === k)?.label}</strong><div className="v-muted">{progress[k]?.done ? "Done" : "Not finished"}</div></div>
              </li>
            ))}
          </ul>
          <Alert tone="warning" title="Payments stay in sandbox">Online gateways and couriers need verified credentials before they can go live. Cash on delivery works immediately.</Alert>
          <Button variant="signal" size="lg" loading={busy} onClick={() => run(async () => { await call("complete", token, {}); sessionStorage.removeItem("voltora:bootstrap"); location.href = "/admin"; })}>Finish setup</Button>
        </div>
      ) : null}
    </Frame>
  );
}

function Frame({ step, progress = {}, children }: { step: StepKey | null; progress?: Progress; children: React.ReactNode }) {
  return (
    <div className="i-wrap">
      <aside className="i-rail">
        <div className="v-row" style={{ gap: 10 }}><Mark /> <strong style={{ letterSpacing: ".1em" }}>VOLTORA</strong></div>
        {step ? (
          <ol>
            {STEPS.map((s) => (
              <li key={s.key} aria-current={s.key === step ? "step" : undefined} data-done={!!progress[s.key]?.done}>{s.label}</li>
            ))}
          </ol>
        ) : null}
        <p className="v-muted" style={{ fontSize: 12 }}>Setup progress is saved on the server. You can close this tab and resume with the same token.</p>
      </aside>
      <main className="i-main"><div className="i-card">{children}</div></main>
    </div>
  );
}

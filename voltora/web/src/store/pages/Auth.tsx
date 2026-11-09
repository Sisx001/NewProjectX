import { useState, type ReactNode } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert } from "@/ui";
import { normalizeBdPhone, phoneHint } from "../lib/helpers";
import { useDocTitle } from "../lib/data";

function Shell({ title, lead, children, foot }: { title: string; lead?: string; children: ReactNode; foot?: ReactNode }) {
  useDocTitle(title);
  return (
    <div className="s-wrap s-narrow s-auth">
      <h1 className="s-h1">{title}</h1>
      {lead ? <p className="s-muted">{lead}</p> : null}
      {children}
      {foot ? <p className="s-auth__foot">{foot}</p> : null}
    </div>
  );
}
function Field({ id, label, value, set, type = "text", ac, error, hint, inputMode }: { id: string; label: string; value: string; set: (v: string) => void; type?: string; ac?: string; error?: string; hint?: string; inputMode?: "tel" | "email" }) {
  return (
    <div className="s-field"><label htmlFor={id}>{label}</label>
      <input id={id} className="s-input" type={type} value={value} onChange={(e) => set(e.target.value)} autoComplete={ac} inputMode={inputMode} aria-invalid={!!error} aria-describedby={`${id}-h`} />
      <p id={`${id}-h`} className={error ? "s-error" : "s-hint"}>{error || hint || ""}</p></div>
  );
}
const safeNext = (n: string | null) => (n && n.startsWith("/") && !n.startsWith("//") ? n : "/account");

export function Login() {
  const { me, refresh } = useAuth();
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (me?.user?.kind === "customer") return <Navigate to={safeNext(sp.get("next"))} replace />;
  return (
    <Shell title="Sign in" lead="Welcome back." foot={<>New here? <Link to={`/account/register${sp.get("next") ? `?next=${encodeURIComponent(sp.get("next")!)}` : ""}`}>Create an account</Link></>}>
      {me?.user?.kind === "staff" ? <Alert tone="info">You're signed in as staff. The admin console is at <Link to="/admin">/admin</Link>.</Alert> : null}
      {err ? <Alert tone="danger">{err}</Alert> : null}
      <form className="s-form" onSubmit={async (e) => {
        e.preventDefault(); setErr(null); setBusy(true);
        try { await post("/api/auth/login", { email: email.trim(), password }); await qc.invalidateQueries({ queryKey: ["cart"] }); await refresh(); nav(safeNext(sp.get("next")), { replace: true }); }
        catch (x) { setErr(x instanceof ApiError ? (x.status === 429 ? "Too many attempts. Wait a few minutes and try again." : x.message) : "Couldn't sign in."); }
        finally { setBusy(false); }
      }}>
        <Field id="li-e" label="Email" type="email" value={email} set={setEmail} ac="username" inputMode="email" />
        <Field id="li-p" label="Password" type="password" value={password} set={setPassword} ac="current-password" />
        <button className="s-btn s-btn--primary s-btn--block s-btn--lg" disabled={busy || !email || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        <Link className="s-link" to="/account/forgot">Forgot your password?</Link>
      </form>
    </Shell>
  );
}

export function Register() {
  const { refresh } = useAuth();
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const [v, setV] = useState({ name: "", email: "", phone: "", password: "" });
  const [consent, setConsent] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const s = (k: keyof typeof v) => (x: string) => { setV((o) => ({ ...o, [k]: x })); setErrs((e) => ({ ...e, [k]: "" })); };
  return (
    <Shell title="Create your account" lead="Track orders, save addresses and check out faster." foot={<>Already registered? <Link to="/account/login">Sign in</Link></>}>
      {err ? <Alert tone="danger">{err}</Alert> : null}
      <form className="s-form" noValidate onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        const e2: Record<string, string> = {};
        if (v.name.trim().length < 2) e2.name = "Enter your name";
        if (!/^\S+@\S+\.\S+$/.test(v.email.trim())) e2.email = "Enter a valid email";
        if (v.phone && !normalizeBdPhone(v.phone)) e2.phone = "Enter a valid Bangladesh mobile number";
        if (v.password.length < 8) e2.password = "Use at least 8 characters";
        if (Object.keys(e2).length) return setErrs(e2);
        setBusy(true);
        try { await post("/api/auth/register", { name: v.name.trim(), email: v.email.trim(), phone: v.phone ? normalizeBdPhone(v.phone) : null, password: v.password, marketing_consent: consent }); await refresh(); nav(safeNext(sp.get("next")), { replace: true }); }
        catch (x) { if (x instanceof ApiError) { const fe = x.fieldErrors(); if (Object.keys(fe).length) setErrs(fe); setErr(x.message); } else setErr("Couldn't create the account."); }
        finally { setBusy(false); }
      }}>
        <Field id="rg-n" label="Full name" value={v.name} set={s("name")} ac="name" error={errs.name} />
        <Field id="rg-e" label="Email" type="email" value={v.email} set={s("email")} ac="email" inputMode="email" error={errs.email} />
        <Field id="rg-ph" label="Mobile number (optional)" type="tel" value={v.phone} set={s("phone")} ac="tel" inputMode="tel" error={errs.phone || phoneHint(v.phone) || undefined} />
        <Field id="rg-p" label="Password" type="password" value={v.password} set={s("password")} ac="new-password" error={errs.password} hint="At least 8 characters" />
        <label className="s-check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> <span>Send me offers and news (optional, unsubscribe any time)</span></label>
        <button className="s-btn s-btn--primary s-btn--block s-btn--lg" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
      </form>
    </Shell>
  );
}

export function Forgot() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Shell title="Reset your password" lead="We'll email you a link that's valid for two hours." foot={<Link to="/account/login">Back to sign in</Link>}>
      {done ? <Alert tone="success">{done}</Alert> : (
        <form className="s-form" onSubmit={async (e) => { e.preventDefault(); setErr(null); setBusy(true); try { const r = await post<{ message?: string }>("/api/auth/password/forgot", { email: email.trim() }); setDone(r.message ?? "If an account exists for that email, a reset link is on its way."); } catch (x) { setErr(x instanceof ApiError ? x.message : "Couldn't send the link."); } finally { setBusy(false); } }}>
          {err ? <Alert tone="danger">{err}</Alert> : null}
          <Field id="fg-e" label="Email" type="email" value={email} set={setEmail} ac="email" inputMode="email" />
          <button className="s-btn s-btn--primary s-btn--block" disabled={busy || !email}>{busy ? "Sending…" : "Email me a link"}</button>
        </form>
      )}
    </Shell>
  );
}

export function Reset() {
  const [sp] = useSearchParams();
  const token = sp.get("token") ?? "";
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Shell title="Choose a new password" foot={<Link to="/account/login">Back to sign in</Link>}>
      {!token ? <Alert tone="danger">This reset link is incomplete. <Link to="/account/forgot">Request a new one</Link>.</Alert> : ok ? <Alert tone="success">Password updated. <Link to="/account/login">Sign in</Link> with your new password.</Alert> : (
        <form className="s-form" onSubmit={async (e) => { e.preventDefault(); if (pw.length < 8) return setErr("Use at least 8 characters."); setErr(null); setBusy(true); try { await post("/api/auth/password/reset", { token, password: pw }); setOk(true); } catch (x) { setErr(x instanceof ApiError ? x.message : "Couldn't reset the password."); } finally { setBusy(false); } }}>
          {err ? <Alert tone="danger">{err}</Alert> : null}
          <Field id="rs-p" label="New password" type="password" value={pw} set={setPw} ac="new-password" hint="At least 8 characters" />
          <button className="s-btn s-btn--primary s-btn--block" disabled={busy}>{busy ? "Saving…" : "Update password"}</button>
        </form>
      )}
    </Shell>
  );
}

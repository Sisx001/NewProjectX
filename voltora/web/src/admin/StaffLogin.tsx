import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { post, errorMessage, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Button, TextField } from "@/ui";
import { Mark } from "./AdminApp";

type Mode = "login" | "mfa" | "invite" | "no-access";

export function StaffLogin({ mode: initial = "login" }: { mode?: Mode }) {
  const { me, refresh, logout } = useAuth();
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>(initial);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const token = new URLSearchParams(location.search).get("token") ?? "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      if (mode === "login") {
        const r = await post<{ mfa_required: boolean; user: { kind: string } }>("/api/auth/login", { email, password });
        if (r.user.kind !== "staff") {
          setErr("This is a customer account. Use the store sign-in instead.");
          await post("/api/auth/logout");
          return;
        }
        if (r.mfa_required) setMode("mfa");
      } else if (mode === "mfa") {
        await post("/api/auth/mfa/challenge", { code });
      } else if (mode === "invite") {
        await post("/api/auth/invite/accept", { token, password, name: name || undefined });
        history.replaceState(null, "", "/admin");
      }
      qc.clear();
      await refresh();
    } catch (e2) {
      setErr(e2 instanceof ApiError && e2.code === "validation_error" ? "Check the highlighted fields." : errorMessage(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="a-auth">
      <div className="a-auth__art">
        <div className="a-auth__grid" />
        <div className="v-row" style={{ gap: 10, position: "relative" }}>
          <Mark size={30} />
          <strong style={{ letterSpacing: ".1em" }}>VOLTORA</strong>
        </div>
        <h2 style={{ position: "relative" }}>
          Run the whole store from <em>one</em> calm place.
        </h2>
        <p style={{ position: "relative", color: "#9a9aa3", maxWidth: 420 }}>Orders, stock, money, delivery, design and automation — with permissions and an audit trail behind every change.</p>
      </div>
      <form className="a-auth__form" onSubmit={submit} noValidate>
        <div className="v-stack" style={{ maxWidth: 380, width: "100%" }}>
          <div>
            <h1 style={{ fontSize: "var(--v-text-2xl)" }}>
              {mode === "login" ? "Staff sign in" : mode === "mfa" ? "Two-step verification" : mode === "invite" ? "Accept your invitation" : "No store access"}
            </h1>
            <p className="v-muted" style={{ marginTop: 6 }}>
              {mode === "login"
                ? "Use the email your store owner invited."
                : mode === "mfa"
                  ? "Enter the 6-digit code from your authenticator app, or a recovery code."
                  : mode === "invite"
                    ? "Choose a password to finish setting up your staff account."
                    : `Signed in as ${me?.user?.email}. Your access may have expired or been removed.`}
            </p>
          </div>
          {err ? <Alert tone="danger">{err}</Alert> : null}
          {mode === "login" ? (
            <>
              <TextField label="Email" type="email" autoComplete="username" value={email} onChange={setEmail} required autoFocus />
              <TextField label="Password" type="password" autoComplete="current-password" value={password} onChange={setPassword} required />
            </>
          ) : null}
          {mode === "mfa" ? <TextField label="Code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={setCode} autoFocus required /> : null}
          {mode === "invite" ? (
            <>
              <TextField label="Your name (optional)" value={name} onChange={setName} autoComplete="name" />
              <TextField label="New password" type="password" autoComplete="new-password" value={password} onChange={setPassword} hint="At least 10 characters, mixing letters with numbers or symbols." required />
            </>
          ) : null}
          {mode === "no-access" ? (
            <Button variant="primary" block onClick={logout}>Sign out</Button>
          ) : (
            <Button type="submit" variant="primary" size="lg" block loading={busy}>
              {mode === "login" ? "Sign in" : mode === "mfa" ? "Verify" : "Activate account"}
            </Button>
          )}
          {mode === "mfa" ? <Button variant="ghost" onClick={logout}>Use a different account</Button> : null}
          {mode === "login" ? <a className="v-muted" href="/account/forgot" style={{ fontSize: 13 }}>Forgot your password?</a> : null}
        </div>
      </form>
    </div>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, errorMessage, get, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Badge, Button, Card, TextField, relTime, useToast } from "@/ui";
import { PageHead } from "../../AdminApp";

type Sess = { id: string; ip: string | null; user_agent: string | null; created_at: string; last_seen_at: string; current: boolean };

export default function Account() {
  const { me, refresh } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => get<{ items: Sess[] }>("/api/auth/sessions") });
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [err, setErr] = useState<string | null>(null);

  const start = useMutation({ mutationFn: () => post<{ secret: string; otpauth_uri: string }>("/api/auth/mfa/setup"), onSuccess: setSetup, onError: (e) => setErr(errorMessage(e)) });
  const enable = useMutation({ mutationFn: () => post<{ recovery_codes: string[] }>("/api/auth/mfa/enable", { code }), onSuccess: (r) => { setCodes(r.recovery_codes); setSetup(null); refresh(); }, onError: (e) => setErr(errorMessage(e)) });
  const revoke = useMutation({ mutationFn: (id: string) => del(`/api/auth/sessions/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }) });
  const change = useMutation({ mutationFn: () => post("/api/auth/password/change", { current_password: pw.current, new_password: pw.next }), onSuccess: () => { toast("Password changed. Other sessions were signed out."); setPw({ current: "", next: "" }); qc.invalidateQueries({ queryKey: ["sessions"] }); }, onError: (e) => toast(errorMessage(e), "danger") });

  return (
    <>
      <PageHead title="Your account" description={me?.user?.email} />
      <div className="v-stack" style={{ maxWidth: 760 }}>
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <Card title="Two-step verification" actions={me?.user?.mfa_enabled ? <Badge tone="success">on</Badge> : <Badge tone="warning">off</Badge>}>
          {codes ? (
            <div className="v-stack">
              <Alert tone="warning" title="Save your recovery codes now">Each code works once if you lose your phone. They won't be shown again.</Alert>
              <pre className="v-mono" style={{ columns: 2, background: "var(--v-surface-2)", padding: 16, borderRadius: 8 }}>{codes.join("\n")}</pre>
              <div><Button onClick={() => setCodes(null)}>I saved them</Button></div>
            </div>
          ) : me?.user?.mfa_enabled ? (
            <p>Your account asks for an authenticator code at every sign-in.</p>
          ) : setup ? (
            <div className="v-stack">
              <p>Add this key to an authenticator app (Google Authenticator, 1Password, Authy…), then enter the 6-digit code it shows.</p>
              <code className="v-mono" style={{ fontSize: 16, letterSpacing: ".12em", background: "var(--v-surface-2)", padding: "10px 14px", borderRadius: 8, wordBreak: "break-all" }}>{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
              <a href={setup.otpauth_uri} className="v-muted" style={{ fontSize: 13 }}>Open in an authenticator app on this device</a>
              <div className="v-row" style={{ alignItems: "flex-end" }}>
                <TextField label="Code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={setCode} />
                <Button variant="primary" loading={enable.isPending} disabled={code.length < 6} onClick={() => { setErr(null); enable.mutate(); }}>Turn on</Button>
              </div>
            </div>
          ) : (
            <div className="v-row v-row--between"><p>Protect store access with a code from your phone.</p><Button variant="primary" loading={start.isPending} onClick={() => start.mutate()}>Set up</Button></div>
          )}
        </Card>
        <Card title="Password">
          <form className="v-grid-2" onSubmit={(e) => { e.preventDefault(); change.mutate(); }}>
            <TextField label="Current password" type="password" autoComplete="current-password" value={pw.current} onChange={(v) => setPw({ ...pw, current: v })} />
            <TextField label="New password" type="password" autoComplete="new-password" value={pw.next} onChange={(v) => setPw({ ...pw, next: v })} />
            <div><Button type="submit" loading={change.isPending} disabled={!pw.current || !pw.next}>Change password</Button></div>
          </form>
        </Card>
        <Card title="Signed-in devices" flush>
          {sessions.data?.items.map((s) => (
            <div key={s.id} className="v-row v-row--between" style={{ padding: "12px 20px", borderBottom: "1px solid var(--v-line)" }}>
              <div>
                <div style={{ fontWeight: 550 }}>{(s.user_agent ?? "Unknown device").slice(0, 70)} {s.current ? <Badge tone="info" plain>this device</Badge> : null}</div>
                <div className="v-muted" style={{ fontSize: 13 }}>{s.ip ?? "unknown IP"} · active {relTime(s.last_seen_at)}</div>
              </div>
              {!s.current ? <Button size="sm" onClick={() => revoke.mutate(s.id)}>Sign out</Button> : null}
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}

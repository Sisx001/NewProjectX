import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History, Search } from "lucide-react";
import { errorMessage, get, patch, post, put } from "@/lib/api";
import { Alert, Badge, Button, Card, Drawer, Field, Input, Select, Switch, Textarea, TextField, dateTime, useToast } from "@/ui";
import { PageHead } from "../../AdminApp";

type SettingDef = { key: string; type: string; label: string; description: string; choices: string[] | null; min: number | null; max: number | null; default: unknown; value: unknown; customized: boolean; writable: boolean };
type Groups = { groups: { name: string; settings: SettingDef[] }[] };
type Revision = { id: string; changes: Record<string, { from: unknown; to: unknown }>; note: string | null; created_at: string };

/** Canonical settings: one definition per setting, rendered from the server registry. */
export default function Settings() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, isLoading, error } = useQuery({ queryKey: ["settings"], queryFn: () => get<Groups>("/api/settings") });
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [q, setQ] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const [history, setHistory] = useState(false);
  const dirty = Object.keys(draft).length > 0;

  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const save = useMutation({
    mutationFn: () => patch<{ changed: Record<string, unknown> }>("/api/settings", { changes: draft }),
    onSuccess: (r) => { toast(`Saved ${Object.keys(r.changed).length} change(s)`); setDraft({}); qc.invalidateQueries({ queryKey: ["settings"] }); },
    onError: (e) => toast(errorMessage(e), "danger"),
  });

  const groups = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (data?.groups ?? []).map((g) => ({ ...g, settings: g.settings.filter((s) => !t || s.label.toLowerCase().includes(t) || s.key.includes(t) || s.description.toLowerCase().includes(t)) })).filter((g) => g.settings.length);
  }, [data, q]);
  const shown = active ? groups.filter((g) => g.name === active) : groups;

  return (
    <>
      <PageHead title="Settings" description="Every store setting has exactly one home. Changes are versioned and can be reverted." actions={<Button onClick={() => setHistory(true)}><History size={16} /> History</Button>} />
      {error ? <Alert tone="danger">{errorMessage(error)}</Alert> : null}
      <div className="a-split" style={{ gridTemplateColumns: "220px minmax(0,1fr)" }}>
        <nav className="v-stack v-stack--sm" aria-label="Setting groups" style={{ position: "sticky", top: 72 }}>
          <div className="v-row" style={{ position: "relative" }}>
            <Search size={14} style={{ position: "absolute", left: 10, color: "var(--v-ink-3)" }} />
            <Input placeholder="Find a setting" value={q} onChange={(e) => setQ(e.target.value)} style={{ paddingLeft: 30 }} aria-label="Find a setting" />
          </div>
          <Button variant={active === null ? "primary" : "ghost"} size="sm" onClick={() => setActive(null)} style={{ justifyContent: "flex-start" }}>All</Button>
          {groups.map((g) => <Button key={g.name} variant={active === g.name ? "primary" : "ghost"} size="sm" style={{ justifyContent: "flex-start" }} onClick={() => setActive(g.name)}>{g.name}</Button>)}
        </nav>
        <div className="v-stack">
          {isLoading ? <Card>Loading…</Card> : null}
          {shown.map((g) => (
            <Card key={g.name} title={g.name}>
              <div className="v-stack">
                {g.settings.map((s) => <SettingRow key={s.key} def={s} value={s.key in draft ? draft[s.key] : s.value} onChange={(v) => setDraft((d) => { const n = { ...d }; if (JSON.stringify(v) === JSON.stringify(s.value)) delete n[s.key]; else n[s.key] = v; return n; })} />)}
                {g.name === "Email" ? <EmailTools /> : null}
              </div>
            </Card>
          ))}
        </div>
      </div>
      {dirty ? (
        <div style={{ position: "sticky", bottom: 16, zIndex: 25, display: "flex", justifyContent: "center", marginTop: 16 }}>
          <div className="v-row" style={{ background: "var(--v-ink)", color: "var(--v-bg)", padding: "10px 12px 10px 18px", borderRadius: 12, boxShadow: "var(--v-shadow-3)" }}>
            <span>{Object.keys(draft).length} unsaved change(s)</span>
            <Button size="sm" variant="ghost" style={{ color: "inherit" }} onClick={() => setDraft({})}>Discard</Button>
            <Button size="sm" variant="signal" loading={save.isPending} onClick={() => save.mutate()}>Save changes</Button>
          </div>
        </div>
      ) : null}
      {history ? <HistoryDrawer onClose={() => setHistory(false)} /> : null}
    </>
  );
}

function SettingRow({ def, value, onChange }: { def: SettingDef; value: unknown; onChange: (v: unknown) => void }) {
  const ro = !def.writable;
  const control = (id: string) => {
    switch (def.type) {
      case "bool":
        return <Switch checked={!!value} onChange={onChange} label={def.label} disabled={ro} />;
      case "enum":
        return <Select id={id} value={String(value)} disabled={ro} onChange={(e) => onChange(e.target.value)}>{def.choices?.map((c) => <option key={c} value={c}>{c}</option>)}</Select>;
      case "int":
      case "money":
        return <Input id={id} type="number" inputMode="numeric" value={String(value ?? "")} min={def.min ?? undefined} max={def.max ?? undefined} disabled={ro} onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))} />;
      case "text":
        return <Textarea id={id} value={String(value ?? "")} disabled={ro} onChange={(e) => onChange(e.target.value)} />;
      case "json":
        return <Textarea id={id} className="v-mono" value={JSON.stringify(value, null, 2)} disabled={ro} onChange={(e) => { try { onChange(JSON.parse(e.target.value)); } catch { /* keep typing */ } }} />;
      default:
        return <Input id={id} type={def.type === "email" ? "email" : def.type === "color" ? "color" : "text"} value={String(value ?? "")} disabled={ro} onChange={(e) => onChange(e.target.value)} />;
    }
  };
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0, 300px)", gap: 16, alignItems: "center" }}>
      <div>
        <div style={{ fontWeight: 550 }}>{def.label} {def.customized ? <Badge plain tone="info">changed</Badge> : null} {ro ? <Badge plain>read-only</Badge> : null}</div>
        {def.description ? <div className="v-muted" style={{ fontSize: 13 }}>{def.description}</div> : null}
        <div className="v-mono v-muted" style={{ fontSize: 11 }}>{def.key}</div>
      </div>
      <Field>{control}</Field>
    </div>
  );
}

function EmailTools() {
  const [pw, setPw] = useState("");
  const [to, setTo] = useState("");
  const toast = useToast();
  const savePw = useMutation({ mutationFn: () => put<{ hint: string }>("/api/messaging/smtp-password", { password: pw }), onSuccess: (r) => { toast(`SMTP password stored (${r.hint})`); setPw(""); }, onError: (e) => toast(errorMessage(e), "danger") });
  const test = useMutation({ mutationFn: () => post<{ status: string; error: string | null }>("/api/messaging/test-email", { to }), onSuccess: (r) => toast(r.status === "sent" ? "Test email sent" : `Not sent: ${r.error}`, r.status === "sent" ? "default" : "danger"), onError: (e) => toast(errorMessage(e), "danger") });
  return (
    <div className="v-grid-2" style={{ borderTop: "1px solid var(--v-line)", paddingTop: 16 }}>
      <div className="v-stack v-stack--sm">
        <TextField label="SMTP password" type="password" value={pw} onChange={setPw} autoComplete="new-password" hint="Stored encrypted; never shown again." />
        <div><Button size="sm" disabled={!pw} loading={savePw.isPending} onClick={() => savePw.mutate()}>Store password</Button></div>
      </div>
      <div className="v-stack v-stack--sm">
        <TextField label="Send a test email to" type="email" value={to} onChange={setTo} hint="Uses saved settings. The result is the real delivery outcome." />
        <div><Button size="sm" disabled={!to} loading={test.isPending} onClick={() => test.mutate()}>Send test</Button></div>
      </div>
    </div>
  );
}

function HistoryDrawer({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["settings-revisions"], queryFn: () => get<{ items: Revision[] }>("/api/settings/revisions") });
  const revert = useMutation({ mutationFn: (id: string) => post(`/api/settings/revisions/${id}/revert`), onSuccess: () => { toast("Reverted"); qc.invalidateQueries(); }, onError: (e) => toast(errorMessage(e), "danger") });
  return (
    <Drawer open onClose={onClose} title="Settings history" width={560}>
      <div className="v-stack">
        {data?.items.length === 0 ? <p className="v-muted">No changes yet.</p> : null}
        {data?.items.map((r) => (
          <div key={r.id} className="v-card" style={{ padding: 14 }}>
            <div className="v-row v-row--between"><strong>{dateTime(r.created_at)}</strong><Button size="sm" onClick={() => revert.mutate(r.id)}>Revert</Button></div>
            {r.note ? <p className="v-muted">{r.note}</p> : null}
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              {Object.entries(r.changes).map(([k, v]) => <li key={k}><span className="v-mono" style={{ fontSize: 12 }}>{k}</span>: {JSON.stringify(v.from)} → {JSON.stringify(v.to)}</li>)}
            </ul>
          </div>
        ))}
      </div>
    </Drawer>
  );
}

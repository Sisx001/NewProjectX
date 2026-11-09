import { NavLink, Route, Routes } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { RefreshCw, ShieldCheck, ShieldAlert } from "lucide-react";
import { errorMessage, get, post, qs } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Badge, Button, Card, Input, Select, StatusBadge, dateTime, relTime, useToast } from "@/ui";
import { DataTable } from "@/ui/DataTable";
import { PageHead } from "../../AdminApp";
import { SYSTEM_TABS } from "./systemTabs";

type Health = {
  version: string; env: string; payments_mode: string; external_network: boolean; uptime_seconds: number; python: string;
  database: { server: string; size_bytes: number; connections: number };
  worker: { running: boolean; last_tick: string | null };
  outbox: Record<string, number>; messages: Record<string, number>;
  jobs: { name: string; every_seconds: number; last_error: string | null; last_result: unknown }[];
  rate_limit_blocks: Record<string, number>;
};

export default function System() {
  const { can } = useAuth();
  const tabs = SYSTEM_TABS.filter((t) => !t.permission || can(t.permission));
  return (
    <>
      <PageHead title="System" description="Live health of this installation: database, background worker, queues, messages, logs and the audit trail." />
      <nav className="v-tabs" aria-label="System sections" style={{ marginBottom: 20 }}>
        {tabs.map((t) => <NavLink key={t.path} to={`/admin/system${t.path ? `/${t.path}` : ""}`} end className="v-tab" style={({ isActive }) => (isActive ? { color: "var(--v-ink)", borderColor: "var(--v-ink)" } : undefined)}>{t.label}</NavLink>)}
      </nav>
      <Routes>
        <Route index element={<HealthTab />} />
        <Route path="events" element={<EventsTab />} />
        <Route path="messages" element={<MessagesTab />} />
        <Route path="logs" element={<LogsTab />} />
        <Route path="audit" element={<AuditTab />} />
        {SYSTEM_TABS.filter((t) => t.element).map((t) => <Route key={t.path} path={t.path} element={t.element} />)}
      </Routes>
    </>
  );
}

function HealthTab() {
  const { data, error, refetch, isFetching } = useQuery({ queryKey: ["health"], queryFn: () => get<Health>("/api/system/health"), refetchInterval: 15_000 });
  if (error) return <Alert tone="danger">{errorMessage(error)}</Alert>;
  if (!data) return null;
  const mb = (b: number) => `${(b / 1024 / 1024).toFixed(1)} MB`;
  return (
    <div className="v-stack">
      <div className="v-row"><Button size="sm" onClick={() => refetch()} loading={isFetching}><RefreshCw size={14} /> Refresh</Button><span className="v-muted">Up {Math.floor(data.uptime_seconds / 3600)}h {Math.floor((data.uptime_seconds % 3600) / 60)}m · v{data.version} · {data.env}</span></div>
      <div className="v-grid-3">
        <Card title="Database"><dl className="v-kv"><dt>Server</dt><dd>{data.database.server}</dd><dt>Size</dt><dd className="v-num">{mb(data.database.size_bytes)}</dd><dt>Connections</dt><dd className="v-num">{data.database.connections}</dd></dl></Card>
        <Card title="Background worker"><dl className="v-kv"><dt>Status</dt><dd>{data.worker.running ? <Badge tone="success">running</Badge> : <Badge tone="danger">stopped</Badge>}</dd><dt>Last tick</dt><dd>{relTime(data.worker.last_tick)}</dd><dt>Events</dt><dd className="v-num">{Object.entries(data.outbox).map(([k, v]) => `${k}: ${v}`).join(" · ") || "none"}</dd></dl></Card>
        <Card title="Runtime"><dl className="v-kv"><dt>Payments</dt><dd>{data.payments_mode === "live" ? <Badge tone="danger">live</Badge> : <Badge tone="warning">sandbox</Badge>}</dd><dt>Provider network</dt><dd>{data.external_network ? "enabled" : "disabled"}</dd><dt>Python</dt><dd>{data.python}</dd></dl></Card>
      </div>
      <Card title="Scheduled jobs" flush>
        <DataTable label="Jobs" rows={data.jobs} rowKey={(j) => j.name} columns={[
          { key: "n", header: "Job", cell: (j) => <span className="v-mono">{j.name}</span> },
          { key: "e", header: "Every", cell: (j) => `${j.every_seconds}s` },
          { key: "s", header: "Status", cell: (j) => (j.last_error ? <Badge tone="danger">error</Badge> : <Badge tone="success">ok</Badge>) },
          { key: "r", header: "Last result", cell: (j) => <span className="v-mono v-muted" style={{ fontSize: 12 }}>{j.last_error ?? JSON.stringify(j.last_result)}</span> },
        ]} />
      </Card>
      <Card title="Messages"><p className="v-num">{Object.entries(data.messages).map(([k, v]) => `${k}: ${v}`).join(" · ") || "No messages yet"}</p></Card>
    </div>
  );
}

type Ev = { id: string; type: string; status: string; attempts: number; last_error: string | null; created_at: string; results: Record<string, { ok: boolean; error?: string }> };
function EventsTab() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const { data, isLoading } = useQuery({ queryKey: ["events", status, page], queryFn: () => get<{ items: Ev[]; total: number }>(`/api/system/events${qs({ status, page })}`) });
  const retry = useMutation({ mutationFn: (id: string) => post(`/api/system/events/${id}/retry`), onSuccess: () => { toast("Queued for retry"); qc.invalidateQueries({ queryKey: ["events"] }); } });
  return (
    <div className="v-stack">
      <div className="a-toolbar"><Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} style={{ width: 180 }}><option value="">All statuses</option>{["pending", "done", "failed", "dead"].map((s) => <option key={s}>{s}</option>)}</Select></div>
      <DataTable label="Domain events" rows={data?.items} loading={isLoading} total={data?.total} page={page} onPage={setPage} rowKey={(e) => e.id} columns={[
        { key: "t", header: "Event", cell: (e) => <span className="v-mono">{e.type}</span> },
        { key: "s", header: "Status", cell: (e) => <StatusBadge status={e.status} /> },
        { key: "a", header: "Attempts", cell: (e) => e.attempts, align: "right" },
        { key: "h", header: "Subscribers", cell: (e) => Object.entries(e.results).map(([k, v]) => <div key={k} style={{ fontSize: 12 }}>{v.ok ? "✓" : "✗"} <span className="v-mono">{k.split(".").slice(-1)[0]}</span>{v.error ? <span className="v-muted"> — {v.error}</span> : null}</div>) },
        { key: "c", header: "Created", cell: (e) => relTime(e.created_at) },
        { key: "r", header: "", cell: (e) => (can("system.manage") && (e.status === "failed" || e.status === "dead") ? <Button size="sm" onClick={() => retry.mutate(e.id)}>Retry</Button> : null) },
      ]} />
    </div>
  );
}

type Msg = { id: string; channel: string; to: string; subject: string; status: string; attempts: number; error: string | null; created_at: string; body: string };
function MessagesTab() {
  const [page, setPage] = useState(1);
  const qc = useQueryClient();
  const { can } = useAuth();
  const { data, isLoading } = useQuery({ queryKey: ["messages", page], queryFn: () => get<{ items: Msg[]; total: number }>(`/api/messaging/messages${qs({ page })}`) });
  const retry = useMutation({ mutationFn: (id: string) => post(`/api/messaging/messages/${id}/retry`), onSuccess: () => qc.invalidateQueries({ queryKey: ["messages"] }) });
  return (
    <div className="v-stack">
      <Alert tone="info">“Held” means no transport is configured for that channel. Held messages are retried automatically once one is set up — they are never reported as sent.</Alert>
      <DataTable label="Outbound messages" rows={data?.items} loading={isLoading} total={data?.total} page={page} onPage={setPage} rowKey={(m) => m.id} columns={[
        { key: "c", header: "Channel", cell: (m) => m.channel },
        { key: "to", header: "To", cell: (m) => m.to },
        { key: "s", header: "Subject", cell: (m) => m.subject },
        { key: "st", header: "Status", cell: (m) => <span title={m.error ?? ""}><StatusBadge status={m.status} /></span> },
        { key: "e", header: "Detail", cell: (m) => <span className="v-muted" style={{ fontSize: 12 }}>{m.error}</span>, hideBelow: 1000 },
        { key: "t", header: "Created", cell: (m) => relTime(m.created_at) },
        { key: "r", header: "", cell: (m) => (can("system.manage") && m.status !== "sent" ? <Button size="sm" onClick={() => retry.mutate(m.id)}>Retry now</Button> : null) },
      ]} />
    </div>
  );
}

type Log = { id: number; level: string; logger: string; message: string; request_id: string | null; created_at: string };
function LogsTab() {
  const [level, setLevel] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const { data, isLoading } = useQuery({ queryKey: ["logs", level, q, page], queryFn: () => get<{ items: Log[]; total: number }>(`/api/system/logs${qs({ level, q, page })}`) });
  return (
    <div className="v-stack">
      <div className="a-toolbar">
        <Select aria-label="Level" value={level} onChange={(e) => setLevel(e.target.value)} style={{ width: 140 }}><option value="">All levels</option><option>WARNING</option><option>ERROR</option><option>INFO</option></Select>
        <Input aria-label="Search logs" placeholder="Search message…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 320 }} />
      </div>
      <p className="v-muted" style={{ fontSize: 13 }}>Warnings and errors only. Passwords, tokens and keys are redacted before storage.</p>
      <DataTable label="Logs" rows={data?.items} loading={isLoading} total={data?.total} page={page} pageSize={100} onPage={setPage} rowKey={(l) => String(l.id)} columns={[
        { key: "t", header: "Time", cell: (l) => dateTime(l.created_at, { timeStyle: "medium", dateStyle: "short" }), width: 170 },
        { key: "l", header: "Level", cell: (l) => <Badge tone={l.level === "ERROR" ? "danger" : l.level === "WARNING" ? "warning" : "neutral"}>{l.level}</Badge>, width: 110 },
        { key: "m", header: "Message", cell: (l) => <span className="v-mono" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{l.message.slice(0, 400)}</span> },
        { key: "r", header: "Request", cell: (l) => <span className="v-mono v-muted" style={{ fontSize: 11 }}>{l.request_id}</span>, hideBelow: 1100 },
      ]} />
    </div>
  );
}

type Audit = { seq: number; action: string; actor_kind: string; actor_label: string | null; target_type: string | null; target_id: string | null; data: Record<string, unknown>; ip: string | null; created_at: string };
function AuditTab() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const { data, isLoading } = useQuery({ queryKey: ["audit", action, page], queryFn: () => get<{ items: Audit[]; total: number }>(`/api/audit${qs({ action, page })}`) });
  const verify = useMutation({ mutationFn: () => get<{ ok: boolean; checked: number; broken_at?: number }>("/api/audit/verify") });
  return (
    <div className="v-stack">
      <div className="a-toolbar">
        <Input aria-label="Filter by action" placeholder="Action starts with… (e.g. orders.)" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} style={{ maxWidth: 320 }} />
        <Button onClick={() => verify.mutate()} loading={verify.isPending}>Verify integrity</Button>
        {verify.data ? verify.data.ok ? <Badge tone="success"><ShieldCheck size={12} /> chain intact · {verify.data.checked} events</Badge> : <Badge tone="danger"><ShieldAlert size={12} /> chain broken at #{verify.data.broken_at}</Badge> : null}
      </div>
      <DataTable label="Audit trail" rows={data?.items} loading={isLoading} total={data?.total} page={page} onPage={setPage} rowKey={(a) => String(a.seq)} columns={[
        { key: "t", header: "When", cell: (a) => dateTime(a.created_at), width: 170 },
        { key: "a", header: "Action", cell: (a) => <span className="v-mono">{a.action}</span> },
        { key: "w", header: "Who", cell: (a) => <span>{a.actor_label ?? a.actor_kind}<span className="v-muted"> · {a.actor_kind}</span></span> },
        { key: "o", header: "Target", cell: (a) => (a.target_type ? <span className="v-mono" style={{ fontSize: 12 }}>{a.target_type}:{a.target_id?.slice(0, 12)}</span> : "—"), hideBelow: 900 },
        { key: "d", header: "Details", cell: (a) => <span className="v-mono v-muted" style={{ fontSize: 11 }}>{JSON.stringify(a.data).slice(0, 120)}</span>, hideBelow: 1100 },
      ]} />
    </div>
  );
}

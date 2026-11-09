import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseBackup, Download, ShieldCheck, Trash2 } from "lucide-react";
import { del, errorMessage, get, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Badge, Button, Card, Confirm, Drawer, Empty, Skeleton, Switch, dateTime, relTime, useToast } from "@/ui";
import { DataTable } from "@/ui/DataTable";

type Step = { name: string; ok: boolean; detail: string };
type Drill = { ok?: boolean; status?: string; steps?: Step[]; duration_seconds?: number; total_rows?: number };
type Backup = {
  id: string; status: "running" | "succeeded" | "failed"; size_bytes: number | null; sha256: string | null; encrypted: boolean;
  file: string | null; error: string | null; created_at: string; finished_at: string | null; restore_verified_at: string | null; drill: Drill | null;
};
type Data = {
  items: Backup[];
  prerequisites: { pg_dump: boolean; pg_restore: boolean; restore_drill_ready: boolean };
  last_success_at: string | null; last_verified_at: string | null;
  schedule: { enabled: boolean; interval_hours: number; encrypt: boolean; retention_count: number; retention_days: number; verify_after_create: boolean };
};

const mb = (b: number | null) => (b == null ? "—" : b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

export default function Backups() {
  const { can } = useAuth();
  const manage = can("system.manage");
  const qc = useQueryClient();
  const toast = useToast();
  const [encrypt, setEncrypt] = useState(true);
  const [detail, setDetail] = useState<Backup | null>(null);
  const [removing, setRemoving] = useState<Backup | null>(null);

  const { data, error } = useQuery({
    queryKey: ["backups"],
    queryFn: () => get<Data>("/api/system/backups"),
    refetchInterval: (q) => (q.state.data?.items.some((b) => b.status === "running" || b.drill?.status === "running") ? 2000 : 30_000),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["backups"] });
  const create = useMutation({ mutationFn: () => post("/api/system/backups", { encrypt }), onSuccess: () => { toast("Backup started"); refresh(); }, onError: (e) => toast(errorMessage(e), "danger") });
  const verify = useMutation({ mutationFn: (id: string) => post(`/api/system/backups/${id}/verify-restore`), onSuccess: () => { toast("Restore drill started"); refresh(); }, onError: (e) => toast(errorMessage(e), "danger") });
  const remove = useMutation({ mutationFn: (id: string) => del(`/api/system/backups/${id}`), onSuccess: () => { toast("Backup deleted"); setRemoving(null); refresh(); }, onError: (e) => toast(errorMessage(e), "danger") });

  if (error) return <Alert tone="danger">{errorMessage(error)}</Alert>;
  if (!data) return <div className="v-stack"><Skeleton h={90} /><Skeleton h={220} /></div>;
  const pre = data.prerequisites;
  const stale = !data.last_success_at || Date.now() - new Date(data.last_success_at).getTime() > 3 * 86_400_000;

  return (
    <div className="v-stack">
      {!pre.pg_dump || !pre.pg_restore ? (
        <Alert tone="danger" title="Backups cannot run on this server">
          PostgreSQL client tools are missing ({[!pre.pg_dump && "pg_dump", !pre.pg_restore && "pg_restore"].filter(Boolean).join(", ")}). Install <span className="v-mono">postgresql-client-16</span>; the Docker image includes them.
        </Alert>
      ) : stale ? (
        <Alert tone="warning" title={data.last_success_at ? "No backup in the last 3 days" : "No backup has been made yet"}>
          Create one now, and enable scheduled backups in <Link to="/admin/settings">Settings → Backups</Link>. Keep a copy off this server.
        </Alert>
      ) : null}

      <div className="v-grid-3">
        <Card title="Last successful backup"><p className="v-num">{data.last_success_at ? relTime(data.last_success_at) : "never"}</p></Card>
        <Card title="Last verified by restore drill">
          <p className="v-num">{data.last_verified_at ? relTime(data.last_verified_at) : "never"}</p>
          <p className="v-muted" style={{ fontSize: 13 }}>An unverified backup is a hope, not a backup.</p>
        </Card>
        <Card title="Schedule" actions={<Link to="/admin/settings">Change</Link>}>
          <dl className="v-kv">
            <dt>Automatic</dt><dd>{data.schedule.enabled ? <Badge tone="success">every {data.schedule.interval_hours}h</Badge> : <Badge>off</Badge>}</dd>
            <dt>Encryption</dt><dd>{data.schedule.encrypt ? "AES-256-GCM" : "none"}</dd>
            <dt>Retention</dt><dd>{data.schedule.retention_count} backups / {data.schedule.retention_days} days</dd>
          </dl>
        </Card>
      </div>

      <Card title="Backups" actions={manage ? (
        <div className="v-row">
          <Switch checked={encrypt} onChange={setEncrypt} label="Encrypt" />
          <Button variant="primary" size="sm" onClick={() => create.mutate()} loading={create.isPending} disabled={!pre.pg_dump}><DatabaseBackup size={14} /> Back up now</Button>
        </div>
      ) : null} flush>
        {data.items.length === 0 ? (
          <Empty icon={<DatabaseBackup />} title="No backups yet">A backup contains the database (orders, customers, settings, audit trail) and uploaded media. It is created with pg_dump and checked by restoring it into a scratch database.</Empty>
        ) : (
          <DataTable label="Backups" rows={data.items} rowKey={(b) => b.id} onRowClick={(b) => setDetail(b)} columns={[
            { key: "t", header: "Created", cell: (b) => dateTime(b.created_at) },
            { key: "s", header: "Status", cell: (b) => (b.status === "running" ? <Badge tone="info">running…</Badge> : b.status === "failed" ? <Badge tone="danger">failed</Badge> : <Badge tone="success">succeeded</Badge>) },
            { key: "z", header: "Size", cell: (b) => <span className="v-num">{mb(b.size_bytes)}</span> },
            { key: "e", header: "Encrypted", cell: (b) => (b.encrypted ? "yes" : "no") },
            { key: "v", header: "Restore drill", cell: (b) => (b.drill?.status === "running" ? <Badge tone="info">running…</Badge> : b.restore_verified_at ? <Badge tone="success">verified {relTime(b.restore_verified_at)}</Badge> : b.drill?.ok === false ? <Badge tone="danger">failed</Badge> : <Badge>not verified</Badge>) },
          ]} />
        )}
      </Card>

      <Drawer open={!!detail} onClose={() => setDetail(null)} title="Backup details" footer={detail && manage ? (
        <div className="v-row" style={{ justifyContent: "space-between", width: "100%" }}>
          <Button variant="danger" size="sm" onClick={() => setRemoving(detail)} disabled={detail.status === "running"}><Trash2 size={14} /> Delete</Button>
          <div className="v-row">
            {detail.status === "succeeded" ? <a className="v-btn v-btn--sm" href={`/api/system/backups/${detail.id}/download`} download><Download size={14} /> Download</a> : null}
            <Button variant="primary" size="sm" disabled={detail.status !== "succeeded" || !pre.restore_drill_ready} loading={verify.isPending} onClick={() => verify.mutate(detail.id)}><ShieldCheck size={14} /> Verify by restoring</Button>
          </div>
        </div>
      ) : null}>
        {detail ? (
          <div className="v-stack">
            {detail.error ? <Alert tone="danger" title="Backup failed">{detail.error}</Alert> : null}
            <dl className="v-kv">
              <dt>File</dt><dd className="v-mono">{detail.file ?? "—"}</dd>
              <dt>SHA-256</dt><dd className="v-mono" style={{ wordBreak: "break-all" }}>{detail.sha256 ?? "—"}</dd>
              <dt>Created</dt><dd>{dateTime(detail.created_at)}</dd>
              <dt>Size</dt><dd className="v-num">{mb(detail.size_bytes)}</dd>
            </dl>
            {detail.drill?.steps ? (
              <Card title={detail.drill.ok ? "Restore drill passed" : "Restore drill failed"}>
                <ul className="v-stack v-stack--sm" style={{ listStyle: "none", margin: 0, padding: 0 }}>
                  {detail.drill.steps.map((s) => (
                    <li key={s.name} className="v-row" style={{ alignItems: "flex-start" }}>
                      <Badge tone={s.ok ? "success" : "danger"}>{s.ok ? "pass" : "fail"}</Badge>
                      <span><span className="v-mono">{s.name}</span> <span className="v-muted">{s.detail}</span></span>
                    </li>
                  ))}
                </ul>
                {detail.drill.duration_seconds != null ? <p className="v-muted" style={{ fontSize: 13 }}>Took {detail.drill.duration_seconds}s{detail.drill.total_rows != null ? ` · ${detail.drill.total_rows} rows restored` : ""}</p> : null}
              </Card>
            ) : <p className="v-muted">This backup has not been verified by a restore drill yet.</p>}
            {detail.encrypted ? <Alert tone="info">Encrypted with a key derived from the server's vault key. Without that key the file cannot be restored; store it separately.</Alert> : <Alert tone="warning">Not encrypted: the file contains customer data and password hashes. Handle and store it accordingly.</Alert>}
          </div>
        ) : null}
      </Drawer>

      <Confirm open={!!removing} onClose={() => setRemoving(null)} onConfirm={() => removing && remove.mutate(removing.id)} title="Delete this backup?" confirmLabel="Delete backup" danger busy={remove.isPending}>
        The file is removed from this server permanently. Make sure another copy exists if you need it.
      </Confirm>
    </div>
  );
}

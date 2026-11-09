import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Copy, UserMinus } from "lucide-react";
import { del, errorMessage, get, patch, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Badge, Button, Card, Checkbox, Confirm, Dialog, Drawer, Field, Select, Tabs, TextField, relTime, useToast } from "@/ui";
import { DataTable } from "@/ui/DataTable";
import { PageHead } from "../../AdminApp";

type Role = { id: string; key: string; name: string; description: string; permissions: string[]; builtin: boolean; members: number };
type Member = { id: string; email: string; name: string; status: string; mfa_enabled: boolean; last_login_at: string | null; role: Role; expires_at: string | null; expired: boolean };
type PermGroups = { groups: Record<string, { key: string; description: string }[]> };

export default function Team() {
  const [tab, setTab] = useState<"members" | "roles">("members");
  const [params, setParams] = useSearchParams();
  const { can, me } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const members = useQuery({ queryKey: ["members"], queryFn: () => get<{ items: Member[] }>("/api/staff/members") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => get<{ items: Role[] }>("/api/staff/roles") });
  const [editing, setEditing] = useState<Member | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);
  const [roleEdit, setRoleEdit] = useState<Role | "new" | null>(null);
  const inviteOpen = params.get("invite") === "1";

  const remove = useMutation({
    mutationFn: (m: Member) => del(`/api/staff/members/${m.id}`),
    onSuccess: () => { toast("Access removed and sessions signed out"); qc.invalidateQueries({ queryKey: ["members"] }); setRemoving(null); },
    onError: (e) => toast(errorMessage(e), "danger"),
  });

  return (
    <>
      <PageHead
        title="Team & roles"
        description="Who can do what. Every grant and change is written to the audit trail."
        actions={can("staff.manage") ? <Button variant="primary" onClick={() => setParams({ invite: "1" })}><Plus size={16} /> Invite</Button> : null}
      />
      <Tabs label="Team sections" value={tab} onChange={setTab} items={[{ value: "members", label: "Members", count: members.data?.items.length }, { value: "roles", label: "Roles", count: roles.data?.items.length }]} />
      <div style={{ height: 16 }} />
      {tab === "members" ? (
        <DataTable
          label="Staff members"
          rows={members.data?.items}
          loading={members.isLoading}
          error={members.error ? errorMessage(members.error) : null}
          rowKey={(m) => m.id}
          onRowClick={can("staff.manage") ? setEditing : undefined}
          columns={[
            { key: "name", header: "Name", cell: (m) => <div><div style={{ fontWeight: 550 }}>{m.name || m.email}{m.id === me?.user?.id ? <span className="v-muted"> (you)</span> : null}</div><div className="v-muted" style={{ fontSize: 12 }}>{m.email}</div></div> },
            { key: "role", header: "Role", cell: (m) => m.role.name },
            { key: "status", header: "Status", cell: (m) => m.expired ? <Badge tone="danger">access expired</Badge> : m.status === "invited" ? <Badge tone="warning">invited</Badge> : <Badge tone="success">active</Badge> },
            { key: "mfa", header: "2-step", cell: (m) => m.mfa_enabled ? <Badge tone="success" plain>on</Badge> : <Badge tone="warning" plain>off</Badge>, hideBelow: 700 },
            { key: "expires", header: "Access until", cell: (m) => m.expires_at ? new Date(m.expires_at).toLocaleDateString() : <span className="v-muted">No limit</span>, hideBelow: 900 },
            { key: "seen", header: "Last sign-in", cell: (m) => relTime(m.last_login_at), hideBelow: 900 },
          ]}
        />
      ) : (
        <div className="v-stack">
          {can("staff.manage") ? <div><Button onClick={() => setRoleEdit("new")}><Plus size={16} /> Custom role</Button></div> : null}
          <div className="v-grid-3">
            {roles.data?.items.map((r) => (
              <Card key={r.id} title={<span className="v-row">{r.name}{r.builtin ? <Badge plain>built-in</Badge> : null}</span>} actions={!r.builtin && can("staff.manage") ? <Button size="sm" onClick={() => setRoleEdit(r)}>Edit</Button> : <Button size="sm" variant="ghost" onClick={() => setRoleEdit({ ...r, id: "", name: `${r.name} (copy)`, builtin: false })}>Duplicate</Button>}>
                <p className="v-muted">{r.description}</p>
                <p style={{ marginTop: 8, fontSize: 13 }}>
                  <span className="v-num">{r.permissions.includes("*") ? "All" : r.permissions.length}</span> permissions · <span className="v-num">{r.members}</span> member{r.members === 1 ? "" : "s"}
                </p>
              </Card>
            ))}
          </div>
        </div>
      )}

      <InviteDialog open={inviteOpen} roles={roles.data?.items ?? []} onClose={() => setParams({})} />
      {editing ? <MemberDrawer member={editing} roles={roles.data?.items ?? []} onClose={() => setEditing(null)} onRemove={() => { setRemoving(editing); setEditing(null); }} /> : null}
      {roleEdit ? <RoleDrawer role={roleEdit === "new" ? null : roleEdit} onClose={() => setRoleEdit(null)} /> : null}
      <Confirm open={!!removing} onClose={() => setRemoving(null)} danger title={`Remove ${removing?.name || removing?.email}?`} confirmLabel="Remove access" busy={remove.isPending} onConfirm={() => removing && remove.mutate(removing)}>
        They will be signed out everywhere immediately. Their past actions stay in the audit trail.
      </Confirm>
    </>
  );
}

function InviteDialog({ open, roles, onClose }: { open: boolean; roles: Role[]; onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [roleId, setRoleId] = useState("");
  const [until, setUntil] = useState("");
  const [link, setLink] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => post<{ invite_link: string }>("/api/staff/invite", { email, name, role_id: roleId || roles.find((r) => r.key === "store_manager")?.id, expires_at: until ? new Date(until).toISOString() : null }),
    onSuccess: (r) => { setLink(r.invite_link); qc.invalidateQueries({ queryKey: ["members"] }); },
    onError: (e) => setErr(errorMessage(e)),
  });
  const close = () => { setLink(null); setEmail(""); setName(""); setErr(null); onClose(); };
  return (
    <Dialog open={open} onClose={close} title={link ? "Invitation created" : "Invite a staff member"} description={link ? undefined : "They'll choose their own password from the invitation link."}
      footer={link ? <Button variant="primary" onClick={close}>Done</Button> : <><Button onClick={close}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!email || !name} onClick={() => { setErr(null); m.mutate(); }}>Send invitation</Button></>}>
      {link ? (
        <div className="v-stack">
          <Alert tone="info">An email was queued. If email delivery isn't configured yet, share this one-time link privately — it expires in 72 hours.</Alert>
          <div className="v-row"><code className="v-mono" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{link}</code><Button size="sm" onClick={() => navigator.clipboard.writeText(link)}><Copy size={14} /> Copy</Button></div>
        </div>
      ) : (
        <div className="v-stack">
          {err ? <Alert tone="danger">{err}</Alert> : null}
          <TextField label="Name" value={name} onChange={setName} required data-autofocus />
          <TextField label="Work email" type="email" value={email} onChange={setEmail} required />
          <Field label="Role">{(id) => <Select id={id} value={roleId} onChange={(e) => setRoleId(e.target.value)}>{roles.map((r) => <option key={r.id} value={r.id}>{r.name} — {r.description}</option>)}</Select>}</Field>
          <TextField label="Access until (optional)" type="date" value={until} onChange={setUntil} hint="Use for temporary staff or contractors. Access stops automatically." />
        </div>
      )}
    </Dialog>
  );
}

function MemberDrawer({ member, roles, onClose, onRemove }: { member: Member; roles: Role[]; onClose: () => void; onRemove: () => void }) {
  const [roleId, setRoleId] = useState(member.role.id);
  const [until, setUntil] = useState(member.expires_at?.slice(0, 10) ?? "");
  const qc = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: () => patch(`/api/staff/members/${member.id}`, { role_id: roleId, expires_at: until ? new Date(until).toISOString() : null, clear_expiry: !until }),
    onSuccess: () => { toast("Access updated"); qc.invalidateQueries({ queryKey: ["members"] }); onClose(); },
    onError: (e) => toast(errorMessage(e), "danger"),
  });
  return (
    <Drawer open onClose={onClose} title={member.name || member.email} footer={<><Button variant="ghost" onClick={onRemove}><UserMinus size={16} /> Remove</Button><span style={{ flex: 1 }} /><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save</Button></>}>
      <div className="v-stack">
        <dl className="v-kv"><dt>Email</dt><dd>{member.email}</dd><dt>Two-step</dt><dd>{member.mfa_enabled ? "On" : "Off"}</dd><dt>Last sign-in</dt><dd>{relTime(member.last_login_at)}</dd></dl>
        <Field label="Role">{(id) => <Select id={id} value={roleId} onChange={(e) => setRoleId(e.target.value)}>{roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</Select>}</Field>
        <TextField label="Access until" type="date" value={until} onChange={setUntil} hint="Leave empty for no time limit." />
      </div>
    </Drawer>
  );
}

function RoleDrawer({ role, onClose }: { role: Role | null; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ["perm-catalog"], queryFn: () => get<PermGroups>("/api/staff/permissions") });
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [perms, setPerms] = useState<Set<string>>(new Set(role?.permissions.filter((p) => p !== "*") ?? []));
  const [err, setErr] = useState<string | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const isEdit = !!role?.id;
  const save = useMutation({
    mutationFn: () => (isEdit ? patch(`/api/staff/roles/${role!.id}`, { name, description, permissions: [...perms] }) : post("/api/staff/roles", { name, description, permissions: [...perms] })),
    onSuccess: () => { toast("Role saved"); qc.invalidateQueries({ queryKey: ["roles"] }); onClose(); },
    onError: (e) => setErr(errorMessage(e)),
  });
  const del_ = useMutation({ mutationFn: () => del(`/api/staff/roles/${role!.id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["roles"] }); onClose(); }, onError: (e) => setErr(errorMessage(e)) });
  const groups = useMemo(() => Object.entries(data?.groups ?? {}), [data]);
  return (
    <Drawer open onClose={onClose} title={isEdit ? `Edit ${role!.name}` : "New role"} width={600}
      footer={<>{isEdit ? <Button variant="ghost" onClick={() => del_.mutate()}>Delete role</Button> : null}<span style={{ flex: 1 }} /><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={name.trim().length < 2} onClick={() => save.mutate()}>Save role</Button></>}>
      <div className="v-stack">
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <TextField label="Role name" value={name} onChange={setName} />
        <TextField label="Description" value={description} onChange={setDescription} />
        {groups.map(([g, list]) => (
          <fieldset key={g} style={{ border: "1px solid var(--v-line)", borderRadius: "var(--v-radius)", padding: "8px 14px 12px" }}>
            <legend style={{ fontWeight: 600, textTransform: "capitalize", padding: "0 6px" }}>{g}</legend>
            <div className="v-stack v-stack--sm">
              {list.map((p) => (
                <Checkbox key={p.key} checked={perms.has(p.key)} onChange={(v) => { const n = new Set(perms); if (v) n.add(p.key); else n.delete(p.key); setPerms(n); }} label={<span><span className="v-mono" style={{ fontSize: 12 }}>{p.key}</span> <span className="v-muted">— {p.description}</span></span>} />
              ))}
            </div>
          </fieldset>
        ))}
      </div>
    </Drawer>
  );
}

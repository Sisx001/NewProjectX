import { Suspense, lazy, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, LogOut, Menu, Moon, Search, Sun, MoreHorizontal, ShieldCheck } from "lucide-react";
import { useAuth, usePref } from "@/lib/auth";
import { get, post } from "@/lib/api";
import { Button, Drawer, Kbd, Skeleton, relTime } from "@/ui";
import { COMMANDS, NAV, NAV_GROUPS, ROUTES, type NavItem } from "./registry";
import { StaffLogin } from "./StaffLogin";
import "./admin.css";

export function Mark({ size = 26 }: { size?: number }) {
  return (
    <span className="a-brand__mark" style={{ width: size, height: size }} aria-hidden>
      <svg width={size * 0.62} height={size * 0.62} viewBox="0 0 32 32"><path d="M7 7l9 18 9-18" stroke="var(--v-signal)" strokeWidth="4" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </span>
  );
}

export function PageHead({ title, description, actions, crumbs }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; crumbs?: { label: string; to: string }[] }) {
  useEffect(() => {
    if (typeof title === "string") document.title = `${title} · VOLTORA`;
  }, [title]);
  return (
    <header className="a-page-head">
      <div>
        {crumbs?.length ? (
          <nav className="a-crumbs" aria-label="Breadcrumb">
            {crumbs.map((c) => (
              <span key={c.to} className="v-row" style={{ gap: 6 }}>
                <Link to={c.to}>{c.label}</Link>
                <span aria-hidden>/</span>
              </span>
            ))}
          </nav>
        ) : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="v-row v-row--wrap">{actions}</div> : null}
    </header>
  );
}

function useVisibleNav(): NavItem[] {
  const { can } = useAuth();
  return NAV.filter((n) => !n.permission || can(n.permission));
}

function SideNav({ onNavigate }: { onNavigate?: () => void }) {
  const items = useVisibleNav();
  const { me } = useAuth();
  const { data: counts } = useQuery({ queryKey: ["nav-counts"], queryFn: () => get<Record<string, number>>("/api/admin/nav-counts").catch(() => ({})), refetchInterval: 60_000 });
  return (
    <nav className="a-nav" aria-label="Admin">
      {NAV_GROUPS.map((g) => {
        const list = items.filter((i) => i.group === g);
        if (!list.length) return null;
        return (
          <div className="a-nav__group" key={g}>
            {g !== "Overview" ? <div className="a-nav__label">{g}</div> : null}
            {list.map((i) => {
              const Icon = i.icon;
              const c = counts?.[i.path];
              return (
                <NavLink key={i.path} to={`/admin/${i.path}`.replace(/\/$/, "")} end={i.path === ""} onClick={onNavigate}>
                  <Icon size={16} strokeWidth={1.8} />
                  {i.label}
                  {c ? <span className="a-nav__count v-num">{c > 99 ? "99+" : c}</span> : null}
                </NavLink>
              );
            })}
          </div>
        );
      })}
      {me?.store.environment !== "production" ? null : null}
    </nav>
  );
}

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const nav = useNavigate();
  const { can } = useAuth();
  const navItems = useVisibleNav();
  const { data: hits } = useQuery({
    queryKey: ["search", q],
    queryFn: () => get<{ items: { label: string; sublabel?: string; path: string; kind: string }[] }>(`/api/admin/search?q=${encodeURIComponent(q)}`).catch(() => ({ items: [] })),
    enabled: open && q.trim().length >= 2,
  });
  const entries = useMemo(() => {
    const t = q.trim().toLowerCase();
    const match = (s: string, kw: string[] = []) => !t || s.toLowerCase().includes(t) || kw.some((k) => k.includes(t));
    const pages = navItems.filter((n) => match(n.label, n.keywords)).map((n) => ({ group: "Go to", label: n.label, run: () => nav(`/admin/${n.path}`) }));
    const cmds = COMMANDS.filter((c) => (!c.permission || can(c.permission)) && match(c.label, c.keywords)).map((c) => ({ group: c.group ?? "Actions", label: c.label, run: () => (c.path ? nav(`/admin/${c.path}`) : c.run?.()) }));
    const records = (hits?.items ?? []).map((h) => ({ group: h.kind, label: h.sublabel ? `${h.label} — ${h.sublabel}` : h.label, run: () => nav(h.path) }));
    return [...records, ...pages, ...cmds].slice(0, 40);
  }, [q, navItems, hits, can, nav]);
  useEffect(() => {
    if (open) {
      setQ("");
      setIdx(0);
    }
  }, [open]);
  if (!open) return null;
  let lastGroup = "";
  return (
    <>
      <div className="v-overlay" onClick={onClose} />
      <div className="a-cmd" role="dialog" aria-modal="true" aria-label="Command palette">
        <input
          autoFocus
          placeholder="Search orders, products, customers, settings…"
          value={q}
          role="combobox"
          aria-expanded="true"
          aria-controls="cmd-list"
          aria-activedescendant={`cmd-${idx}`}
          onChange={(e) => {
            setQ(e.target.value);
            setIdx(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(entries.length - 1, i + 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
            if (e.key === "Enter" && entries[idx]) { entries[idx]!.run(); onClose(); }
          }}
        />
        <ul id="cmd-list" role="listbox">
          {entries.length === 0 ? <li className="v-muted">No matches</li> : null}
          {entries.map((e, i) => {
            const header = e.group !== lastGroup ? (lastGroup = e.group) : null;
            return (
              <div key={`${e.group}-${e.label}-${i}`}>
                {header ? <div className="a-cmd__group">{header}</div> : null}
                <li id={`cmd-${i}`} role="option" aria-selected={i === idx} onMouseEnter={() => setIdx(i)} onClick={() => { e.run(); onClose(); }}>
                  {e.label}
                </li>
              </div>
            );
          })}
        </ul>
      </div>
    </>
  );
}

type Notif = { id: string; title: string; body: string | null; link: string | null; severity: string; read: boolean; created_at: string };
function Notifications() {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["notifications"], queryFn: () => get<{ items: Notif[]; unread: number }>("/api/notifications"), refetchInterval: 45_000 });
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <Button variant="ghost" icon aria-label={`Notifications${data?.unread ? `, ${data.unread} unread` : ""}`} aria-expanded={open} onClick={() => setOpen(!open)}>
        <Bell size={18} />
        {data?.unread ? <span style={{ position: "absolute", top: 6, right: 6, width: 8, height: 8, borderRadius: 9, background: "var(--v-danger)" }} /> : null}
      </Button>
      {open ? (
        <div className="a-pop">
          <div className="v-row v-row--between" style={{ padding: "12px 14px", borderBottom: "1px solid var(--v-line)" }}>
            <strong>Notifications</strong>
            <Button size="sm" variant="ghost" onClick={async () => { await post("/api/notifications/read"); qc.invalidateQueries({ queryKey: ["notifications"] }); }}>Mark all read</Button>
          </div>
          <div style={{ maxHeight: 420, overflow: "auto" }}>
            {data?.items.length ? data.items.map((n) => (
              <Link key={n.id} to={n.link ?? "#"} onClick={() => setOpen(false)} style={{ display: "block", padding: "10px 14px", borderBottom: "1px solid var(--v-line)", textDecoration: "none", background: n.read ? undefined : "var(--v-surface-2)" }}>
                <div style={{ fontWeight: 550 }}>{n.title}</div>
                {n.body ? <div className="v-muted" style={{ fontSize: 13 }}>{n.body}</div> : null}
                <div className="v-muted" style={{ fontSize: 12, marginTop: 2 }}>{relTime(n.created_at)}</div>
              </Link>
            )) : <div className="v-empty">You're all caught up.</div>}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  const [cmd, setCmd] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [mode, setMode] = usePref<"light" | "dark">("mode", "light");
  const [density] = usePref<"comfortable" | "compact">("density", "comfortable");
  const loc = useLocation();
  const visible = useVisibleNav();
  useEffect(() => {
    document.documentElement.dataset.mode = mode;
    document.documentElement.dataset.density = density;
    return () => { delete document.documentElement.dataset.mode; };
  }, [mode, density]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmd((v) => !v); }
      if (e.key === "/" && !(e.target as HTMLElement).closest("input,textarea,select,[contenteditable]")) { e.preventDefault(); setCmd(true); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  const mobileTabs = visible.filter((n) => n.mobile).slice(0, 4);
  return (
    <div className="a-shell">
      <a href="#main" className="v-skip">Skip to content</a>
      <aside className="a-side">
        <Link to="/admin" className="a-brand">
          <Mark />
          <span>
            <span className="a-brand__name">VOLTORA</span>
            <span className="a-brand__store">{me?.store.name}</span>
          </span>
        </Link>
        <SideNav />
        <div className="a-side__foot v-row v-row--between">
          <Link to="/admin/account" style={{ textDecoration: "none", minWidth: 0 }}>
            <div style={{ fontWeight: 550, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{me?.user?.name || me?.user?.email}</div>
            <div className="v-muted" style={{ fontSize: 12 }}>{me?.user?.mfa_enabled ? "MFA on" : "MFA off"}</div>
          </Link>
          <Button variant="ghost" icon size="sm" aria-label="Sign out" onClick={logout}><LogOut size={16} /></Button>
        </div>
      </aside>
      <div className="a-main">
        <header className="a-top">
          <Button className="a-mobile-only" variant="ghost" icon aria-label="Open menu" onClick={() => setDrawer(true)}><Menu size={18} /></Button>
          <button className="a-search" onClick={() => setCmd(true)} aria-label="Search and commands">
            <Search size={16} />
            <span style={{ flex: 1 }}>Search or jump to…</span>
            <Kbd>⌘K</Kbd>
          </button>
          <span style={{ flex: 1 }} />
          {me?.store.environment !== "production" ? <span className="a-env" title="Payments run against sandbox providers only">{me?.store.environment} · sandbox</span> : null}
          <Button variant="ghost" icon aria-label={mode === "dark" ? "Use light mode" : "Use dark mode"} onClick={() => setMode(mode === "dark" ? "light" : "dark")}>
            {mode === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </Button>
          <Notifications />
          <a className="v-btn v-btn--sm" href="/" target="_blank" rel="noreferrer">View store</a>
        </header>
        <main id="main" className="a-content" key={loc.pathname.split("/")[2] ?? ""}>
          {children}
        </main>
        <nav className="a-tabbar" aria-label="Quick navigation">
          {mobileTabs.map((n) => {
            const Icon = n.icon;
            return (
              <NavLink key={n.path} to={`/admin/${n.path}`.replace(/\/$/, "")} end={n.path === ""}>
                <Icon size={20} />
                {n.label}
              </NavLink>
            );
          })}
          <button onClick={() => setDrawer(true)}><MoreHorizontal size={20} />More</button>
        </nav>
      </div>
      <Drawer open={drawer} onClose={() => setDrawer(false)} title="Menu" side="left" width={300}>
        <SideNav onNavigate={() => setDrawer(false)} />
      </Drawer>
      <CommandPalette open={cmd} onClose={() => setCmd(false)} />
    </div>
  );
}

function Forbidden({ permission }: { permission?: string }) {
  return (
    <div className="v-empty" style={{ minHeight: "50vh" }}>
      <ShieldCheck size={28} />
      <h3>You don't have access to this area</h3>
      <p>{permission ? `It requires the “${permission}” permission. Ask a store owner to update your role.` : "Ask a store owner for access."}</p>
      <Link className="v-btn" to="/admin">Back to home</Link>
    </div>
  );
}

const lazyCache = new Map<string, React.LazyExoticComponent<React.ComponentType>>();
function lazyFor(path: string, load: () => Promise<{ default: React.ComponentType }>) {
  let c = lazyCache.get(path);
  if (!c) {
    c = lazy(load);
    lazyCache.set(path, c);
  }
  return c;
}

function Loading() {
  return (
    <div className="v-stack" aria-label="Loading">
      <Skeleton h={28} w={240} />
      <Skeleton h={14} w={360} />
      <Skeleton h={320} />
    </div>
  );
}

export default function AdminApp() {
  const { me, loading, can } = useAuth();
  const loc = useLocation();
  if (loading) return <div style={{ padding: 40 }}><Loading /></div>;
  if (loc.pathname.startsWith("/admin/accept-invite")) return <StaffLogin mode="invite" />;
  if (!me?.user || me.user.kind !== "staff") return <StaffLogin />;
  if (me.mfa_pending) return <StaffLogin mode="mfa" />;
  if (me.permissions.length === 0) return <StaffLogin mode="no-access" />;
  return (
    <Routes>
      {ROUTES.filter((r) => r.fullscreen).map((r) => {
        const C = lazyFor(r.path, r.load);
        return <Route key={r.path} path={r.path} element={r.permission && !can(r.permission) ? <Forbidden permission={r.permission} /> : <Suspense fallback={<Loading />}><C /></Suspense>} />;
      })}
      <Route
        path="*"
        element={
          <Shell>
            <Suspense fallback={<Loading />}>
              <Routes>
                {ROUTES.filter((r) => !r.fullscreen).map((r) => {
                  const C = lazyFor(r.path, r.load);
                  const el = r.permission && !can(r.permission) ? <Forbidden permission={r.permission} /> : <C />;
                  return r.path === "" ? <Route key="index" index element={el} /> : <Route key={r.path} path={r.path} element={el} />;
                })}
                <Route path="*" element={<Navigate to="/admin" replace />} />
              </Routes>
            </Suspense>
          </Shell>
        }
      />
    </Routes>
  );
}

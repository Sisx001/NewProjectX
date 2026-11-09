import { lazy, Suspense, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Mail, MapPin, Menu as MenuIcon, Phone, Search, ShoppingBag, User, X } from "lucide-react";
import { get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Category } from "@/store/types";
import { useThemeState } from "@/store/theme/context";
import { nodeAttrs, str, type Renderer } from "./attrs";
import { useRender } from "./context";
import { linkPublished } from "./css";
import { Picture, SmartLink } from "./content";

const SearchBox = lazy(() => import("@/store/components/SearchBox"));

type L = { label?: string; href?: string; children?: L[] };

function useLinks(items: L[] | undefined): L[] {
  const env = useRender();
  const keep = (l: L) => !!l.label && !!l.href && (env.editing || linkPublished(l.href, env.published));
  return (items ?? []).filter(keep).map((l) => ({ ...l, children: (l.children ?? []).filter(keep) }));
}

/** Close on outside click / Escape for disclosure menus. */
function useDismiss(open: boolean, close: () => void, ref: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && close();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close, ref]);
}

function Dropdown({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLLIElement>(null);
  const id = useId();
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname, loc.search]);
  useDismiss(open, () => setOpen(false), ref);
  return (
    <li ref={ref} className="vt-dd" data-open={open || undefined}>
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
        {label}
        <ChevronDown size={14} aria-hidden />
      </button>
      <div id={id} className="vt-dd__panel" data-wide={wide || undefined} hidden={!open}>
        {children}
      </div>
    </li>
  );
}

function NavItems({ links, categories }: { links: L[]; categories: Category[] | null }) {
  const tops = categories?.filter((c) => !c.parent_id) ?? [];
  return (
    <ul className="vt-nav__list">
      {tops.length ? (
        <Dropdown label="Categories" wide>
          <div className="vt-mega">
            {tops.map((c) => {
              const subs = categories!.filter((s) => s.parent_id === c.id);
              return (
                <div key={c.id} className="vt-mega__col">
                  <Link to={`/c/${encodeURIComponent(c.slug)}`} className="vt-mega__head">{c.name}</Link>
                  {subs.length ? (
                    <ul>{subs.map((s) => <li key={s.id}><Link to={`/c/${encodeURIComponent(s.slug)}`}>{s.name}</Link></li>)}</ul>
                  ) : null}
                </div>
              );
            })}
            <Link to="/shop" className="vt-mega__all">Browse everything</Link>
          </div>
        </Dropdown>
      ) : null}
      {links.map((l, i) =>
        l.children?.length ? (
          <Dropdown key={i} label={l.label!}>
            <ul className="vt-dd__list">
              {l.href && l.href !== "#" ? <li><SmartLink href={l.href}>All {l.label}</SmartLink></li> : null}
              {l.children.map((c, j) => <li key={j}><SmartLink href={c.href!}>{c.label}</SmartLink></li>)}
            </ul>
          </Dropdown>
        ) : (
          <li key={i}><SmartLink href={l.href!} className="vt-nav__link">{l.label}</SmartLink></li>
        ),
      )}
    </ul>
  );
}

const Menu: Renderer = ({ node, p }) => {
  const links = useLinks(p.items as L[]);
  return (
    <nav {...nodeAttrs(node, { className: "vt-nav vb-menu" })} data-orientation={str(p.orientation, "horizontal")} aria-label={node.advanced?.label || "Menu"}>
      <NavItems links={links} categories={null} />
    </nav>
  );
};

function useCartCount(enabled: boolean): number {
  const q = useQuery({ queryKey: ["cart"], queryFn: () => get<{ item_count: number }>("/api/store/cart"), enabled, staleTime: 5_000, retry: 1 });
  return q.data?.item_count ?? 0;
}

function useFocusTrap(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const focusables = () => Array.from(el?.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),input,[tabindex]:not([tabindex="-1"])') ?? []);
    focusables()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key !== "Tab") return;
      const f = focusables();
      if (!f.length) return;
      const first = f[0]!, last = f[f.length - 1]!;
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    const ov = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = ov;
      prev?.focus?.();
    };
  }, [open, onClose]);
  return ref;
}

function MobileDrawer({ open, onClose, links, categories, storeName, accountHref }: { open: boolean; onClose: () => void; links: L[]; categories: Category[]; storeName: string; accountHref: string }) {
  const ref = useFocusTrap(open, onClose);
  const tops = categories.filter((c) => !c.parent_id);
  if (!open) return null;
  return (
    <>
      <div className="vt-scrim" onClick={onClose} />
      <div ref={ref} className="vt-drawer" role="dialog" aria-modal="true" aria-label="Menu">
        <div className="vt-drawer__head">
          <span className="vt-brand__name">{storeName}</span>
          <button type="button" className="vt-icon" onClick={onClose} aria-label="Close menu"><X size={20} /></button>
        </div>
        <nav aria-label="Mobile">
          <ul className="vt-drawer__list">
            <li><Link to="/shop" onClick={onClose}>Shop all</Link></li>
            {links.map((l, i) => (
              <li key={i}>
                <SmartLink href={l.href!} onClick={onClose}>{l.label}</SmartLink>
                {l.children?.length ? <ul>{l.children.map((c, j) => <li key={j}><SmartLink href={c.href!} onClick={onClose}>{c.label}</SmartLink></li>)}</ul> : null}
              </li>
            ))}
          </ul>
          {tops.length ? (
            <>
              <p className="vt-drawer__label">Categories</p>
              <ul className="vt-drawer__list vt-drawer__list--cats">
                {tops.map((c) => <li key={c.id}><Link to={`/c/${encodeURIComponent(c.slug)}`} onClick={onClose}>{c.name}<span className="vb-num">{c.product_count}</span></Link></li>)}
              </ul>
            </>
          ) : null}
          <ul className="vt-drawer__list vt-drawer__list--meta">
            <li><Link to={accountHref} onClick={onClose}>Account</Link></li>
            <li><Link to="/track" onClick={onClose}>Track an order</Link></li>
          </ul>
        </nav>
      </div>
    </>
  );
}

function InlineSearch() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  return (
    <form role="search" className="vt-searchbar" onSubmit={(e) => { e.preventDefault(); if (q.trim()) nav(`/search?q=${encodeURIComponent(q.trim())}`); }}>
      <Search size={18} aria-hidden />
      <input type="search" name="q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search products" aria-label="Search products" />
    </form>
  );
}

const SiteHeader: Renderer = ({ node, p }) => {
  const env = useRender();
  const theme = useThemeState();
  const { me } = useAuth();
  const [drawer, setDrawer] = useState(false);
  const loc = useLocation();
  useEffect(() => setDrawer(false), [loc.pathname]);
  const themeHeader = (theme.components.header ?? {}) as { layout?: string; search?: string; sticky?: boolean; border?: boolean };
  const layout = str(p.layout, "theme") === "theme" ? themeHeader.layout ?? "classic" : str(p.layout);
  const searchStyle = themeHeader.search ?? "icon";
  const links = useLinks(p.menu as L[]);
  const cats = useQuery({ queryKey: ["categories"], queryFn: () => get<{ items: Category[] }>("/api/store/categories"), staleTime: 60_000, enabled: p.show_categories !== false, retry: 1 });
  const categories = p.show_categories !== false ? cats.data?.items ?? [] : [];
  const count = useCartCount(!env.editing);
  const storeName = String(env.data.store.name ?? "");
  const accountHref = me?.user?.kind === "customer" ? "/account" : "/account/login";
  const logo = p.logo as { url?: string; alt?: string } | null;

  const brand = (
    <Link to="/" className="vt-brand" aria-label={`${storeName} home`}>
      {logo?.url ? <Picture img={{ url: logo.url, alt: logo.alt || storeName }} /> : <span className="vt-brand__name">{storeName || "Store"}</span>}
    </Link>
  );
  const searchIcon = (
    <Link to="/search" className="vt-icon" aria-label="Search"><Search size={20} strokeWidth={1.7} /></Link>
  );
  const bar = searchStyle === "bar" || layout === "marketplace";
  const search = p.show_search === false ? null : bar ? (
    <div className="vt-search">
      <Suspense fallback={<InlineSearch />}>
        <SearchBox currency={env.currency} />
      </Suspense>
    </div>
  ) : searchIcon;
  const actions = (
    <div className="vt-actions">
      {bar && p.show_search !== false ? <span className="vt-only-m">{searchIcon}</span> : !bar ? search : null}
      {p.show_account !== false ? <Link to={accountHref} className="vt-icon vt-only-d" aria-label="Account"><User size={20} strokeWidth={1.7} /></Link> : null}
      <button type="button" className="vt-icon vt-cart" onClick={() => window.dispatchEvent(new Event("voltora:open-cart"))} aria-label={`Open cart, ${count} item${count === 1 ? "" : "s"}`} data-testid="cart-button">
        <ShoppingBag size={20} strokeWidth={1.7} />
        {count ? <b className="vb-num">{count > 99 ? "99+" : count}</b> : null}
      </button>
    </div>
  );
  const burger = (
    <button type="button" className="vt-icon vt-burger" onClick={() => setDrawer(true)} aria-label="Open menu" aria-expanded={drawer}>
      <MenuIcon size={22} strokeWidth={1.7} />
    </button>
  );
  const navEl = (
    <nav className="vt-nav" aria-label="Primary">
      <NavItems links={links} categories={categories} />
    </nav>
  );

  let body: ReactNode;
  switch (layout) {
    case "centered":
      body = (
        <>
          <div className="vt-row vt-row--top">{burger}<div className="vt-slot-l vt-only-d">{p.show_search === false ? null : searchIcon}</div>{brand}{actions}</div>
          <div className="vt-row vt-row--nav vt-only-d">{navEl}</div>
        </>
      );
      break;
    case "split":
      body = <div className="vt-row">{burger}<div className="vt-slot-l vt-only-d">{navEl}</div>{brand}{actions}</div>;
      break;
    case "marketplace":
      body = (
        <>
          <div className="vt-row">{burger}{brand}<span className="vt-only-d vt-grow">{search}</span>{actions}</div>
          <div className="vt-row vt-row--nav vt-only-d">{navEl}</div>
        </>
      );
      break;
    case "stacked":
      body = (
        <>
          <div className="vt-row">{burger}{brand}{actions}</div>
          <div className="vt-row vt-row--nav vt-only-d">{navEl}</div>
        </>
      );
      break;
    case "minimal":
      body = <div className="vt-row">{burger}{brand}<span className="vt-grow vt-only-d" />{navEl}{bar ? <span className="vt-only-d">{search}</span> : null}{actions}</div>;
      break;
    default: // classic
      body = <div className="vt-row">{burger}{brand}{navEl}<span className="vt-grow vt-only-d" />{bar ? <span className="vt-only-d">{search}</span> : null}{actions}</div>;
  }
  const sticky = p.sticky !== false && themeHeader.sticky !== false;
  return (
    <header {...nodeAttrs(node, { className: "vt-header" })} data-site-header data-layout={layout} data-sticky={sticky || undefined} data-border={themeHeader.border ? "" : undefined}>
      <div className="vt-header__in">{body}</div>
      <MobileDrawer open={drawer} onClose={() => setDrawer(false)} links={links} categories={categories} storeName={storeName} accountHref={accountHref} />
    </header>
  );
};

const SiteFooter: Renderer = ({ node, p }) => {
  const env = useRender();
  const theme = useThemeState();
  const tf = (theme.components.footer ?? {}) as { layout?: string; tone?: string };
  const layout = str(p.layout, "theme") === "theme" ? tf.layout ?? "columns" : str(p.layout);
  const keep = (l: L) => !!l.label && !!l.href && (env.editing || linkPublished(l.href, env.published));
  const columns = ((p.columns as { title?: string; links?: L[] }[]) ?? []).map((c) => ({ ...c, links: (c.links ?? []).filter(keep) })).filter((c) => c.links.length);
  const s = env.data.settings;
  const contact = p.show_contact !== false ? { email: String(s["store.support_email"] ?? ""), phone: String(s["store.support_phone"] ?? ""), address: String(s["store.address"] ?? "") } : null;
  const storeName = String(env.data.store.name ?? "");
  const blurb = str(p.blurb);
  const contactEl = contact && (contact.email || contact.phone || contact.address) ? (
    <ul className="vt-contact">
      {contact.phone ? <li><Phone size={15} aria-hidden /><a href={`tel:${contact.phone.replace(/\s/g, "")}`}>{contact.phone}</a></li> : null}
      {contact.email ? <li><Mail size={15} aria-hidden /><a href={`mailto:${contact.email}`}>{contact.email}</a></li> : null}
      {contact.address ? <li><MapPin size={15} aria-hidden /><span>{contact.address}</span></li> : null}
    </ul>
  ) : null;
  const cols = (
    <div className="vt-fcols">
      {columns.map((c, i) => (
        <nav key={i} aria-label={c.title || `Footer links ${i + 1}`}>
          {c.title ? <h2 className="vt-fhead">{c.title}</h2> : null}
          <ul>{c.links.map((l, j) => <li key={j}><SmartLink href={l.href!}>{l.label}</SmartLink></li>)}</ul>
        </nav>
      ))}
    </div>
  );
  return (
    <footer {...nodeAttrs(node, { className: "vt-footer" })} data-layout={layout} data-tone={tf.tone ?? "default"}>
      <div className="vt-footer__in">
        {layout === "minimal" ? (
          <>
            <Link to="/" className="vt-fbrand">{storeName}</Link>
            <ul className="vt-finline">{columns.flatMap((c) => c.links).map((l, i) => <li key={i}><SmartLink href={l.href!}>{l.label}</SmartLink></li>)}</ul>
            {contactEl}
          </>
        ) : (
          <>
            <div className="vt-fintro">
              <Link to="/" className="vt-fbrand">{storeName}</Link>
              {blurb ? <p>{blurb}</p> : null}
              {contactEl}
            </div>
            {cols}
          </>
        )}
      </div>
      {str(p.legal) ? <div className="vt-legal"><p>{str(p.legal)}</p></div> : null}
    </footer>
  );
};

export const NAVIGATION: Record<string, Renderer> = { menu: Menu, "site-header": SiteHeader, "site-footer": SiteFooter };

import { Link, useParams } from "react-router-dom";
import { useQueries } from "@tanstack/react-query";
import { Wrench, WifiOff } from "lucide-react";
import { get } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { Alert } from "@/ui";
import { ProductGrid } from "../components/ProductCard";
import { Page } from "../lib/pageview";
import { detailToCard, useCategories, useDocTitle, useProducts } from "../lib/data";
import { useCompare, useWishlist } from "../lib/local";
import { optionMap } from "../lib/helpers";
import type { ProductDetail } from "../types";

export function NotFound({ what = "page" }: { what?: string }) {
  useDocTitle("Not found");
  return (
    <div className="s-wrap s-empty s-empty--page" data-testid="not-found">
      <p className="s-eyebrow">404</p>
      <h1 className="s-h1">We can't find that {what}</h1>
      <p className="s-muted">It may have moved, sold out for good, or the link is mistyped.</p>
      <div className="s-empty__acts"><Link className="s-btn s-btn--primary" to="/shop">Browse the shop</Link><Link className="s-btn s-btn--quiet" to="/">Go home</Link></div>
    </div>
  );
}

export function Maintenance({ name, phone, email }: { name: string; phone?: string; email?: string }) {
  return (
    <main className="s-empty s-empty--page s-maint" data-testid="maintenance">
      <Wrench size={28} aria-hidden />
      <h1 className="s-h1">{name} is getting a tune-up</h1>
      <p className="s-muted">We're doing scheduled maintenance and will be back shortly. Orders already placed are safe.</p>
      {phone || email ? <p>{phone ? <a href={`tel:${phone}`}>{phone}</a> : null}{phone && email ? " · " : null}{email ? <a href={`mailto:${email}`}>{email}</a> : null}</p> : null}
      <Link className="s-link" to="/account/login">Staff sign in</Link>
    </main>
  );
}

export function OfflineBar() {
  return <div className="s-offline" role="status"><WifiOff size={16} aria-hidden /> You're offline. Cart and checkout need a connection — we'll reconnect automatically.</div>;
}

export function ServerDown({ retry }: { retry: () => void }) {
  return (
    <main className="s-empty s-empty--page" data-testid="server-down">
      <h1 className="s-h1">We can't reach the store right now</h1>
      <p className="s-muted">This is on our side. Please try again in a moment.</p>
      <button className="s-btn s-btn--primary" onClick={retry}>Try again</button>
    </main>
  );
}

export function CmsPage() {
  const { slug } = useParams();
  return <Page slug={slug ?? ""} notFound={<NotFound />} />;
}

/** Built-in home shown only when no published "home" page exists, so a fresh store is never blank. */
export function HomeFallback() {
  const cats = useCategories();
  const latest = useProducts({ sort: "newest", page_size: 8 });
  return (
    <div className="s-wrap">
      <section className="s-hero">
        <p className="s-eyebrow">New in</p>
        <h1 className="s-display">Everything we have, <em>honestly priced.</em></h1>
        <Link className="s-btn s-btn--primary s-btn--lg" to="/shop">Shop all</Link>
      </section>
      {cats.data?.items.length ? <p className="s-chips">{cats.data.items.filter((c) => !c.parent_id).map((c) => <Link key={c.id} className="s-pill" to={`/c/${c.slug}`}>{c.name}</Link>)}</p> : null}
      {latest.data?.items.length ? <section className="s-section"><h2 className="s-h2">Latest arrivals</h2><ProductGrid items={latest.data.items} /></section> : null}
    </div>
  );
}

function useDetails(slugs: string[]) {
  return useQueries({ queries: slugs.map((s) => ({ queryKey: ["product", s], queryFn: () => get<ProductDetail>(`/api/store/products/${encodeURIComponent(s)}`), retry: false, staleTime: 20_000 })) });
}

export function Wishlist() {
  useDocTitle("Wishlist");
  const w = useWishlist();
  const res = useDetails(w.items);
  const cards = res.map((r) => r.data).filter((p): p is ProductDetail => !!p).map(detailToCard);
  return (
    <div className="s-wrap">
      <h1 className="s-h1">Wishlist</h1>
      <p className="s-muted">Saved on this device only. Sign-in isn't required, and it isn't synced to your account.</p>
      {w.items.length === 0 ? <div className="s-empty"><p className="s-empty__t">Nothing saved yet</p><Link className="s-btn s-btn--primary" to="/shop">Browse the shop</Link></div>
        : res.some((r) => r.isLoading) && !cards.length ? <p className="s-muted">Loading…</p> : <ProductGrid items={cards} />}
      {res.some((r) => r.isError) ? <Alert tone="info" action={<button className="s-btn s-btn--quiet" onClick={() => res.forEach((r, i) => r.isError && w.remove(w.items[i]!))}>Remove</button>}>Some saved items are no longer available.</Alert> : null}
    </div>
  );
}

export function Compare() {
  useDocTitle("Compare");
  const c = useCompare();
  const res = useDetails(c.items);
  const items = res.map((r) => r.data).filter((p): p is ProductDetail => !!p);
  const optNames = Array.from(new Set(items.flatMap((p) => p.options.map((o) => o.name))));
  const attrNames = Array.from(new Set(items.flatMap((p) => (Array.isArray(p.attributes) ? p.attributes.map((a) => a.name) : Object.keys(p.attributes ?? {})))));
  const attr = (p: ProductDetail, k: string) => (Array.isArray(p.attributes) ? p.attributes.find((a) => a.name === k)?.value : (p.attributes as Record<string, unknown> | undefined)?.[k]);
  return (
    <div className="s-wrap">
      <h1 className="s-h1">Compare</h1>
      {c.items.length === 0 ? <div className="s-empty"><p className="s-empty__t">Pick up to four products to compare</p><p className="s-muted">Use the compare icon on any product.</p><Link className="s-btn s-btn--primary" to="/shop">Browse the shop</Link></div> : (
        <div className="s-tablewrap">
          <table className="s-compare">
            <caption className="s-sr">Product comparison</caption>
            <thead><tr><th scope="col" />{items.map((p) => <th scope="col" key={p.id}><Link to={`/p/${p.slug}`}>{p.images[0] ? <img src={p.images[0].url} alt="" width={120} height={150} loading="lazy" /> : null}{p.title}</Link><button className="s-link" onClick={() => c.remove(p.slug)}>Remove</button></th>)}</tr></thead>
            <tbody>
              <tr><th scope="row">Price</th>{items.map((p) => { const pr = p.variants.map((v) => v.price_minor); const lo = Math.min(...pr), hi = Math.max(...pr); return <td key={p.id}>{lo === hi ? formatMoney(lo, p.currency) : `${formatMoney(lo, p.currency)} – ${formatMoney(hi, p.currency)}`}</td>; })}</tr>
              <tr><th scope="row">Brand</th>{items.map((p) => <td key={p.id}>{p.brand?.name ?? "—"}</td>)}</tr>
              <tr><th scope="row">Availability</th>{items.map((p) => <td key={p.id}>{p.variants.some((v) => v.in_stock) ? "In stock" : "Sold out"}</td>)}</tr>
              {optNames.map((n) => <tr key={n}><th scope="row">{n}</th>{items.map((p) => <td key={p.id}>{p.options.find((o) => o.name === n)?.values.join(", ") ?? "—"}</td>)}</tr>)}
              {attrNames.map((n) => <tr key={n}><th scope="row">{n}</th>{items.map((p) => <td key={p.id}>{String(attr(p, n) ?? "—")}</td>)}</tr>)}
              <tr><th scope="row">Variants</th>{items.map((p) => <td key={p.id}>{p.variants.length}{p.variants[0] && Object.keys(optionMap(p.variants[0])).length ? "" : ""}</td>)}</tr>
              <tr><th scope="row" /> {items.map((p) => <td key={p.id}><Link className="s-btn s-btn--primary" to={`/p/${p.slug}`}>View product</Link></td>)}</tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

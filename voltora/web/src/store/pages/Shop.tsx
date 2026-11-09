import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { SlidersHorizontal, X } from "lucide-react";
import { formatMoney, parseMoney, toMajorInput } from "@/lib/money";
import { Alert } from "@/ui";
import { ProductGrid } from "../components/ProductCard";
import { Sheet } from "../components/Sheet";
import SearchBox from "../components/SearchBox";
import { useCategories, useDocTitle, useProducts } from "../lib/data";
import { activeFilterCount, buildShopParams, parseShopParams, type ShopState } from "../lib/helpers";
import type { Facets } from "../types";

const SORT_LABEL: Record<string, string> = { newest: "Newest", price_asc: "Price: low to high", price_desc: "Price: high to low", title: "Name: A–Z" };

function FilterPanel({ s, facets, set, mode, currency }: { s: ShopState; facets?: Facets; set: (p: Partial<ShopState>) => void; mode: string; currency: string }) {
  const [min, setMin] = useState(s.min);
  const [max, setMax] = useState(s.max);
  useEffect(() => { setMin(s.min); setMax(s.max); }, [s.min, s.max]);
  const commitPrice = () => { if (min !== s.min || max !== s.max) set({ min, max }); };
  return (
    <div className="s-filters">
      {mode !== "category" && facets?.categories?.length ? (
        <fieldset><legend>Category</legend>
          {facets.categories.map((c) => (
            <label key={c.slug} className="s-check"><input type="radio" name="f-cat" checked={s.category === c.slug} onChange={() => set({ category: s.category === c.slug ? "" : c.slug })} onClick={() => s.category === c.slug && set({ category: "" })} /> <span>{c.name}</span><em>{c.count}</em></label>
          ))}
        </fieldset>
      ) : null}
      {mode !== "brand" && facets?.brands?.length ? (
        <fieldset><legend>Brand</legend>
          {facets.brands.map((b) => (
            <label key={b.slug} className="s-check"><input type="radio" name="f-brand" checked={s.brand === b.slug} onChange={() => set({ brand: b.slug })} onClick={() => s.brand === b.slug && set({ brand: "" })} /> <span>{b.name}</span><em>{b.count}</em></label>
          ))}
        </fieldset>
      ) : null}
      <fieldset><legend>Price ({currency === "BDT" ? "৳" : currency})</legend>
        <div className="s-price-in">
          <input className="s-input" inputMode="numeric" aria-label="Minimum price" placeholder={facets?.price ? toMajorInput(facets.price.min, currency) : "Min"} value={min} onChange={(e) => setMin(e.target.value.replace(/[^\d.]/g, ""))} onBlur={commitPrice} onKeyDown={(e) => e.key === "Enter" && commitPrice()} />
          <span aria-hidden>–</span>
          <input className="s-input" inputMode="numeric" aria-label="Maximum price" placeholder={facets?.price ? toMajorInput(facets.price.max, currency) : "Max"} value={max} onChange={(e) => setMax(e.target.value.replace(/[^\d.]/g, ""))} onBlur={commitPrice} onKeyDown={(e) => e.key === "Enter" && commitPrice()} />
        </div>
        {facets?.price ? <p className="s-muted">Range here: {formatMoney(facets.price.min, currency)} – {formatMoney(facets.price.max, currency)}</p> : null}
      </fieldset>
      {Object.entries(facets?.options ?? {}).map(([name, vals]) => (
        <fieldset key={name}><legend>{name}</legend>
          <div className="s-opt__vals">
            {vals.map((v) => {
              const on = s.options[name] === v.value;
              return <button key={v.value} type="button" className="s-chip" data-on={on} aria-pressed={on} onClick={() => set({ options: { ...s.options, [name]: on ? "" : v.value } })}>{v.value} <em>{v.count}</em></button>;
            })}
          </div>
        </fieldset>
      ))}
      <label className="s-check s-check--switch"><input type="checkbox" checked={s.inStock} onChange={(e) => set({ inStock: e.target.checked })} /> <span>In stock only</span></label>
    </div>
  );
}

/** One listing component for /shop, /c/:slug, /b/:slug and /search; all state lives in the URL. */
export default function Shop({ mode }: { mode: "shop" | "category" | "brand" | "search" }) {
  const { slug } = useParams();
  const [sp, setSp] = useSearchParams();
  const s = useMemo(() => parseShopParams(sp), [sp]);
  const cats = useCategories();
  const [sheet, setSheet] = useState(false);
  const eff: ShopState = { ...s, category: mode === "category" ? slug ?? "" : s.category, brand: mode === "brand" ? slug ?? "" : s.brand };
  const currency = "BDT";
  const minor = (v: string) => { const m = v ? parseMoney(v, currency) : null; return m === null ? undefined : m; };
  const q = useProducts({
    q: eff.q || undefined, category: eff.category || undefined, brand: eff.brand || undefined, min: minor(eff.min), max: minor(eff.max),
    in_stock: eff.inStock ? 1 : undefined, sort: eff.sort, page: eff.page, page_size: 24,
    ...Object.fromEntries(Object.entries(eff.options).map(([k, v]) => ["option." + k, v])),
  });
  const set = (p: Partial<ShopState>) => {
    const next = { ...s, ...p, page: "page" in p ? p.page! : 1 };
    setSp(buildShopParams({ ...next, category: mode === "category" ? "" : next.category, brand: mode === "brand" ? "" : next.brand }), { replace: true });
  };
  const cat = cats.data?.items.find((c) => c.slug === slug);
  const title = mode === "search" ? (s.q ? `Results for “${s.q}”` : "Search") : mode === "category" ? cat?.name ?? slug ?? "Category" : mode === "brand" ? q.data?.facets?.brands?.find((b) => b.slug === slug)?.name ?? q.data?.items[0]?.brand?.name ?? slug ?? "Brand" : "Shop";
  useDocTitle(title);
  const ignore = [mode === "category" ? "category" : "", mode === "brand" ? "brand" : ""];
  const n = activeFilterCount(eff, ignore);
  const data = q.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const clear = () => setSp(buildShopParams({ q: s.q, sort: s.sort }), { replace: true });
  const panel = <FilterPanel s={eff} facets={data?.facets} set={set} mode={mode} currency={currency} />;

  return (
    <div className="s-wrap s-shop">
      <nav className="s-crumbs" aria-label="Breadcrumb"><Link to="/">Home</Link><span aria-hidden>/</span>{mode === "shop" ? <span aria-current="page">Shop</span> : <><Link to="/shop">Shop</Link><span aria-hidden>/</span><span aria-current="page">{title}</span></>}</nav>
      <header className="s-pagehead">
        <h1 className="s-h1">{title}</h1>
        {cat && cat.product_count ? <p className="s-muted">{cat.product_count} products</p> : null}
        {mode === "search" ? <SearchBox initial={s.q} /> : null}
      </header>
      <div className="s-toolbar">
        <button type="button" className="s-btn s-btn--quiet s-only-m" onClick={() => setSheet(true)} data-testid="open-filters"><SlidersHorizontal size={16} aria-hidden /> Filters{n ? ` (${n})` : ""}</button>
        <p className="s-muted" role="status" aria-live="polite">{q.isFetching && !data ? "Loading…" : data ? `${data.total} ${data.total === 1 ? "product" : "products"}` : ""}</p>
        <label className="s-sort"><span>Sort</span>
          <select className="s-input" value={s.sort} onChange={(e) => set({ sort: e.target.value })} aria-label="Sort products">{Object.entries(SORT_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        </label>
      </div>
      {n > 0 ? (
        <div className="s-active" aria-label="Active filters">
          {eff.category && mode !== "category" ? <button className="s-pill" onClick={() => set({ category: "" })}>{eff.category} <X size={14} aria-hidden /><span className="s-sr">remove</span></button> : null}
          {eff.brand && mode !== "brand" ? <button className="s-pill" onClick={() => set({ brand: "" })}>{eff.brand} <X size={14} aria-hidden /><span className="s-sr">remove</span></button> : null}
          {eff.min || eff.max ? <button className="s-pill" onClick={() => set({ min: "", max: "" })}>৳{eff.min || "0"}–{eff.max || "∞"} <X size={14} aria-hidden /><span className="s-sr">remove</span></button> : null}
          {eff.inStock ? <button className="s-pill" onClick={() => set({ inStock: false })}>In stock <X size={14} aria-hidden /><span className="s-sr">remove</span></button> : null}
          {Object.entries(eff.options).filter(([, v]) => v).map(([k, v]) => <button key={k} className="s-pill" onClick={() => set({ options: { ...eff.options, [k]: "" } })}>{k}: {v} <X size={14} aria-hidden /><span className="s-sr">remove</span></button>)}
          <button className="s-link" onClick={clear}>Clear all</button>
        </div>
      ) : null}
      <div className="s-shop__body">
        <aside className="s-shop__side s-only-d" aria-label="Filters">{panel}</aside>
        <section aria-label="Products">
          {q.isError ? <Alert tone="danger" action={<button className="s-btn s-btn--quiet" onClick={() => q.refetch()}>Retry</button>}>Products couldn't be loaded.</Alert>
            : !data ? <div className="s-grid" aria-busy="true">{Array.from({ length: 8 }, (_, i) => <div key={i} className="s-skel-card"><div className="s-skel" /><div className="s-skel s-skel--t" /><div className="s-skel s-skel--t s-skel--s" /></div>)}</div>
            : data.items.length === 0 ? (
              <div className="s-empty" data-testid="no-results">
                <p className="s-empty__t">{n || s.q ? "Nothing matches that" : "No products here yet"}</p>
                <p className="s-muted">{n ? "Try removing a filter or widening the price range." : s.q ? "Check the spelling, or try a broader word." : "The catalogue is empty. Check back soon."}</p>
                <div className="s-empty__acts">{n ? <button className="s-btn s-btn--primary" onClick={clear}>Clear filters</button> : null}<Link className="s-btn s-btn--quiet" to="/shop">Browse everything</Link></div>
                {cats.data?.items.length ? <p className="s-chips">{cats.data.items.filter((c) => !c.parent_id).slice(0, 6).map((c) => <Link key={c.id} className="s-pill" to={`/c/${c.slug}`}>{c.name}</Link>)}</p> : null}
              </div>
            ) : (
              <>
                <div style={{ opacity: q.isPlaceholderData ? 0.55 : 1, transition: "opacity .15s" }}><ProductGrid items={data.items} /></div>
                {pages > 1 ? (
                  <nav className="s-pager" aria-label="Pagination">
                    <button className="s-btn s-btn--quiet" disabled={s.page <= 1} onClick={() => { set({ page: s.page - 1 }); window.scrollTo({ top: 0 }); }}>Previous</button>
                    <span aria-live="polite">Page {data.page} of {pages}</span>
                    <button className="s-btn s-btn--quiet" disabled={s.page >= pages} onClick={() => { set({ page: s.page + 1 }); window.scrollTo({ top: 0 }); }}>Next</button>
                  </nav>
                ) : null}
              </>
            )}
        </section>
      </div>
      <Sheet open={sheet} onClose={() => setSheet(false)} title="Filters" footer={<><button className="s-btn s-btn--quiet" onClick={clear}>Clear</button><button className="s-btn s-btn--primary s-btn--grow" onClick={() => setSheet(false)}>Show {data ? data.total : ""} results</button></>}>
        {panel}
      </Sheet>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, ChevronLeft, ChevronRight, Heart, Truck } from "lucide-react";
import { ApiError, get, post } from "@/lib/api";
import { exponent, formatMoney } from "@/lib/money";
import { Alert, Skeleton } from "@/ui";
import { BuyBox, useBuyBox } from "../components/BuyBox";
import { emptyLoc, LocationPicker, type Loc } from "../components/LocationPicker";
import { ProductGrid } from "../components/ProductCard";
import { detailToCard, useDocTitle, useProduct, useProducts } from "../lib/data";
import { useCompare, useRecentlyViewed, useWishlist } from "../lib/local";
import { purchasable } from "../lib/helpers";
import { htmlToText, sanitizeHtml } from "../lib/safeHtml";
import type { ProductDetail, Quote } from "../types";
import { NotFound } from "./Misc";

function Gallery({ images, activeUrl }: { images: ProductDetail["images"]; activeUrl?: string }) {
  const [i, setI] = useState(0);
  const [zoom, setZoom] = useState<{ x: number; y: number } | null>(null);
  const start = useRef<number | null>(null);
  useEffect(() => { if (activeUrl) { const k = images.findIndex((x) => x.url === activeUrl); if (k >= 0) setI(k); } }, [activeUrl, images]);
  if (!images.length) return <div className="s-gal"><div className="s-gal__main s-gal__main--empty">No image</div></div>;
  const go = (d: number) => setI((n) => (n + d + images.length) % images.length);
  const cur = images[Math.min(i, images.length - 1)]!;
  return (
    <div className="s-gal" role="group" aria-roledescription="carousel" aria-label="Product images">
      <div
        className="s-gal__main" tabIndex={0} data-zoom={!!zoom}
        onKeyDown={(e) => { if (e.key === "ArrowRight") { e.preventDefault(); go(1); } if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); } }}
        onPointerDown={(e) => { if (e.pointerType !== "mouse") start.current = e.clientX; }}
        onPointerUp={(e) => { if (start.current !== null) { const dx = e.clientX - start.current; if (Math.abs(dx) > 40) go(dx < 0 ? 1 : -1); start.current = null; } }}
        onPointerMove={(e) => { if (e.pointerType === "mouse") { const r = e.currentTarget.getBoundingClientRect(); setZoom({ x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 }); } }}
        onPointerLeave={() => setZoom(null)}
      >
        <img src={cur.url} alt={cur.alt} fetchPriority="high" width={900} height={1125} style={zoom ? { transformOrigin: `${zoom.x}% ${zoom.y}%` } : undefined} draggable={false} />
        {images.length > 1 ? (
          <>
            <button type="button" className="s-gal__nav s-gal__nav--prev" aria-label="Previous image" onClick={() => go(-1)}><ChevronLeft size={20} /></button>
            <button type="button" className="s-gal__nav s-gal__nav--next" aria-label="Next image" onClick={() => go(1)}><ChevronRight size={20} /></button>
            <p className="s-gal__count" aria-live="polite">{i + 1} / {images.length}</p>
          </>
        ) : null}
      </div>
      {images.length > 1 ? (
        <ul className="s-gal__thumbs">
          {images.map((im, k) => (
            <li key={im.url + k}><button type="button" aria-label={`Show image ${k + 1}`} aria-current={k === i} onClick={() => setI(k)}><img src={im.url} alt="" loading="lazy" width={72} height={90} /></button></li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Delivery({ currency, hasCart }: { currency: string; hasCart: boolean }) {
  const [loc, setLoc] = useState<Loc>(() => { try { return JSON.parse(localStorage.getItem("voltora:store:loc") ?? "") as Loc; } catch { return emptyLoc; } });
  const q = useQuery({
    queryKey: ["delivery-est", loc.district],
    enabled: !!loc.district,
    retry: false,
    queryFn: () => post<Quote>("/api/store/checkout/quote", { address: { district: loc.district, upazila: loc.upazila || "" } }),
  });
  useEffect(() => { try { localStorage.setItem("voltora:store:loc", JSON.stringify(loc)); } catch { /* ignore */ } }, [loc]);
  return (
    <section className="s-deliv" aria-labelledby="deliv-h">
      <h2 id="deliv-h"><Truck size={16} aria-hidden /> Delivery estimate</h2>
      <LocationPicker value={loc} onChange={setLoc} compact idPrefix="est" />
      <div role="status" aria-live="polite">
        {q.isFetching ? <p className="s-muted">Checking delivery to {loc.district}…</p>
          : q.data && q.data.shipping_options.length ? (
            <ul className="s-deliv__opts">{q.data.shipping_options.map((o) => <li key={o.id}><span>{o.label}</span><span>{o.eta_label}</span><strong>{o.amount_minor ? formatMoney(o.amount_minor, q.data!.currency || currency) : "Free"}</strong></li>)}</ul>
          ) : q.error ? <p className="s-muted">{q.error instanceof ApiError && !hasCart ? "Add this to your cart and delivery options for your area appear here and at checkout." : "Delivery options are confirmed at checkout."}</p>
            : loc.district ? <p className="s-muted">Delivery options are confirmed at checkout.</p> : <p className="s-muted">Choose your district to see delivery options.</p>}
      </div>
    </section>
  );
}

function Details({ product }: { product: ProductDetail }) {
  const attrs = Array.isArray(product.attributes) ? product.attributes.map((a) => [a.name, a.value] as const) : Object.entries(product.attributes ?? {}).map(([k, v]) => [k, String(v)] as const);
  return (
    <section className="s-details">
      {product.description ? <div className="s-prose" dangerouslySetInnerHTML={{ __html: sanitizeHtml(product.description) }} /> : null}
      {attrs.length ? <dl className="s-specs">{attrs.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl> : null}
    </section>
  );
}

function Loaded({ product }: { product: ProductDetail }) {
  const box = useBuyBox(product);
  const wish = useWishlist();
  const cmp = useCompare();
  const recent = useRecentlyViewed();
  const barRef = useRef<HTMLDivElement>(null);
  const [showBar, setShowBar] = useState(false);
  useDocTitle(product.seo?.title || product.title);
  useEffect(() => { recent.push(product.slug); }, [product.slug]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = barRef.current;
    if (!el || !("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver(([e]) => setShowBar(!e!.isIntersecting && e!.boundingClientRect.top < 0), { threshold: 0 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    const prices = product.variants.map((v) => v.price_minor);
    const el = document.createElement("script");
    el.type = "application/ld+json";
    el.id = "ld-product";
    el.textContent = JSON.stringify({
      "@context": "https://schema.org", "@type": "Product", name: product.title, description: product.seo?.description || htmlToText(product.description ?? "").slice(0, 300),
      image: product.images.map((i) => new URL(i.url, location.origin).href), brand: product.brand ? { "@type": "Brand", name: product.brand.name } : undefined,
      sku: product.variants[0]?.sku,
      offers: { "@type": "AggregateOffer", priceCurrency: product.currency, lowPrice: (Math.min(...prices) / 10 ** exponent(product.currency)).toFixed(exponent(product.currency)), highPrice: (Math.max(...prices) / 10 ** exponent(product.currency)).toFixed(exponent(product.currency)), offerCount: product.variants.length,
        availability: product.variants.some(purchasable) ? "https://schema.org/InStock" : "https://schema.org/OutOfStock" },
    });
    document.head.querySelector("#ld-product")?.remove();
    document.head.appendChild(el);
    return () => el.remove();
  }, [product]);

  const activeImg = box.variant ? product.images.find((i) => i.variant_ids?.includes(box.variant!.id))?.url : undefined;
  const cat = product.categories[0];
  const others = recent.items.filter((s) => s !== product.slug).slice(0, 4);
  const related = useProducts({ category: cat?.slug, page_size: 5 }, !!cat);
  const relatedItems = (related.data?.items ?? []).filter((p) => p.slug !== product.slug).slice(0, 4);
  const wished = wish.has(product.slug);
  const compared = cmp.has(product.slug);
  return (
    <div className="s-wrap s-pdp">
      <nav className="s-crumbs" aria-label="Breadcrumb">
        <Link to="/">Home</Link><span aria-hidden>/</span><Link to="/shop">Shop</Link>
        {cat ? <><span aria-hidden>/</span><Link to={`/c/${cat.slug}`}>{cat.name}</Link></> : null}
        <span aria-hidden>/</span><span aria-current="page">{product.title}</span>
      </nav>
      <div className="s-pdp__grid">
        <Gallery images={product.images} activeUrl={activeImg} />
        <div className="s-pdp__info">
          {product.brand ? <p className="s-card__brand"><Link to={`/b/${product.brand.slug}`}>{product.brand.name}</Link></p> : null}
          <h1 className="s-h1">{product.title}</h1>
          {product.is_demo ? <p className="s-demo">Demo product — not a real item for sale.</p> : null}
          <div ref={barRef}><BuyBox product={product} box={box} /></div>
          <div className="s-pdp__acts">
            <button type="button" className="s-btn s-btn--quiet" aria-pressed={wished} onClick={() => wish.toggle(product.slug)}><Heart size={16} fill={wished ? "currentColor" : "none"} aria-hidden /> {wished ? "Saved" : "Save"}</button>
            <button type="button" className="s-btn s-btn--quiet" aria-pressed={compared} onClick={() => cmp.toggle(product.slug)}><ArrowLeftRight size={16} aria-hidden /> {compared ? "In compare" : "Compare"}</button>
          </div>
          <Delivery currency={product.currency} hasCart />
          <Details product={product} />
        </div>
      </div>
      {relatedItems.length ? <section className="s-section"><h2 className="s-h2">You may also like</h2><ProductGrid items={relatedItems} /></section> : null}
      {others.length ? <Recent slugs={others} /> : null}
      <div className="s-stickybar" data-show={showBar} aria-hidden={!showBar}>
        <div><strong>{box.variant ? formatMoney(box.variant.price_minor, product.currency) : "Select options"}</strong><span className="s-muted">{box.variant?.title ?? product.title}</span></div>
        <button type="button" className="s-btn s-btn--primary" tabIndex={showBar ? 0 : -1} disabled={box.busy || (!!box.variant && !box.canAdd)}
          onClick={() => { if (!box.variant) barRef.current?.scrollIntoView({ block: "center", behavior: "smooth" }); box.add(); }}>{box.variant ? "Add to cart" : "Choose options"}</button>
      </div>
    </div>
  );
}

export function Recent({ slugs }: { slugs: string[] }) {
  const items = useQuery({
    queryKey: ["recent-cards", slugs.join(",")],
    queryFn: async () => (await Promise.all(slugs.map((s) => get<ProductDetail>(`/api/store/products/${s}`).catch(() => null)))).filter((p): p is ProductDetail => !!p).map(detailToCard),
  });
  if (!items.data?.length) return null;
  return <section className="s-section"><h2 className="s-h2">Recently viewed</h2><ProductGrid items={items.data} /></section>;
}

export default function Product() {
  const { slug } = useParams();
  const q = useProduct(slug);
  const err = useMemo(() => q.error, [q.error]);
  if (q.isLoading) return <div className="s-wrap s-pdp"><div className="s-pdp__grid"><Skeleton h={560} /><div style={{ display: "grid", gap: 12 }}><Skeleton h={36} /><Skeleton h={24} w="40%" /><Skeleton h={120} /></div></div></div>;
  if (err instanceof ApiError && err.status === 404) return <NotFound what="product" />;
  if (!q.data) return <div className="s-wrap"><Alert tone="danger" action={<button className="s-btn s-btn--quiet" onClick={() => q.refetch()}>Retry</button>}>This product couldn't be loaded.</Alert></div>;
  return <Loaded key={q.data.id} product={q.data} />;
}

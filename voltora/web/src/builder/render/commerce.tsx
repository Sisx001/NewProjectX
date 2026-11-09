import { useRef, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, ArrowUpRight } from "lucide-react";
import { get, qs } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { ProductCard } from "@/store/components/ProductCard";
import type { Category, ProductCardData, ProductCardVariant, ProductDetail, ProductList } from "@/store/types";
import { useThemeState } from "@/store/theme/context";
import { nodeAttrs, num, str, type Renderer } from "./attrs";
import { useRender } from "./context";
import { Picture } from "./content";

const CARD_VARIANTS = ["standard", "minimal", "editorial", "dense", "overlay"];

function useProductQuery(p: Record<string, unknown>, limit: number) {
  const params = { sort: str(p.sort, "newest"), page_size: limit, category: str(p.category) || undefined, in_stock: p.in_stock_only ? 1 : undefined };
  return { params, q: useQuery({ queryKey: ["products", params], queryFn: ({ signal }) => get<ProductList>(`/api/store/products${qs(params)}`, signal), staleTime: 30_000, retry: 1 }) };
}

function Notice({ children }: { children: string }) {
  return <p className="vb-notice">{children}</p>;
}

function Head({ title, link }: { title: string; link?: string | null }) {
  if (!title && !link) return null;
  return (
    <div className="vb-blockhead">
      {title ? <h2 className="vb-h" data-size="lg">{title}</h2> : <span />}
      {link ? <Link className="vb-more" to={link}>View all <ArrowUpRight size={16} aria-hidden /></Link> : null}
    </div>
  );
}

function shopLink(p: Record<string, unknown>) {
  const cat = str(p.category);
  const sort = str(p.sort, "newest");
  return cat ? `/c/${encodeURIComponent(cat)}${sort !== "newest" ? `?sort=${sort}` : ""}` : `/shop?sort=${sort}`;
}

const cardVariant = (p: Record<string, unknown>): ProductCardVariant | undefined => {
  const v = str(p.card, "theme");
  return CARD_VARIANTS.includes(v) ? (v as ProductCardVariant) : undefined;
};

const ProductGrid: Renderer = ({ node, p }) => {
  const env = useRender();
  const theme = useThemeState();
  const limit = num(p.limit, 8);
  const { q } = useProductQuery(p, limit);
  const items: ProductCardData[] = q.data?.items ?? [];
  const cols = num(p.columns, num(theme.components.grid?.columns as number, 4));
  const style = { "--cols": cols, "--cols-m": num(theme.components.grid?.columns_mobile as number, 2) } as CSSProperties;
  if (q.isError) return env.editing ? <div {...nodeAttrs(node)}><Notice>Products could not be loaded. The live page hides this block until the catalog responds.</Notice></div> : null;
  if (!q.isLoading && !items.length) return env.editing ? <div {...nodeAttrs(node)}><Head title={str(p.title)} /><Notice>No products match yet. Publish products in Catalog and they appear here automatically.</Notice></div> : null;
  return (
    <div {...nodeAttrs(node, { className: "vb-products" })}>
      <Head title={str(p.title)} link={p.view_all !== false ? shopLink(p) : null} />
      <div className="vb-pgrid" style={style}>
        {q.isLoading
          ? Array.from({ length: Math.min(limit, cols * 2) }, (_, i) => <div key={i} className="vb-skelcard" aria-hidden />)
          : items.map((it) => <div key={it.id} data-stagger-item><ProductCard product={it} variant={cardVariant(p)} /></div>)}
      </div>
    </div>
  );
};

const ProductRail: Renderer = ({ node, p }) => {
  const env = useRender();
  const limit = num(p.limit, 10);
  const { q } = useProductQuery(p, limit);
  const track = useRef<HTMLDivElement>(null);
  const items = q.data?.items ?? [];
  if (q.isError) return env.editing ? <div {...nodeAttrs(node)}><Notice>Products could not be loaded.</Notice></div> : null;
  if (!q.isLoading && !items.length) return env.editing ? <div {...nodeAttrs(node)}><Notice>No products match yet.</Notice></div> : null;
  const scroll = (dir: number) => {
    const el = track.current;
    if (!el) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollBy({ left: dir * el.clientWidth * 0.85, behavior: reduce ? "auto" : "smooth" });
  };
  return (
    <div {...nodeAttrs(node, { className: "vb-rail" })}>
      <div className="vb-blockhead">
        <h2 className="vb-h" data-size="lg">{str(p.title)}</h2>
        <div className="vb-rail__nav">
          <button type="button" onClick={() => scroll(-1)} aria-label="Scroll products left"><ArrowLeft size={18} /></button>
          <button type="button" onClick={() => scroll(1)} aria-label="Scroll products right"><ArrowRight size={18} /></button>
        </div>
      </div>
      <div className="vb-rail__track" ref={track} tabIndex={0} role="region" aria-label={str(p.title, "Products")}>
        {q.isLoading
          ? Array.from({ length: 5 }, (_, i) => <div key={i} className="vb-skelcard" aria-hidden />)
          : items.map((it) => <div key={it.id} className="vb-rail__item" data-stagger-item><ProductCard product={it} variant={cardVariant(p)} /></div>)}
      </div>
    </div>
  );
};

const CategoryTiles: Renderer = ({ node, p }) => {
  const env = useRender();
  const q = useQuery({ queryKey: ["categories"], queryFn: () => get<{ items: Category[] }>("/api/store/categories"), staleTime: 60_000, retry: 1 });
  let cats = q.data?.items ?? [];
  if (p.top_level_only !== false) cats = cats.filter((c) => !c.parent_id);
  cats = cats.slice(0, num(p.limit, 6));
  const style = str(p.style, "tiles");
  if (q.isError) return env.editing ? <div {...nodeAttrs(node)}><Notice>Categories could not be loaded.</Notice></div> : null;
  if (!q.isLoading && !cats.length) return env.editing ? <div {...nodeAttrs(node)}><Head title={str(p.title)} /><Notice>No categories yet. Create categories in Catalog to fill this block.</Notice></div> : null;
  return (
    <div {...nodeAttrs(node, { className: "vb-cats" })} data-style={style}>
      <Head title={str(p.title)} link={null} />
      <ul>
        {q.isLoading
          ? Array.from({ length: 4 }, (_, i) => <li key={i} className="vb-skelcard" aria-hidden />)
          : cats.map((c, i) => (
              <li key={c.id} data-stagger-item>
                <Link to={`/c/${encodeURIComponent(c.slug)}`} className="vb-cat">
                  {style === "tiles" ? (
                    <span className="vb-cat__media" data-empty={!c.image_url || undefined}>
                      {c.image_url ? <img src={c.image_url} alt="" loading="lazy" /> : <span className="vb-cat__index vb-num">{String(i + 1).padStart(2, "0")}</span>}
                    </span>
                  ) : null}
                  <span className="vb-cat__name">{c.name}</span>
                  {p.show_counts !== false ? <span className="vb-cat__count vb-num">{c.product_count}</span> : null}
                  {style !== "chips" ? <ArrowUpRight className="vb-cat__arrow" size={18} aria-hidden /> : null}
                </Link>
              </li>
            ))}
      </ul>
    </div>
  );
};

const FeaturedProduct: Renderer = ({ node, p }) => {
  const env = useRender();
  const slug = str(p.product);
  const q = useQuery({ queryKey: ["product", slug], enabled: !!slug, queryFn: () => get<ProductDetail>(`/api/store/products/${encodeURIComponent(slug)}`), retry: false, staleTime: 20_000 });
  if (!slug || q.isError) return env.editing ? <div {...nodeAttrs(node)}><Notice>{slug ? `Product “${slug}” is not available (draft, archived or missing).` : "Choose a product in the inspector."}</Notice></div> : null;
  const d = q.data;
  const prices = d?.variants.map((v) => v.price_minor) ?? [];
  const img = d?.images[0];
  return (
    <article {...nodeAttrs(node, { className: "vb-feature" })} data-layout={str(p.layout, "media-start")}>
      <div className="vb-feature__media">{img ? <Picture img={{ url: img.url, alt: img.alt || d?.title }} ratio="4:5" /> : <div className="vb-skelcard" />}</div>
      <div className="vb-feature__text">
        {str(p.eyebrow) ? <p className="vb-eyebrow">{str(p.eyebrow)}</p> : null}
        <h2 className="vb-h" data-size="xl">{d?.title ?? "…"}</h2>
        {str(p.blurb) ? <p className="vb-feature__blurb">{str(p.blurb)}</p> : null}
        {prices.length ? <p className="vb-feature__price vb-num">{prices.length > 1 && Math.min(...prices) !== Math.max(...prices) ? "From " : ""}{formatMoney(Math.min(...prices), d!.currency)}</p> : null}
        {d ? <Link className="vb-btn vb-btn--primary vb-btn--lg" to={`/p/${encodeURIComponent(d.slug)}`}>View product</Link> : null}
      </div>
    </article>
  );
};

export const COMMERCE: Record<string, Renderer> = {
  "product-grid": ProductGrid,
  "product-rail": ProductRail,
  "category-tiles": CategoryTiles,
  "featured-product": FeaturedProduct,
};

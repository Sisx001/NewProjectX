import { lazy, Suspense, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeftRight, Eye, Heart } from "lucide-react";
import { formatMoney } from "@/lib/money";
import { useCompare, useWishlist } from "../lib/local";
import { discountPct } from "../lib/helpers";
import { useTheme } from "../theme";
import type { ProductCardData, ProductCardVariant } from "../types";

const QuickView = lazy(() => import("./QuickView"));
const VARIANTS: ProductCardVariant[] = ["standard", "minimal", "editorial", "dense", "overlay"];

export function ProductCard({ product, variant }: { product: ProductCardData; variant?: ProductCardVariant }) {
  const theme = useTheme();
  const themed = theme.components["product-card"]?.variant as ProductCardVariant | undefined;
  const v: ProductCardVariant = variant ?? (themed && VARIANTS.includes(themed) ? themed : "standard");
  const wish = useWishlist();
  const cmp = useCompare();
  const [qv, setQv] = useState(false);
  const sale = product.compare_at_minor !== null && product.compare_at_minor > product.price_minor;
  const wished = wish.has(product.slug);
  const compared = cmp.has(product.slug);
  const ranged = !!product.price_max_minor && product.price_max_minor > product.price_minor;
  return (
    <article className="s-card" data-variant={v} data-testid="product-card">
      <div className="s-card__media">
        <Link to={`/p/${product.slug}`} className="s-card__img" tabIndex={-1} aria-hidden="true">
          {product.image ? <img src={product.image.url} alt="" loading="lazy" decoding="async" width={600} height={750} /> : <span className="s-card__noimg">{product.title.slice(0, 1)}</span>}
        </Link>
        <div className="s-card__badges">
          {!product.in_stock ? <span className="s-badge s-badge--out">Sold out</span> : sale ? <span className="s-badge s-badge--sale">Sale −{discountPct(product.price_minor, product.compare_at_minor)}%</span> : null}
          {product.is_demo ? <span className="s-badge s-badge--demo" title="Demo product, not a real item">Demo</span> : null}
        </div>
        <div className="s-card__tools">
          <button type="button" aria-pressed={wished} aria-label={wished ? `Remove ${product.title} from wishlist` : `Save ${product.title} to wishlist`} onClick={() => wish.toggle(product.slug)}>
            <Heart size={18} fill={wished ? "currentColor" : "none"} />
          </button>
          <button type="button" aria-pressed={compared} aria-label={compared ? `Remove ${product.title} from compare` : `Compare ${product.title}`} onClick={() => cmp.toggle(product.slug)}>
            <ArrowLeftRight size={18} />
          </button>
          <button type="button" aria-label={`Quick view ${product.title}`} onClick={() => setQv(true)} className="s-card__qv">
            <Eye size={18} />
          </button>
        </div>
      </div>
      <div className="s-card__body">
        {product.brand ? <p className="s-card__brand">{product.brand.name}</p> : null}
        <h3 className="s-card__title"><Link to={`/p/${product.slug}`}>{product.title}</Link></h3>
        <p className="s-card__price">
          {ranged ? <span className="s-price__from">From </span> : null}
          <strong>{formatMoney(product.price_minor, product.currency)}</strong>
          {sale && !ranged ? <s>{formatMoney(product.compare_at_minor, product.currency)}</s> : null}
        </p>
      </div>
      {qv ? <Suspense fallback={null}><QuickView slug={product.slug} onClose={() => setQv(false)} /></Suspense> : null}
    </article>
  );
}

export function ProductGrid({ items, variant }: { items: ProductCardData[]; variant?: ProductCardVariant }) {
  return (
    <div className="s-grid">
      {items.map((p) => <ProductCard key={p.id} product={p} variant={variant} />)}
    </div>
  );
}

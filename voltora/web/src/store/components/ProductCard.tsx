import { Link } from "react-router-dom";
import { formatMoney } from "@/lib/money";
import type { ProductCardData, ProductCardVariant } from "../types";

/** Interim implementation; the storefront workstream owns and replaces this file (keep the props contract). */
export function ProductCard({ product, variant = "standard" }: { product: ProductCardData; variant?: ProductCardVariant }) {
  return (
    <article data-variant={variant}>
      <Link to={`/p/${product.slug}`}>
        {product.image ? <img src={product.image.url} alt={product.image.alt} loading="lazy" /> : null}
        <h3>{product.title}</h3>
      </Link>
      <p>{formatMoney(product.price_minor, product.currency)}</p>
    </article>
  );
}

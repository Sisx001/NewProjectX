import { Link } from "react-router-dom";
import { Dialog, Skeleton, Alert } from "@/ui";
import { useProduct } from "../lib/data";
import { BuyBox, useBuyBox } from "./BuyBox";
import type { ProductDetail } from "../types";

function Body({ product, onClose }: { product: ProductDetail; onClose: () => void }) {
  const box = useBuyBox(product);
  const img = product.images.find((i) => box.variant && i.variant_ids?.includes(box.variant.id)) ?? product.images[0];
  return (
    <div className="s-qv s-scope">
      <div className="s-qv__img">{img ? <img src={img.url} alt={img.alt} /> : null}</div>
      <div>
        <BuyBox product={product} box={box} />
        <Link className="s-link" to={`/p/${product.slug}`} onClick={onClose}>View full details</Link>
      </div>
    </div>
  );
}

export default function QuickView({ slug, onClose }: { slug: string; onClose: () => void }) {
  const q = useProduct(slug);
  return (
    <Dialog open onClose={onClose} wide title={q.data?.title ?? "Quick view"}>
      {q.isLoading ? <Skeleton h={260} /> : q.error || !q.data ? <Alert tone="danger">This product couldn't be loaded.</Alert> : <Body product={q.data} onClose={onClose} />}
    </Dialog>
  );
}

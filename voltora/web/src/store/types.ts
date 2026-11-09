/** Shared storefront types mirroring docs/API_CONTRACTS.md. Owned by the storefront workstream; extend additively. */
export type Img = { url: string; alt: string };

export type ProductCardData = {
  id: string;
  slug: string;
  title: string;
  image: Img | null;
  images_count?: number;
  price_minor: number;
  price_max_minor?: number;
  compare_at_minor: number | null;
  currency: string;
  in_stock: boolean;
  variant_count: number;
  brand: { name: string; slug: string } | null;
  is_demo?: boolean;
};

export type ProductCardVariant = "standard" | "minimal" | "editorial" | "dense" | "overlay";

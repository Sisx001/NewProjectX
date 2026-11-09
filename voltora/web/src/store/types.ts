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

export type Variant = {
  id: string;
  sku: string;
  title: string;
  option_values: Record<string, string> | { name: string; value: string }[];
  price_minor: number;
  compare_at_minor: number | null;
  in_stock: boolean;
  available: number | null;
  allow_backorder: boolean;
};
export type ProductDetail = {
  id: string;
  slug: string;
  title: string;
  description: string;
  images: { url: string; alt: string; variant_ids?: string[] }[];
  options: { name: string; values: string[] }[];
  variants: Variant[];
  brand: { name: string; slug: string } | null;
  categories: { name: string; slug: string }[];
  seo?: { title?: string; description?: string };
  attributes?: Record<string, unknown> | { name: string; value: string }[];
  is_demo?: boolean;
  currency: string;
};
export type Facets = {
  brands: { slug: string; name: string; count: number }[];
  categories: { slug: string; name: string; count: number }[];
  price: { min: number; max: number } | null;
  options: Record<string, { value: string; count: number }[]>;
};
export type ProductList = { items: ProductCardData[]; total: number; page: number; page_size: number; facets?: Facets };
export type Category = { id: string; name: string; slug: string; parent_id: string | null; image_url: string | null; product_count: number };

export type CartLine = {
  id: string;
  variant_id: string;
  product_slug: string;
  title: string;
  variant_title: string | null;
  image: Img | null;
  quantity: number;
  unit_price_minor: number;
  compare_at_minor: number | null;
  line_total_minor: number;
  available: number | null;
  issues: string[];
};
export type Cart = {
  id: string;
  currency: string;
  item_count: number;
  lines: CartLine[];
  subtotal_minor: number;
  discount_minor: number;
  discount: { code: string; description: string } | null;
  issues: string[];
};

export type Totals = { subtotal_minor: number; discount_minor: number; shipping_minor: number; payment_fee_minor: number; tax_minor: number; total_minor: number };
export type Quote = {
  shipping_options: { id: string; label: string; amount_minor: number; eta_label: string; cod_allowed: boolean }[];
  payment_methods: { key: string; label: string; kind: "cod" | "redirect" | "manual"; description: string; fee_minor: number; available: boolean; unavailable_reason: string | null; sandbox: boolean; instructions?: string | null }[];
  totals: Totals;
  currency: string;
};
export type Division = { name: string; bn_name: string; districts: { name: string; bn_name: string; upazilas: { name: string; bn_name: string }[] }[] };
export type Address = { line1: string; line2?: string; area?: string; upazila: string; district: string; division: string; postal_code?: string };
export type Next = { type: "redirect"; url: string } | { type: "done" };
export type Order = {
  number: string;
  status: string;
  payment_status: string;
  fulfillment_status: string;
  placed_at: string;
  currency: string;
  lines: { title?: string; variant_title?: string | null; quantity: number; unit_price_minor?: number; line_total_minor?: number; image?: Img | null; product_slug?: string; variant_id?: string }[];
  totals: Totals;
  shipping_address: Partial<Address> & { name?: string; phone?: string };
  payment: { method: string; label: string; status: string; can_retry: boolean };
  timeline: { kind: string; message: string; at: string }[];
  shipments: { courier: string; tracking_code: string | null; status: string; events: { status: string; message: string; at: string }[] }[];
};
export type StorefrontConfig = { store: { name: string; currency: string; locale: string; timezone: string; environment: string }; settings: Record<string, unknown>; payments_mode?: string };

# Storefront API contracts (v1)

Shared by the catalog, commerce, design and storefront workstreams. Implement exactly; extend only additively. All money fields are integer minor units; `currency` accompanies totals.

## Catalog (owner: catalog workstream)

`GET /api/store/categories` → `{ items: [{ id, name, slug, parent_id, image_url, product_count }] }`

`GET /api/store/products?q=&category=<slug>&brand=<slug>&min=&max=&in_stock=1&option.<Name>=<value>&sort=newest|price_asc|price_desc|title&page=1&page_size=24`
→ `{ items: ProductCard[], total, page, page_size, facets: { brands: [{slug,name,count}], categories: [{slug,name,count}], price: {min,max}, options: { [name]: [{value,count}] } } }`

`ProductCard = { id, slug, title, image: {url, alt} | null, images_count, price_minor, price_max_minor, compare_at_minor | null, currency, in_stock, variant_count, brand: {name, slug} | null, is_demo }`
(Only factual badges may be derived client-side: "Sale" when `compare_at_minor > price_minor`, "Sold out" when `!in_stock`.)

`GET /api/store/products/{slug}` → `{ id, slug, title, description, images: [{url, alt, variant_ids}], options: [{name, values}], variants: [{ id, sku, title, option_values, price_minor, compare_at_minor, in_stock, available /* null if not tracked */, allow_backorder }], brand, categories: [{name, slug}], seo: {title, description}, attributes, is_demo, currency }` — 404 for draft/archived unless staff preview.

`GET /api/store/search/suggest?q=` → `{ products: [{slug, title, image, price_minor}], categories: [{slug, name}] }`

## Locations & shipping (owner: commerce workstream)

`GET /api/store/locations` → `{ divisions: [{ name, bn_name, districts: [{ name, bn_name, upazilas: [{ name, bn_name }] }] }] }` (cacheable).

## Cart (owner: commerce workstream)

Cart is bound to an HttpOnly cookie `vt_cart` (and to the customer when signed in; guest cart merges on sign-in).

* `GET /api/store/cart` → `Cart`
* `POST /api/store/cart/lines {variant_id, quantity}` → `Cart` (adds/increments; validates stock, returns 409 `insufficient_stock` with `details.available`)
* `PATCH /api/store/cart/lines/{line_id} {quantity}` → `Cart` (0 removes)
* `DELETE /api/store/cart/lines/{line_id}` → `Cart`
* `POST /api/store/cart/discount {code}` → `Cart` (400 `discount_invalid` with human message) ; `DELETE /api/store/cart/discount` → `Cart`

`Cart = { id, currency, item_count, lines: [{ id, variant_id, product_slug, title, variant_title, image, quantity, unit_price_minor, compare_at_minor, line_total_minor, available, issues: string[] }], subtotal_minor, discount_minor, discount: {code, description} | null, issues: string[] }`

## Checkout (owner: commerce workstream)

`POST /api/store/checkout/quote { address: {district, upazila}, payment_method? }` →
`{ shipping_options: [{ id, label, amount_minor, eta_label, cod_allowed }], payment_methods: [{ key, label, kind: "cod"|"redirect"|"manual", description, fee_minor, available, unavailable_reason, sandbox }], totals: { subtotal_minor, discount_minor, shipping_minor, payment_fee_minor, tax_minor, total_minor }, currency }`

`POST /api/store/checkout { idempotency_key, email, phone, name, address: {line1, line2, area, upazila, district, division, postal_code}, shipping_option_id, payment_method, note, save_address, accept_terms: true }` →
`{ order: { number, access_token, status, payment_status, total_minor, currency }, next: { type: "redirect", url } | { type: "done" } }`
Server recomputes every price, discount, fee and stock reservation; client totals are ignored. Replaying the same idempotency key returns the same order.

`GET /api/store/orders/{number}?token=<access_token>` (or signed-in owner) →
`{ number, status, payment_status, fulfillment_status, placed_at, currency, lines: [...], totals: {...}, shipping_address, payment: { method, label, status, can_retry }, timeline: [{ kind, message, at }], shipments: [{ courier, tracking_code, status, events: [{status, message, at}] }] }`

`POST /api/store/orders/{number}/pay?token=` → `{ next }` (retry an online payment when allowed).

`GET /api/store/track?number=&phone=` → limited public tracking (status + shipment events, no PII).

## Payments sandbox (owner: commerce workstream)

The built-in **VOLTORA Sandbox Gateway** simulates a hosted payment page for development. It is always labelled as a simulation.
* `GET /api/payments/sandbox/{intent_id}` → `{ intent_id, amount_minor, currency, order_number, merchant, status }`
* `POST /api/payments/sandbox/{intent_id}/complete { outcome: "success"|"fail"|"cancel" }` → `{ redirect_url }`. The server then delivers an HMAC-signed webhook to `/api/payments/webhooks/sandbox`; the order is only marked paid when that signed event is verified (never from the redirect).
* Storefront route `/pay/sandbox/:intentId` renders that page.

## Customer account (owner: commerce workstream API, storefront workstream UI)

* `GET /api/account/orders` → `{ items: [{ number, placed_at, status, payment_status, total_minor, currency, item_count }] }`
* `GET /api/account/orders/{number}` → same shape as the public order endpoint
* `GET|POST /api/account/addresses`, `PATCH|DELETE /api/account/addresses/{id}`
* `GET|PATCH /api/account/profile { name, phone, marketing_consent }`
* Auth endpoints: `/api/auth/register|login|logout|me|password/forgot|password/reset` (exist).

## Design / content (owner: design workstream)

* `GET /api/store/theme` → `{ id, name, version, family, tokens: {...}, components: { "product-card": {variant, ...}, ... }, globals: { header: Node, footer: Node, announcement: Node | null } }`
* `GET /api/store/pages/{slug}` → `{ slug, title, seo, document: PageDocument, revision }` (published only; drafts via `/api/content/preview/...` for staff)
* `PageDocument = { schemaVersion: 1, root: Node }`, `Node = { id, type, props, style?: { base?, tablet?, mobile? }, visibility?, motion?, children?: Node[] }`

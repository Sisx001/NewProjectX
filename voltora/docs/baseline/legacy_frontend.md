# Legacy VOLTORA frontend audit (source-evidenced)

Root: `/tmp/handoff/src/frontend`. Paths below are relative to `src/` unless noted. Read-only audit; nothing was installed, built or run.

Scale: 233 files; about 10.9k lines of non-UI TS/TSX written as very long, dense lines (many 1–3k chars). 43 test files (all `lib/*.test.ts` plus `components/Resilience.test.tsx`). `tsconfig.json` has `"strict":false`; about 1,670 `: any`/`as any` outside `components/ui`.

**Build blocker:** the handoff ships no `craco.config.js`, no `jsconfig.json`, no lockfile and no `deploy/.env.example`. Yet `package.json:71-75` runs `craco start|build|test`, and `index.js:9-10` imports `@/index.css` and `@/App` (tsconfig `baseUrl:"src"` does not define an `@/` alias). The legacy frontend is not buildable from the handoff as shipped.

## 1. Route table (`App.tsx`)
`AppRoutes` is at App.tsx:158+.
- `/install` bypasses store-config gating (App.tsx:164-166).
- Workspace routes wait for `authReady` (Splash) and render only when `user.role!=='customer'` (`staff`, App.tsx:168-172). Everyone else gets the storefront 404, with no login page or hint (comment at :169-171).
- `AdminShell` additionally requires `user.mfa_verified`, else shows `MfaPanel` (AdminShell.tsx ~l.33).
- Nav is filtered by `user.permissions` (AdminShell.tsx:17-22, `groups`). The routes themselves are NOT permission-guarded, so a staff user who types a URL renders the page and relies on the backend to refuse.
- Admin base path comes from `REACT_APP_ADMIN_PATH` or a hard-coded default `/w-k4q9v2mx` (lib/adminPath.ts:5). `.env.example` says `/admin`, which is inconsistent. It can be overridden at runtime from `settings.workspace_path`. The file's own comment admits the hidden URL is not a security control.

Storefront routes (`storeRoutes`, App.tsx:82-83; all eager-imported, not lazy; inside `StoreLayout`):

| path | component | notes |
|---|---|---|
| `/` | `Home` (StorePages.tsx:35) | `SectionRenderer` over `config.home` |
| `/categories` | `Categories` (:230) | |
| `/shop`, `/shop/:category`, `/brand/:brand` | `Shop` (:135) | `CategoryHero` :40, `BrandHero` :64 |
| `/product/:slug` | `ProductDetail` (:259) | |
| `/wishlist`, `/compare` | `SavedProducts` (:310, `comparison` prop) | compare via `?ids=` |
| `/cart`, `/checkout`, `/order/:id` | `Cart`, `Checkout`, `Order` (pages/Checkout.tsx:25,54,168) | order access token in `?access=` |
| `/order/:id/invoice`, `/order/:id/packing-slip` | `Invoice`, `PackingSlip` | printable |
| `/find-order`, `/gift-card`, `/affiliates` | `FindOrder`, `GiftCardCheck`, `Affiliates` (StorePages:443,337,366) | |
| `/account` | `Account` (pages/Account.tsx:45) | no route guard; handles logged-out state itself |
| `/support`, `/assistant` | `Support`, `Assistant` | |
| `/pages/:slug` | `ContentPage` (:472) | CMS pages |
| `/login`, `/signup`, `/forgot-password` | `CustomerAuth` (`initial` prop) | App.tsx:176 |
| `*` | `NotFound` | |
| `/offer/:code` | `OfferPage` | outside `StoreLayout` (App.tsx:180) |
| `/install` | `InstallWizard` (lazy) | handled before `Routes` (:166) |

Workspace routes (App.tsx:181-196; every page plus `AdminShell` is lazy via `lazyNamed`, App.tsx:31):
- index → `Overview`.
- Explicit routes: `themes`→ThemeGallery, `localization`→MarketSettings, `setup`→SetupGuide, `website`→VisualBuilder (no store boundary), `releases`→ReleaseHistory, `ai`→MiraWorkspace, `ai-connection`→AIStudio.
- Redirects: `pages`→`website?select=page:home`, `layout`→`website?select=footer`.
- The rest come from `adminRoutes` (App.tsx:84): mail, products, categories/brands/collections (`Taxonomies kind=`), inventory, purchasing, locations, reviews, orders, returns, loyalty, customers, coupons, gift-cards, offers, affiliates, audience, email-templates, connections (redirects to `keys`), keys, diagnostics, api, payments/shipping/navigation/features/settings (all `StoreSettings section=`), media, couriers, oauth, analytics, monitor, support, whatsapp, team, roles, audit, security.
- Dead entry: `localization` in `adminRoutes` (`StoreSettings section="localization"`) is filtered out at App.tsx:193 and replaced by `MarketSettings`, which embeds `StoreSettings` (MarketSettings.tsx:6).

`StoreLayout` (App.tsx ~100-148):
- Accessibility and motion: skip link, focus moved to `#main-content` on route change, scroll-reveal observer with reduced-motion and fail-open timer.
- SEO: meta/robots/favicon/theme-color via `lib/seo`.
- Modes: `?preview` draft banner (30 min), maintenance/coming-soon gate from `settings.mode`.
- Chrome: floating AI link, floating compare chip, `Consent`, `HelpDock`, `MiraChat`, `CartDrawer`, `QuickView`, `OfflineNotice`, `ErrorBoundary`.

## 2. Storefront pages/components → backend endpoints
Transport (`lib/api.ts`):
- Axios instance `api` with `withCredentials`, 25 s timeout and an `X-CSRF-Token` header via `setCsrf`.
- `get` retries twice on 502/503/504 (:27).
- `API_ROOT` comes from `REACT_APP_BACKEND_URL` plus `/api`.
- Errors go through `serverText` and `refusals.json` (Bangla/English).

Global state is a hand-rolled `StoreProvider` (`lib/store.tsx`, 95 long lines):
- `GET /store?…` (preview / theme_preview / builder_preview / appearance) and `/store/translations?lang=`.
- `/auth/session` and `POST /auth/logout`.
- `GET|PUT /cart`, `GET|PUT /customer/wishlist`.
- It also sets `document.documentElement.lang` (store.tsx:56).

Per page:
- **Home/Sections** (`components/store/Sections.tsx`): `GET /products?limit=&category=&sort=newest` or `?ids=`.
- **Shop/Category/Brand**: `/products?q&category&brand&min_price&max_price&in_stock&sort&variant…`; `ShopRecovery` for empty search (:95); pager (`lib/paging.ts`).
- **ProductDetail**: `/products/:slug`, reviews (POST/DELETE `/products/…`, StorePages.tsx:275-279), `ProductQuestions` (GET/POST `/products/…`), `PriceAlert` (POST `/products/:id/price-alert`), `StockAlert` (POST `/products/:id/stock-alert`), `RecentlyViewed`, `DeliveryPromise`, instalment quote, variant picker, preorder.
- **Header** (11 layouts): search suggestions `GET /products?q=&limit=5` plus recent searches; notifications `/notifications*`; currency and language selects; mega category menu; theme-mode toggle; mobile bottom nav.
- **Cart/CartDrawer**: `GET /products?ids=` (reconcile against live price/stock), `POST /cart/quote`, free-delivery progress, save-for-later, `DeliveryPromise` (scope `bag`).
- **Checkout** (Checkout.tsx:54-153): 3 steps address/shipping/payment (:87). Saved addresses, structured or manual location, coupon, gift card, gift wrap and message, loyalty points redeem, store credit, bundle/tier discounts, marketing consent, terms. `POST /checkout` carries an `Idempotency-Key` header and follows `payment.redirect_url` (:113). The order page uses `/orders/:id` plus `/shipment`, `/payment/verify|retry`, `/bank-reference`, `/claim`, `/cancel`, `/return`, all with `?access=`; the token is kept in `sessionStorage` as `voltora-order-<id>`. Totals are quoted server-side via `/cart/quote`.
- **Account**: `/customer/orders|wallet|returns|privacy`, `/auth/profile`, `/auth/profile-extended`, `/auth/password/change`, MFA enroll/verify/disable/recovery, email verify, sessions list/revoke (`DELETE /auth/sessions/(all)`), login history, notifications.
- **CustomerAuth**: login/signup, password forgot/reset, OTP send/login, MFA verify, social providers (labels :15).
- **Support/HelpDock**: `/support/conversations` (create, list, get, messages, typing, reopen, attachments), `?access=` token, support-hours aware.
- **Assistant/MiraChat**: `GET /assistant/prompts?lang=`; streamed `fetch(API_ROOT+'/assistant/chat')`.
- **Misc**: `POST /orders/lookup`; `POST /gift-cards/lookup`; `POST /affiliate/(status|apply)`; `POST /newsletter`; `/offer/:code(/track|/checkout)`; `/locations/(tree|search|nearest|resolve|districts/:id/children|upazilas/:id/unions)`; install wizard `/install/(status|check-database|complete)`; `track()` analytics (lib/analytics.ts); referral/UTM capture (lib/referral.ts).

Worth preserving (UX):
- Delivery promise before the bag (`DeliveryPromise.tsx`, `lib/delivery.ts`).
- 5-level BD location picker: division→district→upazila→union→area, free-text search, geolocation "nearest", smart resolve, postcode autofill, live-region announcements (`LocationPicker.tsx:14-60`, `lib/locationCascade.ts`).
- Quick view with zoom, qty and buy-now.
- Compare (max 4, shareable `?ids=`) and wishlist merged across devices.
- "Buy again", price-drop and back-in-stock alerts, pre-order with per-order limit, instalment quote.
- Gift cards, loyalty points/credit/tiers.
- Idempotent checkout, private order access tokens, bag reconciliation against the live catalog.
- Bangla/English UI with owner `*_bn` fields; display-currency vs charge-currency honesty (`lib/fx.ts`).
- Recently viewed and recent searches, printable invoice/packing slip, maintenance/coming-soon modes, draft preview with expiry, offer landing pages with referral, JSON-LD and prerendered heads.

## 3. Admin pages (`pages/admin/*`): purpose and endpoints (all under `/admin/*`)
- **Overview**: KPIs, `GET /admin/overview`.
- **SetupGuide**: launch checklist; `/admin/setup-guide`, `/admin/settings`, `/admin/builder(/publish)`, `/admin/themes(/apply-industry)`, `/admin/ai`.
- **Catalog.tsx** (166 L, 41 KB): `AdminProducts` (`/admin/products(/bulk)`, import/export, media, `/admin/inventory/adjust`) and `Taxonomies` (`/admin/catalog/*`).
- **Operations.tsx** (415 L): `Inventory`, `AdminOrders`, `Coupons`, `Customers` (ban/unban/timeline), `Reviews` (plus questions, privacy). Endpoints: `/admin/inventory(/adjust)`, `/admin/orders/`, `/admin/coupons`, `/admin/customers`, `/admin/reviews`, `/admin/questions`, `/admin/privacy`, `/admin/shipments`, `/admin/providers/courier`, `/admin/loyalty/adjust`, `/admin/abandoned`, exports.
- **Purchasing**: `/admin/suppliers`, `/admin/purchase-orders(/:id/:action)`, `/admin/purchasing`.
- **Returns** `/admin/returns`; **Loyalty** `/admin/loyalty`; **GiftCards** `/admin/gift-cards`; **Offers** `/admin/offers`; **Affiliates** `/admin/affiliates` (+export); **Audience** `/admin/audience` (+export).
- **Locations** `/admin/locations(/import)`; **Couriers** `/admin/shipments`; **MarketSettings** `/admin/market` (embeds StoreSettings).
- **Settings.tsx** (65 L, 44 KB): one component for general/features/payments/shipping/navigation/localization via `section` prop. Endpoints: `/admin/settings`, `/admin/payment-settings`, `/admin/providers/payment`, `/admin/feeds`, `/admin/support/channels`, `/admin/diagnostics`, `/locations/tree`.
- **Providers.tsx / ProviderConfigurationPanel.tsx**: adapter config `/admin/providers/:kind?environment=`; `TransactionsLog` `GET /admin/transactions`.
- **ServiceKeys** `/admin/service-keys?environment=`; **Connections** is a redirect to `keys`; **OAuth** `/admin/oauth`.
- **Ops.tsx**: `Diagnostics`, `ApiSurface` (`/admin/diagnostics/api-surface`), `EmailTemplates` (+queue, theme, test-send).
- **Analytics** `/admin/analytics?days=` (+sales/tax exports, `/admin/tax-summary`); **Monitor** `/admin/monitor`.
- **Mailbox** `/admin/mail/*`; **WhatsApp** `/admin/whatsapp(/accounts)`.
- **Workspace.tsx** (247 L, 69 KB, 9 exports): `MediaLibrary`, `SupportInbox` (canned replies, departments, rules, tags, analytics), `Team`, `RoleBuilder`, `Audit`, `AIStudio`, `AIAssistantPanel`, `AIApprovals`, `AIMonitor`.
- **Security** `/admin/security(/risk,/sessions)`, `/admin/alerts(/settings)`.
- **MiraWorkspace / OwnerAI / MonyChat**: AI brain (`/admin/mira`, `/admin/mony`, `/admin/ai/memory`); `MonyChat` floats on every admin screen.
- **Components**: `CommandPalette` (⌘K, static route list via `lib/commandSearch.ts`), `MediaField`, `BrainTabs`.

### Duplicated settings / overlapping controls
1. **Navigation/footer**: Settings `navigation` edits main nav, footer links and footer details (Settings.tsx:63, `links('navigation')`, `links('footer_links')`). The builder edits `site.header.show_nav` and `site.footer.columns|copyright|style` (inputs `footer-copyright`, `header-nav-visible`). Two sources of truth.
2. **Auth page styling**: Settings general "Login / signup page" (Settings.tsx:64, `auth-bg-image`) vs builder `site.auth.*` (side_image, form_title, button_label, layout).
3. **Brand/theme**: Settings "Brand & identity" vs the builder `theme` panel vs ThemeGallery "Customize current theme", which redirects to `website?select=theme`.
4. **Providers/keys/payments/couriers**: Settings `payments` (Settings.tsx:61, per-method cards with embedded `ProviderConfig`), `ServiceKeys`, `Connections` (redirect), `Couriers`, Operations (`/admin/providers/courier`), `TransactionsLog`. `deploy/README.md:6` still says to use "Admin → Connections".
5. **Currency/language**: `MarketSettings` (`/admin/market`) vs Settings localization (currencies, language, search terms; Settings.tsx:64).
6. **Same endpoint from two pages**: `/admin/inventory/adjust` in Catalog.tsx and Operations.tsx; `/admin/loyalty/adjust` in Operations and the Loyalty page; `/admin/shipments` in Operations and Couriers.
7. **Discounts**: `coupons` (nav "Discounts"), `offers`, `gift-cards`, loyalty tiers, affiliate codes, bundle discounts: five engines on five pages, no unified promotions model.
8. **AI**: nav `ai` (MiraWorkspace), `ai-connection` (AIStudio), floating `MonyChat` (`/admin/mony`), OwnerAI memory, storefront `MiraChat` and `/assistant`: three names (Mira/Mony/AI Studio) for overlapping concepts.
9. **Feature toggles**: Settings `features` (`featureInfo`, Settings.tsx:17) vs `site.checkout.show_coupon` in the builder (overlaps `features.coupons`).
10. **Sidebar**: "Store settings", "Navigation & links", "Features & toggles", "Payments", "Shipping" and "Market & language" are 6 nav entries over the same `StoreSettings` component (AdminShell.tsx:17-22; App.tsx:84).

### Fake / hardcoded / misleading
- Overview.tsx:41: hard-coded "1 / 4" setup counter, and the "catalog" step is hard-coded `done=true`.
- Overview.tsx:34: hard-coded "Isolated demonstration · real workspace, sample catalog". Also "No charges or messages leave this demo" and the fixed hero art `/api/media/e157…` (Overview.tsx:11, CustomerAuth.tsx:12).
- AdminShell.tsx:37-38: workspace name hard-coded "VOLTORA Store"; the topbar "Sandbox" pill and the footer "Isolated environment · External actions locked" are constants, not read from state.
- AdminShell.tsx:37: `initial` and `expanded` collapse logic is computed but unused, so it is dead code.
- Settings.tsx:61: payment label map includes `mock:'Test gateway'`, a mock gateway shipped in the real UI. `settings.demo_catalog` drives a demo disclosure in the Footer (Footer.tsx:35).
- Settings title claims "Real switches, connected behavior", but the storefront reads only about 21 flags: analytics, ai_customer, wishlist, store_credit, multicurrency, loyalty, multilingual, shipping, returns, pickup, location_picker, coupons, compare, buy_now, support, social_login, otp_login, reviews, questions, guest_checkout, dark_mode. Other flags listed in `featureInfo` may be inert (needs backend cross-check).
- `TransactionsLog` formats `(t.amount/100).toLocaleString()+' BDT'` (Providers.tsx), ignoring currency.
- `swr`, `dayjs`, `date-fns`, `lodash`, `next-themes`, `vaul`, `cmdk`, `embla-carousel-react` have no imports outside `components/ui`. `recharts` is used only in Analytics.tsx.
- `@tanstack/react-query` is mounted in `index.js`, but no `useQuery` call exists. Data fetching is hand-rolled `useEffect`+`get` with no cache.
- No `onClick={()=>{}}`, `href="#"` or TODO/FIXME markers found by grep.
- Admin lists have no bulk-select tables, saved views or column configuration outside Catalog.

## 4. Builder (`pages/admin/builder/*`)
Files: `VisualBuilder.tsx` (46 L), `useBuilder.ts` (26 L), `BuilderTree.tsx` (12 L), `BuilderFrame.tsx` (13 L), `BuilderInspector.tsx` (144 L, 36 KB), `ReleaseHistory.tsx`, `builder.css` (31 KB, 23 `!important`).

**Data model**: a single JSON "bundle".
- `bundle.pages[slug] = {slug,title,seo…,sections:Section[]}`.
- `bundle.site.{header,footer,checkout,auth}` and `bundle.theme` (token object).
- Server wrapper: `GET /admin/builder` → `{version, draft, active_revision, pending, changes, updated_at}`.
- `PUT /admin/builder {version, bundle}` → `{version, saved_at, pending, changes}`, with optimistic concurrency on the integer `version` (useBuilder.ts:14-17).
- `Section` (lib/types.ts:6): `id,type,title(_bn),subtitle(_bn),eyebrow,image,button(_bn),link,enabled,mobile,desktop,alignment,padding,background,color,font_size,border,limit,category,items[]`, plus `product_ids` and `items[].icon/title/text`.
- The model is flat: no nesting or children, no per-breakpoint overrides, no bindings, no conditions, no custom CSS/code.

**Section types (fixed list of 13)**: `SECTION_TYPES`, BuilderTree.tsx:5. They are hero, features, categories, products, image_text ("Editorial banner"), banner, text, image, brands, faq, cta, spacer, divider.
- The renderer is a hard-coded `if/else` chain (Sections.tsx:26-39).
- The hero has 2 render paths: `theme.hero_style` of compact/centered/panoramic uses the "structural" hero, anything else uses the campaign hero.
- `image_text` and `banner` render identically.
- `categories` and `brands` ignore their own props and read global config.
- Per-section visibility is only a desktop/mobile boolean.
- New sections default to `{title:label,link:'/shop',…}` with `id='sec-'+Date.now()` (BuilderTree:12).

**Editable pages**: fixed route list (BuilderTree:4) of home, shop, product, login, signup, forgot-password, account, cart, checkout, plus custom pages (`page:<slug>`, modal at VisualBuilder:34/45, served by `/pages/:slug`).
- Global components: header, footer, categories. The "3" count is hard-coded.
- Only home and custom pages have sections.
- Other pages expose a few knobs only:
  - checkout: coupon toggle, terms label, thank-you title;
  - header: layout (11), sticky, show nav, deal label;
  - footer: style, columns, copyright;
  - auth: layout, titles, image;
  - theme: primary with AA verdict, radius, shadow, card style, input style, announcement style, motion on/duration/easing, fonts incl. custom family/weight, letter spacing.

**Preview**: iframe of the live storefront at `?builder_preview=1` with `sandbox="allow-scripts allow-same-origin"` (BuilderFrame.tsx:20; this combination defeats the sandbox).
- The draft is pushed via `postMessage({type:'voltora-release-preview',bundle,selection,customerState})`, with a `voltora-preview-ready` handshake.
- Device widths are 390/768/1200, scaled to fit.
- "Compare live" toggle.
- `?theme_preview=&appearance=` is used for themes.
- Separate private draft link `?preview` (30 min).

**Edit/save**:
- Undo/redo 50 deep; same-path edits within 900 ms are coalesced (`lib/editPath.firstDiffPath`).
- Autosave debounce 1100 ms.
- Local recovery copy in `localStorage` `voltora-draft-recovery:<userId>`.
- Conflict detection, offline guard, `beforeunload` and click-intercept leave prompts, export draft JSON (useBuilder.ts:25).
- Layer reorder uses HTML5 drag (BuilderTree:13), with no touch or keyboard path apart from move up/down buttons in the inspector.

**Publish/rollback**:
- The "Ready for the world?" modal calls `POST /admin/builder/publish {version, summary}`. The button is disabled unless the user has `content.publish`.
- `ReleaseHistory.tsx`: `GET /admin/builder/revisions`.
- "Restore to draft" → `POST /admin/builder/restore`; "Roll back live" → `POST /admin/builder/rollback`, both with `{version, revision_id, summary}`.
- `active_revision` marks the live release.
- History covers presentation/config only, never orders or payments.

**Limitations**:
- Fixed 13 section types and a hard-coded renderer.
- No nested or reusable blocks, templates, responsive overrides (beyond hide-on-device), data bindings, conditional visibility, or motion timelines (3 theme motion tokens only).
- No structural editing of header, footer, mega menu, product card, cart or checkout steps; the header only picks a preset layout.
- Export only, no import; no per-page history; single draft per site (no staging or branches); no custom CSS/code.
- Builder UI strings are English only; `_bn` fields exist only for section text.
- Desktop-oriented editor (a `mobilePanel` state exists).

## 5. Themes
- **Presets live in the backend**, not the frontend.
  - `backend/themes.py:9 PRESETS` (list); `theme_presets()` at :223; helper functions `luma/contrast/aa_text/dark_border/on_primary/button_primary/dark_tokens` at :264-309.
  - `scripts/seed_reference_themes.py` adds 8 themes. The ones I read: slate-flow, mono-dark, calm-product, audio-forest. Industries: general×3, home×2, marketplace, electronics, accessories.
  - The frontend count is whatever `GET /admin/themes` returns. ThemeGallery shows 6 per page with industry tabs and search. I did not enumerate `PRESETS`; see the backend audit.
- `public/assets/packs/` holds 127 `.webp` demo images (7.6 MB) and no JSON. Pack JSON lives in `backend/data/packs/*.json` and is localised by `scripts/localize_pack_images.py`.
- `structural-themes.css` (2 lines, 8.8 KB) holds the `structural-hero` variants. It does not define presets.
- **Token model** (`lib/types.ts:3 Theme`): primary/secondary/accent/success; background/surface/text/muted/border plus `dark_*`; heading/body/button font; weights and heading scale; letter spacing and line height; radius, spacing, gap, width, columns; card_style, image_ratio, header_style, hero_style, footer_style, button_style, shadow_style, input_style, product_badge_style, category_style, icon_style, motion, motion_duration/easing, animation_level, loading_style, announcement_style.
- **Application**: `themeStyle()` (ThemeShell.tsx:30-50) maps a theme to about 40 CSS custom properties on a wrapper `div`. Examples are `--v-primary`, `--v-bg`, `--v-surface`, `--v-text`, `--v-muted` (AA-nudged by `aaText`), `--v-border`, `--v-heading`, `--v-radius`, `--v-space`, `--v-gap`, `--v-width`, `--v-cols`, `--v-on-primary` and `--motion-duration`.
  - Dark mode derives from `dark_*` tokens.
  - Web fonts are loaded dynamically (`lib/webfonts.ts`: 10 bundled families incl. Noto Sans Bengali, Google Fonts href, custom faces, `probeFamily`).
  - Many `*_style` values select CSS class variants in `App.css`, so part of the "theme" is CSS rather than tokens.
- **Header layouts**: 11 ids in `lib/headerLayout.ts:9-21`: classic, centered, compact, bordered, minimal, split, icons, double, mega, stacked, transparent. The class `hl-<id>` is read by `header-layouts.css` (109 lines); `transparent` degrades to `minimal` off the home hero.
- There are three parallel token vocabularies: shadcn HSL (`index.css`, Tailwind config), `--v-*` (storefront) and `--work-*` (rebuild.css:3). Tailwind 3.4 is configured, but the app is styled mostly by hand-written CSS.
- Worth porting: `lib/themeInk.ts` (WCAG contrast, `onPrimary`, `buttonPrimary`, `primaryVerdict`) and `aaText`/`mixHex`.

## 6. CSS inventory
Method: `grep -o` counts. "hex" is distinct `#rgb/#rrggbb` per file, lowercased; per-file figures overlap.

| file | bytes | lines | !important | gradients | hex | @media |
|---|---|---|---|---|---|---|
| App.css | 274,407 | 2,292 | 25 | 8 | 149 | 137 |
| admin.css | 105,653 | 699 | 6 | 4 | 314 | 53 |
| workspace.css | 82,911 | 878 | 4 | 2 | 163 | 46 |
| rebuild.css | 34,422 | 445 | 10 | 0 | 83 | 13 |
| pages/admin/builder/builder.css | 31,279 | 137 | 23 | 1 | 203 | 7 |
| theme-gallery.css | 15,858 | 56 | 0 | 0 | 33 | 6 |
| pages/admin/mira-workspace.css | 12,945 | 62 | 0 | 0 | 63 | 7 |
| structural-themes.css | 8,823 | 2 | 3 | 1 | 1 | 2 |
| components/store-recovery.css | 8,680 | 1 | 3 | 5 | 41 | 4 |
| pages/customer-auth.css | 7,665 | 37 | 12 | 1 | 23 | 5 |
| header-layouts.css | 6,701 | 109 | 0 | 1 | 5 | 3 |
| pages/admin/setup-guide.css | 6,345 | 76 | 0 | 0 | 27 | 2 |
| components/admin/owner-entry.css | 4,900 | 1 | 10 | 1 | 32 | 4 |
| pages/admin/integration-center.css | 2,310 | 2 | 0 | 0 | 11 | 1 |
| index.css | 647 | 4 | 1 | 0 | 0 | 0 |
| **total (15 files)** | about 603 KB | | **97** | **24** | **1,022 distinct overall** | |

Observations:
- Duplicated selectors across files: `.v-button` appears in App.css (70), rebuild.css (21), admin.css (23), workspace.css (11) and structural-themes.css (3). `.category-item` is in 3 files; `.section-heading` and `.campaign-hero` are in App.css and rebuild.css.
- Later files override earlier ones. Comments say so: "keeps the old rebuild-after-admin cascade" (AdminShell.tsx:11); rebuild.css header says "editorial storefront and shared furniture". `rebuild.css` is an override layer on App.css, not a replacement.
- 1,022 distinct literal hex colours versus about 40 theme tokens means theming leaks. admin.css alone has 314 distinct hex values. Workspace dark mode is a separate `.admin-dark` ruleset (51 selectors).
- Several files are minified in place (1–2 "lines" of 5–9 KB).
- Breakpoints: about 20 distinct widths, with 600px used 129 times, then 760 (23), 900 (13), 1000 (11), 850 (10), 1150 (8).
- No `prefers-color-scheme` in CSS; dark mode is JS-driven.
- `index.css:4` has a leftover `[data-debug-wrapper]` rule from the platform overlay tooling.
- Admin CSS (`admin.css` + `workspace.css`) is imported by lazy `AdminShell` (AdminShell.tsx:10-11), so storefront visitors do not load it. `InstallWizard.tsx:7` also imports `admin.css`.
- `components/ui` holds 46 shadcn files. Only `ui/sonner` is imported (App.tsx:6). The rest is dead code, along with most of the 27 `@radix-ui/*` packages.

## 7. `lib/*.ts` pure logic worth porting
Money convention: all prices are integer minor units (paisa, ×100); display divides by 100.
- `address.ts` `defaultAddressIndex(addresses)`: index of the first row with truthy `default`, else 0 if any rows, else −1.
- `delivery.ts`:
  - `zoneCovers(zone,loc)`: a zone with no `districts` or `divisions` covers everywhere; otherwise it needs `loc.district_id` (membership when districts are listed) or `loc.division_id`.
  - `needsDistrict(shipping)`: true if any enabled zone is keyed by district or division.
  - `deliveryOptions(shipping,features,loc)`: enabled zones only; `pickup` is gated by `features.pickup!==false`, the rest by `features.shipping!==false`; filtered by `zoneCovers` when `loc` is given; sorted paid-before-free, then fee ascending, then name.
  - `zoneFee(zone,subtotal)`: 0 if `free_above>0 && subtotal>=free_above`, else `fee`.
  - `promiseZone`: first of `deliveryOptions(...,null)`.
  - `promiseNote(zone,subtotal,money,t)`: `"<name> · <fee|Free> · <estimate>"`.
  - `freeThreshold`: minimum positive `free_above` over enabled zones (0 means hide the line).
- `installments.ts` `installmentPlan(price,cfg)`: returns [] unless `cfg.enabled`, price > 0 and price ≥ `min_price`. Months are unique integers in [2,36], sorted. `monthly = ceil(price/months)`. `numberText` uses `Intl.NumberFormat('bn-BD'|'en-BD')`.
- `variants.ts`:
  - `variantIndex(variants,value)`: match by id, then SKU, case-insensitive; −1 if none.
  - `pickIndex(variants,current,key,value)`: first variant with `options[key]===value` that holds the other selected axes, else the first carrying that value.
  - `buyableIndex(product)`: first variant with stock > 0, else 0.
  - `shelfIndex(product)`: cheapest in-stock variant, −1 if none.
  - `priceSpans(variants)`: true when more than one variant and min ≠ max price (card prints "From").
- `priceBand.ts`:
  - `displayRate(settings,currency)`: 1 unless `features.multicurrency`; otherwise the rate of the enabled picked currency, else the base currency.
  - `toMinor(text,rate)` = round(n/rate×100), 0 on invalid.
  - `toDisplay(minor,rate)` = round(minor/100×rate, 2 dp), as a string.
  - `priceBand(min,max,rate)`: swaps if lo > hi, returns minor strings ('' when 0).
- `cart.ts` (133 L):
  - `bagCap(line)`: stock or preorder limit, else 50.
  - `saveForLaterLine`.
  - `reconcileBag(cart,products)`: reprice, clamp or drop against the live catalog, with a notice.
  - `mergeBag(server,local)`: server wins per variant, local-only lines are kept.
  - `buyNowLine(bag,line,cap)`.
  - `preorderLimit(product)`: requires enabled and `ships_at`; limit 1..50, default 50.
  - `reorderLines` and `wishlistLines` ("buy again" merges clamped to stock and names missing variants), plus notices.
- `wishlist.ts` `mergeWishlist(server,local,base)`: three-way merge. An id is kept as `inServer==inBase ? inLocal : inServer`, so local edits win only when the server is unchanged since `base`. The sync record is keyed per user.
- `compare.ts` `parseCompareIds`: dedupe, trim, cap `COMPARE_MAX=4`.
- `paging.ts` `lastPage`: clamp.
- `brands.ts` / `categories.ts` `stocked*`: keep rows with `count>0`.
- `fx.ts` `referenceCurrency`: non-base display currency when multicurrency is on.
- `margin.ts` `marginPercent(price,cost)` = round((p−c)/p×100), null when there is no cost or price ≤ 0.
- `coupon.ts`: remembers the code in `localStorage` `voltora-coupon`.
- `countdown.ts` `countdownLabel(ms)`: `Nd HH:MM:SS`, '' at or below 0.
- `funnel.ts`: `funnelSteps` share = visitors/previous (null if previous is 0); `biggestDrop` returns the earliest worst step.
- `orderPayment.ts`: `paymentCopy` and `paymentState` map status × method to i18n keys. Collected amount = total − refunded_amount for paid or partially_refunded.
- `reviewOrder.ts`: sort and filter.
- `supportHours.ts` `hoursInfo(raw,tz='Asia/Dhaka',at)`: open/closed/next-open sentence. Default days are indexes [0,1,2,3,6] (Mon–Thu and Sun) 10:00–18:00, so Fri/Sat off.
- `image.ts`: width rewrite for Unsplash and the own media store (`?w=`), `imageSrcSet`, `imageAlt`.
- `locationCascade.ts`: announcement keys for the picker.
- `recentSearches.ts` (limit 5), `recentlyViewed.ts`, `dateLabel.ts`.
- Error-message maps: `serverText` with `refusals.json`, `providerMessages`, `recoveryText`.
- `apiConfig.ts`: HTTPS-only origin validation with a loopback exception.
- `resilience.ts`, `themeInk.ts`, `webfonts.ts`.
- `commandSearch.ts` (79 L): keyword-scored palette search over nav entries.
- `seo.ts` (321 L): JSON-LD for organization, website, product, offer, breadcrumb, itemlist. It is mirrored in Python by `scripts/inject_site_graph.py`, so the logic is duplicated.
- Impure or untested: `store.tsx`, `analytics.ts`, `referral.ts`, `persistentState.ts`, `useFocusTrap.ts`, `utils.js`.
- Port priority: delivery, installments, variants, priceBand, cart reconcile/merge, wishlist merge, orderPayment, supportHours, themeInk, apiConfig, image, funnel, margin, address. Rewrite UI-coupled code such as `store.tsx`.

## 8. package.json and build risks
- **Non-registry tarballs** (`package.json:92-93`): `@emergentbase/overlay` and `@emergentbase/visual-edits` from `https://assets.emergent.sh/npm/*.tgz`. They are vendor-hosted and have no registry integrity. Drop them.
- **CRA**:
  - `react-scripts 5.0.1` is deprecated and unmaintained.
  - `@craco/craco 7.1.0` is declared but its config is missing from the handoff.
  - `typescript 4.9.5` is pinned while `@types/react` and `@types/react-dom` are the floating major `"19"`.
  - React 19.0.0 with CRA 5 is not a supported combination.
- About 50 `resolutions` (package.json ~96-145) force-patch transitive CVEs, e.g. `serialize-javascript 7.0.5`, `rollup 2.80.0`, `webpack-dev-server 5.2.6` against CRA's expected older majors. This is fragile.
- `packageManager: yarn@1.22.22`; no lockfile in the handoff.
- Ranges: only `@fontsource/*` and `qrcode.react` use `^`; everything else is exactly pinned.
- Redundant or unused: `dayjs` + `date-fns`, `swr` + `@tanstack/react-query`, `lodash`, `next-themes`, and `cra-template` as a runtime dependency.
- Versions not verified against the registry (look unusually high or odd): `lodash 4.18.1`, `axios 1.20.0`, `react-router-dom 7.18.4`, `lucide-react 0.516.0`, `recharts 3.6.0`.
- ESLint 9.23 and plugins are listed but no ESLint config ships.
- **Build scripts**:
  - `prebuild` runs `python3 ../scripts/inject_site_graph.py`. It rewrites `public/index.html` JSON-LD from `<origin>/api/store`. `DEFAULT_ORIGIN` is `https://store.voltorabd.com` (inject_site_graph.py:36; override via `SITE_ORIGIN` or `--origin`, :704-712).
  - `postbuild --prerender` writes per-route heads into `build/` for `shop`, `pages`, `product` and `brand` (`PRERENDERED`, :37), by fetching the live production API and sitemap (:392-560).
  - Failures are swallowed and return 0 ("keeping the existing block"), so builds are non-hermetic and can silently go stale. They need Python in the Node build env and mutate source (`public/index.html`).
- `deploy/Dockerfile.web.prebuilt` copies a `frontend/build/` made outside the container into Caddy, so the build is not reproducible.
- The production domain `store.voltorabd.com` is hard-coded in Caddyfile:12-14 and in `index.html` og:url/og:image.
- `public/index.html` loads Google Fonts from a CDN while `@fontsource` IBM Plex and Space Grotesk are also bundled (index.js:2-7), so fonts are loaded twice.
- Env contract (`.env.example`): `REACT_APP_BACKEND_URL`, `REACT_APP_BROWSER_ORIGIN`, `REACT_APP_ADMIN_PATH`. These are baked at build time, so each domain needs its own build.
- Deploy context: `docker-compose.yml` runs mongo:4.4 (no-AVX workaround), redis:7, an api container (installer self-relaunch) and a Caddy `web`. `deploy/` also has `backup-mongo.sh`, `restore-drill.sh`, `watchdog.sh`, `uptime-check.sh`, `Dockerfile.api`, and a README runbook. `deploy/.env.example` is referenced but missing.

## 9. Accessibility and responsiveness (source evidence)
Positives:
- Skip link and focus moved to `<main tabIndex=-1>` on route change (App.tsx ~107-110, ~133).
- `document.documentElement.lang` follows the language (store.tsx:56).
- 459 `aria-*` and 124 `role=` attributes in pages/components, and 56 `alt=`.
- 78 `:focus-visible` rules; 42 `prefers-reduced-motion` mentions in CSS plus JS guards in the reveal observer and count-up.
- Live regions for the cascading location selects and search (`search-status`).
- `lib/radioKeys.ts`, `useFocusTrap.ts`, `focusRing.ts`.
- AA contrast tooling for muted text and primary buttons.
- Tables use `scope="col"`; ⌘K palette has `aria-haspopup`; builder toggles use `role="switch"`.

Gaps:
- 14 `outline:none/0` rules, including on `main` (each needs a verified replacement).
- Layer reorder is HTML5 drag only (move buttons exist as a fallback).
- Colour inputs are native `<input type=color>` with hex text.
- Builder iframe sandbox allows scripts and same-origin.
- Two dialog systems: Radix/vaul and a custom `Modal` in `components/shared.tsx`.
- No focus-trap evidence for the admin mobile drawer overlay.
- No `prefers-color-scheme` CSS, so dark mode is applied by JS (flash risk before hydration).
- `dir` is not set per language.
- Bangla relies on a `'Noto Sans Bengali'` fallback in the font stack.
- `CaptchaWidget.tsx` (27 L) accessibility is unknown.

Responsive:
- About 20 ad-hoc breakpoints (see §6).
- Builder canvas uses fixed device widths 390/768/1200 with `transform: scale` in an iframe.
- Admin has a mobile drawer and `mobile-only` buttons, but the builder is only partly usable on phones.
- Tables use horizontal-scroll `table-scroll` wrappers instead of a card layout.
- No container queries or fluid type scale found.

## Unverified / not done
- Exact theme preset count (backend `PRESETS` not enumerated).
- Which feature flags have a server-side effect.
- Registry validity of the version pins listed in §8.
- Install, build and tests were not run, because the craco config is missing.
- Per-page admin endpoint lists come from regex grep of string literals. Dynamic URLs built from variables may be missed.

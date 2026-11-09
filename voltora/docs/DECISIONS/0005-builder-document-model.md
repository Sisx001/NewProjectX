# 0005 — Builder: a validated JSON document tree over a typed component registry

Status: accepted · Date: 2026-11-09 · Implementation: `backend/app/modules/content/{registry,validation,models}.py`, `web/src/builder`

## Context

Legacy had two editors for the same thing (Settings navigation/footer vs builder `site.header/footer`, `legacy_frontend.md` §3), hard caps (80 pages, 60 sections), a hand-rolled release engine and unsanitised style fields.

## Decision

* A page, global section, template or theme is **one JSON document**: a tree of nodes `{id, type, props, style, children, responsive, visibility, bindings}` stored in `content_pages` / `content_themes` as JSONB.
* **No fixed list of section types.** Node types come from a registry that core components, themes and plugins (`register_component`) extend; each type declares typed prop schemas (`text, richtext, number, boolean, select, color, image, link, product, category, list, date`) and which **style capability groups** it may use (`spacing, size, layout, typography, color, border, effects`). The same registry drives server validation, the Studio inspector and the renderer, so they cannot drift.
* **Validation on every save**: unknown types/props rejected, style properties limited to the allowed property list per group, values sanitised (no `url(javascript:)`, no expressions), depth/size limits that are *performance* limits, not product limits. Custom code is a typed node with its own sandboxing rules, never raw script injection.
* **Revisions**: every save writes an immutable `content_page_revisions` / `content_theme_revisions` row; **publishing** points the page/theme at a revision (`published_revision_id`), **rollback** repoints to an earlier revision. Previews render a draft revision under a signed preview token. Import/export is the document JSON plus referenced assets.
* Storefront colours/type/space come from **theme tokens** emitted as `--t-*` CSS variables (see `docs/DESIGN_SYSTEM.md`); the admin/studio use the separate `--v-*` system tokens so a bad theme cannot break the editor.
* Settings that belong to presentation (navigation, footer, auth page, checkout field layout) live **only** in the builder; the settings registry keeps business configuration.

## Consequences

* Legacy theme/section JSON cannot be converted mechanically (`docs/MIGRATION_MAP.md` §2); merchants rebuild or re-import starter content.
* Document size and depth limits must be tuned from real pages; they are configuration, not schema.
* Server-side rendering for SEO is deferred (ADR 0001); documents are renderable server-side later because they are plain data.

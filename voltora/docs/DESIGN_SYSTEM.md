# VOLTORA design system

Source of truth: `web/src/ui/tokens.css` (tokens), `web/src/ui/base.css` (primitives' CSS), `web/src/ui/index.tsx` and `web/src/ui/DataTable.tsx` (components). This document describes what those files define; if they disagree, the files win. **Those three files are frozen for feature work** (ENGINEERING.md): add new shared primitives as new files in `src/ui/`.

## 1. Thesis

*Warm paper, ink, one live wire.* VOLTORA is an operating tool a merchant stares at for hours, so the system is calm, dense where data lives, and quiet everywhere else. Surfaces are paper-warm neutrals, text is near-black ink, and a **single signal colour** (`--v-signal`, electric lime, "voltage") marks current state or the primary emphasis — never decoration. Editorial serif is reserved for display moments (storefront headlines, installer welcome); identifiers and numbers use mono / tabular figures. What it deliberately is not: gradients, glows, glassmorphism, rainbow charts, icon-in-circle feature cards.

## 2. Two token layers

| Layer | Prefix | Used by | Who may change it |
|---|---|---|---|
| **System** | `--v-*` | admin, studio/builder chrome, installer, any internal screen | design-system owner only |
| **Theme** | `--t-*` | storefront and builder-rendered content | merchants, through theme documents (ADR 0005) |

A storefront theme can never break the admin because they share no variables. Storefront CSS reads `var(--t-x, var(--v-x))`, so an incomplete theme falls back to system values.

## 3. Palette roles (light / dark)

| Role | Token | Light | Dark | Use |
|---|---|---|---|---|
| Page | `--v-bg` | `#f4f3ef` | `#0f0f12` | app background |
| Surface | `--v-surface`, `-2`, `-sunken` | `#fff`, `#faf9f6`, `#ecebe6` | `#17171b`, `#1c1c21`, `#121215` | cards, inputs, wells |
| Line | `--v-line`, `--v-line-strong` | `#e3e1da`, `#cfccc2` | `#2a2a31`, `#3a3a43` | hairlines, control borders |
| Ink | `--v-ink`, `-2`, `-3` | `#16161a`, `#45454d`, `#75757e` | `#f2f1ec`, `#c3c2bc`, `#8d8c95` | text hierarchy (ink-3 = secondary/meta) |
| Signal | `--v-signal`, `--v-signal-ink` | `#c6f432` on `#16161a` | same | the one emphasis colour (current nav, key CTA, progress) |
| Accent | `--v-accent`, `--v-accent-soft` | `#2b4bf2`, `#e8ecff` | `#8ea2ff`, `#1f2440` | links, focus ring, selection |
| Status | `--v-success/warning/danger/info` + `-soft` | green `#1d7f53`, amber `#a3620a`, red `#bf3a22`, blue | lifted equivalents | status badges and alerts: colour **plus** text/icon, never colour alone |

Rules: signal appears at most once or twice per screen; status colours carry meaning only; dark mode is a full re-mapping (`[data-mode="dark"]`), not an inversion. Text contrast target is WCAG AA (4.5:1 body, 3:1 large/UI); `ink-3` on `surface` is the lowest allowed text pairing.

## 4. Typography

* Families: **Instrument Sans** (variable, UI), **Instrument Serif** (display, 400 / italic), **Hind Siliguri** (Bangla, 400/600; second in every stack so mixed Bangla/Latin text falls through correctly), **JetBrains Mono** (ids, SKUs, hashes). All self-hosted via `@fontsource` — no third-party font requests (CSP `font-src 'self'`).
* Scale (rem): `xs .75 · sm .8125 · md .875 (app body) · base 1 · lg 1.125 · xl 1.375 · 2xl 1.75 · 3xl 2.25`; body leading 1.5; headings 1.2 with −0.01em tracking, weight 600.
* Numbers: `.v-num` (tabular figures) on every money/quantity column; money is formatted from integer minor units by `@/lib/money`.
* Bangla: Hind Siliguri needs ~1.1× line-height headroom; storefront themes expose `--t-font-bangla` and the theme's `--t-leading`.

## 5. Spacing, shape, elevation

* **4 px grid**: `--v-1…--v-16` = 4, 8, 12, 16, 20, 24, 32, 40, 48, 64 px.
* Radii: `sm 6 · md 10 · lg 14 · pill 999`. Controls use `sm`, cards `md`, dialogs/drawers `lg`.
* Elevation is minimal: `--v-shadow-1` hairline for cards, `-2` for popovers/menus, `-3` for dialogs. No coloured shadows.
* Layout constants: sidebar 248 px, topbar 56 px; table row 44 px comfortable / 36 px compact.

## 6. Motion budget

`--v-dur-1 120ms` (hover, press) · `--v-dur-2 200ms` (popovers, toasts) · `--v-dur-3 320ms` (drawers, dialogs, page-level) with `--v-ease` (0.2, 0.7, 0.2, 1) / `--v-ease-out` (0.16, 1, 0.3, 1). Only `transform` and `opacity` animate; nothing loops except skeleton/progress indicators; no animation longer than 320 ms in the admin. `prefers-reduced-motion: reduce` zeroes the durations and clamps animations/transitions globally. Storefront motion is theme-controlled (`--t-dur-fast|base|slow`, `--t-ease`, `data-t-hover`, `data-t-reveal`) but the same reduced-motion rule applies.

## 7. Density and responsiveness

`[data-density="compact"]` sets `--v-row: 36px` (power users and tables); comfortable (44 px) is the default and the touch size. Targets: **≥ 36 px** controls in the admin, **44 px** on the storefront and on touch breakpoints. The admin shell collapses to a drawer + mobile tab bar (max 4 + menu); `DataTable` switches to stacked rows on narrow screens; dialogs become full-height sheets. Every screen must work at 360 px width without horizontal page scroll (tables scroll inside their container).

## 8. Components (`@/ui`)

`Button` (default, primary, signal, danger, ghost; sizes; `loading`, `icon`), `Field/TextField/Input/Select/Textarea/Switch/Checkbox`, `Badge/StatusBadge` (tones: neutral, success, warning, danger, info, signal), `Card` (title, actions, `flush`), `Alert` (info/warning/danger/success; `role=alert` for danger), `Empty`, `Skeleton`, `Tabs`, `Segmented`, `Dialog`, `Drawer`, `Confirm` (destructive confirmation with `danger`), `ToastProvider/useToast`, `Kbd`, `dateTime/relTime`; `DataTable` (selection, bulk actions, sort, paging, saved views, row click + keyboard Enter), utility classes `v-stack`, `v-row`, `v-grid-3`, `v-kv`, `v-muted`, `v-mono`, `v-sr`.

Screen contract (ENGINEERING.md): each screen renders **loading, empty, error and success** states; empty states say what is missing and how to fix it; no fake numbers or placeholder charts; prerequisites are surfaced as alerts (see the Backups tab for the pattern).

## 9. Accessibility

* Visible focus on every interactive element: 2 px surface gap + 2 px accent ring (`--v-focus`), never removed.
* Skip link (`.v-skip`), landmark regions, `aria-label` on icon buttons, `aria-live` for toasts, labelled form controls through `Field`.
* Dialogs/drawers trap focus and restore it on close; Escape closes; the page behind is inert.
* Colour is never the only carrier of meaning (badges carry text; charts need patterns/labels).
* Keyboard: command palette, table row Enter, tab lists with arrow keys.
* Reduced motion (above); text resizes to 200 % without loss; Bangla text uses a real Bangla font, not fallback glyphs.

## 10. Storefront theme token mapping (`--t-*`)

Defined in exactly one place — `web/src/store/theme/tokens.ts` (`tokensToVars`) — and consumed by `store/theme/theme.css`, `store/store.css` and `builder/render/render.css`. A theme document's `tokens` map onto CSS variables:

| Theme token group | CSS variables |
|---|---|
| Colour | `--t-bg, --t-surface, --t-surface-alt, --t-line, --t-text, --t-text-muted (≥ 4.5:1 enforced), --t-primary, --t-on-primary, --t-highlight, --t-on-highlight, --t-sale, --t-success, --t-danger, --t-focus, --t-inverse-bg, --t-inverse-text, --t-announcement-bg, --t-announcement-text` |
| Type | `--t-font-display, --t-font-body, --t-font-mono, --t-font-bangla`, modular scale `--t-step--1 … --t-step-6` (≥ 3 fluid `clamp`), `--t-leading, --t-display-weight, --t-heading-weight, --t-display-tracking, --t-label-tracking, --t-heading-case` |
| Shape & space | `--t-radius-sm|md|lg|card|button|input, --t-shadow-card|raised|overlay, --t-section, --t-section-mobile, --t-container, --t-gutter, --t-grid-gap` |
| Motion | `--t-dur-fast|base|slow, --t-ease` (named easings: standard, out, in-out, emphasized, linear) |
| Aliases | `--t-ink, --t-ink-2, --t-muted, --t-accent (=primary), --t-accent-ink, --t-signal (=highlight), --t-surface-sunken, --t-line-strong, --t-radius, --t-radius-lg` |
| Root switches | `data-t-hover, data-t-reveal, data-t-button, data-t-density, data-t-card, data-t-family` |

Fonts referenced by a theme must resolve to a self-hosted family or a system stack (`FALLBACK_FONTS`); remote font loading is not part of the contract (CSP and privacy).

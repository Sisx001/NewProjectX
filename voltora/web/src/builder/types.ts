/** Page document and registry contracts (mirror backend/app/modules/content). */
export type Breakpoint = "base" | "tablet" | "mobile";
export type Device = "desktop" | "tablet" | "mobile";
export type StyleMap = Partial<Record<Breakpoint, Record<string, string>>>;

export type Visibility = {
  hidden?: boolean;
  devices?: Device[];
  signed_in?: "any" | "signed_in" | "signed_out";
  locales?: string[];
  starts_at?: string;
  ends_at?: string;
  customer_tags?: string[];
};

export type MotionPreset = "none" | "fade" | "rise" | "scale" | "stagger";
export type Motion = { preset: MotionPreset; duration?: number; delay?: number; stagger?: number; distance?: number; easing?: "standard" | "out" | "in-out" | "linear" | "emphasized"; trigger?: "enter" | "load" };

export type Advanced = { css?: string; class_name?: string; label?: string; aria_label?: string; locked?: boolean };

export type Node = {
  id: string;
  type: string;
  props: Record<string, unknown>;
  style?: StyleMap;
  visibility?: Visibility;
  motion?: Motion;
  advanced?: Advanced;
  children?: Node[];
};

export type PageDocument = { schemaVersion: 1; root: Node };

export type PropType = "text" | "richtext" | "number" | "boolean" | "select" | "color" | "image" | "link" | "product" | "category" | "list" | "date";
export type PropDef = {
  name: string;
  type: PropType;
  label: string;
  default?: unknown;
  required?: boolean;
  options?: string[];
  min?: number;
  max?: number;
  fields?: PropDef[];
  max_items?: number;
  help?: string;
  bindable?: boolean;
};
export type ComponentDef = {
  type: string;
  label: string;
  category: "layout" | "content" | "media" | "commerce" | "navigation" | "global";
  description: string;
  props: PropDef[];
  children: "none" | "any" | string[];
  styles: string[];
  data_sources: string[];
  a11y: Record<string, unknown>;
  version: string;
  source: string;
  icon: string;
  insertable: boolean;
};
export type Registry = {
  components: ComponentDef[];
  style_groups: Record<string, string[]>;
  fonts: { key: string; label: string; stack: string; kind: string }[];
  theme_variants: Record<string, Record<string, string[]>>;
  color_keys: string[];
  contrast_pairs: { fg: string; bg: string; kind: "body" | "large" | "ui" }[];
  limits: { max_nodes: number; max_bytes: number; max_css_bytes: number };
};

export type ThemeTokens = {
  color: Record<string, string>;
  type: Record<string, string | number>;
  radius: Record<string, number>;
  space: Record<string, number>;
  shadow: Record<string, string>;
  motion: Record<string, string | number>;
};
export type ThemeManifest = {
  id: string;
  name: string;
  version: string;
  family: string;
  author?: string;
  license?: string;
  description?: string;
  tokens: ThemeTokens;
  components: Record<string, Record<string, unknown>>;
};
export type PublicTheme = {
  id: string | null;
  name: string | null;
  version: string | null;
  family: string | null;
  tokens: ThemeTokens;
  components: Record<string, Record<string, unknown>>;
  revision: number | null;
  fonts: Record<string, string>;
  globals: { header: Node | null; footer: Node | null; announcement: Node | null };
  pages: { slug: string; title: string }[];
  fallback: boolean;
};

export type PublicPage = { slug: string; title: string; seo?: { title?: string; description?: string }; document: PageDocument; revision: number };

/** Messages exchanged between the Studio (parent) and the preview iframe. */
export type StudioMessage =
  | { type: "voltora:doc"; doc: PageDocument; selected?: string | null; kind?: string }
  | { type: "voltora:select"; id: string | null }
  | { type: "voltora:theme"; manifest: ThemeManifest | null }
  | { type: "voltora:sample"; page: "home" | "product" }; // theme editor sample page
export type PreviewMessage =
  | { type: "voltora:ready" }
  | { type: "voltora:click"; id: string; path: string[] }
  | { type: "voltora:hover"; id: string | null; rect?: { x: number; y: number; w: number; h: number } }
  | { type: "voltora:rects"; rects: Record<string, { x: number; y: number; w: number; h: number }> };

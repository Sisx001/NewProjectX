import { createContext, useContext } from "react";
import type { Node, PublicTheme, ThemeTokens } from "@/builder/types";

export type ThemeState = {
  theme: PublicTheme | null;
  tokens: ThemeTokens | null;
  components: Record<string, { variant?: string } & Record<string, unknown>>;
  globals: { header: Node | null; footer: Node | null; announcement: Node | null };
  published: Set<string> | null; // published page slugs (null = unknown, e.g. in the editor)
  loading: boolean;
  error: boolean;
  editing: boolean; // rendering inside the Studio preview
};

export const EMPTY_THEME: ThemeState = {
  theme: null,
  tokens: null,
  components: {},
  globals: { header: null, footer: null, announcement: null },
  published: null,
  loading: false,
  error: false,
  editing: false,
};

export const ThemeCtx = createContext<ThemeState>(EMPTY_THEME);
export const useThemeState = () => useContext(ThemeCtx);

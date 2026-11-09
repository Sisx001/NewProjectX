import { createContext, useContext } from "react";
import type { BindingData } from "./css";

export type RenderEnv = {
  editing: boolean;
  data: BindingData;
  signedIn: boolean;
  locale: string;
  now: Date;
  published: Set<string> | null;
  currency: string;
};

export const RenderCtx = createContext<RenderEnv>({
  editing: false,
  data: { store: {}, settings: {}, year: new Date().getFullYear() },
  signedIn: false,
  locale: "en",
  now: new Date(),
  published: null,
  currency: "BDT",
});
export const useRender = () => useContext(RenderCtx);

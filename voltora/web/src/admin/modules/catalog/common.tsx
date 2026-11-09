import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Package } from "lucide-react";
import { Confirm } from "@/ui";

export type Img = { url: string; alt: string } | null;

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function Thumb({ image, size = 40 }: { image: Img; size?: number }) {
  return (
    <span
      aria-hidden={!image}
      style={{ width: size, height: size, flex: "none", borderRadius: 6, background: "var(--v-bg-sunk, var(--v-line))", display: "grid", placeItems: "center", overflow: "hidden", border: "1px solid var(--v-line)" }}
    >
      {image ? <img src={image.url} alt={image.alt} width={size} height={size} loading="lazy" style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : <Package size={size / 2.4} color="var(--v-ink-3)" />}
    </span>
  );
}

/** Warns before leaving with unsaved edits: tab close/reload and in-app links (BrowserRouter has no blocker API). */
export function useUnsavedGuard(dirty: boolean): { dialog: ReactNode; go: (to: string) => void } {
  const navigate = useNavigate();
  const [pending, setPending] = useState<string | null>(null);
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
      const url = new URL(a.href, location.href);
      if (url.origin !== location.origin || url.pathname + url.search === location.pathname + location.search) return;
      e.preventDefault();
      e.stopPropagation();
      setPending(url.pathname + url.search + url.hash);
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [dirty]);
  const go = (to: string) => (dirty ? setPending(to) : navigate(to));
  const dialog = (
    <Confirm open={pending !== null} onClose={() => setPending(null)} title="Leave without saving?" confirmLabel="Discard changes" danger
      onConfirm={() => { const to = pending; setPending(null); if (to) navigate(to); }}>
      You have unsaved changes on this page. They will be lost if you leave.
    </Confirm>
  );
  return { dialog, go };
}

export function download(filename: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

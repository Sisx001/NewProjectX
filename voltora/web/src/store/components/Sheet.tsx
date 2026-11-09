import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

/** Accessible modal bottom sheet (mobile filters). Traps focus, closes on Escape/backdrop, restores focus. */
export function Sheet({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current!;
    const sel = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea,[tabindex]:not([tabindex="-1"])';
    el.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); }
      if (e.key !== "Tab") return;
      const f = Array.from(el.querySelectorAll<HTMLElement>(sel));
      if (!f.length) return;
      const first = f[0]!, last = f[f.length - 1]!;
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", key);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", key); document.body.style.overflow = overflow; prev?.focus(); };
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <>
      <div className="s-sheet__veil" onClick={onClose} />
      <div ref={ref} className="s-sheet s-scope" role="dialog" aria-modal="true" aria-labelledby={id}>
        <div className="s-sheet__head">
          <h2 id={id}>{title}</h2>
          <button type="button" className="s-iconbtn" aria-label="Close" onClick={onClose} data-autofocus><X size={20} /></button>
        </div>
        <div className="s-sheet__body">{children}</div>
        {footer ? <div className="s-sheet__foot">{footer}</div> : null}
      </div>
    </>,
    document.body,
  );
}

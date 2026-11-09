import {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
export { cx };

/* ------------------------------------------------------------------ Button */
type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "signal" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
  icon?: boolean;
  block?: boolean;
  loading?: boolean;
};
export const Button = forwardRef<HTMLButtonElement, BtnProps>(function Button(
  { variant = "default", size = "md", icon, block, loading, className, children, disabled, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      className={cx("v-btn", variant !== "default" && `v-btn--${variant}`, size !== "md" && `v-btn--${size}`, icon && "v-btn--icon", block && "v-btn--block", className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <span className="v-spin" aria-hidden /> : null}
      {children}
    </button>
  );
});

/* ------------------------------------------------------------------ Fields */
type FieldProps = { label?: ReactNode; hint?: ReactNode; error?: string | null; children: (id: string) => ReactNode; className?: string };
export function Field({ label, hint, error, children, className }: FieldProps) {
  const id = useId();
  return (
    <div className={cx("v-field", className)}>
      {label ? (
        <label className="v-label" htmlFor={id}>
          {label}
        </label>
      ) : null}
      {children(id)}
      {error ? (
        <span className="v-error-text" role="alert">
          {error}
        </span>
      ) : hint ? (
        <span className="v-hint">{hint}</span>
      ) : null}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(function Input({ className, invalid, ...rest }, ref) {
  return <input ref={ref} className={cx("v-input", className)} aria-invalid={invalid || undefined} {...rest} />;
});
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, ...rest }, ref) {
  return <select ref={ref} className={cx("v-select", className)} {...rest} />;
});
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cx("v-textarea", className)} {...rest} />;
});

export function TextField(props: { label: ReactNode; value: string; onChange: (v: string) => void; hint?: ReactNode; error?: string | null } & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const { label, value, onChange, hint, error, ...rest } = props;
  return (
    <Field label={label} hint={hint} error={error}>
      {(id) => <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} invalid={!!error} {...rest} />}
    </Field>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} className="v-switch" disabled={disabled} onClick={() => onChange(!checked)} />
  );
}

export function Checkbox({ checked, onChange, label, indeterminate }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; indeterminate?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = !!indeterminate;
  }, [indeterminate]);
  return (
    <label className="v-check">
      <input ref={ref} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

/* ------------------------------------------------------------------ Display */
export type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "signal";
export function Badge({ tone = "neutral", children, plain }: { tone?: Tone; children: ReactNode; plain?: boolean }) {
  return <span className={cx("v-badge", tone !== "neutral" && `v-badge--${tone}`, plain && "v-badge--plain")}>{children}</span>;
}

const STATUS_TONE: Record<string, Tone> = {
  active: "success", paid: "success", succeeded: "success", delivered: "success", published: "success", sent: "success", done: "success", fulfilled: "success", completed: "success", in_stock: "success", healthy: "success", enabled: "success", installed: "success", approved: "success", verified: "success",
  pending: "warning", processing: "info", awaiting_payment: "warning", held: "warning", on_hold: "warning", draft: "neutral", queued: "neutral", low_stock: "warning", partially_refunded: "warning", requires_approval: "warning", unverified: "warning", in_transit: "info", shipped: "info", confirmed: "info", packed: "info", running: "info", scheduled: "info",
  failed: "danger", cancelled: "danger", dead: "danger", refunded: "neutral", out_of_stock: "danger", returned: "danger", rto: "danger", rejected: "danger", disabled: "neutral", broken: "danger", expired: "neutral",
};
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{label ?? status.replace(/_/g, " ")}</Badge>;
}

export function Card({ title, actions, children, className, bodyClass, flush }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClass?: string; flush?: boolean }) {
  return (
    <section className={cx("v-card", className)}>
      {title || actions ? (
        <header className="v-card__head">
          {title ? <h2 className="v-card__title">{title}</h2> : <span />}
          {actions ? <div className="v-row">{actions}</div> : null}
        </header>
      ) : null}
      <div className={cx(!flush && "v-card__body", bodyClass)}>{children}</div>
    </section>
  );
}

export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="v-empty">
      {icon}
      <h3>{title}</h3>
      {children ? <p style={{ maxWidth: 420 }}>{children}</p> : null}
      {action}
    </div>
  );
}

export function Skeleton({ h = 16, w = "100%", style }: { h?: number; w?: number | string; style?: React.CSSProperties }) {
  return <div className="v-skel" style={{ height: h, width: w, ...style }} aria-hidden />;
}

export function Alert({ tone = "info", title, children, action }: { tone?: "info" | "warning" | "danger" | "success"; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={`v-alert v-alert--${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <div style={{ flex: 1 }}>
        {title ? <strong style={{ display: "block", marginBottom: children ? 2 : 0 }}>{title}</strong> : null}
        {children}
      </div>
      {action}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items, label }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode; count?: number }[]; label: string }) {
  return (
    <div className="v-tabs" role="tablist" aria-label={label}>
      {items.map((it) => (
        <button
          key={it.value}
          role="tab"
          className="v-tab"
          aria-selected={value === it.value}
          tabIndex={value === it.value ? 0 : -1}
          onClick={() => onChange(it.value)}
          onKeyDown={(e) => {
            const i = items.findIndex((x) => x.value === value);
            if (e.key === "ArrowRight") onChange(items[(i + 1) % items.length]!.value);
            if (e.key === "ArrowLeft") onChange(items[(i - 1 + items.length) % items.length]!.value);
          }}
        >
          {it.label}
          {it.count !== undefined ? <span className="v-muted v-num" style={{ marginLeft: 6 }}>{it.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, items, label }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode }[]; label: string }) {
  return (
    <div className="v-seg" role="group" aria-label={label}>
      {items.map((it) => (
        <button key={it.value} type="button" aria-pressed={value === it.value} onClick={() => onChange(it.value)}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ Overlays */
function useFocusTrap(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const focusables = () => Array.from(el?.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])') ?? []);
    (el?.querySelector<HTMLElement>("[data-autofocus]") ?? focusables()[0] ?? el)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
      if (e.key === "Tab") {
        const f = focusables();
        if (!f.length) return;
        const first = f[0]!, last = f[f.length - 1]!;
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      prev?.focus?.();
    };
  }, [open, onClose]);
  return ref;
}

export function Dialog({ open, onClose, title, description, children, footer, wide }: { open: boolean; onClose: () => void; title: ReactNode; description?: ReactNode; children?: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useFocusTrap(open, onClose);
  const id = useId();
  if (!open) return null;
  return createPortal(
    <>
      <div className="v-overlay" onClick={onClose} />
      <div ref={ref} className={cx("v-dialog", wide && "v-dialog--wide")} role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1}>
        <div className="v-dialog__head">
          <h2 id={id} style={{ fontSize: "var(--v-text-lg)" }}>{title}</h2>
          {description ? <p className="v-muted" style={{ marginTop: 4 }}>{description}</p> : null}
        </div>
        {children ? <div className="v-dialog__body">{children}</div> : null}
        {footer ? <div className="v-dialog__foot">{footer}</div> : null}
      </div>
    </>,
    document.body,
  );
}

export function Drawer({ open, onClose, title, children, footer, side = "right", width }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; side?: "right" | "left"; width?: number }) {
  const ref = useFocusTrap(open, onClose);
  const id = useId();
  if (!open) return null;
  return createPortal(
    <>
      <div className="v-overlay" onClick={onClose} />
      <div ref={ref} className={cx("v-drawer", side === "left" && "v-drawer--left")} style={width ? { width: `min(${width}px, 100vw)` } : undefined} role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1}>
        <div className="v-drawer__head">
          <h2 id={id} style={{ fontSize: "var(--v-text-lg)" }}>{title}</h2>
          <Button variant="ghost" icon aria-label="Close" onClick={onClose}>
            <X size={18} />
          </Button>
        </div>
        <div className="v-drawer__body">{children}</div>
        {footer ? <div className="v-drawer__foot">{footer}</div> : null}
      </div>
    </>,
    document.body,
  );
}

export function Confirm({ open, onClose, onConfirm, title, children, confirmLabel = "Confirm", danger, busy }: { open: boolean; onClose: () => void; onConfirm: () => void; title: string; children?: ReactNode; confirmLabel?: string; danger?: boolean; busy?: boolean }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} loading={busy} data-autofocus>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}

/* ------------------------------------------------------------------ Toasts */
type Toast = { id: number; message: ReactNode; tone: "default" | "danger" };
const ToastCtx = createContext<(message: ReactNode, tone?: "default" | "danger") => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((message: ReactNode, tone: "default" | "danger" = "default") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "danger" ? 7000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="v-toasts" aria-live="polite" role="status">
        {toasts.map((t) => (
          <div key={t.id} className={cx("v-toast", t.tone === "danger" && "v-toast--danger")}>
            <div style={{ flex: 1 }}>{t.message}</div>
            <button className="v-btn v-btn--ghost v-btn--sm v-btn--icon" style={{ color: "inherit" }} aria-label="Dismiss" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}>
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/* ------------------------------------------------------------------ Misc */
export function Kbd({ children }: { children: ReactNode }) {
  return <kbd style={{ fontFamily: "var(--v-font-mono)", fontSize: 11, padding: "1px 5px", borderRadius: 4, border: "1px solid var(--v-line-strong)", background: "var(--v-surface-2)", color: "var(--v-ink-3)" }}>{children}</kbd>;
}

export function dateTime(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" }) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Dhaka", ...opts }).format(new Date(iso));
}

export function relTime(iso: string | null | undefined) {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d ago`;
  return dateTime(iso, { dateStyle: "medium" });
}

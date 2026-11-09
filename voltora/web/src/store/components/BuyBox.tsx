import { useEffect, useMemo, useState } from "react";
import { Minus, Plus, ShoppingBag } from "lucide-react";
import { ApiError } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { useCart } from "../lib/cart";
import { defaultSelection, discountPct, optionValueAvailable, purchasable, resolveVariant, type Selection } from "../lib/helpers";
import type { ProductDetail, Variant } from "../types";

export function Price({ price, compare, currency, max, size }: { price: number; compare?: number | null; currency: string; max?: number; size?: "lg" }) {
  const pct = discountPct(price, compare ?? null);
  return (
    <p className={"s-price" + (size ? " s-price--lg" : "")}>
      {max && max > price ? <span className="s-price__from">From </span> : null}
      <strong>{formatMoney(price, currency)}</strong>
      {compare && compare > price ? (
        <>
          <s aria-label={`Was ${formatMoney(compare, currency)}`}>{formatMoney(compare, currency)}</s>
          <span className="s-price__off">−{pct}%</span>
        </>
      ) : null}
    </p>
  );
}

export function stockLabel(v: Variant | null): { text: string; tone: "ok" | "low" | "out" | "back" } | null {
  if (!v) return null;
  if (!purchasable(v)) return { text: "Sold out", tone: "out" };
  if (!v.in_stock && v.allow_backorder) return { text: "Available on backorder", tone: "back" };
  if (v.available !== null && v.available <= 5) return { text: `Only ${v.available} left`, tone: "low" };
  return { text: "In stock", tone: "ok" };
}

export function useBuyBox(product: ProductDetail) {
  const [sel, setSel] = useState<Selection>(() => defaultSelection(product));
  const [qty, setQty] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState(false);
  const cart = useCart();
  useEffect(() => { setSel(defaultSelection(product)); setQty(1); setError(null); }, [product.id]);
  const variant = useMemo(() => resolveVariant(product, sel), [product, sel]);
  const maxQty = variant && variant.available !== null && !variant.allow_backorder ? Math.max(1, variant.available) : 99;
  const canAdd = !!variant && purchasable(variant);
  async function add(openDrawer = true) {
    if (!variant) return setError(`Choose ${product.options.filter((o) => !sel[o.name]).map((o) => o.name.toLowerCase()).join(" and ") || "an option"} first.`);
    setError(null);
    try {
      await cart.add(variant.id, qty, { openDrawer });
      setAdded(true);
      setTimeout(() => setAdded(false), 2200);
    } catch (e) {
      if (e instanceof ApiError && e.code === "insufficient_stock") {
        const avail = (e.details as { available?: number } | undefined)?.available;
        setError(typeof avail === "number" ? `Only ${avail} available right now.` : "That quantity isn't available right now.");
        if (typeof avail === "number" && avail > 0) setQty(avail);
      } else setError(e instanceof ApiError ? e.message : "Couldn't add to cart. Try again.");
    }
  }
  return { sel, setSel, qty, setQty, maxQty, variant, canAdd, error, setError, added, add, busy: cart.busy };
}

export function BuyBox({ product, box, showPrice = true }: { product: ProductDetail; box: ReturnType<typeof useBuyBox>; showPrice?: boolean }) {
  const { sel, setSel, qty, setQty, maxQty, variant, error, added, add, busy, canAdd } = box;
  const prices = product.variants.map((v) => v.price_minor);
  const stock = stockLabel(variant);
  const needs = product.options.some((o) => !sel[o.name]);
  return (
    <div className="s-buybox">
      {showPrice ? (
        variant ? <Price price={variant.price_minor} compare={variant.compare_at_minor} currency={product.currency} size="lg" />
          : <Price price={Math.min(...prices)} max={Math.max(...prices)} currency={product.currency} size="lg" />
      ) : null}
      {product.options.map((o) => (
        <fieldset key={o.name} className="s-opt">
          <legend>{o.name}{sel[o.name] ? <span>: {sel[o.name]}</span> : null}</legend>
          <div className="s-opt__vals" role="radiogroup" aria-label={o.name}>
            {o.values.map((val) => {
              const ok = optionValueAvailable(product, sel, o.name, val);
              const on = sel[o.name] === val;
              return (
                <button key={val} type="button" role="radio" aria-checked={on} aria-disabled={!ok} className="s-chip" data-on={on} data-off={!ok}
                  title={ok ? undefined : "Unavailable with your other choices"}
                  onClick={() => { if (!ok) return; setSel({ ...sel, [o.name]: on ? "" : val }); box.setError(null); }}>
                  {val}
                </button>
              );
            })}
          </div>
        </fieldset>
      ))}
      <p className="s-stock" data-tone={stock?.tone} role="status" aria-live="polite">{stock ? stock.text : needs ? "Select options to see availability" : "Combination unavailable"}</p>
      <div className="s-buyrow">
        <div className="s-qty" role="group" aria-label="Quantity">
          <button type="button" aria-label="Decrease quantity" onClick={() => setQty(Math.max(1, qty - 1))} disabled={qty <= 1}><Minus size={16} /></button>
          <input inputMode="numeric" aria-label="Quantity" value={qty} onChange={(e) => { const n = parseInt(e.target.value.replace(/\D/g, ""), 10); setQty(Number.isFinite(n) ? Math.min(maxQty, Math.max(1, n)) : 1); }} />
          <button type="button" aria-label="Increase quantity" onClick={() => setQty(Math.min(maxQty, qty + 1))} disabled={qty >= maxQty}><Plus size={16} /></button>
        </div>
        <button type="button" className="s-btn s-btn--primary s-btn--grow" data-testid="add-to-cart" disabled={busy || (!!variant && !canAdd)} onClick={() => add()}>
          <ShoppingBag size={18} aria-hidden /> {added ? "Added to cart" : variant && !canAdd ? "Sold out" : "Add to cart"}
        </button>
      </div>
      {error ? <p className="s-error" role="alert">{error}</p> : null}
    </div>
  );
}

import { useState } from "react";
import { Link } from "react-router-dom";
import { Minus, Plus, Tag, Trash2, X } from "lucide-react";
import { ApiError } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { Alert, Drawer, Skeleton } from "@/ui";
import { useCart } from "../lib/cart";
import type { Cart, CartLine } from "../types";

function Line({ line, currency, compact }: { line: CartLine; currency: string; compact?: boolean }) {
  const cart = useCart();
  const [err, setErr] = useState<string | null>(null);
  async function set(q: number) {
    setErr(null);
    try { await (q <= 0 ? cart.remove(line.id) : cart.setQty(line.id, q)); }
    catch (e) { setErr(e instanceof ApiError && e.code === "insufficient_stock" ? "Not enough stock for that quantity." : e instanceof ApiError ? e.message : "Couldn't update."); }
  }
  const max = line.available ?? 99;
  return (
    <li className="s-line" data-testid="cart-line" data-compact={compact}>
      <Link to={`/p/${line.product_slug}`} className="s-line__img">{line.image ? <img src={line.image.url} alt="" loading="lazy" /> : null}</Link>
      <div className="s-line__main">
        <Link to={`/p/${line.product_slug}`} className="s-line__title">{line.title}</Link>
        {line.variant_title ? <p className="s-muted">{line.variant_title}</p> : null}
        <p className="s-line__price">
          {formatMoney(line.unit_price_minor, currency)}
          {line.compare_at_minor && line.compare_at_minor > line.unit_price_minor ? <s>{formatMoney(line.compare_at_minor, currency)}</s> : null}
        </p>
        {line.issues.map((i) => <p key={i} className="s-line__issue" role="alert">{i}</p>)}
        {err ? <p className="s-line__issue" role="alert">{err}</p> : null}
        <div className="s-line__ctl">
          <div className="s-qty s-qty--sm" role="group" aria-label={`Quantity of ${line.title}`}>
            <button type="button" aria-label="Decrease quantity" disabled={cart.busy} onClick={() => set(line.quantity - 1)}><Minus size={14} /></button>
            <output aria-live="polite">{line.quantity}</output>
            <button type="button" aria-label="Increase quantity" disabled={cart.busy || line.quantity >= max} onClick={() => set(line.quantity + 1)}><Plus size={14} /></button>
          </div>
          <button type="button" className="s-iconbtn" aria-label={`Remove ${line.title}`} disabled={cart.busy} onClick={() => set(0)}><Trash2 size={16} /></button>
        </div>
      </div>
      <p className="s-line__total">{formatMoney(line.line_total_minor, currency)}</p>
    </li>
  );
}

export function DiscountForm({ cart }: { cart: Cart }) {
  const api = useCart();
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (cart.discount)
    return (
      <p className="s-code s-code--on"><Tag size={16} aria-hidden /> <span><strong>{cart.discount.code}</strong> {cart.discount.description}</span>
        <button type="button" className="s-iconbtn" aria-label="Remove discount code" onClick={() => api.removeCode()}><X size={16} /></button></p>
    );
  return (
    <form className="s-code" onSubmit={async (e) => {
      e.preventDefault();
      if (!code.trim()) return;
      setErr(null);
      try { await api.applyCode(code.trim()); setCode(""); } catch (x) { setErr(x instanceof ApiError ? x.message : "Couldn't apply that code."); }
    }}>
      <label className="s-sr" htmlFor="discount-code">Discount code</label>
      <input id="discount-code" className="s-input" placeholder="Discount code" value={code} onChange={(e) => setCode(e.target.value)} autoCapitalize="characters" />
      <button className="s-btn s-btn--quiet" type="submit" disabled={api.busy || !code.trim()}>Apply</button>
      {err ? <p className="s-error" role="alert">{err}</p> : null}
    </form>
  );
}

export function Summary({ cart }: { cart: Cart }) {
  return (
    <dl className="s-sum">
      <div><dt>Subtotal</dt><dd>{formatMoney(cart.subtotal_minor, cart.currency)}</dd></div>
      {cart.discount_minor > 0 ? <div><dt>Discount</dt><dd>−{formatMoney(cart.discount_minor, cart.currency)}</dd></div> : null}
      <div className="s-sum__note"><dt>Shipping &amp; fees</dt><dd>Calculated at checkout</dd></div>
    </dl>
  );
}

export function CartLines({ cart, compact }: { cart: Cart; compact?: boolean }) {
  return (
    <>
      {cart.issues.map((i) => <Alert key={i} tone="warning">{i}</Alert>)}
      <ul className="s-lines">{cart.lines.map((l) => <Line key={l.id} line={l} currency={cart.currency} compact={compact} />)}</ul>
    </>
  );
}

export function CartDrawer() {
  const { cart, open, setOpen, loading } = useCart();
  const close = () => setOpen(false);
  const hasIssues = !!cart && (cart.issues.length > 0 || cart.lines.some((l) => l.issues.length > 0));
  return (
    <Drawer open={open} onClose={close} title={`Your cart${cart && cart.item_count ? ` (${cart.item_count})` : ""}`} width={440}
      footer={cart && cart.lines.length ? (
        <div className="s-drawerfoot">
          <Summary cart={cart} />
          <Link to="/checkout" className="s-btn s-btn--primary s-btn--block" data-testid="drawer-checkout" onClick={close} aria-disabled={hasIssues && cart.lines.every((l) => l.issues.length)}>Checkout</Link>
          <Link to="/cart" className="s-link" onClick={close}>View full cart</Link>
        </div>
      ) : undefined}>
      {loading ? <Skeleton h={90} /> : !cart || cart.lines.length === 0 ? (
        <div className="s-empty"><p className="s-empty__t">Your cart is empty</p><p className="s-muted">Add something you like and it will wait here.</p>
          <Link className="s-btn s-btn--primary" to="/shop" onClick={close}>Browse the shop</Link></div>
      ) : (
        <>
          <CartLines cart={cart} compact />
          <DiscountForm cart={cart} />
        </>
      )}
    </Drawer>
  );
}

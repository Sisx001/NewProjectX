import { Link } from "react-router-dom";
import { Alert, Skeleton } from "@/ui";
import { CartLines, DiscountForm, Summary } from "../components/CartView";
import { useCart } from "../lib/cart";
import { useDocTitle } from "../lib/data";
import { formatMoney } from "@/lib/money";

export default function CartPage() {
  const { cart, loading } = useCart();
  useDocTitle("Cart");
  const blocked = !!cart && cart.lines.length > 0 && cart.lines.every((l) => l.issues.length > 0);
  return (
    <div className="s-wrap s-cartpage">
      <h1 className="s-h1">Your cart</h1>
      {loading ? <Skeleton h={160} /> : !cart || cart.lines.length === 0 ? (
        <div className="s-empty" data-testid="cart-empty"><p className="s-empty__t">Your cart is empty</p><p className="s-muted">Items you add will be kept for you here.</p><Link className="s-btn s-btn--primary" to="/shop">Browse the shop</Link></div>
      ) : (
        <div className="s-cartpage__grid">
          <div><CartLines cart={cart} /></div>
          <aside className="s-panel" aria-label="Order summary">
            <h2 className="s-h3">Summary</h2>
            <DiscountForm cart={cart} />
            <Summary cart={cart} />
            <p className="s-sum__total"><span>Subtotal</span><strong>{formatMoney(cart.subtotal_minor - cart.discount_minor, cart.currency)}</strong></p>
            {blocked ? <Alert tone="warning">Resolve the items above before checking out.</Alert> : null}
            <Link to="/checkout" className="s-btn s-btn--primary s-btn--block s-btn--lg" data-testid="cart-checkout" aria-disabled={blocked}>Go to checkout</Link>
            <Link className="s-link" to="/shop">Continue shopping</Link>
          </aside>
        </div>
      )}
    </div>
  );
}

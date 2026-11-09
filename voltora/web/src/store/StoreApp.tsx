import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Link, Route, Routes, useLocation } from "react-router-dom";
import { Heart, Search, ShoppingBag, User } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { Skeleton } from "@/ui";
import { ThemeProvider, SiteFooter, SiteHeader } from "./theme";
import { CartDrawer } from "./components/CartView";
import { CartProvider, useCart } from "./lib/cart";
import { useStorefrontConfig } from "./lib/data";
import { useWishlist } from "./lib/local";
import { Page } from "./lib/pageview";
import { CmsPage, Compare, HomeFallback, Maintenance, NotFound, OfflineBar, ServerDown, Wishlist } from "./pages/Misc";
import "./store.css";

const Shop = lazy(() => import("./pages/Shop"));
const Product = lazy(() => import("./pages/Product"));
const CartPage = lazy(() => import("./pages/Cart"));
const Checkout = lazy(() => import("./pages/Checkout"));
const OrderPage = lazy(() => import("./pages/Order"));
const Track = lazy(() => import("./pages/Track"));
const Sandbox = lazy(() => import("./pages/Sandbox"));
const AccountLayout = lazy(() => import("./pages/Account").then((m) => ({ default: m.AccountLayout })));
const Orders = lazy(() => import("./pages/Account").then((m) => ({ default: m.Orders })));
const AccountOrder = lazy(() => import("./pages/Account").then((m) => ({ default: m.AccountOrder })));
const Addresses = lazy(() => import("./pages/Account").then((m) => ({ default: m.Addresses })));
const Profile = lazy(() => import("./pages/Account").then((m) => ({ default: m.Profile })));
const Login = lazy(() => import("./pages/Auth").then((m) => ({ default: m.Login })));
const Register = lazy(() => import("./pages/Auth").then((m) => ({ default: m.Register })));
const Forgot = lazy(() => import("./pages/Auth").then((m) => ({ default: m.Forgot })));
const Reset = lazy(() => import("./pages/Auth").then((m) => ({ default: m.Reset })));

/** Used only when the active theme renders no header, so navigation is never missing. */
function FallbackHeader() {
  const cart = useCart();
  const wish = useWishlist();
  const cfg = useStorefrontConfig();
  const { me } = useAuth();
  return (
    <header className="s-fbhead" data-testid="fallback-header">
      <div className="s-wrap s-fbhead__in">
        <Link to="/" className="s-fbhead__logo">{cfg.data?.store.name ?? "Store"}</Link>
        <nav aria-label="Primary"><Link to="/shop">Shop</Link><Link to="/track">Track order</Link></nav>
        <div className="s-fbhead__acts">
          <Link to="/search" aria-label="Search"><Search size={20} /></Link>
          <Link to="/wishlist" aria-label={`Wishlist (${wish.items.length})`}><Heart size={20} /></Link>
          <Link to={me?.user?.kind === "customer" ? "/account" : "/account/login"} aria-label="Account"><User size={20} /></Link>
          <button type="button" aria-label={`Open cart (${cart.cart?.item_count ?? 0} items)`} onClick={() => cart.setOpen(true)} data-testid="cart-button"><ShoppingBag size={20} />{cart.cart?.item_count ? <b>{cart.cart.item_count}</b> : null}</button>
        </div>
      </div>
    </header>
  );
}

function Layout() {
  const loc = useLocation();
  const root = useRef<HTMLDivElement>(null);
  const [fallbackHeader, setFallbackHeader] = useState(false);
  const online = useOnline();
  const cfg = useStorefrontConfig();
  const { me } = useAuth();
  const announcement = String(cfg.data?.settings["store.announcement"] ?? "");
  const maintenance = !!cfg.data?.settings["store.maintenance_mode"] && me?.user?.kind !== "staff";

  useEffect(() => {
    window.scrollTo({ top: 0 });
    const t = setTimeout(() => setFallbackHeader(!root.current?.querySelector(":scope > header:not(.s-fbhead), :scope > :not(main):not(.s-fbhead) header, [data-site-header]")), 400);
    return () => clearTimeout(t);
  }, [loc.pathname]);
  const isStaffMaint = !!cfg.data?.settings["store.maintenance_mode"] && me?.user?.kind === "staff";

  if (cfg.isError && !cfg.data) return <ServerDown retry={() => cfg.refetch()} />;
  if (maintenance && !loc.pathname.startsWith("/account") && !loc.pathname.startsWith("/pay/"))
    return <Maintenance name={cfg.data?.store.name ?? "The store"} phone={String(cfg.data?.settings["store.support_phone"] ?? "")} email={String(cfg.data?.settings["store.support_email"] ?? "")} />;
  const sandbox = loc.pathname.startsWith("/pay/sandbox/");
  return (
    <div className="s-root" ref={root}>
      <a href="#main" className="s-skip">Skip to content</a>
      {!online ? <OfflineBar /> : null}
      {isStaffMaint ? <p className="s-staffnote" role="status">Maintenance mode is on — customers see a maintenance page. You're seeing the live store as staff.</p> : null}
      {announcement && !sandbox ? <p className="s-announce" role="note">{announcement}</p> : null}
      {sandbox ? null : <SiteHeader />}
      {!sandbox && fallbackHeader ? <FallbackHeader /> : null}
      <main id="main" tabIndex={-1} className="s-main">
        <Suspense fallback={<div className="s-wrap"><Skeleton h={320} /></div>}>
          <Routes>
            <Route index element={<Page slug="home" fallback={<HomeFallback />} notFound={<HomeFallback />} />} />
            <Route path="shop" element={<Shop mode="shop" />} />
            <Route path="c/:slug" element={<Shop mode="category" />} />
            <Route path="b/:slug" element={<Shop mode="brand" />} />
            <Route path="search" element={<Shop mode="search" />} />
            <Route path="p/:slug" element={<Product />} />
            <Route path="cart" element={<CartPage />} />
            <Route path="checkout" element={<Checkout />} />
            <Route path="order/:number" element={<OrderPage />} />
            <Route path="track" element={<Track />} />
            <Route path="pay/sandbox/:intentId" element={<Sandbox />} />
            <Route path="account/login" element={<Login />} />
            <Route path="account/register" element={<Register />} />
            <Route path="account/forgot" element={<Forgot />} />
            <Route path="account/reset" element={<Reset />} />
            <Route path="account" element={<AccountLayout />}>
              <Route index element={<Orders />} />
              <Route path="orders/:number" element={<AccountOrder />} />
              <Route path="addresses" element={<Addresses />} />
              <Route path="profile" element={<Profile />} />
            </Route>
            <Route path="wishlist" element={<Wishlist />} />
            <Route path="compare" element={<Compare />} />
            <Route path="pages/:slug" element={<CmsPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Suspense>
      </main>
      {sandbox ? null : <SiteFooter />}
      <CartDrawer />
    </div>
  );
}

function useOnline() {
  const [on, setOn] = useState(() => navigator.onLine);
  useEffect(() => {
    const a = () => setOn(true), b = () => setOn(false);
    window.addEventListener("online", a);
    window.addEventListener("offline", b);
    return () => { window.removeEventListener("online", a); window.removeEventListener("offline", b); };
  }, []);
  return on;
}

export default function StoreApp() {
  return (
    <ThemeProvider>
      <CartProvider>
        <Layout />
      </CartProvider>
    </ThemeProvider>
  );
}

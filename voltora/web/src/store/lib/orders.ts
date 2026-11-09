/** Guests keep order access links on this device so "Your orders" survives a closed tab. Tokens never leave the browser except to the order API. */
const K = "voltora:store:orders";
export type Remembered = { number: string; token: string; at: number };
export function rememberedOrders(): Remembered[] {
  try { const v = JSON.parse(localStorage.getItem(K) ?? "[]"); return Array.isArray(v) ? v : []; } catch { return []; }
}
export function rememberOrder(number: string, token: string) {
  try {
    const rest = rememberedOrders().filter((o) => o.number !== number);
    localStorage.setItem(K, JSON.stringify([{ number, token, at: Date.now() }, ...rest].slice(0, 20)));
  } catch { /* storage unavailable */ }
}

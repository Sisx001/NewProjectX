// Storefront end-to-end checks against a running dev stack (API + vite). Usage:
//   BASE=http://127.0.0.1:5174 node e2e/run.mjs [--shots-only] [--no-shots]
// Requires an installed store with demo catalog data (see docs/ENGINEERING.md) and headless Chrome.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.env.BASE ?? "http://127.0.0.1:5174";
const CHROME = process.env.CHROME ?? "/usr/local/bin/google-chrome";
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "../../artifacts/screenshots");
mkdirSync(SHOTS, { recursive: true });
const noShots = process.argv.includes("--no-shots");

const results = [];
let lastOrderUrl = null;
const log = (...a) => console.log(...a);

async function step(name, fn) {
  const t0 = Date.now();
  try {
    await fn();
    results.push({ name, ok: true });
    log(`PASS  ${name} (${Date.now() - t0}ms)`);
  } catch (e) {
    results.push({ name, ok: false, err: String(e.message ?? e).split("\n")[0] });
    log(`FAIL  ${name}: ${String(e.message ?? e).split("\n").slice(0, 3).join(" | ")}`);
  }
}

const visible = (page, sel) => page.locator(sel).locator("visible=true");

async function pickFirstOption(page, selectSel) {
  const sel = page.locator(selectSel);
  await sel.waitFor({ state: "visible" });
  await page.waitForFunction((s) => { const el = document.querySelector(s); return el && !el.disabled && el.options.length > 1; }, selectSel, { timeout: 10000 });
  const value = await sel.evaluate((el) => [...el.options].find((o) => o.value)?.value);
  await sel.selectOption(value);
  return value;
}

async function addFirstProductToCart(page, { viaFilter }) {
  await page.goto(BASE + "/");
  await page.locator("main").waitFor();
  await page.locator('a[href="/shop"]:visible').first().click();
  await page.waitForURL("**/shop");
  await page.locator('[data-testid="product-card"]').first().waitFor({ timeout: 15000 });
  if (viaFilter) {
    const desktop = (page.viewportSize()?.width ?? 1440) >= 900;
    if (desktop) await page.getByLabel("In stock only").click();
    else { await page.getByTestId("open-filters").click(); await page.getByRole("dialog").getByLabel("In stock only").click(); await page.getByRole("dialog").getByRole("button", { name: /Show/ }).click(); }
    await page.waitForFunction(() => location.search.includes("in_stock=1"));
    await page.locator('[data-testid="product-card"]').first().waitFor();
  }
  await page.locator('[data-testid="product-card"]:not(:has(.s-badge--out)) h3 a').first().click();
  await page.waitForURL("**/p/**");
  await page.getByRole("heading", { level: 1 }).waitFor();
  for (const group of await page.locator('[role="radiogroup"][aria-label]:not(.s-opts)').all()) {
    const label = await group.getAttribute("aria-label");
    if (label === "Delivery method" || label === "Payment method") continue;
    await group.locator('[role="radio"]:not([aria-disabled="true"])').first().click();
  }
  const add = page.getByTestId("add-to-cart");
  await add.waitFor();
  if (await add.isDisabled()) throw new Error("add to cart disabled: first product not purchasable");
  await add.click();
  await page.getByRole("dialog", { name: /Your cart/ }).waitFor();
  await page.getByTestId("cart-line").first().waitFor();
}

async function checkoutAs(page, { payment, mobile }) {
  await page.getByTestId("drawer-checkout").click();
  await page.waitForURL("**/checkout");
  await page.locator("#co-name").waitFor();
  await page.locator("#co-name").fill("Test Buyer তামিম");
  await page.locator("#co-phone").fill("01712345678");
  await page.locator("#co-email").fill("buyer@example.com");
  const next = async () => { if (mobile) await page.locator(".s-co__sec[data-active='true'] .s-co__next").click(); };
  await next();
  await pickFirstOption(page, "#co-division");
  await pickFirstOption(page, "#co-district");
  await pickFirstOption(page, "#co-upazila");
  await page.locator("#co-line1").fill("House 12, Road 5, Dhanmondi");
  await next();
  await page.locator('input[name="ship"]').first().waitFor({ timeout: 15000 });
  await page.locator('input[name="ship"]').first().check();
  await next();
  const radios = page.locator('input[name="pay"]:not([disabled])');
  await radios.first().waitFor();
  const keys = await radios.evaluateAll((els) => els.map((e) => e.value));
  const want = payment === "cod" ? keys.find((k) => /cod/i.test(k)) : keys.find((k) => /sandbox/i.test(k));
  if (!want) throw new Error(`no ${payment} payment method offered; got [${keys.join(", ")}]`);
  await page.locator(`input[name="pay"][value="${want}"]`).check();
  await next();
  await page.getByTestId("accept-terms").check();
  await page.getByTestId("place-order").click();
}

async function expectOrderPage(page) {
  await page.waitForURL(/\/order\/[^/?]+\?token=/, { timeout: 20000 });
  await page.getByTestId("order-number").waitFor();
  lastOrderUrl = page.url();
  const n = await page.getByTestId("order-number").innerText();
  if (!n.trim()) throw new Error("empty order number");
  return n.trim();
}

async function main() {
  const browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox"] });
  const only = process.argv.includes("--shots-only");

  if (!only) {
    await step("(a) browse → filter → variant → cart → checkout (COD) → order page", async () => {
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await ctx.newPage();
      await addFirstProductToCart(page, { viaFilter: true });
      await checkoutAs(page, { payment: "cod", mobile: false });
      const n = await expectOrderPage(page);
      const status = await page.getByTestId("payment-status").innerText();
      log(`      order ${n}, payment: ${status}`);
      await ctx.close();
    });

    await step("(b) checkout with Sandbox Gateway success → order shows paid", async () => {
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await ctx.newPage();
      await addFirstProductToCart(page, { viaFilter: false });
      await checkoutAs(page, { payment: "sandbox", mobile: false });
      await page.waitForURL("**/pay/sandbox/**", { timeout: 20000 });
      const txt = await page.getByTestId("sandbox-page").innerText();
      if (!/simulation, no real money/i.test(txt)) throw new Error("sandbox page not labelled as simulation");
      await page.getByTestId("sbx-success").click();
      await expectOrderPage(page);
      await page.waitForFunction(() => /paid/i.test(document.querySelector('[data-testid="payment-status"]')?.textContent ?? ""), null, { timeout: 30000 });
      await ctx.close();
    });

    await step("(c) mobile 390px: stepper checkout (COD) smoke", async () => {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
      const page = await ctx.newPage();
      await addFirstProductToCart(page, { viaFilter: true });
      await checkoutAs(page, { payment: "cod", mobile: true });
      await expectOrderPage(page);
      await ctx.close();
    });
  }

  if (!noShots) {
    await step("screenshots 1440/768/390/320", async () => {
      for (const width of [1440, 768, 390, 320]) {
        const ctx = await browser.newContext({ viewport: { width, height: width > 700 ? 900 : 800 }, deviceScaleFactor: 1, hasTouch: width < 700, isMobile: width < 700 });
        const page = await ctx.newPage();
        const shot = (name, full = true) => page.screenshot({ path: `${SHOTS}/store-${name}-${width}.png`, fullPage: full });
        await page.goto(BASE + "/"); await page.locator("main").waitFor(); await page.waitForTimeout(600); await shot("home");
        await page.goto(BASE + "/shop"); await page.locator('[data-testid="product-card"]').first().waitFor(); await page.waitForTimeout(600); await shot("shop");
        await page.locator('[data-testid="product-card"]:not(:has(.s-badge--out)) h3 a').first().click(); await page.waitForURL("**/p/**"); await page.getByRole("heading", { level: 1 }).waitFor(); await page.waitForTimeout(600); await shot("product");
        for (const group of await page.locator('[role="radiogroup"][aria-label]:not(.s-opts)').all()) await group.locator('[role="radio"]:not([aria-disabled="true"])').first().click();
        await page.getByTestId("add-to-cart").click();
        await page.getByRole("dialog", { name: /Your cart/ }).waitFor(); await page.waitForTimeout(500); await shot("cart-drawer", false);
        await page.getByTestId("drawer-checkout").click(); await page.waitForURL("**/checkout"); await page.locator("#co-name").waitFor(); await page.waitForTimeout(400); await shot("checkout");
        if (lastOrderUrl) { await page.goto(lastOrderUrl); await page.getByTestId("order-number").waitFor(); await page.waitForTimeout(400); await shot("order"); }
        await ctx.close();
      }
    });
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  log(`\n${results.length - failed.length}/${results.length} passed`);
  process.exit(failed.length ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(2); });

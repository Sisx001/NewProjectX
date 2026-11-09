/** Money is always integer minor units on the wire. Exponent is per currency, never assumed. */
const EXPONENT: Record<string, number> = { BDT: 2, USD: 2, EUR: 2, INR: 2, JPY: 0, KWD: 3 };
const SYMBOL: Record<string, string> = { BDT: "৳" };

export function exponent(currency: string): number {
  return EXPONENT[currency.toUpperCase()] ?? 2;
}

export function formatMoney(minor: number | null | undefined, currency = "BDT", locale = "en-BD"): string {
  if (minor === null || minor === undefined || Number.isNaN(minor)) return "—";
  const exp = exponent(currency);
  const major = minor / 10 ** exp;
  const showFraction = minor % 10 ** exp !== 0;
  const n = new Intl.NumberFormat(locale.startsWith("bn") ? "bn-BD" : "en-IN", {
    minimumFractionDigits: showFraction ? exp : 0,
    maximumFractionDigits: exp,
  }).format(major);
  const sym = SYMBOL[currency.toUpperCase()];
  return sym ? `${sym}${n}` : `${currency} ${n}`;
}

/** Parse a user-entered major-unit string ("1,250.50") into minor units; returns null when invalid. */
export function parseMoney(input: string, currency = "BDT"): number | null {
  const clean = input.replace(/[৳,\s]/g, "").replace(/[০-৯]/g, (d) => String("০১২৩৪৫৬৭৮৯".indexOf(d)));
  if (!/^-?\d+(\.\d+)?$/.test(clean)) return null;
  const exp = exponent(currency);
  const [whole, frac = ""] = clean.split(".");
  if (frac.length > exp) return null;
  const sign = clean.startsWith("-") ? -1 : 1;
  return sign * (Math.abs(parseInt(whole ?? "0", 10)) * 10 ** exp + parseInt((frac + "000").slice(0, exp) || "0", 10));
}

export function toMajorInput(minor: number | null | undefined, currency = "BDT"): string {
  if (minor === null || minor === undefined) return "";
  const exp = exponent(currency);
  const v = minor / 10 ** exp;
  return minor % 10 ** exp === 0 ? String(v) : v.toFixed(exp);
}

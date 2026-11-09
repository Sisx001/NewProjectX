import { describe, expect, it } from "vitest";
import { formatMoney, parseMoney, toMajorInput } from "./money";

describe("money", () => {
  it("formats BDT minor units", () => {
    expect(formatMoney(125000)).toBe("৳1,250");
    expect(formatMoney(125050)).toBe("৳1,250.50");
    expect(formatMoney(12345600)).toBe("৳1,23,456");
    expect(formatMoney(null)).toBe("—");
  });
  it("parses user input including Bangla digits", () => {
    expect(parseMoney("1,250.5")).toBe(125050);
    expect(parseMoney("৳ ১২৫০")).toBe(125000);
    expect(parseMoney("12.345")).toBeNull();
    expect(parseMoney("abc")).toBeNull();
  });
  it("round-trips inputs", () => {
    expect(toMajorInput(125050)).toBe("1250.50");
    expect(toMajorInput(125000)).toBe("1250");
  });
});

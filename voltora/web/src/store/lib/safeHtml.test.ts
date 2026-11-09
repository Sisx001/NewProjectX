import { describe, expect, it } from "vitest";
import { htmlToText, sanitizeHtml } from "./safeHtml";

describe("sanitizeHtml", () => {
  it("keeps basic formatting", () => {
    expect(sanitizeHtml("<p>Hi <strong>there</strong></p>")).toBe("<p>Hi <strong>there</strong></p>");
  });
  it("removes scripts, handlers and unsafe links", () => {
    const out = sanitizeHtml('<p onclick="x()">a</p><script>alert(1)</script><a href="javascript:alert(1)">l</a><img src=x onerror=alert(1)><a href="https://ok.example/p">ok</a>');
    expect(out).not.toMatch(/script|onclick|onerror|javascript:|<img/i);
    expect(out).toContain('href="https://ok.example/p"');
  });
  it("extracts text", () => expect(htmlToText("<p>One</p><p> two </p>")).toBe("One two"));
});

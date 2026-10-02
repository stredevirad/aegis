import { describe, expect, it } from "vitest";
import { safeUrl } from "./safeUrl";

describe("safeUrl", () => {
  it.each([
    ["http://prometheus.example:9090/graph?g0.expr=up", "http://prometheus.example:9090/graph?g0.expr=up"],
    ["https://alertmanager.example/#/alerts", "https://alertmanager.example/#/alerts"],
    ["HTTPS://Example.COM/a b".replace(" ", "%20"), "https://example.com/a%20b"],
  ])("allows absolute http(s) URL %s", (input, expected) => {
    expect(safeUrl(input)).toBe(expected);
  });

  it.each([
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    " javascript:alert(document.cookie)",
    "java\tscript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "data:image/png;base64,AAAA",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "//evil.example/path",
    "/relative/path",
    "relative/path",
    "?q=1",
    "#fragment",
    "https://user:pass@example.com/",
    "https://user@example.com/",
    "http://",
    "http:example.com",
    "https:/example.com",
    "",
    "   ",
  ])("rejects %j", (input) => {
    expect(safeUrl(input)).toBeNull();
  });

  it("rejects non-strings", () => {
    expect(safeUrl(null)).toBeNull();
    expect(safeUrl(undefined)).toBeNull();
    expect(safeUrl(42)).toBeNull();
    expect(safeUrl({ href: "https://example.com" })).toBeNull();
  });

  it("returns the normalised href, not the raw input", () => {
    expect(safeUrl("https://EXAMPLE.com")).toBe("https://example.com/");
  });
});

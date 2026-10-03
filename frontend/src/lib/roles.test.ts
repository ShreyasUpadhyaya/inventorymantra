import { describe, expect, it } from "vitest";

import { HOME, canVisit, isProtected, navFor, safeNext } from "./roles";

describe("route guards (UX only; the API is the authority)", () => {
  it("only the Owner sees and opens Users", () => {
    expect(navFor("owner").map((n) => n.href)).toEqual(["/dashboard", "/chat", "/users"]);
    expect(navFor("procurement_manager").map((n) => n.href)).toEqual(["/dashboard", "/chat"]);
    expect(canVisit("inventory_manager", "/users")).toBe(false);
    expect(canVisit("procurement_manager", "/users/new")).toBe(false);
    expect(canVisit("owner", "/users")).toBe(true);
    expect(canVisit("inventory_manager", "/chat")).toBe(true);
  });

  it("protects app pages but not login, assets or look-alike paths", () => {
    expect(isProtected("/dashboard")).toBe(true);
    expect(isProtected("/chat/123")).toBe(true);
    expect(isProtected("/login")).toBe(false);
    expect(isProtected("/usersettings")).toBe(false);
  });
});

describe("safeNext keeps ?next= on this site", () => {
  it.each([
    [null, HOME],
    ["", HOME],
    ["/chat?thread=1", "/chat?thread=1"],
    ["https://evil.example", HOME],
    ["//evil.example", HOME],
    ["/\\evil.example", HOME],
    ["javascript:alert(1)", HOME],
    ["/login?next=/login", HOME],
  ])("%s → %s", (next, expected) => {
    expect(safeNext(next)).toBe(expected);
  });
});

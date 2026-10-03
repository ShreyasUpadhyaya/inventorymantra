import { describe, expect, it } from "vitest";

import { validateLogin, validatePasswordChange } from "./login";

describe("validateLogin", () => {
  it("names each missing field (found in manual testing: empty password showed a generic error)", () => {
    expect(validateLogin("owner@demo.inventorymantra", "")).toEqual({
      password: "Enter your password.",
    });
    expect(validateLogin("  ", "")).toEqual({
      email: "Enter your email.",
      password: "Enter your password.",
    });
  });

  it("rejects text that can't be an email; accepts the demo domain", () => {
    expect(validateLogin("owner", "x").email).toBe("Enter a valid email address.");
    expect(validateLogin(" owner@demo.inventorymantra ", "x")).toEqual({});
  });
});

describe("validatePasswordChange mirrors the API rules", () => {
  const email = "priya@demo.inventorymantra";
  it.each([
    ["", "long enough 123", "long enough 123", { current: "Enter your current password." }],
    ["old-password", "short", "short", { next: "Use at least 10 characters." }],
    ["same password", "same password", "same password",
      { next: "Choose a password different from the current one." }],
    ["old-password", email, email, { next: "Don't use your email as your password." }],
    ["old-password", "long enough 123", "long enough 124",
      { confirm: "The two new passwords don't match." }],
    ["old-password", "long enough 123", "long enough 123", {}],
  ])("%s / %s / %s", (current, next, confirm, expected) => {
    expect(validatePasswordChange(email, current, next, confirm)).toEqual(expected);
  });
});

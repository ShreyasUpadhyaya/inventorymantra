import { describe, expect, it } from "vitest";

import { validateLogin } from "./login";

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

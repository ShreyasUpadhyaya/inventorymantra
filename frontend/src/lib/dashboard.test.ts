import { describe, expect, it } from "vitest";

import { sectionViz } from "./dashboard";
import { formatKpi } from "./format";
import { validateNewUser } from "./users";

describe("formatKpi", () => {
  it("formats each KPI kind", () => {
    expect(formatKpi(4789288.83, "inr")).toBe("₹47.9L");
    expect(formatKpi(24.7, "pct")).toBe("24.7%");
    expect(formatKpi(48, "days")).toBe("48 days");
    expect(formatKpi(1, "days")).toBe("1 day");
    expect(formatKpi(36, "count")).toBe("36");
    expect(formatKpi(1234, "count")).toBe("1,234");
    expect(formatKpi(null, "inr")).toBe("—");
  });
});

describe("sectionViz", () => {
  it("passes the section's chart settings without a duplicate title", () => {
    expect(
      sectionViz({ id: "s", title: "Spend", viz: "line", x: "month", y: ["total_spend"], columns: [], rows: [], empty_message: "" }),
    ).toEqual({ type: "line", title: "", x: "month", y: ["total_spend"] });
  });
});

describe("validateNewUser", () => {
  it("asks for each missing piece", () => {
    expect(validateNewUser({ email: "", full_name: " ", role: "" })).toEqual({
      email: "Enter an email address.",
      full_name: "Enter the person's name.",
      role: "Choose a role.",
    });
    expect(validateNewUser({ email: "priya@", full_name: "Priya", role: "owner" }).email).toBe("Enter a valid email address.");
    expect(validateNewUser({ email: "priya@acme.in", full_name: "Priya", role: "inventory_manager" })).toEqual({});
  });
});

describe("notification times", () => {
  it("reads like a person would say it", async () => {
    const { ago } = await import("./notifications");
    const now = new Date("2026-10-04T12:00:00Z");
    expect(ago("2026-10-04T11:59:40Z", now)).toBe("just now");
    expect(ago("2026-10-04T11:55:00Z", now)).toBe("5 min ago");
    expect(ago("2026-10-04T09:00:00Z", now)).toBe("3 h ago");
  });
});

import { describe, expect, it } from "vitest";

import { columnLabel, formatCell, formatCompact } from "./format";
import { SSEParser } from "./sse";
import { parseEvent } from "./chat";
import { toDrawable } from "./viz";

describe("SSE parser", () => {
  it("handles events split across chunks, CRLF and Hindi text", () => {
    const parser = new SSEParser();
    expect(parser.push('event: status\ndata: {"label":"Rea')).toEqual([]);
    const out = parser.push('ding the data"}\r\n\r\nevent: answer\ndata: {"answer":"२८ ऑर्डर"}\n\n');
    expect(out).toEqual([
      { event: "status", data: '{"label":"Reading the data"}' },
      { event: "answer", data: '{"answer":"२८ ऑर्डर"}' },
    ]);
  });

  it("maps API events to chat events and ignores unknown or broken ones", () => {
    expect(parseEvent("thread", '{"thread_id":"t1"}')).toEqual({ type: "thread", threadId: "t1" });
    expect(parseEvent("clarify", '{"answer":"Which one?","suggestions":["A","B"]}')).toMatchObject({
      type: "clarify",
      turn: { answer: "Which one?", suggestions: ["A", "B"], rows: [] },
    });
    expect(parseEvent("ping", "{}")).toBeNull();
    expect(parseEvent("answer", "not json")).toBeNull();
  });
});

describe("formatting", () => {
  it("uses Indian grouping, ₹ for money and % for shares", () => {
    expect(formatCell(4789288.83, "total_amount")).toBe("₹47,89,288.83");
    expect(formatCell(24.7, "margin_pct")).toBe("24.7%");
    expect(formatCell(28, "overdue_pos")).toBe("28");
    expect(formatCell("2026-08-13", "expected_delivery_date")).toBe("13 Aug 2026");
    expect(formatCell(null)).toBe("—");
    expect(formatCompact(4789288, "spend")).toBe("₹47.9L");
    expect(formatCompact(38600000, "revenue")).toBe("₹3.9Cr");
  });

  it("labels columns readably", () => {
    expect(columnLabel("days_overdue")).toBe("Days overdue");
    expect(columnLabel("po_number")).toBe("PO number");
  });
});

describe("toDrawable", () => {
  const columns = ["quarter", "margin_pct"];
  const rows = [["2026-Q1", 25.2], ["2026-Q2", 20.9], ["2026-Q3", 24.7]];

  it("builds chart points with numbers", () => {
    const d = toDrawable({ type: "bar", title: "m", x: "quarter", y: ["margin_pct"] }, columns, rows);
    expect(d).toEqual({
      type: "bar",
      x: "quarter",
      y: ["margin_pct"],
      data: [
        { quarter: "2026-Q1", margin_pct: 25.2 },
        { quarter: "2026-Q2", margin_pct: 20.9 },
        { quarter: "2026-Q3", margin_pct: 24.7 },
      ],
    });
  });

  it("falls back to a table when the spec doesn't fit the data", () => {
    expect(toDrawable({ type: "line", title: "t", x: "month", y: ["spend"] }, columns, rows)).toEqual({ type: "table" });
    expect(toDrawable({ type: "flow", title: "t" }, columns, rows)).toEqual({ type: "table" });
    expect(toDrawable(null, columns, rows)).toEqual({ type: "table" });
    expect(toDrawable(null, [], [])).toEqual({ type: "none" });
  });

  it("KPI shows the requested figures of the single row", () => {
    const d = toDrawable({ type: "kpi", title: "t", y: ["overdue_pos"] }, ["overdue_pos"], [[28]]);
    expect(d).toEqual({ type: "kpi", items: [{ label: "overdue_pos", column: "overdue_pos", value: 28 }] });
  });
});

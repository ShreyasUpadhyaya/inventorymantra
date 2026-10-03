// Display formatting for tables, KPIs and chart axes: Indian digit grouping, ₹ for money
// columns, % for percentage columns, readable dates. Values themselves are never changed.

import type { Cell } from "./chat";

const MONEY = /(amount|value|price|cost|spend|revenue|total_inr|_inr$|margin_inr|paid|payable)/i;
const PERCENT = /(pct|percent|_share$)/i;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}T/;

const ACRONYMS: Record<string, string> = { po: "PO", pos: "POs", id: "ID", inr: "₹", qty: "Qty", uom: "UoM" };

/** "days_overdue" → "Days overdue", "po_number" → "PO number". */
export function columnLabel(column: string): string {
  const words = column.split("_").filter(Boolean).map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase());
  const label = words.join(" ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export const isMoneyColumn = (column: string) => MONEY.test(column) && !PERCENT.test(column);
export const isPercentColumn = (column: string) => PERCENT.test(column);

const group = (digits: number) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits });

export function formatNumber(value: number, column = ""): string {
  if (isPercentColumn(column)) return `${group(1).format(value)}%`;
  if (isMoneyColumn(column)) return `₹${group(2).format(value)}`;
  return group(Number.isInteger(value) ? 0 : 2).format(value);
}

/** Short axis labels: ₹47.9L, ₹3.9Cr, 12.5K. */
export function formatCompact(value: number, column = ""): string {
  const abs = Math.abs(value);
  const prefix = isMoneyColumn(column) ? "₹" : "";
  if (isPercentColumn(column)) return `${group(1).format(value)}%`;
  if (abs >= 1e7) return `${prefix}${group(1).format(value / 1e7)}Cr`;
  if (abs >= 1e5) return `${prefix}${group(1).format(value / 1e5)}L`;
  if (abs >= 1e3 && !prefix) return `${group(1).format(value / 1e3)}K`;
  return `${prefix}${group(0).format(value)}`;
}

export function formatDate(text: string): string {
  const date = new Date(ISO_DATE.test(text) ? `${text}T00:00:00` : text);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" }).format(date);
}

export function formatCell(value: Cell, column = ""): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return formatNumber(value, column);
  if (ISO_DATE.test(value) || ISO_DATETIME.test(value)) return formatDate(value);
  return value;
}

/** Numbers stored as strings (Postgres numerics) become numbers for charts. */
export function toNumber(value: Cell): number | null {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) return Number(value);
  return null;
}

// Display formatting for tables, KPIs and chart axes: Indian digit grouping, ₹ for money
// columns, % for percentage columns, readable dates. Values themselves are never changed.

import type { Cell } from "./chat";

const MONEY = /(amount|value|price|cost|spend|revenue|profit|po_total|_inr$|paid|payable)/i;
const PERCENT = /(pct|percent|_share$)/i;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}T/;

const ACRONYMS: Record<string, string> = { po: "PO", pos: "POs", id: "ID", inr: "₹", qty: "Qty", uom: "UoM" };

/** "days_overdue" → "Days overdue", "po_number" → "PO number". */
export function columnLabel(column: string): string {
  if (column.endsWith("_pct")) return `${columnLabel(column.slice(0, -4))} %`;
  const words = column.split("_").filter(Boolean).map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase());
  const label = words.join(" ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export const isMoneyColumn = (column: string) => MONEY.test(column) && !PERCENT.test(column);
export const isPercentColumn = (column: string) => PERCENT.test(column);

const group = (digits: number) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits });

const money = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function formatNumber(value: number, column = ""): string {
  if (isPercentColumn(column)) return `${group(1).format(value)}%`;
  if (isMoneyColumn(column)) return `₹${Number.isInteger(value) ? group(0).format(value) : money.format(value)}`;
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

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "13 Aug 2026", the same way the answers write dates (Intl would give "Sept"). */
export function formatDate(text: string): string {
  const date = new Date(ISO_DATE.test(text) ? `${text}T00:00:00` : text);
  if (Number.isNaN(date.getTime())) return text;
  return `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
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

export type KpiFormat = "count" | "inr" | "pct" | "days";

/** Dashboard KPI card value: ₹47.9L, 24.7%, 48 days, 36. */
export function formatKpi(value: number | null, format: KpiFormat): string {
  if (value === null) return "—";
  if (format === "inr") return formatCompact(value, "amount");
  if (format === "pct") return `${group(1).format(value)}%`;
  if (format === "days") return `${group(0).format(value)} ${value === 1 ? "day" : "days"}`;
  return group(0).format(value);
}

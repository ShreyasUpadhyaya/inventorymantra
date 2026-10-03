// Turns a VizSpec + result rows into what the renderers draw. The backend picks the visual
// (DECISION D-008); this only checks the spec fits the data and falls back to a table if not.

import type { Cell, VizSpec, VizType } from "./chat";
import { formatCell, toNumber } from "./format";

export type Point = Record<string, string | number | null>;

export type Drawable =
  | { type: "none" }
  | { type: "kpi"; items: { label: string; column: string; value: Cell }[] }
  | { type: "table" }
  | { type: "bar" | "line" | "pie" | "stacked_bar"; x: string; y: string[]; data: Point[] }
  | { type: "flow"; mermaid: string };

const CHARTS: VizType[] = ["bar", "line", "pie", "stacked_bar"];

export function toDrawable(viz: VizSpec | null, columns: string[], rows: Cell[][]): Drawable {
  if (!viz || viz.type === "none") return rows.length ? { type: "table" } : { type: "none" };
  if (viz.type === "flow") return viz.mermaid ? { type: "flow", mermaid: viz.mermaid } : { type: "table" };
  if (!rows.length) return { type: "none" };
  if (viz.type === "kpi") {
    const wanted = viz.y?.length ? viz.y : columns;
    const items = wanted
      .filter((c) => columns.includes(c))
      .map((column) => ({ label: column, column, value: rows[0][columns.indexOf(column)] }));
    return items.length ? { type: "kpi", items } : { type: "table" };
  }
  if (CHARTS.includes(viz.type)) {
    const x = viz.x ?? "";
    const y = (viz.y ?? []).filter((c) => columns.includes(c) && c !== x);
    if (!columns.includes(x) || !y.length) return { type: "table" };
    const xi = columns.indexOf(x);
    const data = rows.map((row) => {
      const point: Point = { [x]: formatCell(row[xi], x) };
      for (const column of y) point[column] = toNumber(row[columns.indexOf(column)]);
      return point;
    });
    return { type: viz.type as "bar" | "line" | "pie" | "stacked_bar", x, y, data };
  }
  return { type: "table" };
}

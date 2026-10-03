// Dashboard API (backend/app/services/dashboard.py): KPIs and sections in the same
// shape the chat uses, so both draw with the same components.

import { apiJson } from "./api";
import type { Cell, VizSpec } from "./chat";
import type { KpiFormat } from "./format";
import type { Role } from "./roles";

export type Kpi = { id: string; label: string; value: number | null; format: KpiFormat };

export type Section = {
  id: string;
  title: string;
  viz: "table" | "bar" | "line" | "pie";
  x: string | null;
  y: string[];
  columns: string[];
  rows: Cell[][];
  empty_message: string;
};

export type Dashboard = { role: Role; today: string; kpis: Kpi[]; sections: Section[] };

export const dashboard = { get: () => apiJson<Dashboard>("/dashboard") };

export function sectionViz(section: Section): VizSpec {
  // the card already shows the title, so the visual gets none
  return { type: section.viz, title: "", x: section.x, y: section.y };
}

"use client";

import dynamic from "next/dynamic";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { Cell, VizSpec } from "@/lib/chat";
import { columnLabel, formatCell } from "@/lib/format";
import { toDrawable } from "@/lib/viz";

import { DataTable } from "./data-table";

const Chart = dynamic(() => import("./charts").then((m) => m.Chart), {
  ssr: false,
  loading: () => <div className="h-72 animate-pulse rounded-lg bg-muted" />,
});
const MermaidFlow = dynamic(() => import("./mermaid-flow").then((m) => m.MermaidFlow), { ssr: false });

/** Draws the visual the backend chose (VizSpec), with the rows always one click away. */
export function VizView({
  viz,
  columns,
  rows,
  rowCount = rows.length,
}: {
  viz: VizSpec | null;
  columns: string[];
  rows: Cell[][];
  rowCount?: number;
}) {
  const drawable = toDrawable(viz, columns, rows);
  const [showRows, setShowRows] = useState(false);
  const hasRows = rows.length > 0 && columns.length > 0;
  if (drawable.type === "none" && !hasRows) return null;

  let visual: React.ReactNode = null;
  if (drawable.type === "table") {
    visual = <DataTable columns={columns} rows={rows} total={rowCount} />;
  } else if (drawable.type === "kpi") {
    visual = (
      <div className="flex flex-wrap gap-3">
        {drawable.items.map((item) => (
          <div key={item.column} className="min-w-36 rounded-lg border bg-muted/40 px-4 py-3">
            <div className="text-xs text-muted-foreground">{columnLabel(item.label)}</div>
            <div className="text-2xl font-semibold tabular-nums">{formatCell(item.value, item.column)}</div>
          </div>
        ))}
      </div>
    );
  } else if (drawable.type === "flow") {
    visual = <MermaidFlow code={drawable.mermaid} />;
  } else if (drawable.type !== "none") {
    visual = <Chart type={drawable.type} x={drawable.x} y={drawable.y} data={drawable.data} />;
  }

  const canToggle = hasRows && drawable.type !== "table";
  const title = drawable.type === "kpi" || drawable.type === "none" ? null : viz?.title;
  return (
    <figure className="grid w-full min-w-0 grid-cols-[minmax(0,1fr)] gap-2">
      {title && <figcaption className="text-xs font-medium text-muted-foreground">{title}</figcaption>}
      {visual}
      {canToggle && (
        <div>
          <Button variant="link" size="xs" className="px-0" onClick={() => setShowRows((v) => !v)}>
            {showRows ? "Hide data" : "Show data"}
          </Button>
          {showRows && <DataTable columns={columns} rows={rows} total={rowCount} />}
        </div>
      )}
    </figure>
  );
}

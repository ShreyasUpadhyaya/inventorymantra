import type { Cell } from "@/lib/chat";
import { columnLabel, formatCell, toNumber } from "@/lib/format";

/** Result table: readable headers, numbers right-aligned, scrolls inside its own box. */
export function DataTable({ columns, rows, total }: { columns: string[]; rows: Cell[][]; total: number }) {
  const numeric = columns.map((_, i) => rows.every((r) => r[i] === null || toNumber(r[i]) !== null));
  return (
    <div className="grid gap-1">
      <div className="max-h-80 overflow-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-muted">
            <tr>
              {columns.map((c, i) => (
                <th key={c} scope="col" className={`whitespace-nowrap px-3 py-2 font-medium ${numeric[i] ? "text-right" : "text-left"}`}>
                  {columnLabel(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="border-t">
                {row.map((value, i) => (
                  <td key={i} className={`px-3 py-1.5 ${numeric[i] ? "text-right tabular-nums" : ""} ${/(number|code|date|_on)$/.test(columns[i]) ? "whitespace-nowrap" : ""}`}>
                    {formatCell(value, columns[i])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > rows.length && (
        <p className="text-xs text-muted-foreground">
          Showing {rows.length} of {total} rows.
        </p>
      )}
    </div>
  );
}

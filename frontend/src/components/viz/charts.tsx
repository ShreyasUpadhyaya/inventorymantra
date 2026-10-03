"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { columnLabel, formatCompact, formatNumber } from "@/lib/format";
import type { Point } from "@/lib/viz";

const COLORS = [1, 2, 3, 4, 5].map((n) => `var(--chart-${n})`);

type Props = { type: "bar" | "line" | "pie" | "stacked_bar"; x: string; y: string[]; data: Point[] };

export function Chart({ type, x, y, data }: Props) {
  const tooltip = (
    <Tooltip
      formatter={(value, name) => [formatNumber(Number(value), String(name)), columnLabel(String(name))]}
      contentStyle={{ borderRadius: 8, fontSize: 12 }}
    />
  );
  const legend = y.length > 1 ? <Legend formatter={(name) => columnLabel(String(name))} /> : null;
  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" vertical={false} />
      <XAxis dataKey={x} tick={{ fontSize: 12 }} interval="preserveStartEnd" />
      <YAxis tick={{ fontSize: 12 }} width={64} tickFormatter={(v: number) => formatCompact(v, y[0])} />
    </>
  );

  let chart: React.ReactElement;
  if (type === "pie") {
    chart = (
      <PieChart>
        <Pie data={data} dataKey={y[0]} nameKey={x} outerRadius="80%" label={(p) => String(p.name)}>
          {data.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip formatter={(value) => formatNumber(Number(value), y[0])} />
      </PieChart>
    );
  } else if (type === "line") {
    chart = (
      <LineChart data={data}>
        {axes}
        {tooltip}
        {legend}
        {y.map((column, i) => (
          <Line key={column} dataKey={column} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={data.length <= 24} />
        ))}
      </LineChart>
    );
  } else {
    chart = (
      <BarChart data={data}>
        {axes}
        {tooltip}
        {legend}
        {y.map((column, i) => (
          <Bar
            key={column}
            dataKey={column}
            fill={COLORS[i % COLORS.length]}
            stackId={type === "stacked_bar" ? "stack" : undefined}
            radius={type === "stacked_bar" ? 0 : [4, 4, 0, 0]}
          />
        ))}
      </BarChart>
    );
  }
  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        {chart}
      </ResponsiveContainer>
    </div>
  );
}

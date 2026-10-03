"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
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

import { columnLabel, formatCompact, formatNumber, isPercentColumn } from "@/lib/format";
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
  // long category names (vendors, items) are slanted and all shown, not silently skipped
  const slanted = data.length > 3 && data.some((d) => String(d[x]).length > 10);
  const xAxis = (
    <XAxis
      dataKey={x}
      tick={{ fontSize: 12 }}
      interval={slanted ? 0 : "preserveStartEnd"}
      angle={slanted ? -30 : 0}
      textAnchor={slanted ? "end" : "middle"}
      height={slanted ? 80 : 30}
      tickFormatter={(v: string) => (String(v).length > 18 ? `${String(v).slice(0, 17)}…` : String(v))}
    />
  );
  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" vertical={false} />
      {xAxis}
      <YAxis tick={{ fontSize: 12 }} width={64} tickFormatter={(v: number) => formatCompact(v, y[0])} />
    </>
  );

  let chart: React.ReactElement;
  if (type === "pie") {
    chart = (
      <PieChart>
        <Pie isAnimationActive={false} data={data} dataKey={y[0]} nameKey={x} outerRadius="80%" label={(p) => String(p.name)}>
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
          <Line isAnimationActive={false} key={column} dataKey={column} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={data.length <= 24} />
        ))}
      </LineChart>
    );
  } else if (type === "bar" && y.some(isPercentColumn) && y.some((c) => !isPercentColumn(c))) {
    // money bars with a % line on its own right-hand axis (e.g. revenue, cost and margin)
    const bars = y.filter((c) => !isPercentColumn(c));
    const lines = y.filter(isPercentColumn);
    chart = (
      <ComposedChart data={data}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        {xAxis}
        <YAxis yAxisId="left" tick={{ fontSize: 12 }} width={64} tickFormatter={(v: number) => formatCompact(v, bars[0])} />
        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 12 }} width={48} tickFormatter={(v: number) => formatCompact(v, lines[0])} />
        {tooltip}
        {legend}
        {bars.map((column, i) => (
          <Bar isAnimationActive={false} key={column} yAxisId="left" dataKey={column} fill={COLORS[i % COLORS.length]} radius={[4, 4, 0, 0]} />
        ))}
        {lines.map((column, i) => (
          <Line isAnimationActive={false} key={column} yAxisId="right" dataKey={column} stroke={COLORS[(bars.length + i) % COLORS.length]} strokeWidth={2} />
        ))}
      </ComposedChart>
    );
  } else {
    chart = (
      <BarChart data={data}>
        {axes}
        {tooltip}
        {legend}
        {y.map((column, i) => (
          <Bar
            isAnimationActive={false}
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
    <div className="h-72 w-full min-w-0">
      <ResponsiveContainer width="100%" height="100%">
        {chart}
      </ResponsiveContainer>
    </div>
  );
}

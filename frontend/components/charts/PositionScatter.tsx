"use client";

import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface PositionPoint {
  id: string;
  health_factor: number;
  usd_value: number;
  risk_level: "SAFE" | "WARNING" | "CRITICAL";
}

const RISK_COLORS: Record<PositionPoint["risk_level"], string> = {
  SAFE: "#00ff87",
  WARNING: "#ffb800",
  CRITICAL: "#ff0040",
};

export function PositionScatter({ points }: { points: PositionPoint[] }) {
  if (points.length === 0) {
    return <div className="rounded-lg border border-dashed border-border-subtle p-6 text-sm text-text-secondary">No open positions in current selection.</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ScatterChart margin={{ top: 16, right: 16, bottom: 20, left: 6 }}>
        <CartesianGrid stroke="rgba(255,255,255,0.08)" strokeDasharray="3 3" />
        <XAxis
          type="number"
          dataKey="health_factor"
          domain={[0, 2]}
          name="Health Factor"
          stroke="#a1a1aa"
          tick={{ fontSize: 11 }}
        />
        <YAxis
          type="number"
          dataKey="usd_value"
          name="USD at Risk"
          stroke="#a1a1aa"
          tick={{ fontSize: 11 }}
        />
        <Tooltip
          cursor={{ stroke: "rgba(255,255,255,0.2)" }}
          contentStyle={{ background: "#111111", border: "1px solid #1a1a1a" }}
        />
        <ReferenceLine x={1.0} stroke="#ff0040" strokeDasharray="6 4" label="Liquidation" />
        <Scatter
          data={points.map((point) => ({ ...point, fill: RISK_COLORS[point.risk_level] }))}
          fill="#3b82f6"
        />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface LatencyPoint {
  bucket: string;
  density: number;
  p50?: number;
  p95?: number;
  p99?: number;
}

export function LatencyViolin({ data }: { data: LatencyPoint[] }) {
  if (data.length === 0) {
    return <div className="rounded-lg border border-dashed border-border-subtle p-6 text-sm text-text-secondary">No latency samples in the selected window.</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <ComposedChart data={data}>
        <defs>
          <linearGradient id="latencyArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.7} />
            <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.08} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="rgba(255,255,255,0.08)" strokeDasharray="3 3" />
        <XAxis dataKey="bucket" stroke="#a1a1aa" tick={{ fontSize: 11 }} />
        <YAxis stroke="#a1a1aa" tick={{ fontSize: 11 }} />
        <Tooltip
          cursor={{ stroke: "rgba(255,255,255,0.2)" }}
          contentStyle={{ background: "#111111", border: "1px solid #1a1a1a" }}
        />
        <ReferenceLine y={500} stroke="#ffb800" strokeDasharray="6 4" label="SLA 500ms" />
        <Area type="monotone" dataKey="density" stroke="#3b82f6" fill="url(#latencyArea)" />
        <Line type="monotone" dataKey="p50" dot={false} stroke="#00ff87" strokeWidth={1.6} />
        <Line type="monotone" dataKey="p95" dot={false} stroke="#ffb800" strokeWidth={1.6} />
        <Line type="monotone" dataKey="p99" dot={false} stroke="#ff0040" strokeWidth={1.8} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

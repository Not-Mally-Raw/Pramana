"use client";

import { useId } from "react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";

interface RiskSparklineProps {
  values: number[];
  color?: string;
}

export function RiskSparkline({ values, color = "#3b82f6" }: RiskSparklineProps) {
  const gradientId = useId().replace(/:/g, "");
  const normalized = values.length > 0 ? values : [0, 0, 0, 0, 0];
  const data = normalized.map((value, idx) => ({
    idx,
    value,
  }));

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ left: 0, right: 0, top: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.7} />
            <stop offset="100%" stopColor={color} stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.6}
          fillOpacity={1}
          fill={`url(#${gradientId})`}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

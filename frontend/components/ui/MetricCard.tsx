"use client";

import clsx from "clsx";
import { formatDelta } from "@/lib/design-tokens";
import { RiskSparkline } from "@/components/charts/RiskSparkline";

type Tone = "safe" | "warning" | "critical" | "data";

interface MetricCardProps {
  title: string;
  value: number | string;
  delta: number;
  sparkline: number[];
  tone: Tone;
}

const TONE_STYLES: Record<Tone, { border: string; glow: string; chart: string }> = {
  safe: { border: "border-pass-0/20", glow: "from-pass-0/10", chart: "#00ff87" },
  warning: { border: "border-warn-0/20", glow: "from-warn-0/10", chart: "#ffb800" },
  critical: { border: "border-critical-1/30", glow: "from-critical-1/12", chart: "#ff0040" },
  data: { border: "border-data-0/25", glow: "from-data-0/12", chart: "#3b82f6" },
};

export function MetricCard({ title, value, delta, sparkline, tone }: MetricCardProps) {
  const style = TONE_STYLES[tone];

  return (
    <article className={clsx("panel relative overflow-hidden p-4", style.border)}>
      <div className={clsx("pointer-events-none absolute inset-0 bg-gradient-to-br to-transparent", style.glow)} />

      <div className="relative">
        <p className="card-label">{title}</p>
        <div className="mt-2 flex items-baseline justify-between gap-3">
          <p className="number-display text-2xl leading-none text-white">{value}</p>
          <p className={clsx("text-xs font-semibold", delta >= 0 ? "text-pass-0" : "text-critical-1")}>
            {formatDelta(delta)}
          </p>
        </div>

        <div className="mt-3 h-10">
          <RiskSparkline values={sparkline} color={style.chart} />
        </div>
      </div>
    </article>
  );
}

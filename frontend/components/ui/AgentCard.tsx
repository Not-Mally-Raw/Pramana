"use client";

import Link from "next/link";
import clsx from "clsx";
import { RiskSparkline } from "@/components/charts/RiskSparkline";
import { lifecyclePalette, riskGradientStyle } from "@/lib/design-tokens";
import type { AgentFleetItem } from "@/lib/types";

interface AgentCardProps {
  agent: AgentFleetItem;
  compact?: boolean;
}

export function AgentCard({ agent, compact = false }: AgentCardProps) {
  const lifecycleColor = lifecyclePalette[agent.lifecycle_state] || lifecyclePalette.UNKNOWN;
  const isPaused = agent.lifecycle_state === "PAUSED";
  const isRecovering = agent.lifecycle_state === "RECOVERING";

  return (
    <Link
      href={`/agents/${agent.agent_addr}`}
      className={clsx(
        "panel block border px-3 py-3 transition duration-200 ease-out hover:border-white/25",
        isPaused && "risk-pulse border-critical-1/55",
        isRecovering && "amber-breathe border-warn-0/45",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-mono text-xs text-text-secondary">
            {agent.agent_addr.slice(0, 8)}...{agent.agent_addr.slice(-6)}
          </p>
          <div className="mt-1 inline-flex items-center gap-2 rounded-full border border-border-subtle px-2 py-0.5 text-[10px] uppercase tracking-[0.08em] text-text-secondary">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: lifecycleColor }} />
            {agent.lifecycle_state}
          </div>
        </div>

        <span className="rounded-full border border-border-subtle px-2 py-0.5 text-[10px] uppercase tracking-[0.08em] text-text-secondary">
          {agent.profile_type}
        </span>
      </div>

      <div className={clsx("mt-3", compact ? "h-8" : "h-12")}>
        <RiskSparkline values={agent.risk_trend} color={lifecycleColor} />
      </div>

      {!compact ? (
        <div className="mt-2 h-2 rounded-full bg-white/10">
          <div
            className="h-full rounded-full"
            style={{
              width: `${Math.max(4, Math.min(100, agent.latest_risk * 100))}%`,
              ...riskGradientStyle(agent.latest_risk),
            }}
          />
        </div>
      ) : null}
    </Link>
  );
}

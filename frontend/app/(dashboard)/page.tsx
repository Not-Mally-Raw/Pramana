"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { MetricCard } from "@/components/ui/MetricCard";
import { DecisionBadge } from "@/components/ui/DecisionBadge";
import { AgentCard } from "@/components/ui/AgentCard";
import { HowItWorksPanel } from "@/components/ui/HowItWorksPanel";
import { fetchAgents, fetchRealtimeMetrics } from "@/lib/api";
import type { DecisionEvent } from "@/lib/types";
import { useAlertsStore } from "@/stores/alerts";
import { useAgentsStore } from "@/stores/agents";
import { useDecisionsStore } from "@/stores/decisions";
import { useRuntimeConfigStore } from "@/stores/runtime";
import { riskGradientStyle } from "@/lib/design-tokens";

export default function CommandCenterPage() {
  const orgId = useRuntimeConfigStore((state) => state.orgId);
  const decisions = useDecisionsStore((state) => state.decisions);
  const addBatch = useDecisionsStore((state) => state.addBatch);
  const fleet = useAgentsStore((state) => state.fleet);
  const setFleet = useAgentsStore((state) => state.setFleet);
  const setAlertsFromMetrics = useAlertsStore((state) => state.setFromMetrics);

  const metricsQuery = useQuery({
    queryKey: ["metrics-realtime", orgId],
    queryFn: fetchRealtimeMetrics,
    refetchInterval: 5_000,
    staleTime: 4_000,
  });

  const agentsQuery = useQuery({
    queryKey: ["agents-list", orgId],
    queryFn: fetchAgents,
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (!metricsQuery.data) {
      return;
    }
    if (decisions.length === 0 && metricsQuery.data.recent_decisions.length > 0) {
      addBatch(metricsQuery.data.recent_decisions);
    }
    setFleet(metricsQuery.data.fleet);
    setAlertsFromMetrics(metricsQuery.data);
  }, [addBatch, decisions.length, metricsQuery.data, setAlertsFromMetrics, setFleet]);

  useEffect(() => {
    if (!agentsQuery.data || agentsQuery.data.length === 0) {
      return;
    }

    if (fleet.length > 0) {
      return;
    }

    setFleet(
      agentsQuery.data.map((agent) => ({
        agent_addr: agent.agent_addr,
        did: agent.did,
        profile_type: "generic",
        lifecycle_state: Number(agent.paused || 0) > 0 ? "PAUSED" : "ACTIVE",
        latest_risk: 0,
        risk_trend: [],
      })),
    );
  }, [agentsQuery.data, fleet.length, setFleet]);

  const data = metricsQuery.data;
  const recentDecisions = useMemo(() => decisions.slice(0, 20), [decisions]);

  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Command Center</h1>
          <p className="text-sm text-text-secondary">Three-second answer: is everything okay right now?</p>
        </div>
      </header>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <MetricCard
          title="Active Agents"
          value={data?.active_agents ?? 0}
          delta={data?.delta.active_agents ?? 0}
          sparkline={data?.sparkline.active_agents ?? []}
          tone="safe"
        />
        <MetricCard
          title="Decisions/Min"
          value={data?.decisions_per_min ?? 0}
          delta={data?.delta.decisions_per_min ?? 0}
          sparkline={data?.sparkline.decisions_per_min ?? []}
          tone="data"
        />
        <MetricCard
          title="Escalation Rate"
          value={`${(data?.escalation_rate_pct ?? 0).toFixed(2)}%`}
          delta={data?.delta.escalation_rate_pct ?? 0}
          sparkline={data?.sparkline.escalation_rate_pct ?? []}
          tone="warning"
        />
        <MetricCard
          title="Pause Rate"
          value={`${(data?.pause_rate_pct ?? 0).toFixed(2)}%`}
          delta={data?.delta.pause_rate_pct ?? 0}
          sparkline={data?.sparkline.pause_rate_pct ?? []}
          tone="critical"
        />
        <MetricCard
          title="Recovery Backlog"
          value={data?.recovery_backlog ?? 0}
          delta={data?.delta.recovery_backlog ?? 0}
          sparkline={data?.sparkline.recovery_backlog ?? []}
          tone="warning"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <article className="panel p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="card-label">Active Flow Feed</h2>
            <span className="text-xs text-text-secondary">Last 20 decisions</span>
          </div>

          {metricsQuery.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, idx) => (
                <div key={idx} className="h-12 animate-pulse rounded-lg bg-white/5" />
              ))}
            </div>
          ) : recentDecisions.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border-subtle p-6 text-sm text-text-secondary">
              No decisions yet. Submit a validation request to begin streaming.
            </div>
          ) : (
            <div className="space-y-2">
              {recentDecisions.map((decision) => (
                <DecisionRow key={`${decision.trace_id}-${decision.timestamp}`} decision={decision} />
              ))}
            </div>
          )}
        </article>

        <article className="panel p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="card-label">Agent Fleet</h2>
            <span className="text-xs text-text-secondary">Urgency sorted</span>
          </div>

          {fleet.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border-subtle p-6 text-sm text-text-secondary">
              No agents registered yet. Start with POST /agents/register.
            </div>
          ) : (
            <div className="grid gap-3">
              {fleet.map((agent) => (
                <AgentCard key={agent.agent_addr} agent={agent} compact />
              ))}
            </div>
          )}
        </article>
      </div>

      <HowItWorksPanel />
    </section>
  );
}

function DecisionRow({ decision }: { decision: DecisionEvent }) {
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    const id = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(id);
  }, []);

  const ageLabel = useMemo(() => {
    const ageSec = Math.max(0, Math.floor((Date.now() - decision.timestamp) / 1000));
    if (ageSec < 60) {
      return `${ageSec}s ago`;
    }
    const ageMin = Math.floor(ageSec / 60);
    if (ageMin < 60) {
      return `${ageMin}m ago`;
    }
    return `${Math.floor(ageMin / 60)}h ago`;
  }, [decision.timestamp]);

  return (
    <div
      className={`grid grid-cols-[1fr_auto] items-center gap-4 rounded-lg border border-border-subtle bg-white/[0.02] p-3 transition-all duration-300 ease-out ${entered ? "translate-y-0 opacity-100" : "-translate-y-3 opacity-0"}`}
      style={decision.decision === "PAUSE" ? { borderLeft: "4px solid #ff0040" } : undefined}
    >
      <div className="grid gap-2 md:grid-cols-[170px_120px_1fr_140px] md:items-center">
        <p className="font-mono text-xs text-text-secondary">
          {decision.agent_addr.slice(0, 10)}...{decision.agent_addr.slice(-4)}
        </p>
        <DecisionBadge decision={decision.decision} />
        <div className="h-2 rounded-full bg-white/10">
          <div
            className="h-full rounded-full"
            style={{
              width: `${Math.max(5, Math.min(100, decision.risk_score * 100))}%`,
              ...riskGradientStyle(decision.risk_score),
            }}
          />
        </div>
        <p className="text-xs text-text-secondary">{decision.top_agent || "fusion"}</p>
      </div>
      <time className="text-xs text-text-tertiary" title={new Date(decision.timestamp).toLocaleString()}>
        {ageLabel}
      </time>
    </div>
  );
}

"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { ComplianceExport } from "@/components/ui/ComplianceExport";
import { fetchRealtimeMetrics } from "@/lib/api";

export default function LifecyclePage() {
  const metricsQuery = useQuery({
    queryKey: ["lifecycle-metrics"],
    queryFn: fetchRealtimeMetrics,
    refetchInterval: 20_000,
  });

  const fleet = useMemo(() => metricsQuery.data?.fleet || [], [metricsQuery.data]);
  const transitions = metricsQuery.data?.lifecycle_transitions || [];

  const distribution = useMemo(() => {
    const total = Math.max(1, fleet.length);
    const counts = {
      ACTIVE: 0,
      DEGRADED: 0,
      RECOVERING: 0,
      PAUSED: 0,
      UNKNOWN: 0,
    };

    for (const item of fleet) {
      counts[item.lifecycle_state] = (counts[item.lifecycle_state] || 0) + 1;
    }

    return {
      total,
      counts,
      percentages: {
        ACTIVE: (counts.ACTIVE / total) * 100,
        DEGRADED: (counts.DEGRADED / total) * 100,
        RECOVERING: (counts.RECOVERING / total) * 100,
        PAUSED: (counts.PAUSED / total) * 100,
        UNKNOWN: (counts.UNKNOWN / total) * 100,
      },
    };
  }, [fleet]);

  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Lifecycle and Audit</h1>
        <p className="text-sm text-text-secondary">Fleet state distribution, transition timeline, and compliance bundle export.</p>
      </header>

      <article className="panel p-4">
        <h2 className="card-label mb-3">State Distribution</h2>
        <div className="h-8 overflow-hidden rounded-full border border-border-subtle bg-surface-card">
          <div className="flex h-full w-full">
            <div style={{ width: `${distribution.percentages.ACTIVE}%` }} className="bg-pass-0" />
            <div style={{ width: `${distribution.percentages.DEGRADED}%` }} className="bg-warn-1" />
            <div style={{ width: `${distribution.percentages.RECOVERING}%` }} className="bg-warn-0" />
            <div style={{ width: `${distribution.percentages.PAUSED}%` }} className="bg-critical-1" />
            <div style={{ width: `${distribution.percentages.UNKNOWN}%` }} className="bg-zinc-600" />
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-3 text-xs text-text-secondary">
          <span>ACTIVE {distribution.counts.ACTIVE}</span>
          <span>DEGRADED {distribution.counts.DEGRADED}</span>
          <span>RECOVERING {distribution.counts.RECOVERING}</span>
          <span>PAUSED {distribution.counts.PAUSED}</span>
          <span>UNKNOWN {distribution.counts.UNKNOWN}</span>
        </div>
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Transition Timeline</h2>
        {transitions.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border-subtle p-6 text-sm text-text-secondary">No lifecycle transitions in selected window.</div>
        ) : (
          <div className="space-y-2">
            {transitions.slice(0, 30).map((transition) => (
              <div key={`${transition.agent_addr}-${transition.timestamp}`} className="rounded-lg border border-border-subtle bg-surface-card p-3">
                <p className="font-mono text-xs text-text-secondary">{transition.agent_addr}</p>
                <p className="mt-1 text-sm text-white">{transition.from_state} to {transition.to_state}</p>
                <p className="text-xs text-text-secondary">{transition.reason || "state transition"}</p>
              </div>
            ))}
          </div>
        )}
      </article>

      <ComplianceExport />
    </section>
  );
}

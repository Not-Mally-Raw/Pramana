"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchRealtimeMetrics, fetchTraces } from "@/lib/api";

export default function GovernancePage() {
  const tracesQuery = useQuery({
    queryKey: ["governance-traces"],
    queryFn: () => fetchTraces(500),
  });

  const metricsQuery = useQuery({
    queryKey: ["governance-metrics"],
    queryFn: fetchRealtimeMetrics,
    refetchInterval: 20_000,
  });

  const composition = useMemo(() => {
    const traces = tracesQuery.data || [];
    const grouped = new Map<string, { PASS: number; ESCALATE: number; PAUSE: number }>();

    for (const trace of traces) {
      const day = new Date(trace.created_at).toLocaleDateString();
      const row = grouped.get(day) || { PASS: 0, ESCALATE: 0, PAUSE: 0 };
      const action = trace.council_action.toUpperCase();
      if (action.includes("PAUSE")) {
        row.PAUSE += 1;
      } else if (action.includes("ESCALATE")) {
        row.ESCALATE += 1;
      } else {
        row.PASS += 1;
      }
      grouped.set(day, row);
    }

    return Array.from(grouped.entries()).map(([day, counts]) => ({ day, ...counts }));
  }, [tracesQuery.data]);

  const transitions = metricsQuery.data?.lifecycle_transitions || [];

  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Governance</h1>
        <p className="text-sm text-text-secondary">Decision composition, Guardian activity, and council agreement quality.</p>
      </header>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Decision Composition</h2>
        {composition.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border-subtle p-6 text-sm text-text-secondary">No decision history available for this window.</div>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={composition}>
              <CartesianGrid stroke="rgba(255,255,255,0.08)" strokeDasharray="3 3" />
              <XAxis dataKey="day" stroke="#a1a1aa" tick={{ fontSize: 11 }} />
              <YAxis stroke="#a1a1aa" tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={{ background: "#111111", border: "1px solid #1a1a1a" }} />
              <Legend />
              <Bar dataKey="PASS" stackId="a" fill="#00ff87" />
              <Bar dataKey="ESCALATE" stackId="a" fill="#ffb800" />
              <Bar dataKey="PAUSE" stackId="a" fill="#ff0040" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </article>

      <article className="panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="card-label">Guardian Activity Timeline</h2>
          <span className="text-xs text-text-secondary">Pending votes: {metricsQuery.data?.pending_guardian_votes ?? 0}</span>
        </div>

        {transitions.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border-subtle p-6 text-sm text-text-secondary">
            Guardian not deployed or no lifecycle activity in selected window.
          </div>
        ) : (
          <div className="space-y-2">
            {transitions.slice(0, 20).map((transition) => (
              <div key={`${transition.agent_addr}-${transition.timestamp}`} className="rounded-lg border border-border-subtle bg-surface-card p-3">
                <p className="font-mono text-xs text-text-secondary">{transition.agent_addr}</p>
                <p className="mt-1 text-sm text-white">
                  {transition.from_state} to {transition.to_state}
                </p>
                <p className="text-xs text-text-secondary">{transition.reason || "State change"}</p>
              </div>
            ))}
          </div>
        )}
      </article>
    </section>
  );
}

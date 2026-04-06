"use client";

import { useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AgentScoreMatrix } from "@/components/charts/AgentScoreMatrix";
import { TraceDAGRenderer } from "@/components/dag/TraceDAGRenderer";
import { DecisionBadge } from "@/components/ui/DecisionBadge";
import { fetchTraces } from "@/lib/api";

const L1_AGENTS = [
  "sanctions",
  "aa_validator",
  "network",
  "operations",
  "hallucination",
  "injection",
  "validator",
  "economic_sanity",
  "drift",
];

export default function AgentDetailPage() {
  const params = useParams<{ address: string }>();
  const address = decodeURIComponent(params.address || "");
  const [selectedTrace, setSelectedTrace] = useState<string>("");

  const tracesQuery = useQuery({
    queryKey: ["agent-traces", address],
    queryFn: () => fetchTraces(600),
  });

  const agentTraces = useMemo(
    () => (tracesQuery.data || []).filter((trace) => trace.agent_addr.toLowerCase() === address.toLowerCase()),
    [address, tracesQuery.data],
  );

  const selected = useMemo(() => {
    if (!selectedTrace) {
      return agentTraces[0] || null;
    }
    return agentTraces.find((trace) => trace.trace_hash === selectedTrace) || agentTraces[0] || null;
  }, [agentTraces, selectedTrace]);

  const matrix = useMemo(() => {
    const slice = agentTraces.slice(0, 50);
    return L1_AGENTS.map((agentName) =>
      slice.map((trace) => {
        try {
          const parsed = JSON.parse(trace.dag_json || "{}");
          const nodes = parsed?.nodes || {};
          for (const [name, payload] of Object.entries(nodes)) {
            const normalized = name.replace(/[^a-zA-Z0-9_]/g, "").toLowerCase();
            if (normalized.includes(agentName.replace(/[^a-zA-Z0-9_]/g, ""))) {
              return Number((payload as { score?: number }).score || 0);
            }
          }
        } catch {
          return 0;
        }
        return 0;
      }),
    );
  }, [agentTraces]);

  const avgRisk = useMemo(() => {
    if (agentTraces.length === 0) {
      return 0;
    }
    return agentTraces.slice(0, 1000).reduce((sum, trace) => sum + Number(trace.risk_score || 0), 0) / Math.min(1000, agentTraces.length);
  }, [agentTraces]);

  return (
    <section className="space-y-4">
      <header className="panel p-4">
        <h1 className="text-xl font-semibold">Agent Detail</h1>
        <p className="mt-1 font-mono text-xs text-text-secondary">{address}</p>

        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <StatRing label="Success Rate" value={agentTraces.length > 0 ? (agentTraces.filter((trace) => trace.council_action.toUpperCase().includes("PASS")).length / agentTraces.length) * 100 : 0} color="#3b82f6" />
          <StatRing label="Uptime Score" value={agentTraces.length > 0 ? 100 - (agentTraces.filter((trace) => trace.council_action.toUpperCase().includes("PAUSE")).length / agentTraces.length) * 100 : 100} color="#00ff87" />
          <StatRing label="L1 Risk Trend" value={avgRisk * 100} color="#ffb800" />
        </div>
      </header>

      <article className="panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="card-label">Decision Timeline</h2>
          <span className="text-xs text-text-secondary">{agentTraces.length} decisions</span>
        </div>

        {agentTraces.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border-subtle p-5 text-sm text-text-secondary">No decisions found for this agent.</div>
        ) : (
          <div className="flex gap-2 overflow-x-auto pb-2">
            {agentTraces.slice(0, 120).map((trace) => {
              const decision = trace.council_action.toUpperCase().includes("PAUSE")
                ? "PAUSE"
                : trace.council_action.toUpperCase().includes("ESCALATE")
                  ? "ESCALATE"
                  : "PASS";

              return (
                <button
                  key={trace.trace_hash}
                  type="button"
                  onClick={() => setSelectedTrace(trace.trace_hash)}
                  className="min-w-[90px] rounded-lg border border-border-subtle bg-surface-card px-2 py-2 text-left hover:border-white/30"
                >
                  <DecisionBadge decision={decision} />
                  <p className="mt-1 text-[11px] text-text-secondary">{new Date(trace.created_at).toLocaleTimeString()}</p>
                  <p className="text-[11px] text-text-tertiary">risk {(trace.risk_score * 100).toFixed(1)}%</p>
                </button>
              );
            })}
          </div>
        )}
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">L1 Agent Score Matrix</h2>
        <AgentScoreMatrix agentNames={L1_AGENTS} values={matrix} />
      </article>

      {selected ? (
        <TraceDAGRenderer traceId={selected.trace_hash} mode="replay" />
      ) : (
        <div className="panel p-4 text-sm text-text-secondary">No trace selected for replay.</div>
      )}
    </section>
  );
}

function StatRing({ label, value, color }: { label: string; value: number; color: string }) {
  const normalized = Math.max(0, Math.min(100, value));
  const radius = 32;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (normalized / 100) * circumference;

  return (
    <div className="elevated rounded-xl p-3">
      <p className="text-[11px] uppercase tracking-[0.08em] text-text-secondary">{label}</p>
      <div className="mt-2 flex items-center gap-3">
        <svg width="78" height="78" viewBox="0 0 78 78">
          <circle cx="39" cy="39" r={radius} stroke="rgba(255,255,255,0.1)" strokeWidth="8" fill="transparent" />
          <circle
            cx="39"
            cy="39"
            r={radius}
            stroke={color}
            strokeWidth="8"
            fill="transparent"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            strokeLinecap="round"
            transform="rotate(-90 39 39)"
          />
        </svg>
        <p className="number-display text-xl">{normalized.toFixed(1)}%</p>
      </div>
    </div>
  );
}

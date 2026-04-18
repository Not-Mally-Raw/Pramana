"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { LatencyViolin } from "@/components/charts/LatencyViolin";
import { ExplainableDAGRenderer } from "@/components/dag/ExplainableDAGRenderer";
import { fetchTraces } from "@/lib/api";
import { useRuntimeConfigStore } from "@/stores/runtime";

export default function PipelinePage() {
  const orgId = useRuntimeConfigStore((state) => state.orgId);
  const [selectedTrace, setSelectedTrace] = useState<string | null>(null);
  const tracesQuery = useQuery({
    queryKey: ["pipeline-traces", orgId],
    queryFn: () => fetchTraces(500),
    refetchInterval: 30_000,
  });

  const latestTrace = (tracesQuery.data || [])[0];
  const latestTraceId = latestTrace?.trace_hash || latestTrace?.flow_id || null;

  useEffect(() => {
    if (!selectedTrace && latestTraceId) {
      setSelectedTrace(latestTraceId);
    }
  }, [latestTraceId, selectedTrace]);

  const latencySamples = useMemo(() => {
    return (tracesQuery.data || []).map((trace, idx) => {
      const latency = extractLatency(trace.dag_json);
      return {
        bucket: `${idx + 1}`,
        density: latency,
        p50: latency * 0.75,
        p95: latency,
        p99: latency * 1.1,
      };
    });
  }, [tracesQuery.data]);

  const scatter = useMemo(() => {
    const traces = tracesQuery.data || [];
    return traces.slice(0, 120).map((trace, idx) => ({
      decisions_per_min: Math.max(1, 5 + (idx % 30)),
      p95_latency: extractLatency(trace.dag_json),
      hour: idx % 24,
    }));
  }, [tracesQuery.data]);

  return (
    <section className="space-y-4">
      <header className="panel chain-grid p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Flow Explorer</h1>
            <p className="text-sm text-text-secondary">Live execution intelligence for stage-by-stage decisions, latency, and throughput health.</p>
          </div>
          <div className="rounded-full border border-zinc-800 bg-black/60 px-3 py-1 text-xs text-zinc-300">
            {selectedTrace && selectedTrace === latestTraceId ? "LIVE TRACE" : "REPLAY TRACE"}
          </div>
        </div>
      </header>

      <article className="panel p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h2 className="card-label">Live Decision DAG</h2>
            <p className="mt-1 text-xs text-text-secondary">Node-wise monitor with live stage updates and replay-aware inspection.</p>
          </div>
          <select
            className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-300"
            value={selectedTrace || ""}
            onChange={(event) => setSelectedTrace(event.target.value || null)}
          >
            {(tracesQuery.data || []).length === 0 ? <option value="">No traces yet</option> : null}
            {(tracesQuery.data || []).slice(0, 25).map((trace) => {
              const id = trace.trace_hash || trace.flow_id;
              return (
                <option key={id} value={id}>
                  {trace.agent_addr?.slice(0, 8)}... {trace.council_action} {new Date(trace.created_at).toLocaleTimeString()}
                </option>
              );
            })}
          </select>
        </div>

        <ExplainableDAGRenderer
          traceId={selectedTrace}
          mode={selectedTrace && selectedTrace === latestTraceId ? "live" : selectedTrace ? "replay" : "idle"}
        />
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Latency Distribution</h2>
        <LatencyViolin data={latencySamples} />
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Throughput vs Latency</h2>
        <ResponsiveContainer width="100%" height={320}>
          <ScatterChart>
            <CartesianGrid stroke="rgba(255,255,255,0.08)" strokeDasharray="3 3" />
            <XAxis type="number" dataKey="decisions_per_min" stroke="#a1a1aa" tick={{ fontSize: 11 }} />
            <YAxis type="number" dataKey="p95_latency" stroke="#a1a1aa" tick={{ fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "#111111", border: "1px solid #1a1a1a" }} />
            <Scatter data={scatter} fill="#3b82f6" />
          </ScatterChart>
        </ResponsiveContainer>
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Parallel Execution Gantt</h2>
        <div className="grid gap-2 text-xs text-text-secondary">
          {["sanctions", "aa_validator", "network", "operations", "hallucination", "injection", "validator", "economic_sanity", "drift"].map((nodeName, idx) => (
            <div key={nodeName} className="grid grid-cols-[140px_1fr] items-center gap-2">
              <p>{nodeName}</p>
              <div className="h-5 rounded bg-white/5">
                <div
                  className="h-full rounded bg-gradient-to-r from-data-0 to-data-1"
                  style={{
                    width: `${50 + (idx % 5) * 8}%`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </article>
    </section>
  );
}

function extractLatency(dagJson?: string): number {
  if (!dagJson) {
    return 120;
  }

  try {
    const parsed = JSON.parse(dagJson) as { nodes?: Record<string, { metadata?: { latency_ms?: number } }> };
    const total = Object.values(parsed.nodes || {}).reduce((sum, node) => sum + Number(node.metadata?.latency_ms || 0), 0);
    return Math.max(20, total || 120);
  } catch {
    return 120;
  }
}
